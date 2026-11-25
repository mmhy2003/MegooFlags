/** A copy of `keys` with `key` moved by one place; unchanged at the ends or for an unknown key. */
export function moveKey(keys: string[], key: string, by: -1 | 1): string[] {
  const next = [...keys];
  const from = next.indexOf(key);
  const to = from + by;
  if (from < 0 || to < 0 || to >= next.length) return next;
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

/** How changes are made in an environment: directly, or after `required` approvals. */
export function approvalsLabel(required: number): string {
  if (required <= 0) return "Direct changes";
  return required === 1 ? "Needs 1 approval" : `Needs ${required} approvals`;
}
