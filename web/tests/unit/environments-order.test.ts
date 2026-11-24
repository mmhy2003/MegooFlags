import { describe, expect, it } from "vitest";

import { approvalsLabel, moveKey } from "@/lib/environments/order";

const KEYS = ["development", "staging", "production"];

describe("moveKey", () => {
  it("moves a key up or down by one", () => {
    expect(moveKey(KEYS, "staging", -1)).toEqual([
      "staging",
      "development",
      "production",
    ]);
    expect(moveKey(KEYS, "staging", 1)).toEqual([
      "development",
      "production",
      "staging",
    ]);
  });

  it("returns a copy and leaves the input alone", () => {
    const moved = moveKey(KEYS, "production", -1);
    expect(moved).not.toBe(KEYS);
    expect(KEYS).toEqual(["development", "staging", "production"]);
  });

  it("keeps the order at the ends", () => {
    expect(moveKey(KEYS, "development", -1)).toEqual(KEYS);
    expect(moveKey(KEYS, "production", 1)).toEqual(KEYS);
  });

  it("keeps the order for an unknown key", () => {
    const moved = moveKey(KEYS, "qa", 1);
    expect(moved).toEqual(KEYS);
    expect(moved).not.toBe(KEYS);
  });
});

describe("approvalsLabel", () => {
  it("names direct changes and the approvals needed", () => {
    expect(approvalsLabel(0)).toBe("Direct changes");
    expect(approvalsLabel(1)).toBe("Needs 1 approval");
    expect(approvalsLabel(3)).toBe("Needs 3 approvals");
  });
});
