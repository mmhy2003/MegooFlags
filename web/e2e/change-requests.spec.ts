import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import { adminApi, createTestFlag, ok, SHOP } from "./support/api";
import { noHorizontalScroll, smallTargets } from "./support/checks";
import { ADMIN, REVIEWER, VIEWER } from "./support/users";

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

/** Opens a change request in production, on `existing` or a new flag. */
async function openRequest(
  api: APIRequestContext,
  kind: "config" | "toggle",
  title: string,
  existing?: string,
): Promise<Opened> {
  const flag = existing ?? (await createTestFlag(api, { prefix: RUN }));
  let payload: object = { enabled: true };
  if (kind === "config") {
    const path = `/api/projects/${SHOP}/flags/${flag}/environments/production`;
    const current = await ok(await api.get(path), `GET ${path}`);
    const { version } = (await current.json()) as { version: number };
    // A new flag serves its first variation, "true", so this is a real change.
    payload = { version, enabled: true, fallthrough: { variation: "false" } };
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

  test("shows the diff of a targeting request", async ({ page }) => {
    await page.goto(`${LIST}/${config.id}`);
    await expect(
      page.getByRole("heading", { level: 1, name: config.title }),
    ).toBeVisible();
    await expect(page.getByText("0 of 1 approvals")).toBeVisible();
    const diff = page.getByRole("group", { name: "Proposed change" });
    const after = diff.getByRole("region", { name: "After" });
    await expect(after.locator('[data-kind="add"]')).toContainText(
      '"variation": "false"',
    );
    await expect(page.getByRole("link", { name: config.flag })).toHaveAttribute(
      "href",
      `/projects/${SHOP}/flags/${config.flag}?env=production`,
    );
  });

  test("summarises a toggle request", async ({ page }) => {
    await page.goto(`${LIST}/${toggle.id}`);
    await expect(page.getByText("Turn the flag on")).toBeVisible();
    await expect(page.getByText("Currently off")).toBeVisible();
    await expect(
      page.getByRole("group", { name: "Proposed change" }),
    ).toHaveCount(0);
  });

  test("comments", async ({ page }) => {
    const body = `${RUN} looks good to me`;
    await page.goto(`${LIST}/${toggle.id}`);
    await page.getByLabel("Comment", { exact: true }).fill(body);
    await page.getByRole("button", { name: "Comment" }).click();
    const comments = page.getByRole("list", { name: "Comments" });
    const item = comments.getByRole("listitem").filter({ hasText: body });
    await expect(item).toContainText(ADMIN.email);
    await expect(item.locator("time")).toBeVisible();
    await expect(page.getByLabel("Comment", { exact: true })).toHaveValue("");
  });

  test.describe("as a viewer", () => {
    test.use({ storageState: VIEWER.storageState });

    test("there is no comment box", async ({ page }) => {
      await page.goto(`${LIST}/${toggle.id}`);
      await expect(
        page.getByRole("list", { name: "Comments" }).getByRole("listitem"),
      ).toContainText(`${RUN} looks good to me`);
      await expect(page.getByLabel("Comment", { exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Comment" })).toHaveCount(
        0,
      );
    });
  });

  test("unknown request", async ({ page }) => {
    await page.goto(`${LIST}/00000000-0000-0000-0000-000000000000`);
    await expect(
      page.getByRole("heading", { level: 1, name: "Page not found" }),
    ).toBeVisible();
  });
});

/** The fallthrough of `flag` in production, read through the API. */
async function fallthrough(flag: string): Promise<unknown> {
  const path = `/api/projects/${SHOP}/flags/${flag}/environments/production`;
  const response = await ok(await api.get(path), `GET ${path}`);
  return ((await response.json()) as { fallthrough: unknown }).fallthrough;
}

/** The request's status pill in the page header. */
function status(page: Page, label: string) {
  return page.locator("header").getByText(label, { exact: true });
}

/** The sticky bar with Approve, Reject and Cancel request. */
function actions(page: Page) {
  return page.getByRole("region", { name: "Review actions" });
}

async function approve(page: Page, comment?: string) {
  await actions(page).getByRole("button", { name: "Approve" }).click();
  const dialog = page.getByRole("dialog", { name: "Approve change request" });
  if (comment) await dialog.getByLabel("Comment").fill(comment);
  await dialog.getByRole("button", { name: "Approve" }).click();
}

test.describe("reviewing", () => {
  test.use({
    storageState: REVIEWER.storageState,
    viewport: { width: 1440, height: 900 },
  });

  test("approve applies the change", async ({ page }) => {
    const cr = await openRequest(api, "config", `${RUN} approve`);
    await page.goto(`${LIST}/${cr.id}`);
    await actions(page).getByRole("button", { name: "Approve" }).click();
    const dialog = page.getByRole("dialog", { name: "Approve change request" });
    await expect(dialog).toContainText(
      "This is the last approval needed, so the change is applied at once.",
    );
    await dialog.getByLabel("Comment").fill(`${RUN} ship it`);
    await dialog.getByRole("button", { name: "Approve" }).click();

    await expect(status(page, "Applied")).toBeVisible();
    await expect(page.getByText("Change applied")).toBeVisible();
    await expect(
      page.getByRole("list", { name: "Reviews" }).getByRole("listitem"),
    ).toContainText(`${RUN} ship it`);
    await expect(actions(page)).toHaveCount(0);
    expect(await fallthrough(cr.flag)).toMatchObject({ variation: "false" });
  });

  test("reject needs a reason", async ({ page }) => {
    const cr = await openRequest(api, "toggle", `${RUN} reject`);
    await page.goto(`${LIST}/${cr.id}`);
    await actions(page).getByRole("button", { name: "Reject" }).click();
    const dialog = page.getByRole("dialog", { name: "Reject change request" });
    const submit = dialog.getByRole("button", { name: "Reject" });
    await expect(submit).toBeDisabled();
    await dialog.getByLabel("Reason").fill("   ");
    await expect(submit).toBeDisabled();
    await dialog.getByLabel("Reason").fill(`${RUN} not now`);
    await submit.click();

    await expect(status(page, "Rejected")).toBeVisible();
    await expect(page.getByText("Change request rejected")).toBeVisible();
    await expect(actions(page)).toHaveCount(0);
  });

  test("a stale request fails", async ({ page }) => {
    const first = await openRequest(api, "config", `${RUN} stale one`);
    const second = await openRequest(
      api,
      "config",
      `${RUN} stale two`,
      first.flag,
    );
    await page.goto(`${LIST}/${first.id}`);
    await approve(page);
    await expect(status(page, "Applied")).toBeVisible();

    await page.goto(`${LIST}/${second.id}`);
    await approve(page);
    await expect(status(page, "Failed")).toBeVisible();
    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: "The change couldn't be applied" }),
    ).toContainText("the flag changed since this request was made");
  });

  test("someone else closed it", async ({ page }) => {
    const cr = await openRequest(api, "toggle", `${RUN} closed`);
    await page.goto(`${LIST}/${cr.id}`);
    await expect(
      actions(page).getByRole("button", { name: "Approve" }),
    ).toBeVisible();
    await ok(
      await api.post(`/api/projects/${SHOP}/change-requests/${cr.id}/cancel`),
      "cancel through the API",
    );
    await approve(page);

    await expect(
      page.getByText("Someone else closed this request"),
    ).toBeVisible();
    await expect(status(page, "Cancelled")).toBeVisible();
    await expect(actions(page)).toHaveCount(0);
  });

  test("approve and reject at 344 px", async ({ page }) => {
    await page.setViewportSize({ width: 344, height: 740 });
    const cr = await openRequest(api, "config", `${RUN} narrow`);
    await page.goto(`${LIST}/${cr.id}`);
    for (const name of ["Approve", "Reject"])
      await expect(
        actions(page).getByRole("button", { name }),
      ).toBeInViewport();
    expect(await smallTargets(page)).toEqual([]);
    expect(await noHorizontalScroll(page)).toBe(true);

    await approve(page);
    await expect(status(page, "Applied")).toBeVisible();
  });
});

test.describe("the author", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("cancels the request", async ({ page }) => {
    const cr = await openRequest(api, "toggle", `${RUN} cancel`);
    await page.goto(`${LIST}/${cr.id}`);
    const bar = actions(page);
    await expect(
      bar.getByRole("button", { name: "Cancel request" }),
    ).toBeVisible();
    await expect(bar.getByRole("button", { name: "Approve" })).toHaveCount(0);
    await expect(bar.getByRole("button", { name: "Reject" })).toHaveCount(0);

    await bar.getByRole("button", { name: "Cancel request" }).click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Cancel request" })
      .click();
    await expect(status(page, "Cancelled")).toBeVisible();
    await expect(page.getByText("Change request cancelled")).toBeVisible();
    await expect(actions(page)).toHaveCount(0);
  });
});

test.describe("a viewer", () => {
  test.use({ storageState: VIEWER.storageState });

  test("sees no actions", async ({ page }) => {
    const cr = await openRequest(api, "toggle", `${RUN} viewer`);
    await page.goto(`${LIST}/${cr.id}`);
    await expect(
      page.getByRole("heading", { level: 1, name: cr.title }),
    ).toBeVisible();
    for (const name of ["Approve", "Reject", "Cancel request"])
      await expect(page.getByRole("button", { name })).toHaveCount(0);
  });
});
