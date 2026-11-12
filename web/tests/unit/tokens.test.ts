import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(
  fileURLToPath(new URL("../../app/globals.css", import.meta.url)),
  "utf8",
);

const REQUIRED = [
  "background",
  "foreground",
  "card",
  "card-foreground",
  "popover",
  "popover-foreground",
  ...[
    "primary",
    "secondary",
    "accent",
    "success",
    "warning",
    "destructive",
  ].flatMap((name) => [name, `${name}-foreground`]),
  "muted",
  "muted-foreground",
  "border",
  "input",
  "ring",
  "chart-1",
  "chart-2",
  "chart-3",
  "chart-4",
  "chart-5",
  "sidebar",
  "sidebar-foreground",
  "sidebar-primary",
  "sidebar-primary-foreground",
  "sidebar-accent",
  "sidebar-accent-foreground",
  "sidebar-border",
  "sidebar-ring",
];

// [text, surface] pairs that must reach WCAG AA (4.5:1) in both themes.
const CONTRAST_PAIRS: [string, string][] = [
  ["foreground", "background"],
  ["foreground", "card"],
  ["popover-foreground", "popover"],
  ["muted-foreground", "background"],
  ["muted-foreground", "card"],
  ["muted-foreground", "muted"],
  ...[
    "primary",
    "secondary",
    "accent",
    "success",
    "warning",
    "destructive",
  ].map((name): [string, string] => [`${name}-foreground`, name]),
  ...["primary", "secondary", "success", "warning", "destructive"].flatMap(
    (name): [string, string][] => [
      [name, "background"],
      [name, "card"],
    ],
  ),
  ["sidebar-foreground", "sidebar"],
  ["sidebar-accent-foreground", "sidebar-accent"],
];

function block(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`).exec(CSS);
  if (!match) throw new Error(`no ${selector} block in globals.css`);
  return match[1];
}

function hexTokens(body: string): Map<string, string> {
  const tokens = new Map<string, string>();
  for (const [, name, value] of body.matchAll(
    /--([\w-]+):\s*(#[0-9a-f]{6})\s*;/gi,
  )) {
    tokens.set(name, value);
  }
  return tokens;
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("contrast ratio", () => {
  it("matches the WCAG reference values", () => {
    expect(contrast("#000000", "#FFFFFF")).toBeCloseTo(21, 5);
    expect(contrast("#FFFFFF", "#FFFFFF")).toBeCloseTo(1, 5);
    expect(contrast("#777777", "#FFFFFF")).toBeCloseTo(4.48, 2);
  });
});

describe.each([":root", ".dark"])("%s palette tokens", (selector) => {
  const body = block(selector);
  const tokens = hexTokens(body);

  it("defines every token as a hex colour", () => {
    expect(REQUIRED.filter((name) => !tokens.has(name))).toEqual([]);
  });

  it("declares the glow colour", () => {
    expect(body).toMatch(/--glow:\s*[^;]+;/);
  });

  it.each(CONTRAST_PAIRS)("%s on %s is at least 4.5:1", (text, surface) => {
    const ratio = contrast(tokens.get(text)!, tokens.get(surface)!);
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  });
});

describe("globals.css utilities", () => {
  it.each([
    "@utility shadow-glow",
    "prefers-reduced-motion: reduce",
    "scroll-padding-top",
    "--color-success:",
    "--color-warning:",
  ])("contains %s", (snippet) => {
    expect(CSS).toContain(snippet);
  });
});
