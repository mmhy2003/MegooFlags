import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import {
  adminApi,
  createTestFlag,
  type FlagConfig,
  ok,
  SHOP,
} from "./support/api";
import { noHorizontalScroll, smallTargets } from "./support/checks";

// UI-15: the main mobile tasks for flags work fully at 344 px.

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  await api.dispose();
});

const LIST = `/projects/${SHOP}/flags`;

function detail(flag: string, query = ""): string {
  return `${LIST}/${flag}${query}`;
}

async function configIn(flag: string, env: string): Promise<FlagConfig> {
  const response = await ok(
    await api.get(`/api/projects/${SHOP}/flags/${flag}/environments/${env}`),
    `read ${flag} in ${env}`,
  );
  return (await response.json()) as FlagConfig;
}

function card(page: Page, env: string) {
  return page
    .getByRole("region", { name: "Environments" })
    .getByRole("group", { name: env, exact: true });
}

function unsaved(page: Page) {
  return page.getByRole("status", { name: "Unsaved changes" });
}

async function fits(page: Page) {
  expect(await noHorizontalScroll(page)).toBe(true);
  expect(await smallTargets(page)).toEqual([]);
}

test.describe("flags at 344 px", () => {
  test.use({ viewport: { width: 344, height: 800 } });

  test("search shows exactly the flag's card", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(LIST);
    await page.getByRole("searchbox", { name: "Search flags" }).fill(flag);
    const cards = page.getByRole("list", { name: "Flags" });
    await expect(cards.getByRole("listitem")).toHaveCount(1);
    await expect(cards.getByRole("link", { name: flag })).toBeVisible();
  });

  test("Filters opens a bottom sheet and Lifecycle filters", async ({
    page,
  }) => {
    const flag = await createTestFlag(api);
    await ok(
      await api.post(`/api/projects/${SHOP}/flags/${flag}/archive`),
      `archive ${flag}`,
    );
    await page.goto(`${LIST}?q=${flag}`);
    await expect(page.getByText("No flags match these filters")).toBeVisible();
    await page.getByRole("button", { name: "Filters (0)" }).click();
    const sheet = page.getByRole("dialog", { name: "Filters" });
    await expect(sheet).toBeVisible();
    // A bottom sheet: it ends at the viewport's bottom once it has slid in.
    await expect
      .poll(async () => {
        const box = await sheet.boundingBox();
        return box && Math.round(box.y + box.height);
      })
      .toBe(800);
    await sheet
      .getByRole("combobox", { name: "Lifecycle" })
      .selectOption("archived");
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    await expect(page).toHaveURL(/[?&]lifecycle=archived/);
    const cards = page.getByRole("list", { name: "Flags" });
    await expect(cards.getByRole("link", { name: flag })).toBeVisible();
    await expect(cards.getByRole("listitem")).toHaveCount(1);
  });

  test("toggle turns Staging on", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const toggle = page.getByRole("switch", { name: `${flag} in Staging` });
    await toggle.click();
    await expect(toggle).toBeChecked();
    await expect(card(page, "Staging")).toContainText("On");
    expect((await configIn(flag, "staging")).enabled).toBe(true);
  });

  test("the kill switch turns Staging off", async ({ page }) => {
    const flag = await createTestFlag(api);
    await ok(
      await api.post(
        `/api/projects/${SHOP}/flags/${flag}/environments/staging/toggle`,
        { data: { enabled: true } },
      ),
      "toggle on",
    );
    await page.goto(detail(flag));
    await page.getByRole("button", { name: "Kill switch in Staging" }).click();
    const dialog = page.getByRole("alertdialog", {
      name: `Kill ${flag} in Staging?`,
    });
    // Full screen at 344 px, once it has animated in.
    await expect
      .poll(async () => Math.round((await dialog.boundingBox())?.width ?? 0))
      .toBe(344);
    await dialog.getByRole("button", { name: "Kill switch" }).click();
    await expect(dialog).toBeHidden();
    await expect(
      page.getByRole("switch", { name: `${flag} in Staging` }),
    ).not.toBeChecked();
    await expect(card(page, "Staging")).toContainText("Off");
    expect((await configIn(flag, "staging")).enabled).toBe(false);
  });

  test("a 40 % rollout is reviewed and saved", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag, "?env=development&tab=targeting"));
    const fallthrough = page.getByRole("region", { name: "Default rule" });
    await fallthrough.getByRole("radio", { name: "Serve a rollout" }).check();
    const percent = fallthrough.getByRole("spinbutton", {
      name: "Rollout percentage",
    });
    await percent.fill("40");
    await percent.press("Enter");
    await unsaved(page).getByRole("button", { name: "Review" }).click();
    const dialog = page.getByRole("dialog", { name: "Review changes" });
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toBeHidden();

    expect((await configIn(flag, "development")).fallthrough).toEqual({
      variation: null,
      rollout: {
        bucket_by: "key",
        weights: [
          { variation: "true", weight: 40000 },
          { variation: "false", weight: 60000 },
        ],
      },
    });
  });

  test("the list fits", async ({ page }) => {
    await createTestFlag(api);
    await page.goto(LIST);
    await expect(page.getByRole("list", { name: "Flags" })).toBeVisible();
    await fits(page);
  });

  for (const tab of ["targeting", "test", "settings"]) {
    test(`the ${tab} tab fits`, async ({ page }) => {
      const flag = await createTestFlag(api);
      await page.goto(detail(flag, `?tab=${tab}`));
      await expect(page.getByRole("tabpanel")).toBeVisible();
      await expect(page.getByRole("status")).toHaveCount(0);
      await fits(page);
    });
  }

  test("New flag fits", async ({ page }) => {
    await page.goto(`${LIST}/new`);
    await expect(page.getByText("Step 1 of 3")).toBeVisible();
    await fits(page);
  });

  test("unfolding keeps the review open with the same diff", async ({
    page,
  }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const targets = page.getByRole("textbox", {
      name: "Context keys served true",
    });
    await targets.fill("user-4");
    await targets.blur();
    await unsaved(page).getByRole("button", { name: "Review" }).click();
    const dialog = page.getByRole("dialog", { name: "Review changes" });
    const after = dialog.getByRole("region", { name: "After" });
    await expect(after).toContainText("user-4");
    const before = (await after.textContent()) ?? "";

    await page.setViewportSize({ width: 880, height: 800 });
    await expect(dialog).toBeVisible();
    await expect(after).toHaveText(before);
    await expect(dialog.getByRole("button", { name: "Save" })).toBeVisible();
  });
});
