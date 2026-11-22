import { readFileSync } from "node:fs";

import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import { adminApi, createTestFlag, ok, SHOP } from "./support/api";
import { ADMIN } from "./support/users";

const AUDIT = `/projects/${SHOP}/audit`;
// The flag this run creates starts with it, so its entries are this run's own.
const RUN = `e2e-${Date.now().toString(36)}`;
const REQUEST_ID = `${RUN}-toggle`;

let api: APIRequestContext;
let flag: string;

test.beforeAll(async () => {
  api = await adminApi();
  flag = await createTestFlag(api, { prefix: RUN });
  await ok(
    await api.post(
      `/api/projects/${SHOP}/flags/${flag}/environments/development/toggle`,
      // A proxy in front of the API would set it; the entry records it as the request ID.
      { data: { enabled: true }, headers: { "X-Request-ID": REQUEST_ID } },
    ),
    "toggle on in development",
  );
});

test.afterAll(async () => {
  await api.dispose();
});

/** Today as the browser's date inputs write it, in local time. */
function today(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** The table rows of the flag's entries at desktop widths. */
function rows(page: Page) {
  return page.getByRole("row").filter({ hasText: flag });
}

/** One term of an opened entry's details, with its value. */
function detail(page: Page, term: string) {
  return rows(page)
    .locator("dl > div")
    .filter({ has: page.getByRole("term").getByText(term, { exact: true }) });
}

test.describe("audit log", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("filters by action, actor, resource and environment", async ({
    page,
  }) => {
    await page.goto(AUDIT);
    await expect(
      page.getByRole("heading", { name: "Audit log" }),
    ).toBeVisible();

    await page.getByLabel("Action", { exact: true }).fill("flag.create");
    await page.getByLabel("Resource key", { exact: true }).fill(flag);
    await expect(page).toHaveURL(/[?&]action=flag\.create/);
    await expect(page).toHaveURL(new RegExp(`[?&]resource_key=${flag}`));
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page)).toContainText("Flag created");
    await expect(
      rows(page).locator(".font-mono", { hasText: "flag.create" }),
    ).toBeVisible();

    await page.getByLabel("Actor", { exact: true }).fill(ADMIN.email);
    await expect(page).toHaveURL(/[?&]actor=admin%40e2e\.test/);
    await expect(rows(page)).toHaveCount(1);

    await page
      .getByLabel("Resource type", { exact: true })
      .selectOption("flag");
    await page
      .getByLabel("Environment", { exact: true })
      .selectOption("development");
    await page.getByLabel("Action", { exact: true }).fill("flag.toggle");
    await expect(page).toHaveURL(/[?&]action=flag\.toggle/);
    await expect(page).toHaveURL(/[?&]env=development/);
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page)).toContainText("Flag turned on or off");
    await expect(rows(page)).toContainText("development");

    await page.getByLabel("From", { exact: true }).fill(today());
    await page.getByLabel("To", { exact: true }).fill(today());
    await expect(page).toHaveURL(new RegExp(`[?&]to=${today()}`));
    await expect(rows(page)).toHaveCount(1);

    await page.getByLabel("From", { exact: true }).fill("2020-01-01");
    await page.getByLabel("To", { exact: true }).fill("2020-01-02");
    await expect(page).toHaveURL(/[?&]to=2020-01-02/);
    await expect(page.getByText("No entries match")).toBeVisible();
    await expect(rows(page)).toHaveCount(0);
  });

  test("the flag links to its page", async ({ page }) => {
    await page.goto(`${AUDIT}?action=flag.create&resource_key=${flag}`);
    await rows(page).getByRole("link", { name: flag }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${SHOP}/flags/${flag}(\\?|$)`),
    );
  });

  test("details show the diff", async ({ page }) => {
    await page.goto(`${AUDIT}?action=flag.toggle&resource_key=${flag}`);
    await expect(rows(page)).toHaveCount(1);
    await rows(page).getByRole("button", { name: "Details" }).click();
    const diff = rows(page).getByRole("group", { name: "Before and after" });
    await expect(diff.getByRole("region", { name: "Before" })).toContainText(
      '"enabled": false',
    );
    await expect(diff.getByRole("region", { name: "After" })).toContainText(
      '"enabled": true',
    );
    await expect(detail(page, "Request ID")).toContainText(REQUEST_ID);
    await expect(detail(page, "IP address")).toBeVisible();
    await expect(detail(page, "User agent")).toContainText("as recorded");
  });

  // Sign-in entries belong to no project, so the project page can't list them; the admin's
  // own entries carry the method of the session that made them.
  test("an entry made in a session shows its sign-in method", async ({
    page,
  }) => {
    await page.goto(`${AUDIT}?action=flag.toggle&resource_key=${flag}`);
    await rows(page).getByRole("button", { name: "Details" }).click();
    await expect(
      detail(page, "Sign-in method").getByRole("definition"),
    ).toHaveText("password");
  });

  test("exports CSV and NDJSON", async ({ page }) => {
    await page.goto(`${AUDIT}?resource_key=${flag}`);
    await expect(rows(page)).toHaveCount(2);

    const download = async (format: "CSV" | "NDJSON") => {
      await page.getByRole("button", { name: "Export" }).click();
      const dialog = page.getByRole("dialog", { name: "Export audit log" });
      await expect(dialog).toContainText(
        "Exports every entry that matches the current filters.",
      );
      await dialog.getByRole("radio", { name: format }).check();
      const [file] = await Promise.all([
        page.waitForEvent("download"),
        dialog.getByRole("link", { name: "Download" }).click(),
      ]);
      await page.keyboard.press("Escape");
      await expect(dialog).toBeHidden();
      return file;
    };

    const csv = await download("CSV");
    expect(csv.suggestedFilename()).toMatch(/^audit-.*\.csv$/);
    const csvText = readFileSync(await csv.path(), "utf8");
    expect(csvText).toContain(flag);

    const ndjson = await download("NDJSON");
    expect(ndjson.suggestedFilename()).toMatch(/^audit-.*\.ndjson$/);
    const lines = readFileSync(await ndjson.path(), "utf8")
      .split("\n")
      .filter(Boolean);
    expect(lines).toHaveLength(2);
    for (const line of lines)
      expect(JSON.parse(line)).toMatchObject({ resource_key: flag });
  });
});

test("cards under 768 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`${AUDIT}?resource_key=${flag}`);
  const cards = page.getByRole("list", { name: "Audit log" });
  await expect(cards).toBeVisible();
  await expect(cards.getByRole("listitem")).toHaveCount(2);
  await expect(page.getByRole("table")).toBeHidden();

  await page.getByRole("button", { name: "Filters (1)" }).click();
  const sheet = page.getByRole("dialog", { name: "Filters" });
  await expect(sheet).toBeVisible();
  await sheet.getByLabel("Action", { exact: true }).fill("flag.toggle");
  await expect(page).toHaveURL(/[?&]action=flag\.toggle/);
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(cards.getByRole("listitem")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Filters (2)" })).toBeVisible();

  await page.setViewportSize({ width: 1024, height: 800 });
  await expect(page.getByRole("table")).toBeVisible();
  await expect(cards).toBeHidden();
  await expect(page.getByRole("button", { name: /^Filters/ })).toBeHidden();
});

test("the organization tab shows org entries", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/settings?tab=audit&action=auth.sign_in");
  const table = page.getByRole("table", { name: "Audit log" });
  await expect(
    table.getByRole("columnheader", { name: "Project" }),
  ).toBeVisible();
  const body = table.locator("tbody tr");
  await expect(body.first()).toContainText("Signed in");
  for (const row of await body.all())
    await expect(row).toContainText("auth.sign_in");

  // Sign-in entries belong to no project, so the project filter starts from every action.
  await page.getByLabel("Action", { exact: true }).fill("");
  await page.getByLabel("Project", { exact: true }).selectOption(SHOP);
  await expect(page).toHaveURL(new RegExp(`[?&]project=${SHOP}(&|$)`));
  await expect(page).not.toHaveURL(/[?&]action=/);
  await expect(page).toHaveURL(/[?&]tab=audit(&|$)/);
  // The project column is the fifth: time, action, actor, resource, project.
  const projects = body.locator("td:nth-child(5)");
  await expect(projects.first()).toHaveText(SHOP);
  for (const cell of await projects.all()) await expect(cell).toHaveText(SHOP);
});
