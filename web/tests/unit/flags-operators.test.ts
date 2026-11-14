import { describe, expect, it } from "vitest";

import {
  formatValues,
  OPERATORS,
  operatorKind,
  parseValues,
} from "@/lib/flags/operators";

describe("OPERATORS", () => {
  it("covers exactly the API's operators", () => {
    expect(OPERATORS.map((o) => o.value).sort()).toEqual(
      [
        "in",
        "not_in",
        "contains",
        "starts_with",
        "ends_with",
        "matches",
        "lt",
        "lte",
        "gt",
        "gte",
        "semver_eq",
        "semver_lt",
        "semver_gt",
        "before",
        "after",
        "in_segment",
      ].sort(),
    );
  });

  it("labels and kinds each operator", () => {
    expect(OPERATORS.find((o) => o.value === "in")?.label).toBe("is one of");
    expect(operatorKind("in")).toBe("list");
    expect(operatorKind("contains")).toBe("text");
    expect(operatorKind("matches")).toBe("regex");
    expect(operatorKind("gte")).toBe("number");
    expect(operatorKind("semver_lt")).toBe("semver");
    expect(operatorKind("after")).toBe("date");
    expect(operatorKind("in_segment")).toBe("segment");
  });
});

describe("parseValues", () => {
  it("trims and drops empty lines", () => {
    expect(parseValues("in", "a\n\n b ")).toEqual({
      ok: true,
      values: ["a", "b"],
    });
  });

  it("reads numbers", () => {
    expect(parseValues("gt", "1\n2.5")).toEqual({ ok: true, values: [1, 2.5] });
    expect(parseValues("gt", "x").ok).toBe(false);
  });

  it("checks versions", () => {
    expect(parseValues("semver_eq", "1.2.3-beta.1+b5")).toEqual({
      ok: true,
      values: ["1.2.3-beta.1+b5"],
    });
    expect(parseValues("semver_eq", "1.2").ok).toBe(false);
  });

  it("checks RFC 3339 dates", () => {
    expect(parseValues("before", "2026-01-31T00:00:00Z")).toEqual({
      ok: true,
      values: ["2026-01-31T00:00:00Z"],
    });
    expect(parseValues("before", "2026-01-31").ok).toBe(false);
  });

  it("checks regular expressions compile", () => {
    expect(parseValues("matches", "^a+$").ok).toBe(true);
    expect(parseValues("matches", "(").ok).toBe(false);
  });

  it("needs at least one value", () => {
    expect(parseValues("in", " \n ")).toEqual({
      ok: false,
      error: "Add at least one value",
    });
  });
});

describe("formatValues", () => {
  it("puts one value per line", () => {
    expect(formatValues(["a", 1, true])).toBe("a\n1\ntrue");
  });
});
