import { describe, expect, it } from "vitest";

import { isPaletteShortcut } from "@/lib/shortcuts";

const inEditor = {
  closest: (s: string) => (s === "[data-code-editor]" ? {} : null),
};
const outside = { closest: () => null };

describe("isPaletteShortcut", () => {
  it("is true for Ctrl+K and Cmd+K in any case", () => {
    expect(isPaletteShortcut({ key: "k", ctrlKey: true })).toBe(true);
    expect(isPaletteShortcut({ key: "K", metaKey: true })).toBe(true);
    expect(
      isPaletteShortcut({ key: "k", ctrlKey: true, target: outside }),
    ).toBe(true);
  });

  it("is false without a modifier or for another key", () => {
    expect(isPaletteShortcut({ key: "k" })).toBe(false);
    expect(isPaletteShortcut({ key: "j", ctrlKey: true })).toBe(false);
  });

  it("is false inside a code editor", () => {
    expect(
      isPaletteShortcut({ key: "k", ctrlKey: true, target: inEditor }),
    ).toBe(false);
  });
});
