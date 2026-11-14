import { describe, expect, it } from "vitest";
import {
  bucket,
  bucketText,
  pickVariation,
} from "../../src/internal/engine/bucketing.js";

describe("bucketText", () => {
  it.each([
    ["u", "u"],
    [42, "42"],
    [42.0, "42"],
    [-0.0, "0"],
    [-7, "-7"],
    [9007199254740991, "9007199254740991"],
    [-9007199254740991, "-9007199254740991"],
  ])("%j gives %j", (v, want) => {
    expect(bucketText(v)).toBe(want);
  });

  it.each([
    [42.5],
    [true],
    [null],
    [[1]],
    [9007199254740992],
    [-9007199254740992],
    [Number.NaN],
    [Number.POSITIVE_INFINITY],
    [undefined],
  ])("%j gives none", (v) => {
    expect(bucketText(v)).toBeUndefined();
  });
});

describe("bucket", () => {
  it('bucket("f", "u") is 68258', () => {
    expect(bucket("f", "u")).toBe(68258);
  });

  it("is in [0, 100000)", () => {
    for (let i = 0; i < 200; i++) {
      const b = bucket("flag", `user-${i}`);
      expect(b).toBeGreaterThanOrEqual(0);
      expect(b).toBeLessThan(100000);
      expect(Number.isInteger(b)).toBe(true);
    }
  });
});

describe("pickVariation", () => {
  const weights = [
    { variation: "a", weight: 30000 },
    { variation: "b", weight: 40000 },
    { variation: "c", weight: 30000 },
  ];

  it.each([
    [0, "a"],
    [29999, "a"],
    [30000, "b"],
    [68258, "b"],
    [69999, "b"],
    [70000, "c"],
    [99999, "c"],
  ])("bucket %d gives %s", (b, want) => {
    expect(pickVariation(weights, b)).toBe(want);
  });

  it("skips a zero weight", () => {
    expect(
      pickVariation(
        [
          { variation: "a", weight: 0 },
          { variation: "b", weight: 100000 },
        ],
        0,
      ),
    ).toBe("b");
  });
});
