import { describe, expect, it } from "vitest";
import type { Clause } from "../../src/internal/engine/model.js";
import { ParseError, parseRuleset } from "../../src/internal/engine/parse.js";
import {
  compareSemver,
  parseSemver,
} from "../../src/internal/engine/semver.js";
import { cl, flagJson, rulesetJson } from "../helpers/ruleset.js";

type Json = Record<string, unknown>;

/** base is a valid ruleset: flag f with one rule, rollout fallthrough, segment s. */
function base(): Json {
  return rulesetJson(
    [
      flagJson("f", {
        rules: [
          {
            description: "r",
            clauses: [cl("country", "in", false, "NL")],
            serve: { variation: "on" },
          },
        ],
        fallthrough: {
          rollout: {
            bucket_by: "key",
            weights: [
              { variation: "on", weight: 50000 },
              { variation: "off", weight: 50000 },
            ],
          },
        },
      }),
    ],
    {
      s: {
        included: ["a"],
        excluded: ["b"],
        rules: [{ clauses: [cl("org", "in", false, "acme")] }],
      },
    },
  );
}

const flag = (r: Json) => (r["flags"] as Json)["f"] as Json;
const rule = (r: Json) => (flag(r)["rules"] as Json[])[0] as Json;
const clause = (r: Json) => (rule(r)["clauses"] as Json[])[0] as Json;
const rollout = (r: Json) =>
  (flag(r)["fallthrough"] as Json)["rollout"] as Json;
const segment = (r: Json) => (r["segments"] as Json)["s"] as Json;

function parseError(input: unknown): ParseError {
  try {
    parseRuleset(input);
  } catch (err) {
    expect(err).toBeInstanceOf(ParseError);
    return err as ParseError;
  }
  throw new Error("parseRuleset did not throw");
}

function firstClause(root: Json): Clause {
  return parseRuleset(root).flags.get("f")!.rules[0]!.clauses[0]!;
}

describe("parseRuleset", () => {
  it("parses a valid ruleset from text and from a tree", () => {
    for (const input of [JSON.stringify(base()), base()]) {
      const rs = parseRuleset(input);
      expect(rs.project).toBe("p");
      expect(rs.environment).toBe("e");
      expect(rs.warnings).toEqual([]);
      const f = rs.flags.get("f")!;
      expect(f.key).toBe("f");
      expect(f.type).toBe("string");
      expect(f.enabled).toBe(true);
      expect(f.version).toBe(1);
      expect([...f.variations.keys()]).toEqual(["on", "off"]);
      expect(f.variations.get("on")!.value).toBe("on");
      expect(f.offVariation).toBe("off");
      expect(f.prerequisites).toEqual([]);
      expect(f.targets).toEqual([]);
      expect(f.rules).toHaveLength(1);
      expect(f.rules[0]!.description).toBe("r");
      expect(f.rules[0]!.serve).toEqual({ variation: "on", rollout: null });
      expect(f.fallthrough).toEqual({
        variation: null,
        rollout: {
          bucketBy: "key",
          weights: [
            { variation: "on", weight: 50000 },
            { variation: "off", weight: 50000 },
          ],
        },
      });
      const s = rs.segments.get("s")!;
      expect(s.included).toEqual(new Set(["a"]));
      expect(s.excluded).toEqual(new Set(["b"]));
      expect(s.rules).toHaveLength(1);
      expect(s.rules[0]![0]!.attribute).toBe("org");
    }
  });

  it("parses prerequisites, targets and a null off_variation", () => {
    const root = base();
    Object.assign(flag(root), {
      off_variation: null,
      prerequisites: [{ flag: "g", variation: "on" }],
      targets: [{ variation: "off", values: ["u1", "u2"] }],
    });
    const f = parseRuleset(root).flags.get("f")!;
    expect(f.offVariation).toBeNull();
    expect(f.prerequisites).toEqual([{ flag: "g", variation: "on" }]);
    expect(f.targets).toEqual([
      { variation: "off", values: new Set(["u1", "u2"]) },
    ]);
  });

  it("keeps a null variation value", () => {
    const root = base();
    flag(root)["variations"] = [
      { key: "on", value: null },
      { key: "off", value: { a: [1] } },
    ];
    const f = parseRuleset(root).flags.get("f")!;
    expect(f.variations.get("on")!.value).toBeNull();
    expect(f.variations.get("off")!.value).toEqual({ a: [1] });
  });

  it("defaults the rule description to empty", () => {
    const root = base();
    delete rule(root)["description"];
    expect(parseRuleset(root).flags.get("f")!.rules[0]!.description).toBe("");
  });

  it("accepts schema_version 1.0, which JS cannot tell from 1", () => {
    expect(() =>
      parseRuleset(
        '{"schema_version": 1.0, "project": "p", "environment": "e", "flags": {}, "segments": {}}',
      ),
    ).not.toThrow();
  });

  it("ignores unknown fields", () => {
    const root = base();
    root["extra"] = { x: 1 };
    flag(root)["extra"] = true;
    clause(root)["extra"] = [];
    segment(root)["extra"] = null;
    expect(parseRuleset(root).flags.has("f")).toBe(true);
  });

  it("defaults negate to false and reads it when present", () => {
    const root = base();
    delete clause(root)["negate"];
    expect(firstClause(root).negate).toBe(false);
    clause(root)["negate"] = true;
    expect(firstClause(root).negate).toBe(true);
  });

  it("treats __proto__ and constructor as ordinary keys", () => {
    // Object.fromEntries and JSON.parse make __proto__ an own property.
    const root = rulesetJson([flagJson("__proto__"), flagJson("constructor")]);
    root["segments"] = JSON.parse(
      '{"__proto__": {"included": ["a"], "excluded": [], "rules": []}}',
    );
    const rs = parseRuleset(JSON.stringify(root));
    expect([...rs.flags.keys()].sort()).toEqual(["__proto__", "constructor"]);
    expect(rs.segments.get("__proto__")!.included).toEqual(new Set(["a"]));
    expect(rs.flags.has("toString")).toBe(false);
  });

  it("does not read inherited properties", () => {
    const root = base();
    const inherited = Object.create({ key: "f" }) as Json;
    Object.assign(inherited, flag(root));
    delete inherited["key"];
    (root["flags"] as Json)["f"] = inherited;
    expect(parseError(root).path).toBe("flags.f.key");
  });
});

describe("parseRuleset errors", () => {
  const cases: [string, string | ((r: Json) => void), string][] = [
    ["invalid JSON", '{"schema_version": 1,', "ruleset"],
    ["trailing data", "{} {}", "ruleset"],
    ["non-object root", "[]", "ruleset"],
    ["null root", "null", "ruleset"],
    ["schema_version 2", (r) => (r["schema_version"] = 2), "schema_version"],
    [
      "schema_version true",
      (r) => (r["schema_version"] = true),
      "schema_version",
    ],
    [
      "schema_version string",
      (r) => (r["schema_version"] = "1"),
      "schema_version",
    ],
    [
      "missing schema_version",
      (r) => delete r["schema_version"],
      "schema_version",
    ],
    ["missing project", (r) => delete r["project"], "project"],
    ["environment not a string", (r) => (r["environment"] = 1), "environment"],
    ["flags not an object", (r) => (r["flags"] = []), "flags"],
    ["flags null", (r) => (r["flags"] = null), "flags"],
    ["segments missing", (r) => delete r["segments"], "segments"],
    ["flag not an object", (r) => ((r["flags"] as Json)["f"] = 1), "flags.f"],
    ["flag key mismatch", (r) => (flag(r)["key"] = "g"), "flags.f.key"],
    ["missing flag key", (r) => delete flag(r)["key"], "flags.f.key"],
    ["bad type", (r) => (flag(r)["type"] = "int"), "flags.f.type"],
    ["enabled yes", (r) => (flag(r)["enabled"] = "yes"), "flags.f.enabled"],
    ["version 1.5", (r) => (flag(r)["version"] = 1.5), "flags.f.version"],
    ["version true", (r) => (flag(r)["version"] = true), "flags.f.version"],
    [
      "version out of int64",
      (r) => (flag(r)["version"] = 2 ** 63),
      "flags.f.version",
    ],
    [
      "empty variations",
      (r) => (flag(r)["variations"] = []),
      "flags.f.variations",
    ],
    [
      "variations not a list",
      (r) => (flag(r)["variations"] = {}),
      "flags.f.variations",
    ],
    [
      "variation not an object",
      (r) => (flag(r)["variations"] = [1]),
      "flags.f.variations[0]",
    ],
    [
      "variation without value",
      (r) => (flag(r)["variations"] = [{ key: "on" }]),
      "flags.f.variations[0].value",
    ],
    [
      "variation key not a string",
      (r) => (flag(r)["variations"] = [{ key: 1, value: true }]),
      "flags.f.variations[0].key",
    ],
    [
      "duplicate variation",
      (r) =>
        (flag(r)["variations"] = [
          { key: "on", value: true },
          { key: "on", value: false },
        ]),
      "flags.f.variations[1].key",
    ],
    [
      "unknown off_variation",
      (r) => (flag(r)["off_variation"] = "nope"),
      "flags.f.off_variation",
    ],
    [
      "missing off_variation",
      (r) => delete flag(r)["off_variation"],
      "flags.f.off_variation",
    ],
    [
      "missing prerequisites",
      (r) => delete flag(r)["prerequisites"],
      "flags.f.prerequisites",
    ],
    [
      "prerequisite flag not a string",
      (r) => (flag(r)["prerequisites"] = [{ flag: 1, variation: "on" }]),
      "flags.f.prerequisites[0].flag",
    ],
    [
      "prerequisite variation missing",
      (r) => (flag(r)["prerequisites"] = [{ flag: "g" }]),
      "flags.f.prerequisites[0].variation",
    ],
    ["targets not a list", (r) => (flag(r)["targets"] = {}), "flags.f.targets"],
    [
      "unknown target variation",
      (r) => (flag(r)["targets"] = [{ variation: "nope", values: ["a"] }]),
      "flags.f.targets[0].variation",
    ],
    [
      "target value not a string",
      (r) => (flag(r)["targets"] = [{ variation: "on", values: ["a", 2] }]),
      "flags.f.targets[0].values[1]",
    ],
    ["missing rules", (r) => delete flag(r)["rules"], "flags.f.rules"],
    [
      "missing fallthrough",
      (r) => delete flag(r)["fallthrough"],
      "flags.f.fallthrough",
    ],
    [
      "description not a string",
      (r) => (rule(r)["description"] = null),
      "flags.f.rules[0].description",
    ],
    [
      "serve with both",
      (r) => (flag(r)["fallthrough"] = { variation: "on", rollout: {} }),
      "flags.f.fallthrough",
    ],
    [
      "serve with neither",
      (r) => (rule(r)["serve"] = {}),
      "flags.f.rules[0].serve",
    ],
    [
      "serve not an object",
      (r) => (rule(r)["serve"] = "on"),
      "flags.f.rules[0].serve",
    ],
    [
      "unknown serve variation",
      (r) => (rule(r)["serve"] = { variation: "nope" }),
      "flags.f.rules[0].serve.variation",
    ],
    [
      "bucket_by not a string",
      (r) => (rollout(r)["bucket_by"] = null),
      "flags.f.fallthrough.rollout.bucket_by",
    ],
    [
      "empty weights",
      (r) => (rollout(r)["weights"] = []),
      "flags.f.fallthrough.rollout.weights",
    ],
    [
      "weights summing to 99999",
      (r) =>
        (rollout(r)["weights"] = [
          { variation: "on", weight: 50000 },
          { variation: "off", weight: 49999 },
        ]),
      "flags.f.fallthrough.rollout.weights",
    ],
    [
      "weight of 100001",
      (r) => (rollout(r)["weights"] = [{ variation: "on", weight: 100001 }]),
      "flags.f.fallthrough.rollout.weights[0].weight",
    ],
    [
      "negative weight",
      (r) => (rollout(r)["weights"] = [{ variation: "on", weight: -1 }]),
      "flags.f.fallthrough.rollout.weights[0].weight",
    ],
    [
      "fractional weight",
      (r) => (rollout(r)["weights"] = [{ variation: "on", weight: 99999.5 }]),
      "flags.f.fallthrough.rollout.weights[0].weight",
    ],
    [
      "boolean weight",
      (r) => (rollout(r)["weights"] = [{ variation: "on", weight: true }]),
      "flags.f.fallthrough.rollout.weights[0].weight",
    ],
    [
      "unknown weight variation",
      (r) => (rollout(r)["weights"] = [{ variation: "nope", weight: 100000 }]),
      "flags.f.fallthrough.rollout.weights[0].variation",
    ],
    [
      "empty clauses",
      (r) => (rule(r)["clauses"] = []),
      "flags.f.rules[0].clauses",
    ],
    [
      "attribute not a string",
      (r) => (clause(r)["attribute"] = 1),
      "flags.f.rules[0].clauses[0].attribute",
    ],
    [
      "unknown operator",
      (r) => (clause(r)["operator"] = "like"),
      "flags.f.rules[0].clauses[0].operator",
    ],
    [
      "empty clause values",
      (r) => (clause(r)["values"] = []),
      "flags.f.rules[0].clauses[0].values",
    ],
    [
      "null clause value",
      (r) => (clause(r)["values"] = ["a", null]),
      "flags.f.rules[0].clauses[0].values[1]",
    ],
    [
      "array clause value",
      (r) => (clause(r)["values"] = [[]]),
      "flags.f.rules[0].clauses[0].values[0]",
    ],
    [
      "object clause value",
      (r) => (clause(r)["values"] = [{}]),
      "flags.f.rules[0].clauses[0].values[0]",
    ],
    [
      "negate not a boolean",
      (r) => (clause(r)["negate"] = "no"),
      "flags.f.rules[0].clauses[0].negate",
    ],
    [
      "in_segment in a segment rule",
      (r) =>
        (segment(r)["rules"] = [
          { clauses: [cl("key", "in_segment", false, "s")] },
        ]),
      "segments.s.rules[0].clauses[0].operator",
    ],
    [
      "segment not an object",
      (r) => ((r["segments"] as Json)["s"] = []),
      "segments.s",
    ],
    [
      "segment included [1]",
      (r) => (segment(r)["included"] = [1]),
      "segments.s.included[0]",
    ],
    [
      "segment excluded missing",
      (r) => delete segment(r)["excluded"],
      "segments.s.excluded",
    ],
    [
      "segment rules missing",
      (r) => delete segment(r)["rules"],
      "segments.s.rules",
    ],
    [
      "segment rule not an object",
      (r) => (segment(r)["rules"] = [1]),
      "segments.s.rules[0]",
    ],
    [
      "segment rule empty clauses",
      (r) => (segment(r)["rules"] = [{ clauses: [] }]),
      "segments.s.rules[0].clauses",
    ],
  ];

  it.each(cases)("%s fails at %s", (_name, input, path) => {
    let arg: unknown = input;
    if (typeof input === "function") {
      const root = base();
      input(root);
      arg = JSON.stringify(root);
    }
    const err = parseError(arg);
    expect(err.path).toBe(path);
    expect(err.message).toBe(`${path}: ${err.reason}`);
    expect(err.reason).not.toBe("");
  });

  it("names the unknown operator", () => {
    const root = base();
    clause(root)["operator"] = "like";
    expect(parseError(root).message).toBe(
      'flags.f.rules[0].clauses[0].operator: unknown operator "like"',
    );
  });
});

describe("prepared values", () => {
  function prepared(operator: string, values: unknown[]): Clause {
    const root = base();
    Object.assign(clause(root), { operator, values });
    return firstClause(root);
  }

  it("in and not_in keep every value", () => {
    for (const op of ["in", "not_in"]) {
      expect(prepared(op, ["a", 1.5, true]).prepared).toEqual({
        kind: "equal",
        values: ["a", 1.5, true],
      });
    }
    expect(prepared("in", ["a"]).values).toEqual(["a"]);
  });

  it("string operators keep strings", () => {
    for (const op of ["contains", "starts_with", "ends_with"]) {
      expect(prepared(op, ["a", 1, true]).prepared).toEqual({
        kind: "strings",
        values: ["a"],
      });
    }
  });

  it("matches keeps the patterns that compile and warns about the others", () => {
    const root = base();
    Object.assign(clause(root), {
      operator: "matches",
      values: ["(", "^a", 1],
    });
    const rs = parseRuleset(root);
    const c = rs.flags.get("f")!.rules[0]!.clauses[0]!;
    if (c.prepared.kind !== "regex") throw new Error(c.prepared.kind);
    expect(c.prepared.values).toHaveLength(1);
    expect(c.prepared.values[0]!.test("abc")).toBe(true);
    expect(rs.warnings).toHaveLength(1);
    expect(rs.warnings[0]).toMatch(
      /^flags\.f\.rules\[0\]\.clauses\[0\]\.values\[0\]: regex does not compile: .+/,
    );
  });

  it("warns about a bad pattern in a segment rule", () => {
    const root = base();
    segment(root)["rules"] = [
      { clauses: [cl("email", "matches", false, "a{,2}")] },
    ];
    expect(parseRuleset(root).warnings).toEqual([
      expect.stringMatching(
        /^segments\.s\.rules\[0\]\.clauses\[0\]\.values\[0\]: regex does not compile: /,
      ),
    ]);
  });

  it("numeric operators keep numbers", () => {
    for (const op of ["lt", "lte", "gt", "gte"]) {
      expect(prepared(op, ["5", 5, 2.5, false]).prepared).toEqual({
        kind: "numbers",
        values: [5, 2.5],
      });
    }
  });

  it("semver operators keep valid versions", () => {
    for (const op of ["semver_eq", "semver_lt", "semver_gt"]) {
      const c = prepared(op, ["x", "1.0.0", 1]);
      if (c.prepared.kind !== "semver") throw new Error(c.prepared.kind);
      expect(c.prepared.values).toHaveLength(1);
      expect(compareSemver(c.prepared.values[0]!, parseSemver("1.0.0")!)).toBe(
        0,
      );
    }
  });

  it("date operators keep valid RFC 3339 strings, never numbers", () => {
    expect(prepared("before", [123, "2026-01-01T00:00:00Z"]).prepared).toEqual({
      kind: "millis",
      values: [1767225600000],
    });
    expect(prepared("after", ["not a date"]).prepared).toEqual({
      kind: "millis",
      values: [],
    });
  });

  it("in_segment keeps strings", () => {
    expect(prepared("in_segment", ["s", 1]).prepared).toEqual({
      kind: "segments",
      values: ["s"],
    });
  });
});
