import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import { adminApi, createTestFlag, ok, SHOP } from "./support/api";

const LIST = `/projects/${SHOP}/change-requests`;
// Every change request this run opens starts with it, so afterAll cancels exactly those.
const RUN = `e2e-${Date.now().toString(36)}`;

type Opened = { id: string; flag: string; title: string };

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  const list = await ok(
    await api.get(`/api/projects/${SHOP}/change-requests`, {
      params: { status: "open", limit: 200 },
    }),
    "list open change requests",
  );
  const { items } = (await list.json()) as {
    items: { id: string; title: string }[];
  };
  for (const cr of items) {
    if (cr.title.startsWith(RUN))
      await ok(
        await api.post(`/api/projects/${SHOP}/change-requests/${cr.id}/cancel`),
        `cancel ${cr.title}`,
      );
  }
  await api.dispose();
});

/** Opens a change request in production on a new flag. */
async function openRequest(
  api: APIRequestContext,
  kind: "config" | "toggle",
  title: string,
): Promise<Opened> {
  const flag = await createTestFlag(api, { prefix: RUN });
  let payload: object = { enabled: true };
  if (kind === "config") {
    const path = `/api/projects/${SHOP}/flags/${flag}/environments/production`;
    const current = await ok(await api.get(path), `GET ${path}`);
    const { version } = (await current.json()) as { version: number };
    payload = { version, enabled: true, fallthrough: { variation: "true" } };
  }
  const created = await ok(
    await api.post(`/api/projects/${SHOP}/change-requests`, {
      data: { environment: "production", flag, kind, title, payload },
    }),
    `open ${title}`,
  );
  const { id } = (await created.json()) as { id: string };
  return { id, flag, title };
}

/** The table row at desktop widths. */
function row(page: Page, title: string) {
  return page.getByRole("row").filter({ hasText: title });
}

test.describe.serial("change requests", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  let config: Opened;
  let toggle: Opened;

  test("lists open requests and filters them", async ({ page }) => {
    config = await openRequest(api, "config", `${RUN} targeting`);
    toggle = await openRequest(api, "toggle", `${RUN} toggle`);

    await page.goto(LIST);
    await expect(
      page.getByRole("heading", { level: 1, name: "Change requests" }),
    ).toBeVisible();
    await expect(page.getByLabel("Status")).toHaveValue("open");
    for (const cr of [config, toggle]) {
      const r = row(page, cr.title);
      await expect(r).toBeVisible();
      await expect(r.getByText("Open", { exact: true })).toBeVisible();
      await expect(r.getByText("0 of 1 approvals")).toBeVisible();
    }
    await expect(row(page, config.title)).toContainText("Targeting");
    await expect(row(page, toggle.title)).toContainText("On/off");

    await page.getByLabel("Flag key").fill(toggle.flag);
    await expect(row(page, config.title)).toHaveCount(0);
    await expect(row(page, toggle.title)).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`flag=${toggle.flag}`));

    await page.getByLabel("Flag key").fill("");
    await expect(row(page, config.title)).toBeVisible();
    await page.getByLabel("Status").selectOption("cancelled");
    await expect(page).toHaveURL(/status=cancelled/);
    await expect(row(page, config.title)).toHaveCount(0);
    await page.getByLabel("Status").selectOption("all");
    await expect(row(page, config.title)).toBeVisible();
    await expect(row(page, toggle.title)).toBeVisible();

    await page.getByLabel("Environment").selectOption("staging");
    await expect(page).toHaveURL(/env=staging/);
    await expect(
      page.getByRole("heading", { name: "No change requests match" }),
    ).toBeVisible();
    await page.getByLabel("Environment").selectOption("production");
    await expect(row(page, config.title)).toBeVisible();

    await row(page, config.title)
      .getByRole("link", { name: config.title })
      .click();
    await expect(page).toHaveURL(new RegExp(`${LIST}/${config.id}$`));
  });
});
