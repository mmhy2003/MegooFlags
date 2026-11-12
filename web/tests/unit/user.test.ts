import { describe, expect, it } from "vitest";

import { initials } from "@/lib/user";

describe("initials", () => {
  it("uses the first letters of the first two words", () => {
    expect(initials("Ada Admin", "ada@example.com")).toBe("AA");
  });

  it("uses one letter for a one-word name", () => {
    expect(initials("ada", "x@example.com")).toBe("A");
  });

  it("falls back to the email", () => {
    expect(initials(null, "viewer@example.com")).toBe("V");
    expect(initials("   ", "viewer@example.com")).toBe("V");
  });

  it("falls back to a question mark", () => {
    expect(initials(null, null)).toBe("?");
    expect(initials("", "")).toBe("?");
  });
});
