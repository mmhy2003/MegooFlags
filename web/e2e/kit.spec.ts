import { expect, test } from "@playwright/test";

const FLAGS = ["new-checkout", "dark-mode", "pricing-copy"];

test.describe("desktop", () => {
  test.use({ viewport: { width: 1024, height: 900 } });

  test("the data list is a table and the cards are hidden", async ({
    page,
  }) => {
    await page.goto("/dev/kit");
    const table = page.getByRole("table");
    await expect(table).toBeVisible();
    await expect(
      table.getByRole("columnheader", { name: "Key" }),
    ).toBeVisible();
    for (const key of FLAGS) {
      await expect(
        table.getByRole("row", { name: new RegExp(key) }),
      ).toBeVisible();
    }
    // Hidden cards drop out of the accessibility tree, so count the elements.
    await expect(page.locator("article")).toHaveCount(3);
    for (const card of await page.locator("article").all()) {
      await expect(card).toBeHidden();
    }
  });

  test("status pills pair an icon with their label", async ({ page }) => {
    await page.goto("/dev/kit");
    const pills = page.getByRole("region", { name: "Status pills" });
    for (const label of ["On", "Off", "Stale", "Killed", "Pending", "Error"]) {
      const pill = pills.getByText(label, { exact: true });
      await expect(pill).toBeVisible();
      await expect(pill.locator("..").locator("svg")).toHaveCount(1);
    }
  });

  test("the toggle flips its state and its text", async ({ page }) => {
    await page.goto("/dev/kit");
    const toggle = page.getByRole("switch", {
      name: "new-checkout in production",
    });
    const label = page.locator("label", { has: toggle });
    const before = await toggle.getAttribute("aria-checked");
    const after = before === "true" ? "false" : "true";
    await expect(label).toContainText(before === "true" ? "On" : "Off");
    await expect(async () => {
      if ((await toggle.getAttribute("aria-checked")) === before) {
        await toggle.click();
      }
      await expect(toggle).toHaveAttribute("aria-checked", after, {
        timeout: 1000,
      });
    }).toPass();
    await expect(label).toContainText(after === "true" ? "On" : "Off");
  });

  test("the rollout input and slider stay in step", async ({ page }) => {
    await page.goto("/dev/kit");
    const input = page.getByRole("spinbutton", { name: "Rollout percentage" });
    const slider = page.getByRole("slider");
    await expect(async () => {
      await input.fill("37.5");
      await input.press("Tab");
      await expect(slider).toHaveAttribute("aria-valuenow", "37.5", {
        timeout: 1000,
      });
    }).toPass();
    await slider.press("ArrowRight");
    await expect(slider).toHaveAttribute("aria-valuenow", "37.6");
    await expect(input).toHaveValue("37.6");
  });
});

test.describe("unfolded", () => {
  test.use({ viewport: { width: 880, height: 900 } });

  test("extra columns move into the row's More", async ({ page }) => {
    await page.goto("/dev/kit");
    const table = page.getByRole("table");
    await expect(table).toBeVisible();
    await expect(
      table.getByRole("columnheader", { name: "Updated" }),
    ).toBeHidden();
    const row = table.getByRole("row", { name: /new-checkout/ });
    await expect(row.getByText("More", { exact: true })).toBeVisible();
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 800 } });

  test("the data list is a stack of cards", async ({ page }) => {
    await page.goto("/dev/kit");
    await expect(page.getByRole("table")).toBeHidden();
    const cards = page.getByRole("article");
    await expect(cards).toHaveCount(3);
    const first = cards.filter({ hasText: "new-checkout" });
    await expect(first).toBeVisible();
    await expect(first.getByText("On", { exact: true })).toBeVisible();
    await expect(
      cards.filter({ hasText: "dark-mode" }).getByText("Off", { exact: true }),
    ).toBeVisible();

    const extra = first.getByText("Updated", { exact: true });
    await expect(extra).toBeHidden();
    await first.getByText("More", { exact: true }).click();
    await expect(extra).toBeVisible();
  });
});
