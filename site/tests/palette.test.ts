import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const read = (path: string) =>
  readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");

/** The custom properties declared directly in the block that `selector` opens. */
function block(css: string, selector: string): Map<string, string> {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no ${selector} block`);
  const body = css.slice(start, css.indexOf("}", start));
  const vars = new Map<string, string>();
  for (const [, name, value] of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) {
    vars.set(name, value.trim().toLowerCase());
  }
  return vars;
}

const globals = read("../../web/app/globals.css");
const brand = read("../src/styles/brand.css");

const themes = [
  { theme: "light", app: ":root", site: ':root[data-theme="light"]' },
  { theme: "dark", app: ".dark", site: ":root" },
] as const;

describe.each(themes)("$theme palette", ({ theme, app, site }) => {
  const appVars = block(globals, app);
  const siteVars = [...block(brand, site)].filter(([n]) => n.startsWith("mf-"));

  it("declares the brand tokens", () => {
    expect(siteVars.map(([n]) => n).sort()).toEqual(
      [
        "mf-accent",
        "mf-background",
        "mf-border",
        "mf-card",
        "mf-destructive",
        "mf-foreground",
        "mf-muted",
        "mf-muted-foreground",
        "mf-primary",
        "mf-secondary",
        "mf-success",
        "mf-warning",
      ].sort(),
    );
  });

  it.each(siteVars)("--%s matches the app", (name, value) => {
    expect(value).toBe(appVars.get(name.slice("mf-".length)));
  });

  it("logo uses only palette colours", () => {
    const palette = new Set(siteVars.map(([, v]) => v));
    const hexes = read(`../src/assets/logo-${theme}.svg`)
      .toLowerCase()
      .match(/#[0-9a-f]{3,8}\b/g);
    expect(hexes).not.toBeNull();
    for (const hex of hexes!) expect(palette).toContain(hex);
  });
});
