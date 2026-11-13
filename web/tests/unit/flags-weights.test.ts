import { describe, expect, it } from "vitest";

import {
  booleanWeights,
  evenWeights,
  fromPercent,
  percentOf,
  toPercent,
  WEIGHT_TOTAL,
  weightsSum,
  weightsValid,
} from "@/lib/flags/weights";

describe("toPercent and fromPercent", () => {
  it("converts thousandths of a percent", () => {
    expect(WEIGHT_TOTAL).toBe(100_000);
    expect(toPercent(12345)).toBe(12.345);
    expect(fromPercent(12.3)).toBe(12300);
  });

  it("clamps at 0 and 100000", () => {
    expect(fromPercent(-5)).toBe(0);
    expect(fromPercent(150)).toBe(100_000);
  });
});

describe("booleanWeights", () => {
  it("splits between true and false", () => {
    expect(booleanWeights(25)).toEqual([
      { variation: "true", weight: 25_000 },
      { variation: "false", weight: 75_000 },
    ]);
  });
});

describe("weightsValid", () => {
  it("needs a sum of exactly 100000", () => {
    const ok = [
      { variation: "a", weight: 40_000 },
      { variation: "b", weight: 60_000 },
    ];
    expect(weightsSum(ok)).toBe(100_000);
    expect(weightsValid(ok)).toBe(true);
    expect(
      weightsValid([
        { variation: "a", weight: 39_999 },
        { variation: "b", weight: 60_000 },
      ]),
    ).toBe(false);
  });

  it("rejects negative weights", () => {
    expect(
      weightsValid([
        { variation: "a", weight: -1 },
        { variation: "b", weight: 100_001 },
      ]),
    ).toBe(false);
  });
});

describe("evenWeights", () => {
  it("gives the remainder to the first variation", () => {
    expect(evenWeights(["a", "b", "c"])).toEqual([
      { variation: "a", weight: 33_334 },
      { variation: "b", weight: 33_333 },
      { variation: "c", weight: 33_333 },
    ]);
  });
});

describe("percentOf", () => {
  it("reads a variation's percent", () => {
    const ws = [{ variation: "a", weight: 12_500 }];
    expect(percentOf(ws, "a")).toBe(12.5);
    expect(percentOf(ws, "missing")).toBe(0);
  });
});
