import { expect, type Locator, type Page, test } from "@playwright/test";

import { adminApi, ok } from "./support/api";
import { ADMIN, PASSWORD, VIEWER } from "./support/users";

const SETTINGS = "/settings";
const MEMBERS = `${SETTINGS}?tab=members`;
// Every user this run invites has an email starting with it, so afterAll disables exactly those.
const RUN = `e2e-${Date.now().toString(36)}`;

function tablist(page: Page) {
  return page.getByRole("tablist", { name: "Settings" });
}

function memberRow(page: Page, email: string): Locator {
  return page
    .getByRole("table", { name: "Members" })
    .getByRole("row")
    .filter({ hasText: email });
}

/** Fills and sends the Invite member form; `role` is a role key, or none for "No role". */
async function invite(page: Page, email: string, name: string, role?: string) {
  await page.getByRole("button", { name: "Invite member" }).click();
  const dialog = page.getByRole("dialog", { name: "Invite member" });
  await dialog.getByLabel("Email").fill(email);
  await dialog.getByLabel("Name").fill(name);
  if (role) await dialog.getByLabel("Organization role").selectOption(role);
  await dialog.getByRole("button", { name: "Invite", exact: true }).click();
}

test.afterAll(async () => {
  const api = await adminApi();
  try {
    const found = await ok(
      await api.get("/api/users", { params: { q: RUN, limit: 500 } }),
      "list the run's users",
    );
    const { items } = (await found.json()) as {
      items: { id: string; email: string; status: string }[];
    };
    for (const user of items) {
      if (!user.email.startsWith(RUN) || user.status === "disabled") continue;
      await ok(
        await api.post(`/api/users/${user.id}/disable`),
        `disable ${user.email}`,
      );
    }
  } finally {
    await api.dispose();
  }
});

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

test.describe("members", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("invite a member with a role", async ({ page, browser }) => {
    const email = `${RUN}-joins@invite.e2e.test`;
    await page.goto(MEMBERS);
    await invite(page, email, "Ivy Invitee", "viewer");

    const reveal = page.getByRole("alertdialog", { name: "Invite link" });
    await expect(
      reveal.getByText(/^No email is sent\. Share this link; it works once/),
    ).toBeVisible();
    const url = (await reveal.locator("code").textContent()) ?? "";
    expect(url).toMatch(/\/invite\/[^/]+$/);
    await reveal.getByRole("button", { name: "Done" }).click();

    const row = memberRow(page, email);
    await expect(row).toContainText("Invited");
    await expect(row).toContainText("link expires");
    await expect(row).toContainText("viewer");
    await expect(row).toContainText("Never");

    const fresh = await browser.newContext({
      storageState: { cookies: [], origins: [] },
    });
    try {
      const other = await fresh.newPage();
      await other.goto(url);
      await other.getByLabel("New password").fill(PASSWORD);
      await other.getByLabel("Confirm password").fill(PASSWORD);
      await other.getByRole("button", { name: "Set password" }).click();
      await expect(other).toHaveURL(/\/sign-in/);
    } finally {
      await fresh.close();
    }

    await page.reload();
    await expect(memberRow(page, email)).toContainText("Active");
  });

  test("search", async ({ page }) => {
    const email = `${RUN}-search@invite.e2e.test`;
    const api = await adminApi();
    try {
      await ok(
        await api.post("/api/users", { data: { email, name: "Sam Search" } }),
        "invite the search user",
      );
    } finally {
      await api.dispose();
    }

    await page.goto(MEMBERS);
    await expect(memberRow(page, ADMIN.email)).toBeVisible();
    await page.getByRole("searchbox", { name: "Search members" }).fill(email);
    await expect(page).toHaveURL(
      new RegExp(`[?&]q=${encodeURIComponent(email)}(&|$)`),
    );
    await expect(
      page.getByRole("table", { name: "Members" }).getByRole("row"),
    ).toHaveCount(2); // the header and the one match
    await expect(memberRow(page, email)).toBeVisible();

    await page.reload();
    await expect(
      page.getByRole("searchbox", { name: "Search members" }),
    ).toHaveValue(email);
    await expect(memberRow(page, ADMIN.email)).toHaveCount(0);
  });

  test("role refused", async ({ page }) => {
    await page.route("**/api/bindings", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      await route.fulfill({
        status: 403,
        json: { code: "forbidden", message: "You can't grant viewer" },
      });
    });
    await page.goto(MEMBERS);
    await invite(
      page,
      `${RUN}-refused@invite.e2e.test`,
      "Rex Refused",
      "viewer",
    );

    const reveal = page.getByRole("alertdialog", { name: "Invite link" });
    await expect(reveal.locator("code")).toContainText("/invite/");
    await expect(
      reveal.getByText("The role was not assigned: You can't grant viewer"),
    ).toBeVisible();
  });

  test("duplicate email", async ({ page }) => {
    await page.goto(MEMBERS);
    await invite(page, ADMIN.email, "Ada Again");
    const dialog = page.getByRole("dialog", { name: "Invite member" });
    const field = dialog.getByLabel("Email");
    await expect(field).toHaveAttribute("aria-invalid", "true");
    await expect(
      dialog.getByText("Someone with this email is already a member"),
    ).toBeVisible();
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
