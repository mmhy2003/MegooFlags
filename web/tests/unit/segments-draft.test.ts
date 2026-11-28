import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import {
  MAX_KEY_LENGTH,
  addClause,
  addRule,
  isDirty,
  moveRule,
  problemField,
  removeClause,
  removeRule,
  setKeys,
  toConfigIn,
  toDraft,
  updateClause,
  updateRule,
  usageByFlag,
  usedByText,
  validateDraft,
  type SegmentDraft,
} from "@/lib/segments/segment-draft";

type S = components["schemas"];

function deepFreeze<T>(value: T): T {
  if (value && typeof value === "object") {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}

const segment: S["SegmentOut"] = deepFreeze({
  key: "beta-testers",
  name: "Beta testers",
  description: "",
  included: ["u1", "u2"],
  excluded: ["u9"],
  rules: [
    {
      description: "Pro plan",
      clauses: [
        { attribute: "plan", operator: "in", values: ["pro"], negate: false },
      ],
    },
    {
      description: "",
      clauses: [
        { attribute: "country", operator: "in", values: ["DE"], negate: true },
        { attribute: "age", operator: "gte", values: [18], negate: false },
      ],
    },
  ],
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-02T00:00:00Z",
});

const empty: SegmentDraft = { included: [], excluded: [], rules: [] };

describe("toDraft / toConfigIn", () => {
  it("round-trips a segment's config without the client ids", () => {
    const draft = toDraft(segment);
    expect(draft.rules[0]!.id).toEqual(expect.any(String));
    expect(draft.rules[1]!.clauses[0]!.id).toEqual(expect.any(String));
    expect(toConfigIn(draft)).toEqual({
      included: segment.included,
      excluded: segment.excluded,
      rules: segment.rules,
    });
  });
});

describe("isDirty", () => {
  it("is false for the same content and true after an edit", () => {
    const base = toDraft(segment);
    expect(isDirty(base, toDraft(segment))).toBe(false);
    expect(isDirty(base, setKeys(base, "included", ["u1", "u2", "u3"]))).toBe(
      true,
    );
  });
});

describe("setKeys", () => {
  it("trims, drops empty lines and dedupes in order", () => {
    const d = setKeys(empty, "excluded", [" b ", "", "a", "b", "   ", "c"]);
    expect(d.excluded).toEqual(["b", "a", "c"]);
    expect(d.included).toEqual([]);
  });
});

describe("rule helpers", () => {
  it("adds a rule with one empty clause", () => {
    const d = addRule(empty);
    expect(d.rules).toHaveLength(1);
    expect(d.rules[0]!.description).toBe("");
    expect(d.rules[0]!.clauses).toEqual([
      {
        id: expect.any(String),
        attribute: "key",
        operator: "in",
        values: [],
        negate: false,
      },
    ]);
  });

  it("updates and removes a rule", () => {
    const d = toDraft(segment);
    const id = d.rules[1]!.id;
    expect(
      updateRule(d, id, { description: "No DE" }).rules[1]!.description,
    ).toBe("No DE");
    expect(toConfigIn(removeRule(d, id)).rules).toEqual([segment.rules[0]]);
  });

  it("moves a rule and is a no-op at either end", () => {
    const d = toDraft(segment);
    const [first, second] = d.rules;
    expect(moveRule(d, first!.id, 1).rules.map((r) => r.id)).toEqual([
      second!.id,
      first!.id,
    ]);
    expect(moveRule(d, first!.id, -1)).toBe(d);
    expect(moveRule(d, second!.id, 1)).toBe(d);
  });
});

describe("clause helpers", () => {
  it("adds, updates and removes a clause", () => {
    const d = toDraft(segment);
    const rule = d.rules[0]!;
    const added = addClause(d, rule.id);
    expect(added.rules[0]!.clauses).toHaveLength(2);
    const clauseId = added.rules[0]!.clauses[1]!.id;
    const updated = updateClause(added, rule.id, clauseId, {
      attribute: "email",
      values: ["a@b.c"],
    });
    expect(updated.rules[0]!.clauses[1]).toMatchObject({
      attribute: "email",
      operator: "in",
      values: ["a@b.c"],
    });
    expect(removeClause(updated, rule.id, clauseId).rules[0]!.clauses).toEqual(
      d.rules[0]!.clauses,
    );
  });

  it("never removes a rule's last clause", () => {
    const d = toDraft(segment);
    const rule = d.rules[0]!;
    expect(removeClause(d, rule.id, rule.clauses[0]!.id)).toBe(d);
  });
});

describe("validateDraft", () => {
  it("is empty for a valid draft", () => {
    expect(validateDraft(toDraft(segment))).toEqual([]);
  });

  it("finds a key in both lists, long keys and clauses without values, in order", () => {
    const long = "k".repeat(MAX_KEY_LENGTH + 1);
    let d = setKeys(empty, "included", ["u1", long]);
    d = setKeys(d, "excluded", [long + "x", "u1"]);
    d = addRule(d);
    expect(MAX_KEY_LENGTH).toBe(256);
    expect(validateDraft(d)).toEqual([
      { path: "included[1]", message: "Keys can be at most 256 characters" },
      { path: "excluded[0]", message: "Keys can be at most 256 characters" },
      { path: "excluded[1]", message: "u1 is both included and excluded" },
      { path: "rules[0].clauses[0].values", message: "Add at least one value" },
    ]);
  });

  it("accepts a key of exactly 256 characters", () => {
    const d = setKeys(empty, "included", ["k".repeat(MAX_KEY_LENGTH)]);
    expect(validateDraft(d)).toEqual([]);
  });
});

describe("problemField", () => {
  it("maps a problem path to the field it belongs to", () => {
    expect(problemField("included")).toBe("included");
    expect(problemField("included[0]")).toBe("included");
    expect(problemField("excluded[3]")).toBe("excluded");
    expect(problemField("rules[1].clauses[0].values")).toBe(
      "rules[1].clauses[0]",
    );
    expect(problemField("rules[2]")).toBe("rules[2]");
    expect(problemField("rules[2].description")).toBe("rules[2]");
    expect(problemField("version")).toBeNull();
    expect(problemField(null)).toBeNull();
  });
});

describe("usageByFlag", () => {
  it("groups environments by flag in input order", () => {
    expect(
      usageByFlag([
        { flag: "checkout", environment: "development" },
        { flag: "checkout", environment: "production" },
        { flag: "banner", environment: "staging" },
      ]),
    ).toEqual([
      { flag: "checkout", environments: ["development", "production"] },
      { flag: "banner", environments: ["staging"] },
    ]);
    expect(usageByFlag([])).toEqual([]);
  });
});

describe("usedByText", () => {
  it("counts the flags", () => {
    expect(usedByText(0)).toBe("Not used");
    expect(usedByText(1)).toBe("1 flag");
    expect(usedByText(2)).toBe("2 flags");
  });
});
