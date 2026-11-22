import { expect, type Page, test } from "@playwright/test";

import { VIEWER } from "./support/users";

const SETTINGS = "/settings";

function tablist(page: Page) {
  return page.getByRole("tablist", { name: "Settings" });
}

test.describe("settings", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("tabs follow the URL", async ({ page }) => {
    await page.goto(`${SETTINGS}?tab=tokens`);
    await expect(
      page.getByRole("heading", { name: "Settings", level: 1 }),
    ).toBeVisible();
    await expect(
      tablist(page).getByRole("tab", { name: "Service tokens" }),
    ).toHaveAttribute("aria-selected", "true");

    await tablist(page).getByRole("tab", { name: "SSO" }).click();
    await expect(page).toHaveURL(/[?&]tab=sso(&|$)/);
    await expect(
      tablist(page).getByRole("tab", { name: "SSO" }),
    ).toHaveAttribute("aria-selected", "true");

    await page.goto(`${SETTINGS}?tab=bogus`);
    await expect(
      tablist(page).getByRole("tab", { name: "Members" }),
    ).toHaveAttribute("aria-selected", "true");
  });

  test("changing the tab drops the other tab's filters", async ({ page }) => {
    await page.goto(`${SETTINGS}?tab=audit&action=auth.sign_in`);
    await tablist(page).getByRole("tab", { name: "Security" }).click();
    await expect(page).toHaveURL(/\/settings\?tab=security$/);
  });

  test("Require two-factor", async ({ page }) => {
    // Turning it on for real would lock out the other specs' users.
    let sent: unknown = null;
    await page.route("**/api/settings", async (route) => {
      const request = route.request();
      if (request.method() === "PUT") {
        sent = request.postDataJSON();
        await route.fulfill({ json: { require_mfa: true } });
      } else {
        await route.fulfill({ json: { require_mfa: false } });
      }
    });
    await page.goto(`${SETTINGS}?tab=security`);
    await expect(
      page.getByText(
        "People who sign in with a password must set up a second factor. SSO users rely on their identity provider.",
      ),
    ).toBeVisible();
    const toggle = page.getByRole("switch", {
      name: "Require two-factor sign-in",
    });
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    await expect(
      page.getByText("Two-factor sign-in is now required"),
    ).toBeVisible();
    expect(sent).toEqual({ require_mfa: true });
    await expect(toggle).toBeChecked();
  });
});

test.describe("as a viewer", () => {
  test.use({
    storageState: VIEWER.storageState,
    viewport: { width: 1440, height: 900 },
  });

  test("a viewer sees Teams, Roles and Audit log", async ({ page }) => {
    await page.goto(`${SETTINGS}?tab=members`);
    await expect(tablist(page).getByRole("tab")).toHaveText([
      "Teams",
      "Roles",
      "Audit log",
    ]);
    await expect(
      tablist(page).getByRole("tab", { name: "Teams" }),
    ).toHaveAttribute("aria-selected", "true");
  });
});

test("the tabs scroll inside themselves at 280 px", async ({ page }) => {
  await page.setViewportSize({ width: 280, height: 800 });
  await page.goto(`${SETTINGS}?tab=audit`);
  await expect(tablist(page)).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
