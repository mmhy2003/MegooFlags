import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import { adminApi, ok, SHOP } from "./support/api";

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  await api.dispose();
});

test("global setup seeds the shop project", async () => {
  const flags = await ok(
    await api.get(`/api/projects/${SHOP}/flags`, { params: { limit: 500 } }),
    "list shop flags",
  );
  const { items } = (await flags.json()) as { items: { key: string }[] };
  const keys = items.map((f) => f.key);
  expect(keys).toContain("checkout-v2");
  expect(keys.filter((k) => k.startsWith("load-"))).toHaveLength(52);

  const production = await ok(
    await api.get(`/api/projects/${SHOP}/environments/production`),
    "read production",
  );
  expect(await production.json()).toMatchObject({ approvals_required: 1 });
});

const LIST = `/projects/${SHOP}/flags`;

/** The flag keys in the list table, in order. */
function listedKeys(page: Page): Promise<string[]> {
  return page
    .getByRole("table", { name: "Flags" })
    .locator("tbody tr td:first-child a")
    .allTextContents();
}

async function openFilters(page: Page) {
  const toggle = page.getByRole("button", { name: /^Filters \(\d+\)$/ });
  if ((await toggle.getAttribute("aria-expanded")) !== "true")
    await toggle.click();
}

test.describe("flag list", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("search narrows the list", async ({ page }) => {
    await page.goto(LIST);
    await page
      .getByRole("searchbox", { name: "Search flags" })
      .fill("checkout");
    await expect.poll(() => listedKeys(page)).toEqual(["checkout-v2"]);
    await expect(page).toHaveURL(/\?q=checkout$/);
  });

  test("tags, owner and lifecycle filter the list", async ({ page }) => {
    await page.goto(LIST);
    await openFilters(page);

    const tags = page.getByRole("textbox", { name: "Tags" });
    await tags.fill("search");
    await tags.press("Enter");
    await expect.poll(() => listedKeys(page)).toEqual(["search-ranking"]);

    await page.getByRole("button", { name: "Clear filters" }).click();
    await expect(page).toHaveURL(/\/flags$/);
    await openFilters(page);
    const owner = page.getByRole("textbox", { name: "Owner" });
    await owner.fill("team-web");
    await owner.press("Enter");
    await expect.poll(() => listedKeys(page)).toEqual(["checkout-v2"]);

    await page.getByRole("button", { name: "Clear filters" }).click();
    await openFilters(page);
    await page
      .getByRole("combobox", { name: "Lifecycle" })
      .selectOption("archived");
    await expect.poll(() => listedKeys(page)).toEqual(["legacy-header"]);
  });

  test("state in an environment filters and survives a reload", async ({
    page,
  }) => {
    await page.goto(LIST);
    await openFilters(page);
    const state = page.getByRole("combobox", { name: "State in environment" });
    await expect(state).toBeDisabled();
    await page
      .getByRole("combobox", { name: "Environment", exact: true })
      .selectOption("staging");
    await state.selectOption("on");
    await expect.poll(() => listedKeys(page)).toEqual(["checkout-v2"]);
    await expect(page).toHaveURL(/\?env=staging&enabled=on$/);
    await expect(
      page.getByRole("button", { name: "Filters (1)" }),
    ).toBeVisible();

    await page.reload();
    await expect.poll(() => listedKeys(page)).toEqual(["checkout-v2"]);
    await openFilters(page);
    await expect(
      page.getByRole("combobox", { name: "Environment", exact: true }),
    ).toHaveValue("staging");
    await expect(
      page.getByRole("combobox", { name: "State in environment" }),
    ).toHaveValue("on");
  });

  test("filters that match nothing offer to clear them", async ({ page }) => {
    await page.goto(`${LIST}?q=zzzz-none`);
    await expect(
      page.getByRole("heading", { name: "No flags match these filters" }),
    ).toBeVisible();
    await page
      .getByRole("main")
      .getByRole("button", { name: "Clear filters" })
      .last()
      .click();
    await expect(page).toHaveURL(/\/flags$/);
    await expect.poll(async () => (await listedKeys(page)).length).toBe(50);
  });

  test("pages through the flags", async ({ page }) => {
    await page.goto(LIST);
    await expect.poll(async () => (await listedKeys(page)).length).toBe(50);
    expect(await listedKeys(page)).toContain("load-01");

    await page.getByRole("button", { name: "Next page" }).click();
    await expect(page).toHaveURL(/\?cursor=/);
    await expect.poll(() => listedKeys(page)).toContain("load-52");
    expect(await listedKeys(page)).not.toContain("load-01");

    await page.getByRole("button", { name: "First page" }).click();
    await expect(page).toHaveURL(/\/flags$/);
    await expect.poll(() => listedKeys(page)).toContain("load-01");
  });

  test("shows each flag's state per environment and links to it", async ({
    page,
  }) => {
    await page.goto(`${LIST}?q=checkout`);
    const table = page.getByRole("table", { name: "Flags" });
    await expect(table.getByText("staging: On")).toBeVisible();
    await expect(table.getByText("production: Off")).toBeVisible();
    await table.getByRole("link", { name: "checkout-v2" }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${SHOP}/flags/checkout-v2$`),
    );
  });

  test("an admin can start a new flag", async ({ page }) => {
    await page.goto(LIST);
    await expect(page.getByRole("link", { name: "New flag" })).toHaveAttribute(
      "href",
      `/projects/${SHOP}/flags/new`,
    );
  });

  test("the demo list has no query string and its 3 flags", async ({
    page,
  }) => {
    await page.goto("/projects/demo/flags");
    await expect
      .poll(() => page.evaluate(() => window.location.href))
      .toMatch(/\/projects\/demo\/flags$/);
    await expect
      .poll(async () => (await listedKeys(page)).sort())
      .toEqual(["dark-mode", "new-checkout", "pricing-copy"]);
    await expect(page).toHaveURL(/\/projects\/demo\/flags$/);
  });
});

test.describe("flag filters on a phone", () => {
  test.use({ viewport: { width: 344, height: 800 } });

  test("open in a sheet and filter the cards", async ({ page }) => {
    await page.goto(LIST);
    await page.getByRole("button", { name: "Filters (0)" }).click();
    const sheet = page.getByRole("dialog", { name: "Filters" });
    await sheet
      .getByRole("combobox", { name: "Lifecycle" })
      .selectOption("archived");
    await expect(page).toHaveURL(/\?lifecycle=archived$/);
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    const cards = page.getByRole("list", { name: "Flags" });
    await expect(
      cards.getByRole("link", { name: "legacy-header" }),
    ).toBeVisible();
    await expect(cards.getByRole("listitem")).toHaveCount(1);
  });
});
