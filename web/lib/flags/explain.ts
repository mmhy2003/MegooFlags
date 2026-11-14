import type { components } from "@/lib/api/schema";

/** Why an evaluation gave its variation, in a few words. */
export function explain(r: components["schemas"]["EvaluationResult"]): string {
  switch (r.reason) {
    case "ERROR":
      return `Error: ${r.error_message || r.error_code}`;
    case "DISABLED":
      return "Flag is off";
    case "PREREQUISITE_FAILED":
      return `Prerequisite ${String(r.metadata.prerequisite)} failed`;
    case "TARGETING_MATCH": {
      const index = r.metadata.rule_index;
      return typeof index === "number"
        ? `Rule ${index + 1}`
        : "Individual target";
    }
    default:
      return "Default rule";
  }
}
