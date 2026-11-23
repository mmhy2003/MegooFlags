import {
  type APIRequestContext,
  expect,
  type Locator,
  type Page,
  request,
  test,
} from "@playwright/test";

import { APP, ok, SHOP } from "./support/api";
import { VIEWER } from "./support/users";

const TOKENS = "/account/tokens";
// Every token this run creates starts with it, so afterAll revokes exactly this run's tokens.
const RUN = `e2e-${Date.now().toString(36)}`;

// As the viewer, so the admin's list stays empty for the visual matrix.
test.use({
  storageState: VIEWER.storageState,
  viewport: { width: 1440, height: 900 },
});

test.afterAll(async () => {
  const api: APIRequestContext = await request.newContext({
    baseURL: APP,
    storageState: VIEWER.storageState,
    extraHTTPHeaders: { Origin: APP },
  });
  const list = await ok(await api.get("/api/account/tokens"), "list tokens");
  const tokens = (await list.json()) as {
    id: string;
    name: string;
    revoked_at: string | null;
  }[];
  for (const token of tokens) {
    if (!token.name.startsWith(RUN) || token.revoked_at !== null) continue;
    await ok(
      await api.delete(`/api/account/tokens/${token.id}`),
      `revoke ${token.name}`,
    );
  }
  await api.dispose();
});

function row(page: Page, name: string): Locator {
  return page.getByRole("row").filter({ hasText: name });
}

/** Opens the New token dialog and names the token. */
async function openForm(page: Page, name: string): Promise<Locator> {
  await page.goto(TOKENS);
  await expect(page.getByRole("heading", { name: "API tokens" })).toBeVisible();
  await page.getByRole("button", { name: "New token" }).first().click();
  const form = page.getByRole("dialog", { name: "New API token" });
  await form.getByLabel("Name", { exact: true }).fill(name);
  return form;
}

/** The permission checkboxes the form offers, by label. */
async function offered(form: Locator): Promise<string[]> {
  const group = form.getByRole("group", { name: "Permissions" });
  await expect(group.getByRole("checkbox").first()).toBeVisible();
  return group
    .getByRole("checkbox")
    .evaluateAll((boxes) =>
      boxes.map((b) => (b as HTMLInputElement).labels?.[0]?.innerText ?? ""),
    );
}

/** Creates a project-scoped token with two permissions and checks the one-time secret. */
async function createScoped(page: Page, name: string): Promise<void> {
  const form = await openForm(page, name);
  await form.getByLabel("Where it works").selectOption("project");
  await form.getByLabel("Project").selectOption(SHOP);
  await expect
    .poll(() => offered(form))
    .toEqual([
      "Read the audit log",
      "See the project",
      "Evaluate flags",
      "See SDK keys",
    ]);
  await expect(form.getByText("Manage members and teams")).toHaveCount(0);
  await expect(form.getByText("Edit targeting")).toHaveCount(0);
  await form.getByLabel("See the project").check();
  await form.getByLabel("Evaluate flags").check();
  await expect(form.getByLabel("Expires")).toHaveValue("90d");
  await form.getByRole("button", { name: "Create token" }).click();

  const reveal = page.getByRole("alertdialog", { name: "API token" });
  await expect(reveal).toBeVisible();
  await expect(
    reveal.getByRole("button", { name: "Copy token" }),
  ).toBeVisible();
  const secret = (await reveal.locator("code").textContent()) ?? "";
  expect(secret.length).toBeGreaterThan(20);
  await reveal.getByRole("button", { name: "Done" }).click();
  await expect(reveal).toBeHidden();
  await expect(page.getByText(secret)).toHaveCount(0);
}

test("create a scoped token", async ({ page }) => {
  const name = `${RUN} laptop`;
  await createScoped(page, name);
  const created = row(page, name);
  await expect(created).toContainText(`Project ${SHOP}`);
  await expect(created).toContainText("See the project");
  await expect(created).toContainText("Evaluate flags");
  await expect(created).toContainText("Expires in 3 months");
});

test("one environment narrows the list", async ({ page }) => {
  const form = await openForm(page, `${RUN} env`);
  await form.getByLabel("Where it works").selectOption("environment");
  await form.getByLabel("Project").selectOption(SHOP);
  await form.getByLabel("Environment").selectOption("production");
  await expect
    .poll(() => offered(form))
    .toEqual(["Evaluate flags", "See SDK keys"]);
});

test("revoke", async ({ page }) => {
  const name = `${RUN} revoke`;
  await createScoped(page, name);
  await row(page, name).getByRole("button", { name: "Revoke" }).click();
  await page
    .getByRole("alertdialog", { name: `Revoke ${name}?` })
    .getByRole("button", { name: "Revoke token" })
    .click();
  await expect(row(page, name)).toHaveCount(0);
  await page.getByLabel("Show revoked and expired").check();
  await expect(row(page, name)).toContainText("Revoked");
});
