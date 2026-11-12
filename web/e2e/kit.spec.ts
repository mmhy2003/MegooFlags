import { expect, test } from "@playwright/test";

import { noHorizontalScroll } from "./support/checks";

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

test.describe("dialogs", () => {
  test.use({ viewport: { width: 344, height: 740 } });

  test("the dialog is a full-screen sheet when folded and keeps its input across a fold", async ({
    page,
  }) => {
    await page.goto("/dev/kit");
    await page.getByRole("button", { name: "Open sample dialog" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    const folded = async () => {
      const box = await dialog.boundingBox();
      expect(box).not.toBeNull();
      expect(Math.round(box!.x)).toBe(0);
      expect(Math.round(box!.y)).toBe(0);
      expect(Math.round(box!.width)).toBe(344);
      expect(Math.round(box!.height)).toBe(740);
    };
    await expect(folded).toPass();
    const note = dialog.getByRole("textbox", { name: "Note" });
    await note.fill("keep me");

    await page.setViewportSize({ width: 880, height: 740 });
    await expect(dialog).toBeVisible();
    await expect(async () => {
      const box = await dialog.boundingBox();
      expect(box!.width).toBeLessThan(880);
    }).toPass();
    await expect(note).toHaveValue("keep me");

    await page.setViewportSize({ width: 344, height: 740 });
    await expect(folded).toPass();
    await expect(note).toHaveValue("keep me");
  });

  test("the confirm dialog cancels or confirms", async ({ page }) => {
    await page.goto("/dev/kit");
    const trigger = page.getByRole("button", { name: "Kill new-checkout" });
    await trigger.click();
    const alert = page.getByRole("alertdialog");
    await expect(alert).toBeVisible();
    await alert.getByRole("button", { name: "Cancel" }).click();
    await expect(alert).toBeHidden();
    await expect(page.getByText("Confirmed", { exact: true })).toHaveCount(0);

    await trigger.click();
    await expect(alert).toBeVisible();
    await alert.getByRole("button", { name: "Kill", exact: true }).click();
    await expect(alert).toBeHidden();
    await expect(page.getByText("Confirmed", { exact: true })).toBeVisible();
  });
});

test.describe("narrowest", () => {
  test.use({ viewport: { width: 280, height: 740 } });

  test("long JSON scrolls inside its own box, not the page", async ({
    page,
  }) => {
    await page.goto("/dev/kit");
    const pre = page.locator("[data-testid=json-viewer] pre");
    await expect(pre).toBeVisible();
    expect(await noHorizontalScroll(page)).toBe(true);
    expect(await pre.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(
      true,
    );
    await expect(page.getByRole("button", { name: "Copy JSON" })).toBeVisible();
  });
});

for (const { width, sideBySide } of [
  { width: 1440, sideBySide: true },
  { width: 390, sideBySide: false },
]) {
  test.describe(`diff at ${width}`, () => {
    test.use({ viewport: { width, height: 900 } });

    test(`the panes are ${sideBySide ? "side by side" : "stacked"}`, async ({
      page,
    }) => {
      await page.goto("/dev/kit");
      const before = page.getByRole("region", { name: "Before", exact: true });
      const after = page.getByRole("region", { name: "After", exact: true });
      await before.scrollIntoViewIfNeeded();
      const b = (await before.boundingBox())!;
      const a = (await after.boundingBox())!;
      if (sideBySide) {
        expect(Math.round(a.y)).toBe(Math.round(b.y));
      } else {
        expect(a.y).toBeGreaterThanOrEqual(b.y + b.height);
      }

      const removed = before.locator("[data-kind=remove]").first();
      await expect(removed).toContainText("−");
      await expect(removed.locator(".sr-only")).toHaveText("removed");
      const added = after.locator("[data-kind=add]").first();
      await expect(added).toContainText("+");
      await expect(added.locator(".sr-only")).toHaveText("added");
    });
  });
}
