import { expect, type Locator, type Page, test } from "@playwright/test";

import { adminApi, ok } from "./support/api";
import { ADMIN, PASSWORD, VIEWER } from "./support/users";

const SETTINGS = "/settings";
const MEMBERS = `${SETTINGS}?tab=members`;
const TEAMS = `${SETTINGS}?tab=teams`;
const ROLES = `${SETTINGS}?tab=roles`;
const SSO = `${SETTINGS}?tab=sso`;
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

function teamRow(page: Page, key: string): Locator {
  return page
    .getByRole("table", { name: "Teams" })
    .getByRole("row")
    .filter({ hasText: key });
}

function roleRow(page: Page, key: string): Locator {
  return page
    .getByRole("table", { name: "Roles" })
    .getByRole("row")
    .filter({ has: page.getByText(key, { exact: true }) });
}

function providerRow(page: Page, key: string): Locator {
  return page
    .getByRole("table", { name: "SSO providers" })
    .getByRole("row")
    .filter({ has: page.getByText(key, { exact: true }) });
}

/** Picks an item of a provider's actions menu. */
async function providerAction(page: Page, row: Locator, item: string) {
  await row.getByRole("button", { name: /^Actions for / }).click();
  await page.getByRole("menuitem", { name: item }).click();
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
    const teams = await ok(
      await api.get("/api/teams", { params: { limit: 500 } }),
      "list the teams",
    );
    for (const team of ((await teams.json()) as { items: { key: string }[] })
      .items) {
      if (!team.key.startsWith(RUN)) continue;
      await ok(
        await api.delete(`/api/teams/${team.key}`),
        `delete ${team.key}`,
      );
    }
    const roles = await ok(
      await api.get("/api/roles", { params: { limit: 500 } }),
      "list the roles",
    );
    for (const role of ((await roles.json()) as { items: { key: string }[] })
      .items) {
      if (!role.key.startsWith(RUN)) continue;
      await ok(
        await api.delete(`/api/roles/${role.key}`),
        `delete ${role.key}`,
      );
    }
    const providers = await ok(
      await api.get("/api/sso/providers", { params: { limit: 500 } }),
      "list the SSO providers",
    );
    for (const provider of (
      (await providers.json()) as { items: { key: string }[] }
    ).items) {
      if (!provider.key.startsWith(RUN)) continue;
      await ok(
        await api.delete(`/api/sso/providers/${provider.key}`),
        `delete ${provider.key}`,
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

test.describe("teams", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a team with members and a role", async ({ page }) => {
    const name = `${RUN} Web`;
    const key = `${RUN}-web`;
    await page.goto(TEAMS);
    await page.getByRole("button", { name: "New team" }).click();
    const create = page.getByRole("dialog", { name: "New team" });
    await create.getByLabel("Name").fill(name);
    await expect(create.getByLabel("Key")).toHaveValue(key);
    await create.getByLabel("Key").fill(`${key}-x`);
    await create.getByLabel("Key").fill(key);
    await create.getByRole("button", { name: "Create team" }).click();
    await expect(create).toHaveCount(0);

    const row = teamRow(page, key);
    await expect(row).toContainText(name);
    await row.getByRole("button", { name: `Open ${name}` }).click();
    const dialog = page.getByRole("dialog", { name });

    const members = dialog.getByRole("region", { name: "Members" });
    await expect(members.getByText("No members yet.")).toBeVisible();
    await members
      .getByRole("searchbox", { name: "Add a member" })
      .fill(VIEWER.email);
    await members.getByRole("button", { name: `Add ${VIEWER.email}` }).click();
    const member = members
      .getByRole("list", { name: "Team members" })
      .getByRole("listitem")
      .filter({ hasText: VIEWER.email });
    await expect(member).toContainText("Added by hand");

    const access = dialog.getByRole("region", { name: "Access" });
    const form = access.getByRole("form", { name: "Add access" });
    await form.getByLabel("Role").selectOption("viewer");
    await form.getByLabel("Level").selectOption("project");
    await form.getByLabel("Project", { exact: true }).selectOption("shop");
    await form.getByRole("button", { name: "Add" }).click();
    await expect(access.getByRole("listitem")).toHaveText([
      "viewer · Project shop",
    ]);

    await closeDialog(dialog);
    await expect(row.getByRole("cell").nth(1)).toHaveText("1 member");
    await expect(row).toContainText("viewer · Project shop");

    await row.getByRole("button", { name: `Open ${name}` }).click();
    await dialog.getByLabel("Name", { exact: true }).fill(`${name} Renamed`);
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Team renamed")).toBeVisible();
    const renamed = page.getByRole("dialog", { name: `${name} Renamed` });

    await renamed
      .getByRole("region", { name: "Members" })
      .getByRole("button", { name: `Remove ${VIEWER.email}` })
      .click();
    await page
      .getByRole("alertdialog", { name: "Remove from the team?" })
      .getByRole("button", { name: "Remove" })
      .click();
    await expect(
      renamed
        .getByRole("region", { name: "Members" })
        .getByText("No members yet."),
    ).toBeVisible();

    await renamed.getByRole("button", { name: "Delete team" }).click();
    await page
      .getByRole("alertdialog", { name: `Delete ${name} Renamed?` })
      .getByRole("button", { name: "Delete team" })
      .click();
    await expect(renamed).toHaveCount(0);
    await expect(teamRow(page, key)).toHaveCount(0);
  });

  test("a key already in use shows on the key field", async ({ page }) => {
    const key = `${RUN}-taken`;
    const api = await adminApi();
    try {
      await ok(
        await api.post("/api/teams", { data: { key, name: "Taken" } }),
        "create the taken team",
      );
    } finally {
      await api.dispose();
    }
    await page.goto(TEAMS);
    await page.getByRole("button", { name: "New team" }).click();
    const create = page.getByRole("dialog", { name: "New team" });
    await create.getByLabel("Name").fill("Taken");
    await create.getByLabel("Key").fill(key);
    await create.getByRole("button", { name: "Create team" }).click();
    await expect(create.getByLabel("Key")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await expect(
      create.getByText("Another team already uses this key"),
    ).toBeVisible();
  });

  test("last admin", async ({ page }) => {
    const name = `${RUN} Admins`;
    const key = `${RUN}-admins`;
    const api = await adminApi();
    try {
      await ok(
        await api.post("/api/teams", { data: { key, name } }),
        "create the admins team",
      );
    } finally {
      await api.dispose();
    }
    await page.route(`**/api/teams/${key}`, (route) => {
      if (route.request().method() !== "DELETE") return route.fallback();
      return route.fulfill({
        status: 409,
        json: {
          code: "last_admin",
          message: "The last admin can't be removed",
          details: null,
        },
      });
    });
    await page.goto(TEAMS);
    await teamRow(page, key)
      .getByRole("button", { name: `Open ${name}` })
      .click();
    const dialog = page.getByRole("dialog", { name });
    await dialog.getByRole("button", { name: "Delete team" }).click();
    await page
      .getByRole("alertdialog", { name: `Delete ${name}?` })
      .getByRole("button", { name: "Delete team" })
      .click();
    await expect(dialog.getByRole("alert")).toHaveText(
      "This would leave the organization without an admin.",
    );
  });
});

test.describe("roles", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("create, edit and delete a custom role", async ({ page }) => {
    const name = `${RUN} Toggler`;
    const key = `${RUN}-toggler`;
    await page.goto(ROLES);
    await page.getByRole("button", { name: "New role" }).click();
    const create = page.getByRole("dialog", { name: "New role" });
    await create.getByLabel("Name").fill(name);
    // "Key" alone would also match the "See SDK keys" permission.
    await expect(create.getByLabel("Key", { exact: true })).toHaveValue(key);
    for (const [label, where] of [
      ["Organization", "Works only when granted for the whole organization."],
      ["Project", "Works for the whole organization or for one project."],
      [
        "Environment",
        "Works for the organization, a project or one environment.",
      ],
    ])
      await expect(create.getByRole("group", { name: label })).toContainText(
        where,
      );
    await create.getByLabel("See the project").check();
    await create.getByLabel("Turn flags on and off").check();
    await create.getByRole("button", { name: "Create role" }).click();
    await expect(create).toHaveCount(0);

    const row = roleRow(page, key);
    await expect(row).toContainText(name);
    await expect(row).toContainText("Custom");
    await expect(row).toContainText("2 permissions");

    await row.getByRole("button", { name: `Open ${name}` }).click();
    const dialog = page.getByRole("dialog", { name });
    await expect(dialog.getByLabel("See the project")).toBeChecked();
    await dialog.getByLabel("Edit targeting").check();
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Role saved")).toBeVisible();
    await closeDialog(dialog);
    await expect(row).toContainText("3 permissions");

    await row.getByRole("button", { name: `Open ${name}` }).click();
    await dialog.getByRole("button", { name: "Delete role" }).click();
    await page
      .getByRole("alertdialog", { name: `Delete ${name}?` })
      .getByRole("button", { name: "Delete role" })
      .click();
    await expect(dialog).toHaveCount(0);
    await expect(roleRow(page, key)).toHaveCount(0);
  });

  test("a role in use", async ({ page }) => {
    const name = `${RUN} Used`;
    const key = `${RUN}-used`;
    const team = `${RUN}-users`;
    const api = await adminApi();
    let binding = "";
    try {
      await ok(
        await api.post("/api/roles", {
          data: { key, name, permissions: ["project.read"] },
        }),
        "create the used role",
      );
      const created = await ok(
        await api.post("/api/teams", { data: { key: team, name: team } }),
        "create the team that uses it",
      );
      const { id } = (await created.json()) as { id: string };
      const bound = await ok(
        await api.post("/api/bindings", {
          data: {
            subject_kind: "team",
            subject_id: id,
            role: key,
            scope: "org",
          },
        }),
        "bind the role to the team",
      );
      binding = ((await bound.json()) as { id: string }).id;

      await page.goto(ROLES);
      await roleRow(page, key)
        .getByRole("button", { name: `Open ${name}` })
        .click();
      const dialog = page.getByRole("dialog", { name });
      await dialog.getByRole("button", { name: "Delete role" }).click();
      await page
        .getByRole("alertdialog", { name: `Delete ${name}?` })
        .getByRole("button", { name: "Delete role" })
        .click();
      await expect(dialog.getByRole("alert")).toHaveText(
        /^This role is still used by: 1 binding/,
      );
    } finally {
      if (binding) await api.delete(`/api/bindings/${binding}`);
      await api.delete(`/api/teams/${team}`);
      await api.delete(`/api/roles/${key}`);
      await api.dispose();
    }
  });

  test("built-in roles are read-only", async ({ page }) => {
    await page.goto(ROLES);
    const row = roleRow(page, "admin");
    await expect(row).toContainText("Built-in");
    await row.getByRole("button", { name: "Open Admin" }).click();
    const dialog = page.getByRole("dialog", { name: "Admin" });
    await expect(
      dialog.getByRole("listitem").filter({ hasText: "Manage roles" }),
    ).toHaveCount(1);
    await expect(dialog.getByRole("checkbox")).toHaveCount(0);
    for (const name of ["Save", "Delete role"])
      await expect(dialog.getByRole("button", { name })).toHaveCount(0);
  });
});

test.describe("sso", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("add, edit, disable and delete an OIDC provider", async ({ page }) => {
    const key = `${RUN}-oidc`;
    const name = `${RUN} Okta`;
    const renamed = `${RUN} Okta SSO`;
    await page.goto(SSO);
    await page.getByRole("button", { name: "Add provider" }).click();
    const create = page.getByRole("dialog", { name: "Add SSO provider" });
    await create.getByRole("radio", { name: "OIDC" }).check();
    await create.getByLabel("Name", { exact: true }).fill(name);
    await create.getByLabel("Key", { exact: true }).fill(key);
    await create.getByLabel("Issuer").fill("https://idp.example.test");
    await create.getByLabel("Client ID").fill("megooflags");
    await create.getByLabel("Client secret").fill("e2e-client-secret");
    await expect(
      create.getByRole("switch", { name: "Create users on first sign-in" }),
    ).toBeChecked();
    await create.getByLabel("Default role").selectOption("viewer");
    await create.getByRole("button", { name: "Add provider" }).click();
    await expect(create).toHaveCount(0);

    const row = providerRow(page, key);
    await expect(row).toContainText(name);
    await expect(row).toContainText("OIDC");
    await expect(row).toContainText("Enabled");
    await expect(row).toContainText("New users join as viewer");

    await providerAction(page, row, "Edit");
    const edit = page.getByRole("dialog", { name: `Edit ${name}` });
    const secret = edit.getByLabel("Client secret");
    await expect(secret).toHaveValue("");
    await expect(secret).toHaveAttribute(
      "placeholder",
      "Saved — leave empty to keep it",
    );
    await edit.getByLabel("Name", { exact: true }).fill(renamed);
    await edit.getByRole("button", { name: "Save" }).click();
    await expect(edit).toHaveCount(0);
    await expect(row).toContainText(renamed);
    const api = await adminApi();
    try {
      const read = await ok(
        await api.get(`/api/sso/providers/${key}`),
        "read the provider",
      );
      expect(await read.json()).toMatchObject({
        name: renamed,
        has_client_secret: true,
        oidc: { issuer: "https://idp.example.test", client_id: "megooflags" },
      });
    } finally {
      await api.dispose();
    }

    await providerAction(page, row, "Disable");
    await expect(row).toContainText("Disabled");

    await providerAction(page, row, "Delete");
    await page
      .getByRole("alertdialog", { name: `Delete ${renamed}?` })
      .getByRole("button", { name: "Delete provider" })
      .click();
    await expect(providerRow(page, key)).toHaveCount(0);
  });

  test("SAML fields", async ({ page }) => {
    await page.goto(SSO);
    await page.getByRole("button", { name: "Add provider" }).click();
    const dialog = page.getByRole("dialog", { name: "Add SSO provider" });
    await dialog.getByRole("radio", { name: "SAML" }).check();
    await expect(dialog.getByLabel("Issuer")).toHaveCount(0);
    await expect(dialog.getByLabel("Client secret")).toHaveCount(0);

    await expect(
      dialog.getByRole("radio", { name: "From a URL" }),
    ).toBeChecked();
    await dialog.getByRole("radio", { name: "Paste the XML" }).check();
    await expect(dialog.getByLabel("Metadata XML")).toBeVisible();
    await expect(dialog.getByLabel("Metadata URL")).toHaveCount(0);
    await dialog.getByRole("radio", { name: "From a URL" }).check();
    for (const label of [
      "SP entity ID",
      "Email attribute",
      "Name attribute",
      "Groups attribute",
      "SP private key",
      "SP certificate",
    ])
      await expect(dialog.getByLabel(label)).toBeVisible();

    await dialog.getByLabel("Name", { exact: true }).fill(`${RUN} SAML`);
    await dialog.getByLabel("Key", { exact: true }).fill(`${RUN}-saml`);
    const url = dialog.getByLabel("Metadata URL");
    await url.fill("https://127.0.0.1:9/metadata");
    await dialog.getByRole("button", { name: "Add provider" }).click();
    await expect(url).toHaveAttribute("aria-invalid", "true");
    await expect(
      dialog.getByText(
        "cannot fetch SAML IdP metadata from https://127.0.0.1:9/metadata",
      ),
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

  test("teams are read-only without member.manage", async ({ page }) => {
    await page.goto(TEAMS);
    await expect(
      page
        .getByRole("table", { name: "Teams" })
        .or(page.getByText("No teams yet")),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: "New team" })).toHaveCount(0);
    const row = teamRow(page, "gov-team");
    test.skip((await row.count()) === 0, "the gov matrix team isn't seeded");
    await row.getByRole("button", { name: /^Open / }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("region", { name: "Members" })).toBeVisible();
    for (const name of [/^Add/, /^Remove/, "Save", "Delete team"])
      await expect(dialog.getByRole("button", { name })).toHaveCount(0);
    await expect(
      dialog.getByRole("searchbox", { name: "Add a member" }),
    ).toHaveCount(0);
    await expect(dialog.getByLabel("Name", { exact: true })).toHaveCount(0);
  });

  test("roles are read-only without role.manage", async ({ page }) => {
    await page.goto(ROLES);
    await expect(page.getByRole("table", { name: "Roles" })).toBeVisible();
    await expect(page.getByRole("button", { name: "New role" })).toHaveCount(0);
    const row = roleRow(page, "gov-role");
    test.skip((await row.count()) === 0, "the gov matrix role isn't seeded");
    await row.getByRole("button", { name: /^Open / }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByRole("checkbox")).toHaveCount(0);
    for (const name of ["Save", "Delete role"])
      await expect(dialog.getByRole("button", { name })).toHaveCount(0);
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
