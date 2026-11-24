import {
  type APIRequestContext,
  expect,
  type Locator,
  type Page,
  test,
} from "@playwright/test";

import { slugifyKey } from "../lib/flags/flag-form";
import { segmentPath } from "../lib/routes";

import { noHorizontalScroll } from "./support/checks";
import { adminApi, createTestFlag, ok, putConfig, SHOP } from "./support/api";
import { VIEWER } from "./support/users";

const LIST = `/projects/${SHOP}/segments`;
// Every segment this run creates starts with it, so afterAll deletes exactly this run's segments.
const RUN = `e2e-${Date.now().toString(36)}`;
const ENV = "development";

let api: APIRequestContext;
// Flags whose rules use a RUN segment; afterAll removes those rules before the delete.
const usingFlags: string[] = [];

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  for (const flag of usingFlags)
    await putConfig(api, flag, ENV, (config) => {
      config.rules = [];
    });
  const list = await ok(
    await api.get(`/api/projects/${SHOP}/segments`, {
      params: { q: RUN, limit: 500 },
    }),
    "list this run's segments",
  );
  const { items } = (await list.json()) as { items: { key: string }[] };
  for (const { key } of items) {
    if (!key.startsWith(RUN)) continue;
    await ok(
      await api.delete(`/api/projects/${SHOP}/segments/${key}`),
      `delete segment ${key}`,
    );
  }
  await api.dispose();
});

async function createSegment(key: string, name = key): Promise<void> {
  await ok(
    await api.post(`/api/projects/${SHOP}/segments`, {
      data: { key, name },
    }),
    `create segment ${key}`,
  );
}

/** The table row of one segment at desktop widths. */
function row(page: Page, key: string): Locator {
  return page.getByRole("row").filter({ hasText: key });
}

test("create a segment", async ({ page }) => {
  const name = `${RUN} Beta`;
  const key = slugifyKey(name);
  await page.goto(LIST);
  await page.getByRole("button", { name: "New segment" }).click();
  const dialog = page.getByRole("dialog", { name: "New segment" });
  await dialog.getByLabel("Name").fill(name);
  await expect(dialog.getByLabel("Key")).toHaveValue(key);
  await dialog.getByLabel("Description").fill("People who try things first");
  await dialog.getByRole("button", { name: "Create segment" }).click();

  await expect(page).toHaveURL(segmentPath(SHOP, key));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
});

test("a taken key shows on the Key field", async ({ page }) => {
  const key = `${RUN}-taken`;
  await createSegment(key);
  await page.goto(LIST);
  await page.getByRole("button", { name: "New segment" }).click();
  const dialog = page.getByRole("dialog", { name: "New segment" });
  await dialog.getByLabel("Name").fill("Taken");
  await dialog.getByLabel("Key").fill(key);
  await dialog.getByRole("button", { name: "Create segment" }).click();
  await expect(dialog.getByLabel("Key")).toHaveAttribute(
    "aria-invalid",
    "true",
  );
  await expect(dialog).toContainText("Another segment already uses this key");
});

test("search", async ({ page }) => {
  await createSegment(`${RUN}-alpha`);
  await createSegment(`${RUN}-omega`);
  await page.goto(`${LIST}?q=${RUN}`);
  await expect(row(page, `${RUN}-alpha`)).toBeVisible();
  await expect(row(page, `${RUN}-omega`)).toBeVisible();

  await page.getByLabel("Search segments").fill(`${RUN}-al`);
  await expect(page).toHaveURL(/[?&]q=/);
  await expect(row(page, `${RUN}-omega`)).toHaveCount(0);
  await expect(row(page, `${RUN}-alpha`)).toBeVisible();

  await page.getByLabel("Search segments").fill(`${RUN}-nothing`);
  await expect(page.getByText("No segments match")).toBeVisible();
});

test("used by in the list", async ({ page }) => {
  const key = `${RUN}-used`;
  await createSegment(key);
  await page.goto(`${LIST}?q=${key}`);
  await expect(row(page, key)).toContainText("Not used");

  const flag = await createTestFlag(api);
  await putConfig(api, flag, ENV, (config) => {
    config.rules = [
      {
        description: "",
        clauses: [
          {
            attribute: "key",
            operator: "in_segment",
            values: [key],
            negate: false,
          },
        ],
        variation: "true",
      },
    ];
  });
  usingFlags.push(flag);
  await page.reload();
  await expect(row(page, key)).toContainText("1 flag");
});

test("cards under 768 px", async ({ page }) => {
  await createSegment(`${RUN}-card`);
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto(`${LIST}?q=${RUN}`);
  const list = page.getByRole("list", { name: "Segments" });
  await expect(list).toBeVisible();
  await expect(list.getByRole("article").first()).toBeVisible();
  await expect(page.getByRole("table")).toBeHidden();
  expect(await noHorizontalScroll(page)).toBe(true);
});

test.describe("as a viewer", () => {
  test.use({ storageState: VIEWER.storageState });

  test("cannot create", async ({ page }) => {
    await page.goto(LIST);
    await expect(
      page.getByRole("heading", { level: 1, name: "Segments" }),
    ).toBeVisible();
    await expect(row(page, "beta-testers")).toBeVisible();
    await expect(page.getByRole("button", { name: "New segment" })).toHaveCount(
      0,
    );
  });
});
