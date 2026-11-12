import { expect, test } from "@playwright/test";

import {
  axeViolations,
  noHorizontalScroll,
  smallTargets,
} from "./support/checks";

// UI-3, UI-6, UI-12, UI-16, UI-19: every width from a folded phone to a desktop, in both themes.
const WIDTHS = [280, 344, 390, 880, 1024, 1440];
const THEMES = ["light", "dark"] as const;
const PAGES = [
  { name: "flags", path: "/projects/demo/flags" },
  { name: "settings", path: "/settings" },
  { name: "kit", path: "/dev/kit" },
];

for (const theme of THEMES) {
  test.describe(theme, () => {
    // The admin's stored theme is "system", so the page follows the colour scheme.
    test.use({ colorScheme: theme });

    for (const { name, path } of PAGES) {
      for (const width of WIDTHS) {
        test(`${name} at ${width} px`, async ({ page }) => {
          await page.setViewportSize({ width, height: 900 });
          await page.goto(path);
          await page.evaluate(() => document.fonts.ready);

          await expect(page.locator("html")).toHaveClass(
            new RegExp(`\\b${theme}\\b`),
          );
          expect(await axeViolations(page)).toEqual([]);
          expect(await noHorizontalScroll(page)).toBe(true);
          expect(await smallTargets(page)).toEqual([]);
          await expect(page).toHaveScreenshot(`${name}-${theme}-${width}.png`, {
            fullPage: true,
          });
        });
      }
    }
  });
}

for (const width of [280, 1440]) {
  test(`account security has no horizontal scroll at ${width} px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/account/security");
    await expect(page.getByRole("main")).toBeVisible();
    expect(await noHorizontalScroll(page)).toBe(true);
  });
}
