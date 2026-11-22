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
  // The e2e API runs as a control plane (AGENTS.md §9), so the admin manages its nodes.
  "Nodes",
  "Settings",
];

test("/ redirects to the demo project's flags", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/projects\/demo\/flags$/);
});

test.describe("desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the admin sees every item, with Flags current", async ({ page }) => {
    await page.goto("/projects/demo/flags");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link")).toHaveText(ADMIN_ITEMS);
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

  test("a viewer sees Settings but not Integrations", async ({ page }) => {
    await page.goto("/projects/demo/flags");
    const nav = page.getByRole("navigation", { name: "Main" });
    await expect(nav.getByRole("link", { name: "Audit log" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Settings" })).toHaveCount(1);
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

test.describe("running version", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  // The e2e API leaves the update check on but has stored no release, so no pill shows.
  for (const [who, storageState] of [
    ["the admin", undefined],
    ["a viewer", VIEWER.storageState],
  ] as const) {
    test.describe(who, () => {
      if (storageState) test.use({ storageState });

      test(`${who} sees the running version`, async ({ page }) => {
        await page.goto("/projects/demo/flags");
        await expect(page.getByTestId("app-version")).toHaveText(
          /^v\d+\.\d+\.\d+/,
        );
        await expect(page.getByRole("link", { name: /Update:/ })).toHaveCount(
          0,
        );
      });
    });
  }
});
