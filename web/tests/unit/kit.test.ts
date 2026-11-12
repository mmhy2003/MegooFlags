import { describe, expect, it } from "vitest";

import { kitEnabled } from "@/lib/kit";

describe("kitEnabled", () => {
  it('is on only for "1"', () => {
    expect(kitEnabled("1")).toBe(true);
    expect(kitEnabled(undefined)).toBe(false);
    expect(kitEnabled("true")).toBe(false);
  });
});
