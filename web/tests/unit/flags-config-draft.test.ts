import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import {
  addClause,
  addPrerequisite,
  addRule,
  configText,
  isDirty,
  moveRule,
  removeClause,
  removePrerequisite,
  removeRule,
  setFallthrough,
  setTargets,
  toConfigIn,
  toDraft,
  updateClause,
  updatePrerequisite,
  updateRule,
  validateDraft,
  withState,
} from "@/lib/flags/config-draft";

type S = components["schemas"];

const VARIATIONS = ["true", "false"];

function deepFreeze<T>(value: T): T {
  if (value && typeof value === "object") {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

const config: S["FlagConfigOut"] = deepFreeze({
  environment: "development",
  enabled: true,
  version: 7,
  updated_at: "2026-11-01T00:00:00Z",
  targets: [{ variation: "true", values: ["user-1", "user-2"] }],
  rules: [
    {
      description: "Dutch users",
      clauses: [
        {
          attribute: "country",
          operator: "in",
          values: ["NL"],
          negate: false,
        },
      ],
      variation: "true",
      rollout: null,
    },
    {
      description: "Half of beta",
      clauses: [
        {
          attribute: "key",
          operator: "in_segment",
          values: ["beta-testers"],
          negate: true,
        },
      ],
      variation: null,
      rollout: {
        bucket_by: "key",
        weights: [
          { variation: "true", weight: 50_000 },
          { variation: "false", weight: 50_000 },
        ],
      },
    },
  ],
  prerequisites: [{ flag: "login-v2", variation: "true" }],
  fallthrough: { variation: "false", rollout: null },
});

const configIn: S["FlagConfigIn"] = {
  enabled: true,
  version: 7,
  targets: [{ variation: "true", values: ["user-1", "user-2"] }],
  rules: [
    {
      description: "Dutch users",
      clauses: [
        {
          attribute: "country",
          operator: "in",
          values: ["NL"],
          negate: false,
        },
      ],
      variation: "true",
    },
    {
      description: "Half of beta",
      clauses: [
        {
          attribute: "key",
          operator: "in_segment",
          values: ["beta-testers"],
          negate: true,
        },
      ],
      rollout: {
        bucket_by: "key",
        weights: [
          { variation: "true", weight: 50_000 },
          { variation: "false", weight: 50_000 },
        ],
      },
    },
  ],
  prerequisites: [{ flag: "login-v2", variation: "true" }],
  fallthrough: { variation: "false" },
};

const base = () => toDraft(config, VARIATIONS);

describe("toDraft and toConfigIn", () => {
  it("round-trip a config", () => {
    expect(toConfigIn(base())).toEqual(configIn);
  });

  it("drops empty target lists", () => {
    const d = base();
    expect(d.targets.false).toEqual([]);
    expect(toConfigIn(d).targets).toEqual([
      { variation: "true", values: ["user-1", "user-2"] },
    ]);
  });
});

describe("configText", () => {
  it("is the JSON without version and enabled", () => {
    const text = configText(base());
    const rest = {
      targets: configIn.targets,
      rules: configIn.rules,
      prerequisites: configIn.prerequisites,
      fallthrough: configIn.fallthrough,
    };
    expect(JSON.parse(text)).toEqual(rest);
    expect(text).toBe(JSON.stringify(rest, null, 2));
  });
});

describe("isDirty and withState", () => {
  it("is false at first and true after an edit", () => {
    const d = base();
    expect(isDirty(d, d)).toBe(false);
    const edited = updateRule(d, d.rules[0]!.id, { description: "NL" });
    expect(isDirty(d, edited)).toBe(true);
  });

  it("ignores state changes", () => {
    const d = base();
    const after = withState(d, { enabled: false, version: 8 });
    expect(after.enabled).toBe(false);
    expect(after.version).toBe(8);
    expect(isDirty(d, after)).toBe(false);
  });
});

describe("rules", () => {
  it("adds a rule with one empty key clause", () => {
    const d = addRule(base(), "true");
    const rule = d.rules[2]!;
    expect(rule.description).toBe("");
    expect(rule.serve).toEqual({ mode: "variation", variation: "true" });
    expect(rule.clauses).toHaveLength(1);
    expect(rule.clauses[0]).toMatchObject({
      attribute: "key",
      operator: "in",
      values: [],
      negate: false,
    });
    expect(rule.id).not.toBe(rule.clauses[0]!.id);
  });

  it("moves rules, with no-ops at the ends", () => {
    const d = base();
    const [first, second] = d.rules;
    expect(moveRule(d, first!.id, -1).rules).toEqual(d.rules);
    expect(moveRule(d, second!.id, 1).rules).toEqual(d.rules);
    expect(moveRule(d, first!.id, 1).rules.map((r) => r.id)).toEqual([
      second!.id,
      first!.id,
    ]);
  });

  it("removes a rule", () => {
    const d = base();
    expect(removeRule(d, d.rules[0]!.id).rules.map((r) => r.id)).toEqual([
      d.rules[1]!.id,
    ]);
  });
});

describe("clauses", () => {
  it("adds, updates and removes clauses", () => {
    const d = base();
    const ruleId = d.rules[0]!.id;
    const added = addClause(d, ruleId);
    expect(added.rules[0]!.clauses).toHaveLength(2);
    const clauseId = added.rules[0]!.clauses[1]!.id;
    const updated = updateClause(added, ruleId, clauseId, {
      attribute: "plan",
      values: ["pro"],
    });
    expect(updated.rules[0]!.clauses[1]).toMatchObject({
      attribute: "plan",
      values: ["pro"],
    });
    expect(
      removeClause(updated, ruleId, clauseId).rules[0]!.clauses,
    ).toHaveLength(1);
  });

  it("keeps a rule's last clause", () => {
    const d = base();
    const rule = d.rules[0]!;
    expect(removeClause(d, rule.id, rule.clauses[0]!.id)).toEqual(d);
  });
});

describe("setTargets", () => {
  it("trims, dedupes and moves keys between variations", () => {
    const d = setTargets(base(), "false", [" user-1 ", "user-3", "user-3", ""]);
    expect(d.targets.false).toEqual(["user-1", "user-3"]);
    expect(d.targets.true).toEqual(["user-2"]);
  });
});

describe("prerequisites and fallthrough", () => {
  it("adds, updates and removes prerequisites", () => {
    const d = addPrerequisite(base());
    const id = d.prerequisites[1]!.id;
    expect(d.prerequisites[1]).toMatchObject({ flag: "", variation: "" });
    const updated = updatePrerequisite(d, id, { flag: "other" });
    expect(updated.prerequisites[1]!.flag).toBe("other");
    expect(removePrerequisite(updated, id).prerequisites).toHaveLength(1);
  });

  it("sets the default rule", () => {
    const d = setFallthrough(base(), { mode: "variation", variation: "true" });
    expect(toConfigIn(d).fallthrough).toEqual({ variation: "true" });
  });
});

describe("validateDraft", () => {
  it("accepts a valid draft", () => {
    expect(validateDraft(base())).toEqual([]);
  });

  it("reports empty values, bad weights and incomplete prerequisites", () => {
    let d = addRule(base(), "true");
    d = updateRule(d, d.rules[0]!.id, {
      clauses: [{ ...d.rules[0]!.clauses[0]!, values: [] }],
    });
    d = setFallthrough(d, {
      mode: "rollout",
      bucketBy: "key",
      weights: [
        { variation: "true", weight: 40_000 },
        { variation: "false", weight: 50_000 },
      ],
    });
    d = addPrerequisite(d);
    expect(validateDraft(d)).toEqual([
      {
        path: "prerequisites[1]",
        message: "Choose a flag and a variation",
      },
      {
        path: "rules[0].clauses[0].values",
        message: "Add at least one value",
      },
      {
        path: "rules[2].clauses[0].values",
        message: "Add at least one value",
      },
      {
        path: "fallthrough.rollout.weights",
        message: "Weights must add up to 100 %",
      },
    ]);
  });
});

describe("immutability", () => {
  it("never mutates a frozen input", () => {
    const d = deepFreeze(base());
    const r0 = d.rules[0]!;
    const c0 = r0.clauses[0]!;
    expect(() => {
      addRule(d, "true");
      updateRule(d, r0.id, { description: "x" });
      removeRule(d, r0.id);
      moveRule(d, r0.id, 1);
      addClause(d, r0.id);
      updateClause(d, r0.id, c0.id, { negate: true });
      removeClause(addClause(d, r0.id), r0.id, c0.id);
      setTargets(d, "false", ["user-1"]);
      addPrerequisite(d);
      updatePrerequisite(d, d.prerequisites[0]!.id, { flag: "x" });
      removePrerequisite(d, d.prerequisites[0]!.id);
      setFallthrough(d, { mode: "variation", variation: "true" });
      withState(d, { enabled: false, version: 9 });
      toConfigIn(d);
      validateDraft(d);
    }).not.toThrow();
    expect(toConfigIn(d)).toEqual(configIn);
  });
});
