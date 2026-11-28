import type { components } from "@/lib/api/schema";

type Operator = components["schemas"]["Operator"];
type Scalar = string | number | boolean;

/** How a clause's values are entered and checked. */
export type OperatorKind =
  "list" | "text" | "regex" | "number" | "semver" | "date" | "segment";

export const OPERATORS: {
  value: Operator;
  label: string;
  kind: OperatorKind;
}[] = [
  { value: "in", label: "is one of", kind: "list" },
  { value: "not_in", label: "is not one of", kind: "list" },
  { value: "contains", label: "contains", kind: "text" },
  { value: "starts_with", label: "starts with", kind: "text" },
  { value: "ends_with", label: "ends with", kind: "text" },
  { value: "matches", label: "matches regex", kind: "regex" },
  { value: "lt", label: "<", kind: "number" },
  { value: "lte", label: "≤", kind: "number" },
  { value: "gt", label: ">", kind: "number" },
  { value: "gte", label: "≥", kind: "number" },
  { value: "semver_eq", label: "version =", kind: "semver" },
  { value: "semver_lt", label: "version <", kind: "semver" },
  { value: "semver_gt", label: "version >", kind: "semver" },
  { value: "before", label: "before date", kind: "date" },
  { value: "after", label: "after date", kind: "date" },
  { value: "in_segment", label: "is in segment", kind: "segment" },
];

// Copied from api/src/megooflags_api/evaluation/operators.py.
const SEMVER =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$/;
const RFC3339 =
  /^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$/;

export function operatorKind(op: Operator): OperatorKind {
  return OPERATORS.find((o) => o.value === op)?.kind ?? "list";
}

/** Clause values as text for a textarea, one per line. */
export function formatValues(values: Scalar[]): string {
  return values.map(String).join("\n");
}

function isRegex(text: string): boolean {
  try {
    new RegExp(text);
    return true;
  } catch {
    return false;
  }
}

/** Reads one value per line for an operator: trimmed, without empty lines, checked for its kind. */
export function parseValues(
  op: Operator,
  text: string,
): { ok: true; values: Scalar[] } | { ok: false; error: string } {
  const lines = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) return { ok: false, error: "Add at least one value" };

  switch (operatorKind(op)) {
    case "number": {
      const values = lines.map(Number);
      return values.every(Number.isFinite)
        ? { ok: true, values }
        : { ok: false, error: "Enter one number per line" };
    }
    case "semver":
      return lines.every((l) => SEMVER.test(l))
        ? { ok: true, values: lines }
        : { ok: false, error: "Enter versions like 1.2.3" };
    case "date":
      return lines.every((l) => RFC3339.test(l))
        ? { ok: true, values: lines }
        : { ok: false, error: "Enter dates like 2026-01-31T00:00:00Z" };
    case "regex":
      return lines.every(isRegex)
        ? { ok: true, values: lines }
        : { ok: false, error: "Enter a valid regular expression" };
    default:
      return { ok: true, values: lines };
  }
}
