import { describe, expect, it } from "vitest";
import {
  evaluate,
  metadata,
  type Result,
} from "../../src/internal/engine/evaluate.js";
import type { Ruleset } from "../../src/internal/engine/model.js";
import { parseRuleset } from "../../src/internal/engine/parse.js";
import { cl, flagJson, ruleFlag, rulesetJson } from "../helpers/ruleset.js";

type Json = Record<string, unknown>;

function build(flags: Json[], segments: Json = {}): Ruleset {
  return parseRuleset(JSON.stringify(rulesetJson(flags, segments)));
}

function expectError(r: Result, def: unknown, code: string): void {
  expect(r).toMatchObject({
    value: def,
    variant: null,
    reason: "ERROR",
    errorCode: code,
    ruleIndex: null,
    prerequisite: null,
  });
  expect(r.errorMessage).not.toBe("");
}

function expectServed(r: Result, variant: string, reason: string): void {
  expect(r.variant).toBe(variant);
  expect(r.reason).toBe(reason);
  expect(r.errorCode).toBeNull();
}

describe("evaluate errors", () => {
  const rs = build([flagJson("f")]);

  it.each([[[]], ["user"], [null], [1]])(
    "context %j is INVALID_CONTEXT",
    (ctx) => {
      expectError(evaluate(rs, "f", ctx, "d"), "d", "INVALID_CONTEXT");
    },
  );

  it("a missing flag is FLAG_NOT_FOUND", () => {
    expectError(evaluate(rs, "nope", {}, "d"), "d", "FLAG_NOT_FOUND");
  });

  it("a flag named __proto__ or constructor is an ordinary flag", () => {
    const named = build([flagJson("__proto__"), flagJson("constructor")]);
    expectServed(evaluate(named, "__proto__", {}, "d"), "on", "STATIC");
    expectServed(evaluate(named, "constructor", {}, "d"), "on", "STATIC");
  });

  it("an inherited name is not a flag", () => {
    expectError(evaluate(rs, "toString", {}, "d"), "d", "FLAG_NOT_FOUND");
    expectError(evaluate(rs, "__proto__", {}, "d"), "d", "FLAG_NOT_FOUND");
  });
});

describe("evaluate reasons", () => {
  it("serves STATIC for a flag with nothing but a fallthrough", () => {
    const r = evaluate(build([flagJson("f")]), "f", {}, "d");
    expect(r).toEqual({
      value: "on",
      variant: "on",
      reason: "STATIC",
      errorCode: null,
      errorMessage: "",
      ruleIndex: null,
      prerequisite: null,
    });
    expect(metadata(r)).toEqual({});
  });

  it("serves DEFAULT when a flag has targets or rules that do not match", () => {
    const rs = build([
      flagJson("t", { targets: [{ variation: "off", values: ["x"] }] }),
      ruleFlag("r", cl("a", "in", false, "x")),
    ]);
    expectServed(evaluate(rs, "t", { key: "u" }, "d"), "on", "DEFAULT");
    expectServed(evaluate(rs, "r", {}, "d"), "off", "DEFAULT");
  });

  it("serves the off variation when disabled", () => {
    const rs = build([flagJson("f", { enabled: false })]);
    expect(evaluate(rs, "f", {}, "d")).toMatchObject({
      value: "off",
      variant: "off",
      reason: "DISABLED",
    });
  });

  it("serves the default without a variant when disabled without an off variation", () => {
    const rs = build([flagJson("f", { enabled: false, off_variation: null })]);
    expect(evaluate(rs, "f", {}, "d")).toEqual({
      value: "d",
      variant: null,
      reason: "DISABLED",
      errorCode: null,
      errorMessage: "",
      ruleIndex: null,
      prerequisite: null,
    });
  });
});

describe("evaluate type check", () => {
  const rs = build([
    flagJson("s"),
    flagJson("b", {
      type: "boolean",
      variations: [
        { key: "on", value: true },
        { key: "off", value: false },
      ],
    }),
    flagJson("n", {
      type: "number",
      variations: [
        { key: "on", value: 1.5 },
        { key: "off", value: 0 },
      ],
    }),
    flagJson("j", {
      type: "json",
      variations: [
        { key: "on", value: null },
        { key: "off", value: {} },
      ],
    }),
    flagJson("x", { enabled: false, off_variation: null }),
  ]);

  it.each([
    ["s", "string"],
    ["b", "boolean"],
    ["n", "number"],
    ["j", "json"],
    ["s", "json"],
    ["s", undefined],
  ] as const)("%s as %s passes", (key, type) => {
    expect(evaluate(rs, key, {}, "d", type).errorCode).toBeNull();
  });

  it.each([
    ["s", "boolean"],
    ["s", "number"],
    ["b", "number"],
    ["b", "string"],
    ["n", "string"],
    ["j", "string"],
  ] as const)("%s as %s is TYPE_MISMATCH", (key, type) => {
    expectError(evaluate(rs, key, {}, "d", type), "d", "TYPE_MISMATCH");
  });

  it("a null variation value served for json", () => {
    const r = evaluate(rs, "j", {}, "d", "json");
    expect(r.value).toBeNull();
    expectServed(r, "on", "STATIC");
  });

  it("is not checked when no variation was served", () => {
    expect(evaluate(rs, "x", {}, 7, "boolean")).toMatchObject({
      value: 7,
      reason: "DISABLED",
      errorCode: null,
    });
  });
});

describe("evaluate prerequisites", () => {
  it("fails with the first unmet prerequisite", () => {
    const rs = build([
      flagJson("ok"),
      flagJson("off", { enabled: false }),
      flagJson("f", {
        prerequisites: [
          { flag: "ok", variation: "on" },
          { flag: "off", variation: "on" },
          { flag: "missing", variation: "on" },
        ],
      }),
    ]);
    const r = evaluate(rs, "f", {}, "d");
    expect(r).toMatchObject({
      value: "off",
      variant: "off",
      reason: "PREREQUISITE_FAILED",
      prerequisite: "off",
    });
    expect(metadata(r)).toEqual({ prerequisite: "off" });
  });

  it("fails on a missing prerequisite flag or a different variant", () => {
    const rs = build([
      flagJson("p"),
      flagJson("a", { prerequisites: [{ flag: "missing", variation: "on" }] }),
      flagJson("b", { prerequisites: [{ flag: "p", variation: "off" }] }),
    ]);
    expect(evaluate(rs, "a", {}, "d").prerequisite).toBe("missing");
    expect(evaluate(rs, "b", {}, "d").reason).toBe("PREREQUISITE_FAILED");
  });

  it("passes when the prerequisite serves its off variation and that is the one required", () => {
    const rs = build([
      flagJson("p", { prerequisites: [{ flag: "missing", variation: "on" }] }),
      flagJson("f", { prerequisites: [{ flag: "p", variation: "off" }] }),
    ]);
    expectServed(evaluate(rs, "f", {}, "d"), "on", "DEFAULT");
  });

  it("fails when the prerequisite serves no variation", () => {
    const rs = build([
      flagJson("p", {
        off_variation: null,
        prerequisites: [{ flag: "missing", variation: "on" }],
      }),
      flagJson("f", { prerequisites: [{ flag: "p", variation: "off" }] }),
    ]);
    expect(evaluate(rs, "f", {}, "d").reason).toBe("PREREQUISITE_FAILED");
  });

  function chain(n: number): Ruleset {
    // flag-0 requires flag-1, ..., flag-(n-1) requires flag-n.
    const flags = Array.from({ length: n + 1 }, (_, i) =>
      flagJson(`flag-${i}`, {
        prerequisites:
          i < n ? [{ flag: `flag-${i + 1}`, variation: "on" }] : [],
      }),
    );
    return build(flags);
  }

  it("follows a chain of 32 prerequisites", () => {
    expectServed(evaluate(chain(32), "flag-0", {}, "d"), "on", "DEFAULT");
  });

  it("refuses a chain of 33 prerequisites", () => {
    expectError(evaluate(chain(33), "flag-0", {}, "d"), "d", "GENERAL");
  });

  it("refuses a cycle", () => {
    const rs = build([
      flagJson("a", { prerequisites: [{ flag: "b", variation: "on" }] }),
      flagJson("b", { prerequisites: [{ flag: "a", variation: "on" }] }),
    ]);
    expectError(evaluate(rs, "a", {}, "d"), "d", "GENERAL");
  });
});

describe("evaluate targets and rules", () => {
  const rs = build([
    flagJson("f", {
      targets: [
        { variation: "off", values: ["u1", "42"] },
        { variation: "on", values: ["u1", "u2"] },
      ],
      rules: [
        { clauses: [cl("a", "in", false, 1)], serve: { variation: "off" } },
        { clauses: [cl("a", "in", false, 2)], serve: { variation: "on" } },
      ],
      fallthrough: { variation: "off" },
    }),
  ]);

  it("serves the first target containing the key", () => {
    expectServed(
      evaluate(rs, "f", { key: "u1" }, "d"),
      "off",
      "TARGETING_MATCH",
    );
    expectServed(
      evaluate(rs, "f", { key: "u2" }, "d"),
      "on",
      "TARGETING_MATCH",
    );
    expect(evaluate(rs, "f", { key: "u2" }, "d").ruleIndex).toBeNull();
  });

  it("matches targets only with a string key", () => {
    expectServed(evaluate(rs, "f", { key: 42 }, "d"), "off", "DEFAULT");
  });

  it("reports the index of the matching rule", () => {
    const r = evaluate(rs, "f", { a: 2 }, "d");
    expectServed(r, "on", "TARGETING_MATCH");
    expect(r.ruleIndex).toBe(1);
    expect(metadata(r)).toEqual({ rule_index: 1 });
    expect(evaluate(rs, "f", { a: 1 }, "d").ruleIndex).toBe(0);
  });

  it("needs every clause of a rule", () => {
    const rs = build([
      ruleFlag("f", cl("a", "in", false, 1), cl("b", "in", false, 2)),
    ]);
    expect(evaluate(rs, "f", { a: 1, b: 2 }, "d").variant).toBe("on");
    expect(evaluate(rs, "f", { a: 1, b: 3 }, "d").variant).toBe("off");
  });
});

describe("evaluate clauses", () => {
  const matches = (clause: Json, ctx: Json): boolean =>
    evaluate(build([ruleFlag("f", clause)]), "f", ctx, "d").variant === "on";

  it.each([
    ["missing attribute", false, {}, false],
    ["missing attribute, negated", true, {}, false],
    ["null attribute, negated", true, { a: null }, false],
    ["wrong type", false, { a: 1 }, false],
    ["wrong type, negated", true, { a: 1 }, true],
    ["empty array", false, { a: [] }, false],
    ["empty array, negated", true, { a: [] }, true],
    ["array with a match", false, { a: [1, "x"] }, true],
    ["array with a match, negated", true, { a: [1, "x"] }, false],
    ["object", false, { a: { x: "x" } }, false],
    ["nested array", false, { a: [["x"]] }, false],
  ] as const)("%s", (_name, negate, ctx, want) => {
    expect(matches(cl("a", "contains", negate, "x"), ctx)).toBe(want);
  });

  it.each([
    ["in", ["a", 1], "a", true],
    ["in", ["a", 1], 1.0, true],
    ["in", [1], true, false],
    ["in", [true], true, true],
    ["in", ["1"], 1, false],
    ["not_in", ["a"], "b", true],
    ["not_in", ["a"], "a", false],
    ["not_in", ["a"], [{}], false],
    ["contains", ["ell"], "hello", true],
    ["contains", ["x"], "hello", false],
    ["starts_with", ["he"], "hello", true],
    ["starts_with", ["lo"], "hello", false],
    ["ends_with", ["lo"], "hello", true],
    ["ends_with", ["he"], "hello", false],
    ["matches", ["^h.l"], "hello", true],
    ["matches", ["^\\d+$"], "12a", false],
    ["matches", ["^\\d+$"], 12, false],
    ["lt", [5], 4, true],
    ["lt", [5], 5, false],
    ["lte", [5], 5, true],
    ["gt", [5], 6, true],
    ["gt", [5], "6", false],
    ["gte", [5], 5, true],
    ["gte", [5], 4.9, false],
    ["lt", [5], true, false],
    ["semver_eq", ["1.0.0"], "1.0.0+build", true],
    ["semver_lt", ["1.0.0"], "1.0.0-rc.1", true],
    ["semver_gt", ["1.0.0"], "1.0.1", true],
    ["semver_gt", ["1.0.0"], "v1.0.1", false],
    ["before", ["2026-01-01T00:00:00Z"], "2025-12-31T23:59:59Z", true],
    ["before", ["2026-01-01T00:00:00Z"], 1767225599999, true],
    ["before", ["2026-01-01T00:00:00Z"], 1767225600000, false],
    ["after", ["2026-01-01T00:00:00Z"], "2026-01-01T00:00:00.001Z", true],
    ["after", ["2026-01-01T00:00:00Z"], "not a date", false],
    ["after", [0], 1, false],
  ] as const)("%s %j against %j is %s", (op, values, attr, want) => {
    expect(matches(cl("a", op, false, ...values), { a: attr })).toBe(want);
  });

  it("reads context attributes as own properties only", () => {
    const rs = build([
      ruleFlag("p", cl("__proto__", "in", false, "x")),
      ruleFlag("c", cl("constructor", "in", false, "x")),
    ]);
    const own = JSON.parse('{"__proto__": "x", "constructor": "x"}') as Json;
    expect(evaluate(rs, "p", own, "d").variant).toBe("on");
    expect(evaluate(rs, "c", own, "d").variant).toBe("on");
    expect(evaluate(rs, "p", {}, "d").variant).toBe("off");
    expect(evaluate(rs, "c", {}, "d").variant).toBe("off");
    const inherited = Object.create({ a: "x" }) as Json;
    expect(matches(cl("a", "in", false, "x"), inherited)).toBe(false);
    expect(
      evaluate(
        build([
          flagJson("f", { targets: [{ variation: "off", values: ["k"] }] }),
        ]),
        "f",
        Object.create({ key: "k" }),
        "d",
      ).variant,
    ).toBe("on");
  });
});

describe("evaluate segments", () => {
  const rs = build(
    [
      ruleFlag("f", cl("ignored", "in_segment", false, "s", "unknown")),
      ruleFlag("n", cl("ignored", "in_segment", true, "s")),
      ruleFlag("u", cl("ignored", "in_segment", false, "unknown")),
    ],
    {
      s: {
        included: ["in", "both"],
        excluded: ["out", "both"],
        rules: [{ clauses: [cl("org", "in", false, "acme")] }],
      },
    },
  );

  it.each([
    ["included", { key: "in" }, true],
    ["excluded before included", { key: "both" }, false],
    ["excluded even when a rule matches", { key: "out", org: "acme" }, false],
    ["by rule", { key: "other", org: "acme" }, true],
    ["by rule without a key", { org: "acme" }, true],
    ["a number key is not looked up", { key: 1, org: "x" }, false],
    ["no match", { key: "other" }, false],
  ] as const)("%s", (_name, ctx, want) => {
    expect(evaluate(rs, "f", ctx, "d").variant).toBe(want ? "on" : "off");
    expect(evaluate(rs, "n", ctx, "d").variant).toBe(want ? "off" : "on");
  });

  it("an unknown segment never matches", () => {
    expect(evaluate(rs, "u", { key: "in" }, "d").variant).toBe("off");
  });
});

describe("evaluate rollouts", () => {
  const weights = [
    { variation: "on", weight: 30000 },
    { variation: "off", weight: 70000 },
  ];
  const rs = build([
    flagJson("f", { fallthrough: { rollout: { bucket_by: "key", weights } } }),
    flagJson("g", { fallthrough: { rollout: { bucket_by: "org", weights } } }),
  ]);

  it("splits by the bucket of the attribute", () => {
    // bucket("f", "u") is 68258: past "on" (30000), so "off".
    expectServed(evaluate(rs, "f", { key: "u" }, "d"), "off", "SPLIT");
  });

  it("buckets by another attribute", () => {
    const byOrg = new Set(
      Array.from(
        { length: 50 },
        (_, i) => evaluate(rs, "g", { key: "same", org: `o${i}` }, "d").variant,
      ),
    );
    expect(byOrg).toEqual(new Set(["on", "off"]));
  });

  it("uses bucket 0 when the value is missing or cannot be bucketed", () => {
    for (const ctx of [{}, { org: null }, { org: 1.5 }, { org: true }]) {
      expectServed(evaluate(rs, "g", ctx, "d"), "on", "SPLIT");
    }
  });
});
