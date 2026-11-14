import { createHash } from "node:crypto";

/** BUCKETS is the number of rollout buckets; weights add up to it. */
export const BUCKETS = 100000;

/** Weight is one variation's share of a rollout, out of BUCKETS. */
export interface Weight {
  variation: string;
  weight: number;
}

/**
 * bucketText is the text hashed for v, or undefined when v can't be bucketed
 * (which counts as a missing attribute). A string is used as is, an integral
 * number with |n| <= 2^53-1 as its base-10 text; anything else is refused.
 */
export function bucketText(v: unknown): string | undefined {
  if (typeof v === "string") return v;
  if (typeof v !== "number" || !Number.isSafeInteger(v)) return undefined;
  return String(v); // String(-0) is "0"
}

/** bucket is uint64_be(sha256(flagKey + "." + text)[0:8]) mod BUCKETS. */
export function bucket(flagKey: string, text: string): number {
  const sum = createHash("sha256")
    .update(`${flagKey}.${text}`, "utf8")
    .digest();
  let r = 0;
  for (let i = 0; i < 8; i++) r = (r * 256 + (sum[i] as number)) % BUCKETS;
  return r;
}

/**
 * pickVariation returns the first variation whose running total of weights
 * is greater than bucket. The parser guarantees the weights add up to BUCKETS.
 */
export function pickVariation(
  weights: readonly Weight[],
  bucket: number,
): string | undefined {
  let total = 0;
  for (const w of weights) {
    total += w.weight;
    if (total > bucket) return w.variation;
  }
  return undefined;
}
