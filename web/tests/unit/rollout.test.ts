import { describe, expect, it } from "vitest";

import { clampRollout, parseRollout } from "@/lib/rollout";

describe("clampRollout", () => {
  it("clamps to 0..100", () => {
    expect(clampRollout(-1)).toBe(0);
    expect(clampRollout(100.04)).toBe(100);
  });

  it("rounds to one decimal place", () => {
    expect(clampRollout(12.345)).toBe(12.3);
  });
});

describe("parseRollout", () => {
  it("gives null for empty or non-numeric text", () => {
    expect(parseRollout("")).toBeNull();
    expect(parseRollout("abc")).toBeNull();
  });

  it("clamps and rounds numbers", () => {
    expect(parseRollout("150")).toBe(100);
    expect(parseRollout("12.34")).toBe(12.3);
    expect(parseRollout("0")).toBe(0);
  });
});
