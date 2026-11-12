import { expect, test, type Page } from "@playwright/test";

async function openPalette(page: Page, shortcut = "Control+k") {
  await page.goto("/projects/demo/flags");
  const dialog = page.getByRole("dialog");
  const input = dialog.getByRole("combobox");
  // The shortcut listener exists only once the page has hydrated, so retry until it opens.
  await expect(async () => {
    if (!(await input.isVisible())) await page.keyboard.press(shortcut);
    await expect(input).toBeVisible({ timeout: 1000 });
  }).toPass();
  return dialog;
}

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("Ctrl+K finds a flag and opens it", async ({ page }) => {
    const dialog = await openPalette(page);
    await dialog.getByRole("combobox").fill("new-ch");
    await expect(
      dialog.getByRole("option", { name: /new-checkout/ }),
    ).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/projects\/demo\/flags\/new-checkout$/);
  });

  test("a query with no match says so", async ({ page }) => {
    const dialog = await openPalette(page);
    await dialog.getByRole("combobox").fill("zzzz-none");
    await expect(dialog.getByText("No results")).toBeVisible();
  });

  test("pages are listed and open", async ({ page }) => {
    const dialog = await openPalette(page);
    await dialog.getByRole("combobox").fill("Audit");
    await dialog.getByRole("option", { name: "Audit log" }).click();
    await expect(page).toHaveURL(/\/projects\/demo\/audit$/);
  });

  test("environments are listed", async ({ page }) => {
    const dialog = await openPalette(page);
    await dialog.getByRole("combobox").fill("production");
    await expect(
      dialog.getByRole("option", { name: "Production" }),
    ).toBeVisible();
  });

  test("theme actions apply the theme", async ({ page }) => {
    let dialog = await openPalette(page);
    await dialog.getByRole("combobox").fill("dark");
    await dialog.getByRole("option", { name: "Use dark theme" }).click();
    await expect(page.locator("html")).toHaveClass(/\bdark\b/);

    await page.keyboard.press("Control+k");
    dialog = page.getByRole("dialog");
    await dialog.getByRole("combobox").fill("system");
    const saved = page.waitForResponse(
      (r) => r.url().endsWith("/api/account/preferences") && r.ok(),
    );
    await dialog.getByRole("option", { name: "Use system theme" }).click();
    await saved;
  });

  test("Meta+K also opens the palette", async ({ page }) => {
    await openPalette(page, "Meta+k");
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 800 } });

  test("the Search button opens a full-screen palette", async ({ page }) => {
    await page.goto("/projects/demo/flags");
    const dialog = page.getByRole("dialog");
    await expect(async () => {
      await page.getByRole("button", { name: "Search" }).click();
      await expect(dialog.getByRole("combobox")).toBeVisible({ timeout: 1000 });
    }).toPass();
    await expect
      .poll(() => dialog.boundingBox())
      .toEqual({ x: 0, y: 0, width: 390, height: 800 });
  });
});
