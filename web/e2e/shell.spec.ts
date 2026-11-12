import { expect, test } from "@playwright/test";

import { VIEWER } from "./support/users";

const ADMIN_ITEMS = [
  "Flags",
  "Segments",
  "Environments",
  "Change requests",
  "Audit log",
  "API & SDK keys",
  "Integrations",
  "Settings",
];

test("/ redirects to the demo project's flags", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects\/demo\/flags$/);
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the admin sees every item except Nodes, with Flags current", async ({
    page,
  }) => {
    await page.goto("/projects/demo/flags");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link")).toHaveText(ADMIN_ITEMS);
    await expect(nav.getByRole("link", { name: "Nodes" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Flags" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  test("an unknown project shows the 404 page inside the shell", async ({
    page,
  }) => {
    await page.goto("/projects/nope/flags");
    await expect(
      page.getByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
  });

  test("the project switcher keeps the section", async ({ page }) => {
    await page.goto("/projects/demo/audit");
    await page.getByRole("button", { name: "Project: Demo" }).click();
    await page.getByRole("menuitem", { name: "Other" }).click();
    await expect(page).toHaveURL(/\/projects\/other\/audit$/);
  });
});

test.describe("viewer", () => {
  test.use({
    storageState: VIEWER.storageState,
    viewport: { width: 1440, height: 900 },
  });

  test("a viewer doesn't see Settings or Integrations", async ({ page }) => {
    await page.goto("/projects/demo/flags");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Audit log" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Settings" })).toHaveCount(0);
    await expect(nav.getByRole("link", { name: "Integrations" })).toHaveCount(
      0,
    );
  });
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 800 } });

  test("the menu button opens a drawer that closes on navigation", async ({
    page,
  }) => {
    await page.goto("/projects/demo/segments");
    await page.getByRole("button", { name: "Open menu" }).click();
    const drawer = page.getByRole("dialog");
    await drawer.getByRole("link", { name: "Flags", exact: true }).click();
    await expect(page).toHaveURL(/\/projects\/demo\/flags$/);
    await expect(drawer).toBeHidden();
  });
});

test.describe("unfolded", () => {
  test.use({ viewport: { width: 880, height: 800 } });

  test("the sidebar is forced to icons with tooltips", async ({ page }) => {
    await page.goto("/projects/demo/flags");
    await expect(
      page.locator("[data-slot=sidebar][data-state=collapsed]"),
    ).toBeVisible();
    await page
      .getByRole("navigation", { name: "Main" })
      .getByRole("link", { name: "Flags" })
      .hover();
    // Base UI tooltips are visual only (no role="tooltip"); the link's name is the label.
    await expect(page.locator("[data-slot=tooltip-content]")).toHaveText(
      "Flags",
    );
  });
});
