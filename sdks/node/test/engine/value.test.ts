import { describe, expect, it } from "vitest";
import {
  isNumber,
  isScalar,
  jsonEqual,
} from "../../src/internal/engine/value.js";

describe("isNumber", () => {
  it.each([
    [1, true],
    [1.5, true],
    [-0, true],
    [true, false],
    ["1", false],
    [null, false],
    [undefined, false],
    [[1], false],
    [{}, false],
  ])("%j is %s", (v, want) => {
    expect(isNumber(v)).toBe(want);
  });
});

describe("isScalar", () => {
  it.each([
    ["a", true],
    [true, true],
    [false, true],
    [1, true],
    [null, false],
    [undefined, false],
    [[1], false],
    [{ a: 1 }, false],
  ])("%j is %s", (v, want) => {
    expect(isScalar(v)).toBe(want);
  });
});

describe("jsonEqual", () => {
  it.each([
    [1, 1.0, true],
    [true, 1, false],
    [1, true, false],
    ["1", 1, false],
    [null, null, false],
    [false, false, true],
    [true, false, false],
    ["a", "a", true],
    ["a", "b", false],
    [1, 2, false],
    [[1], [1], false],
    [{}, {}, false],
  ])("(%j, %j) is %s", (a, b, want) => {
    expect(jsonEqual(a, b)).toBe(want);
  });
});
