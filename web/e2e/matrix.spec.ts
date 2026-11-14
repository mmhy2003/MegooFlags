import { expect, test } from "@playwright/test";

import {
  axeViolations,
  noHorizontalScroll,
  smallTargets,
} from "./support/checks";

// UI-3, UI-6, UI-12, UI-16, UI-19: every width from a folded phone to a desktop, in both themes.
const WIDTHS = [280, 344, 390, 880, 1024, 1440];
const THEMES = ["light", "dark"] as const;
const FLAG = "/projects/demo/flags/new-checkout?env=development";
// `mask` hides <time> elements, whose relative text changes from run to run. demo is never
// changed and has no evaluations, so the flag pages (Insights too) are stable.
const PAGES: {
  name: string;
  path: string;
  mask?: boolean;
  detail?: boolean;
}[] = [
  { name: "flags", path: "/projects/demo/flags", mask: true },
  { name: "flag-new", path: "/projects/demo/flags/new", mask: true },
  ...["targeting", "test", "history", "insights", "settings"].map((tab) => ({
    name: `flag-${tab}`,
    path: `${FLAG}&tab=${tab}`,
    mask: true,
    detail: true,
  })),
  { name: "settings", path: "/settings" },
  { name: "kit", path: "/dev/kit" },
];

for (const theme of THEMES) {
  test.describe(theme, () => {
    // The admin's stored theme is "system", so the page follows the colour scheme.
    test.use({ colorScheme: theme });

    for (const { name, path, mask, detail } of PAGES) {
      for (const width of WIDTHS) {
        test(`${name} at ${width} px`, async ({ page }) => {
          await page.setViewportSize({ width, height: 900 });
          await page.goto(path);
          await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
          if (detail) {
            await expect(
              page.getByRole("region", { name: "Environments" }),
            ).toBeVisible();
          }
          await page.evaluate(() => document.fonts.ready);

          await expect(page.locator("html")).toHaveClass(
            new RegExp(`\\b${theme}\\b`),
          );
          expect(await axeViolations(page)).toEqual([]);
          expect(await noHorizontalScroll(page)).toBe(true);
          expect(await smallTargets(page)).toEqual([]);
          await expect(page).toHaveScreenshot(`${name}-${theme}-${width}.png`, {
            fullPage: true,
            mask: mask ? [page.locator("time")] : [],
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
