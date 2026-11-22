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

let api: APIRequestContext;
let flag: string;

test.beforeAll(async () => {
  api = await adminApi();
  flag = await createTestFlag(api, { prefix: RUN });
  await ok(
    await api.post(
      `/api/projects/${SHOP}/flags/${flag}/environments/development/toggle`,
      { data: { enabled: true } },
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
