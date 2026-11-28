import { describe, expect, it } from "vitest";

import {
  parseTheme,
  sidebarOpen,
  themeCookieToSet,
  themeToSync,
} from "@/lib/theme";

describe("parseTheme", () => {
  it.each(["light", "dark", "system"] as const)("accepts %s", (theme) => {
    expect(parseTheme(theme)).toBe(theme);
  });

  it.each([undefined, null, "", "Dark", "neon"])("rejects %s", (value) => {
    expect(parseTheme(value)).toBeNull();
  });
});

describe("themeCookieToSet", () => {
  it("sets the stored theme when the cookie is missing", () => {
    expect(themeCookieToSet(undefined, "dark")).toBe("dark");
  });

  it("leaves an existing cookie alone", () => {
    expect(themeCookieToSet("light", "dark")).toBeNull();
  });

  it("sets nothing without a stored theme", () => {
    expect(themeCookieToSet(undefined, null)).toBeNull();
  });
});

describe("sidebarOpen", () => {
  it("follows a valid cookie", () => {
    expect(sidebarOpen("false", false)).toBe(false);
    expect(sidebarOpen("true", true)).toBe(true);
  });

  it("falls back to the stored preference", () => {
    expect(sidebarOpen(undefined, true)).toBe(false);
  });

  it("defaults to open", () => {
    expect(sidebarOpen(undefined, undefined)).toBe(true);
    expect(sidebarOpen("junk", false)).toBe(true);
  });
});

describe("themeToSync", () => {
  it("applies the stored theme when it differs", () => {
    expect(themeToSync("light", "dark", false)).toBe("dark");
  });

  it("does nothing when the theme already matches", () => {
    expect(themeToSync("dark", "dark", false)).toBeNull();
  });

  it("does nothing without a stored theme", () => {
    expect(themeToSync("light", null, false)).toBeNull();
    expect(themeToSync("light", undefined, false)).toBeNull();
  });

  it("keeps this browser's choice when its last save failed", () => {
    expect(themeToSync("light", "dark", true)).toBeNull();
  });
});
