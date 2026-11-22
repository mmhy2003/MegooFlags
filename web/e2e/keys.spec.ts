import {
  type APIRequestContext,
  expect,
  type Locator,
  type Page,
  test,
} from "@playwright/test";

import { adminApi, ok, SHOP } from "./support/api";
import { VIEWER } from "./support/users";

const KEYS = `/projects/${SHOP}/keys`;
const ENV = "staging";
// Every key this run creates starts with it, so afterAll revokes exactly this run's keys.
const RUN = `e2e-${Date.now().toString(36)}`;

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
  // Bound at org scope; created here so only this run's spec ever shows it.
  await ok(
    await api.post("/api/tokens", {
      data: {
        name: `${RUN} org`,
        bindings: [{ role: "viewer", scope: "org" }],
      },
    }),
    "create the org-wide token",
  );
});

test.afterAll(async () => {
  for (const env of ["development", "staging", "production"]) {
    const path = `/api/projects/${SHOP}/environments/${env}/sdk-keys`;
    const list = await ok(await api.get(path), `list ${env} SDK keys`);
    const keys = (await list.json()) as {
      id: string;
      name: string;
      revoked_at: string | null;
    }[];
    for (const key of keys) {
      if (!key.name.startsWith(RUN) || key.revoked_at !== null) continue;
      await ok(await api.delete(`${path}/${key.id}`), `revoke ${key.name}`);
    }
  }
  const tokens = await ok(
    await api.get("/api/tokens", { params: { limit: 500 } }),
    "list service tokens",
  );
  const { items } = (await tokens.json()) as {
    items: { id: string; name: string; revoked_at: string | null }[];
  };
  for (const token of items) {
    if (!token.name.startsWith(RUN) || token.revoked_at !== null) continue;
    await ok(
      await api.delete(`/api/tokens/${token.id}`),
      `revoke ${token.name}`,
    );
  }
  await api.dispose();
});

/** The table rows of one key name at desktop widths; a rotation leaves two. */
function rows(page: Page | Locator, name: string): Locator {
  return page.getByRole("row").filter({ hasText: name });
}

/** The Service tokens section of the page. */
function tokens(page: Page): Locator {
  return page.getByRole("region", { name: "Service tokens" });
}

/** The one-time token dialog: only Done closes it. Returns the secret. */
async function readToken(page: Page): Promise<string> {
  const reveal = page.getByRole("alertdialog", { name: "Service token" });
  await expect(reveal).toBeVisible();
  await expect(
    reveal.getByRole("button", { name: "Copy token" }),
  ).toBeVisible();
  const secret = (await reveal.locator("code").textContent()) ?? "";
  expect(secret.length).toBeGreaterThan(20);
  await reveal.getByRole("button", { name: "Done" }).click();
  await expect(reveal).toBeHidden();
  return secret;
}

/** Creates a key through the dialog, checks the one-time secret, and returns it. */
async function createKey(
  page: Page,
  name: string,
  kind: "Server" | "Client",
  help: RegExp,
): Promise<string> {
  await page.getByRole("button", { name: "New SDK key" }).first().click();
  const form = page.getByRole("dialog", { name: "New SDK key" });
  await form.getByLabel("Name", { exact: true }).fill(name);
  await form.getByRole("radio", { name: kind }).check();
  await expect(form.getByText(help)).toBeVisible();
  await form.getByRole("button", { name: "Create key" }).click();
  return readSecret(page);
}

/** The one-time secret dialog: only Done closes it. Returns the secret. */
async function readSecret(page: Page): Promise<string> {
  const reveal = page.getByRole("alertdialog", { name: "SDK key" });
  await expect(reveal).toBeVisible();
  await expect(reveal.getByRole("button", { name: "Copy key" })).toBeVisible();
  const secret = (await reveal.locator("code").textContent()) ?? "";
  expect(secret.length).toBeGreaterThan(20);
  await page.keyboard.press("Escape");
  await expect(reveal).toBeVisible();
  await reveal.getByRole("button", { name: "Done" }).click();
  await expect(reveal).toBeHidden();
  return secret;
}

test.describe("SDK keys", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("create a server and a client key", async ({ page }) => {
    await page.goto(`${KEYS}?env=${ENV}`);
    await expect(
      page.getByRole("heading", { name: "API & SDK keys" }),
    ).toBeVisible();
    await expect(page.getByRole("tab", { name: ENV })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    for (const [kind, help] of [
      ["Server", /Keep it secret/],
      ["Client", /can ship in an app/],
    ] as const) {
      const name = `${RUN} ${kind.toLowerCase()}`;
      const secret = await createKey(page, name, kind, help);
      const row = rows(page, name);
      await expect(row).toHaveCount(1);
      await expect(row).toContainText("Active");
      await expect(row).toContainText(kind);
      await expect(row).toContainText(secret.slice(0, 14));
      await expect(row).not.toContainText(secret);
    }
  });

  test("rotate with an overlap", async ({ page }) => {
    const name = `${RUN} server`;
    await page.goto(`${KEYS}?env=${ENV}`);
    await rows(page, name).getByRole("button", { name: "Rotate" }).click();
    const dialog = page.getByRole("dialog", { name: `Rotate ${name}` });
    await expect(dialog.getByRole("radio", { name: "24 hours" })).toBeChecked();
    await dialog.getByRole("radio", { name: "1 hour" }).check();
    await dialog.getByRole("button", { name: "Rotate key" }).click();
    await readSecret(page);

    const old = rows(page, name).filter({ hasText: "Expires in" });
    await expect(old).toHaveCount(1);
    await expect(rows(page, name).filter({ hasText: "Active" })).toHaveCount(1);

    await old.getByRole("button", { name: "Revoke now" }).click();
    await expect(rows(page, name)).toHaveCount(1);
    await expect(rows(page, name)).toContainText("Active");

    await page
      .getByRole("region", { name: "SDK keys" })
      .getByLabel("Show revoked and expired")
      .check();
    await expect(rows(page, name)).toHaveCount(2);
    await expect(rows(page, name).filter({ hasText: "Revoked" })).toHaveCount(
      1,
    );
  });

  test("revoke", async ({ page }) => {
    const name = `${RUN} client`;
    await page.goto(`${KEYS}?env=${ENV}`);
    await rows(page, name).getByRole("button", { name: "Revoke" }).click();
    await page
      .getByRole("alertdialog", { name: `Revoke ${name}?` })
      .getByRole("button", { name: "Revoke key" })
      .click();
    await expect(rows(page, name)).toHaveCount(0);
  });

  test("the environment is kept in the URL", async ({ page }) => {
    await page.goto(`${KEYS}?env=${ENV}`);
    await page.getByRole("tab", { name: "production" }).click();
    await expect(page).toHaveURL(/[?&]env=production/);
    await page.reload();
    await expect(page.getByRole("tab", { name: "production" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

test.describe("a viewer", () => {
  test.use({
    storageState: VIEWER.storageState,
    viewport: { width: 1440, height: 900 },
  });

  test("a viewer is read-only", async ({ page }) => {
    // Global setup creates "e2e server" in development.
    await page.goto(`${KEYS}?env=development`);
    await expect(rows(page, "e2e server").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "New SDK key" })).toHaveCount(
      0,
    );
    await expect(page.getByRole("button", { name: "Rotate" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Revoke/ })).toHaveCount(0);
  });
});

test.describe("Service tokens", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("create a service token from the project", async ({ page }) => {
    const name = `${RUN} ci`;
    await page.goto(KEYS);
    const section = tokens(page);
    await section
      .getByRole("button", { name: "New service token" })
      .first()
      .click();
    const form = page.getByRole("dialog", { name: "New service token" });
    await form.getByLabel("Name", { exact: true }).fill(name);
    await expect(form.getByLabel("Expires")).toHaveValue("never");
    const access = form.getByRole("group", { name: "Access 1" });
    await expect(access.getByLabel("Role")).toHaveValue("viewer");
    await expect(access.getByLabel("Level")).toHaveValue("project");
    await expect(access.getByLabel("Project")).toHaveValue(SHOP);
    await expect(access.getByLabel("Project")).toBeDisabled();
    await form.getByRole("button", { name: "Create token" }).click();
    const secret = await readToken(page);

    const row = rows(section, name);
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("Active");
    await expect(row).toContainText(`viewer · Project ${SHOP}`);
    await expect(row).toContainText("Never");
    await expect(row).toContainText(secret.slice(0, 12));
    await expect(row).not.toContainText(secret);
  });

  test("change its access", async ({ page }) => {
    const name = `${RUN} ci`;
    await page.goto(KEYS);
    const row = rows(tokens(page), name);
    await row.getByRole("button", { name: "Access" }).click();
    const dialog = page.getByRole("dialog", { name: `Access of ${name}` });
    const add = dialog.getByRole("form", { name: "Add access" });
    await add.getByLabel("Role").selectOption("editor");
    await add.getByLabel("Level").selectOption("environment");
    await add.getByLabel("Environment").selectOption("development");
    await add.getByRole("button", { name: "Add" }).click();
    await expect(
      dialog.getByText(`editor · ${SHOP} / development`),
    ).toBeVisible();
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(row).toContainText(`viewer · Project ${SHOP}`);
    await expect(row).toContainText(`editor · ${SHOP} / development`);

    await row.getByRole("button", { name: "Access" }).click();
    await dialog
      .getByRole("button", { name: `Remove viewer · Project ${SHOP}` })
      .click();
    await page
      .getByRole("alertdialog", { name: "Remove access?" })
      .getByRole("button", { name: "Remove access" })
      .click();
    await expect(
      dialog.getByText(`viewer · Project ${SHOP}`, { exact: true }),
    ).toHaveCount(0);
    await dialog.getByRole("button", { name: "Close" }).click();
    await expect(row).not.toContainText(`viewer · Project ${SHOP}`);
    await expect(row).toContainText(`editor · ${SHOP} / development`);
  });

  test("rotate and revoke", async ({ page }) => {
    const name = `${RUN} ci`;
    await page.goto(KEYS);
    const section = tokens(page);
    await rows(section, name).getByRole("button", { name: "Rotate" }).click();
    const dialog = page.getByRole("dialog", { name: `Rotate ${name}` });
    await dialog.getByRole("radio", { name: "Revoke the old one now" }).check();
    await dialog.getByRole("button", { name: "Rotate token" }).click();
    await readToken(page);
    await expect(rows(section, name)).toHaveCount(1);
    await expect(rows(section, name)).toContainText("Active");

    await rows(section, name).getByRole("button", { name: "Revoke" }).click();
    await page
      .getByRole("alertdialog", { name: `Revoke ${name}?` })
      .getByRole("button", { name: "Revoke token" })
      .click();
    await expect(rows(section, name)).toHaveCount(0);
  });

  test("org-wide tokens are marked", async ({ page }) => {
    await page.goto(KEYS);
    const row = rows(tokens(page), `${RUN} org`);
    await expect(row).toContainText("viewer · Organization");
    await expect(row.getByText("Org-wide", { exact: true })).toBeVisible();
  });
});

test.describe("a viewer and service tokens", () => {
  test.use({
    storageState: VIEWER.storageState,
    viewport: { width: 1440, height: 900 },
  });

  test("hidden without token.manage", async ({ page }) => {
    await page.goto(`${KEYS}?env=development`);
    await expect(rows(page, "e2e server").first()).toBeVisible();
    await expect(tokens(page)).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Service tokens" }),
    ).toHaveCount(0);
  });
});
