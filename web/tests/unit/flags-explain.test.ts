import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api/schema";
import { explain } from "@/lib/flags/explain";

type Result = components["schemas"]["EvaluationResult"];

function result(patch: Partial<Result>): Result {
  return {
    key: "checkout-v2",
    reason: "DEFAULT",
    value: true,
    variant: "true",
    metadata: {},
    error_code: null,
    error_message: null,
    ...patch,
  };
}

describe("explain", () => {
  it("names the matching rule, counting from 1", () => {
    expect(
      explain(
        result({ reason: "TARGETING_MATCH", metadata: { rule_index: 1 } }),
      ),
    ).toBe("Rule 2");
  });

  it("names an individual target when no rule matched", () => {
    expect(explain(result({ reason: "TARGETING_MATCH" }))).toBe(
      "Individual target",
    );
  });

  it("names the failed prerequisite", () => {
    expect(
      explain(
        result({
          reason: "PREREQUISITE_FAILED",
          metadata: { prerequisite: "payments-v2" },
        }),
      ),
    ).toBe("Prerequisite payments-v2 failed");
  });

  it.each(["DEFAULT", "SPLIT", "STATIC"])(
    "reads %s as the default rule",
    (reason) => {
      expect(explain(result({ reason }))).toBe("Default rule");
    },
  );

  it("reads DISABLED as the flag being off", () => {
    expect(explain(result({ reason: "DISABLED" }))).toBe("Flag is off");
  });

  it("shows the error message", () => {
    expect(
      explain(
        result({
          reason: "ERROR",
          error_code: "FLAG_NOT_FOUND",
          error_message: "flag 'x' not found",
        }),
      ),
    ).toBe("Error: flag 'x' not found");
  });

  it("falls back to the error code", () => {
    expect(
      explain(result({ reason: "ERROR", error_code: "PARSE_ERROR" })),
    ).toBe("Error: PARSE_ERROR");
  });
});
