// JSON values as the engine sees them. Every JSON number is a JS number
// (a double), so there is no separate integer kind.

/** isNumber reports whether v is a number. A boolean is never a number. */
export function isNumber(v: unknown): v is number {
  return typeof v === "number";
}

/** isScalar reports whether v is a string, a boolean or a number. */
export function isScalar(v: unknown): v is string | boolean | number {
  return (
    typeof v === "string" || typeof v === "boolean" || typeof v === "number"
  );
}

/**
 * jsonEqual is JSON scalar equality: a boolean only equals a boolean, numbers
 * compare numerically, a string equals a string. Anything else, null
 * included, is never equal.
 */
export function jsonEqual(a: unknown, b: unknown): boolean {
  if (typeof a === "boolean" || typeof b === "boolean") return a === b;
  if (typeof a === "number") return typeof b === "number" && a === b;
  if (typeof a === "string") return typeof b === "string" && a === b;
  return false;
}
