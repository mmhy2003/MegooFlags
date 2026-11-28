import type { components } from "@/lib/api/schema";

type Weight = components["schemas"]["WeightIn"];

/** Rollout weights are thousandths of a percent and add up to this. */
export const WEIGHT_TOTAL = 100_000;

/** A weight as a percent: 12345 is 12.345 %. */
export function toPercent(weight: number): number {
  return weight / 1000;
}

/** A percent as a weight, rounded and clamped to 0..WEIGHT_TOTAL. */
export function fromPercent(percent: number): number {
  return Math.min(WEIGHT_TOTAL, Math.max(0, Math.round(percent * 1000)));
}

export function weightsSum(weights: Weight[]): number {
  return weights.reduce((sum, w) => sum + w.weight, 0);
}

/** True when every weight is in 0..WEIGHT_TOTAL and they add up to WEIGHT_TOTAL. */
export function weightsValid(weights: Weight[]): boolean {
  return (
    weights.every((w) => w.weight >= 0 && w.weight <= WEIGHT_TOTAL) &&
    weightsSum(weights) === WEIGHT_TOTAL
  );
}

/** A boolean flag's rollout serving `true` to `percentTrue` %. */
export function booleanWeights(percentTrue: number): Weight[] {
  const weight = fromPercent(percentTrue);
  return [
    { variation: "true", weight },
    { variation: "false", weight: WEIGHT_TOTAL - weight },
  ];
}

/** The percent a rollout serves a variation; 0 when it isn't in the rollout. */
export function percentOf(weights: Weight[], variation: string): number {
  const found = weights.find((w) => w.variation === variation);
  return found ? toPercent(found.weight) : 0;
}

/** Equal weights for the variations, with the remainder going to the first. */
export function evenWeights(variations: string[]): Weight[] {
  if (variations.length === 0) return [];
  const each = Math.floor(WEIGHT_TOTAL / variations.length);
  const remainder = WEIGHT_TOTAL - each * variations.length;
  return variations.map((variation, i) => ({
    variation,
    weight: i === 0 ? each + remainder : each,
  }));
}
