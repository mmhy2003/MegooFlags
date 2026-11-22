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

/**
 * Invites a member through the API, with an organization role when given; `active` also sets
 * their password, as accepting the invitation does.
 */
async function seedMember(
  email: string,
  name: string,
  { role, active = false }: { role?: string; active?: boolean } = {},
): Promise<void> {
  const api = await adminApi();
  try {
    const created = await ok(
      await api.post("/api/users", { data: { email, name } }),
      `invite ${email}`,
    );
    const { user, invite } = (await created.json()) as {
      user: { id: string };
      invite: { url: string };
    };
    if (role)
      await ok(
        await api.post("/api/bindings", {
          data: {
            subject_kind: "user",
            subject_id: user.id,
            role,
            scope: "org",
          },
        }),
        `give ${email} ${role}`,
      );
    if (active) {
      const token = invite.url.split("/").pop() ?? "";
      await ok(
        await api.post(`/api/invitations/${token}`, {
          data: { password: PASSWORD },
        }),
        `accept ${email}'s invitation`,
      );
    }
  } finally {
    await api.dispose();
  }
}

/** Opens the Manage dialog of the member with this email, on Members filtered to them. */
async function manage(page: Page, email: string, name: string) {
  await page.goto(`${MEMBERS}&q=${encodeURIComponent(email)}`);
  await memberRow(page, email).getByRole("button", { name: "Manage" }).click();
  const dialog = page.getByRole("dialog", { name });
  await expect(dialog).toBeVisible();
  return dialog;
}

/** Closes a dialog; the page behind it is hidden from the accessibility tree while it is open. */
async function closeDialog(dialog: Locator) {
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(dialog).toHaveCount(0);
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

test.describe("manage a member", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("assign and remove roles", async ({ page }) => {
    const email = `${RUN}-roles@invite.e2e.test`;
    const name = "Rory Roles";
    await seedMember(email, name, { role: "viewer" });
    const dialog = await manage(page, email, name);

    const access = dialog.getByRole("listitem");
    await expect(access).toHaveText(["viewer · Organization"]);

    const form = dialog.getByRole("form", { name: "Add access" });
    await form.getByLabel("Role").selectOption("editor");
    await form.getByLabel("Level").selectOption("project");
    await form.getByLabel("Project", { exact: true }).selectOption("shop");
    await form.getByRole("button", { name: "Add" }).click();
    // The API doesn't promise an order, so look for each one.
    await expect(access).toHaveCount(2);
    await expect(
      access.filter({ hasText: "editor · Project shop" }),
    ).toHaveCount(1);
    await expect(
      access.filter({ hasText: "viewer · Organization" }),
    ).toHaveCount(1);

    await dialog
      .getByRole("button", { name: "Remove editor · Project shop" })
      .click();
    await page
      .getByRole("alertdialog", { name: "Remove access?" })
      .getByRole("button", { name: "Remove access" })
      .click();
    await expect(access).toHaveText(["viewer · Organization"]);
  });

  test("rename", async ({ page }) => {
    const email = `${RUN}-rename@invite.e2e.test`;
    await seedMember(email, "Nina Name");
    const dialog = await manage(page, email, "Nina Name");

    await dialog.getByLabel("Name").fill("Nina Newname");
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Name saved")).toBeVisible();
    await closeDialog(dialog);
    await expect(memberRow(page, email)).toContainText("Nina Newname");
  });

  test("new invite link", async ({ page }) => {
    const invited = `${RUN}-link@invite.e2e.test`;
    await seedMember(invited, "Lee Link");
    const dialog = await manage(page, invited, "Lee Link");
    await expect(
      dialog.getByRole("button", { name: "Password reset link" }),
    ).toHaveCount(0);
    await dialog.getByRole("button", { name: "New invite link" }).click();
    const reveal = page.getByRole("alertdialog", { name: "Invite link" });
    await expect(reveal.locator("code")).toContainText("/invite/");
    await reveal.getByRole("button", { name: "Done" }).click();
    await expect(reveal).toHaveCount(0);

    const active = `${RUN}-reset@invite.e2e.test`;
    await seedMember(active, "Rita Reset", { active: true });
    const other = await manage(page, active, "Rita Reset");
    await expect(
      other.getByRole("button", { name: "New invite link" }),
    ).toHaveCount(0);
    await other.getByRole("button", { name: "Password reset link" }).click();
    await expect(
      page
        .getByRole("alertdialog", { name: "Password reset link" })
        .locator("code"),
    ).toContainText("/invite/");
  });

  test("reset two-factor", async ({ page }) => {
    const email = `${RUN}-mfa@invite.e2e.test`;
    await seedMember(email, "Max Factor", { active: true });
    const dialog = await manage(page, email, "Max Factor");
    await dialog.getByRole("button", { name: "Reset two-factor" }).click();
    await page
      .getByRole("alertdialog", { name: "Reset two-factor?" })
      .getByRole("button", { name: "Reset two-factor" })
      .click();
    await expect(page.getByText("Two-factor reset")).toBeVisible();
  });

  test("disable and enable", async ({ page }) => {
    const email = `${RUN}-toggle@invite.e2e.test`;
    await seedMember(email, "Dee Disable", { active: true });
    const dialog = await manage(page, email, "Dee Disable");

    await dialog.getByRole("button", { name: "Disable" }).click();
    const confirm = page.getByRole("alertdialog", {
      name: "Disable Dee Disable?",
    });
    await expect(
      confirm.getByText("They are signed out everywhere."),
    ).toBeVisible();
    await confirm.getByRole("button", { name: "Disable" }).click();
    await expect(dialog).toContainText("Disabled");
    await closeDialog(dialog);
    await expect(memberRow(page, email)).toContainText("Disabled");

    await memberRow(page, email)
      .getByRole("button", { name: "Manage" })
      .click();
    await dialog.getByRole("button", { name: "Enable" }).click();
    await expect(dialog).toContainText("Active");
    await closeDialog(dialog);
    await expect(memberRow(page, email)).toContainText("Active");
  });

  test("last admin", async ({ page }) => {
    const email = `${RUN}-admin@invite.e2e.test`;
    await seedMember(email, "Al Admin", { active: true });
    await page.route("**/api/users/*/disable", (route) =>
      route.fulfill({
        status: 409,
        json: {
          code: "last_admin",
          message: "The last admin can't be disabled",
          details: null,
        },
      }),
    );
    const dialog = await manage(page, email, "Al Admin");
    await dialog.getByRole("button", { name: "Disable" }).click();
    await page
      .getByRole("alertdialog", { name: "Disable Al Admin?" })
      .getByRole("button", { name: "Disable" })
      .click();
    await expect(dialog.getByRole("alert")).toHaveText(
      "This would leave the organization without an admin.",
    );
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
