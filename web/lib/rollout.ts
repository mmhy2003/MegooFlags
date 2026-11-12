/** A rollout percentage clamped to 0..100 and rounded to one decimal place. */
export function clampRollout(n: number): number {
  return Math.round(Math.min(100, Math.max(0, n)) * 10) / 10;
}

/** The typed rollout percentage, or null when the text is empty or not a number. */
export function parseRollout(text: string): number | null {
  if (text.trim() === "") return null;
  const n = Number(text);
  return Number.isNaN(n) ? null : clampRollout(n);
}
