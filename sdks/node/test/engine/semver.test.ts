import { describe, expect, it } from "vitest";
import {
  compareSemver,
  parseSemver,
} from "../../src/internal/engine/semver.js";

function cmp(a: string, b: string): number {
  const x = parseSemver(a);
  const y = parseSemver(b);
  if (x === undefined || y === undefined)
    throw new Error(`invalid: ${a} or ${b}`);
  return compareSemver(x, y);
}

describe("parseSemver", () => {
  it.each([
    "1.2.3",
    "0.0.0",
    "1.0.0-alpha.1",
    "1.0.0+build.5",
    "1.0.0-0.3.7",
    "1.0.0-x-y-z.--",
    "1.0.0-alpha+b.1",
  ])("accepts %j", (s) => {
    expect(parseSemver(s)).toBeDefined();
  });

  it.each([
    "v1.2.3",
    "1.2",
    "1.2.3.4",
    "01.2.3",
    "1.02.3",
    "1.2.03",
    "1.2.3-01",
    "1.2.3-",
    "1.2.3+",
    "1.2.3-a..b",
    "1.2.3+a..b",
    "1.2.3\n",
    " 1.2.3",
    "1.2.3 ",
    "١.2.3",
    "1.2.3-α",
    "",
  ])("refuses %j", (s) => {
    expect(parseSemver(s)).toBeUndefined();
  });
});

describe("compareSemver", () => {
  const ordered = [
    "1.0.0-alpha",
    "1.0.0-alpha.1",
    "1.0.0-alpha.beta",
    "1.0.0-beta",
    "1.0.0-beta.2",
    "1.0.0-beta.11",
    "1.0.0-rc.1",
    "1.0.0",
  ];

  it("orders the SemVer 2.0.0 precedence example", () => {
    for (let i = 0; i < ordered.length; i++) {
      for (let j = 0; j < ordered.length; j++) {
        const a = ordered[i] as string;
        const b = ordered[j] as string;
        expect(cmp(a, b), `${a} vs ${b}`).toBe(Math.sign(i - j));
      }
    }
  });

  it("ignores the build", () => {
    expect(cmp("1.0.0+b1", "1.0.0+b2")).toBe(0);
  });

  it("compares core parts numerically without overflow", () => {
    expect(cmp("99999999999999999999.0.0", "9.0.0")).toBe(1);
    expect(cmp("9.0.0", "99999999999999999999.0.0")).toBe(-1);
    expect(cmp("1.10.0", "1.9.0")).toBe(1);
    expect(cmp("1.2.3", "1.2.4")).toBe(-1);
  });
});
