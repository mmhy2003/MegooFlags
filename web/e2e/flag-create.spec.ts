import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import { adminApi, ok, SHOP } from "./support/api";

const LIST = `/projects/${SHOP}/flags`;
const NEW = `${LIST}/new`;

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  await api.dispose();
});

type Flag = {
  key: string;
  tags: string[];
  off_variation: string;
  variations: { key: string; value: unknown }[];
};

async function readFlag(key: string): Promise<Flag> {
  const response = await ok(
    await api.get(`/api/projects/${SHOP}/flags/${key}`),
    `read flag ${key}`,
  );
  return (await response.json()) as Flag;
}

function rand(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

function field(page: Page, name: string) {
  return page.getByRole("textbox", { name, exact: true });
}

test.describe("new flag on a desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("a boolean flag gets a key from its name", async ({ page }) => {
    const id = rand();
    await page.goto(NEW);
    await expect(page.getByRole("heading", { name: "New flag" })).toBeVisible();
    await expect(
      page.getByText("Boolean flags serve true or false."),
    ).toBeVisible();

    await field(page, "Name").fill(`Promo banner ${id}`);
    await expect(field(page, "Key")).toHaveValue(`promo-banner-${id}`);
    await field(page, "Tags").fill("web, promo");
    await page.getByRole("button", { name: "Create flag" }).click();

    await expect(page).toHaveURL(new RegExp(`/flags/promo-banner-${id}$`));
    await expect(page.getByText("Flag created")).toBeVisible();
    const flag = await readFlag(`promo-banner-${id}`);
    expect(flag.variations.map((v) => v.value)).toEqual([true, false]);
    expect(flag.tags).toEqual(["web", "promo"]);
  });

  test("a string flag keeps its variations and off variation", async ({
    page,
  }) => {
    const key = `greeting-${rand()}`;
    await page.goto(NEW);
    await field(page, "Name").fill("Greeting");
    await field(page, "Key").fill(key);
    await page.getByRole("combobox", { name: "Type" }).selectOption("string");

    await field(page, "Variation 1 key").fill("control");
    await field(page, "Variation 1 value").fill("Hi");
    await field(page, "Variation 2 key").fill("short");
    await field(page, "Variation 2 value").fill("Yo");
    await page
      .getByRole("combobox", { name: "Off variation" })
      .selectOption("control");
    await page.getByRole("button", { name: "Create flag" }).click();

    await expect(page).toHaveURL(new RegExp(`/flags/${key}$`));
    const flag = await readFlag(key);
    expect(flag.off_variation).toBe("control");
    expect(flag.variations.map((v) => [v.key, v.value])).toEqual([
      ["control", "Hi"],
      ["short", "Yo"],
    ]);
  });

  test("a JSON value must be valid JSON", async ({ page }) => {
    await page.goto(NEW);
    await field(page, "Name").fill(`Layout ${rand()}`);
    await page.getByRole("combobox", { name: "Type" }).selectOption("json");
    await field(page, "Variation 1 key").fill("a");
    await field(page, "Variation 1 value").fill("{");
    await page.getByRole("button", { name: "Create flag" }).click();

    const value = field(page, "Variation 1 value");
    await expect(value).toHaveAttribute("aria-invalid", "true");
    await expect(value).toHaveAccessibleDescription("Enter valid JSON");
    await expect(page).toHaveURL(new RegExp(`${NEW}$`));
  });

  test("variations can be added and removed", async ({ page }) => {
    await page.goto(NEW);
    await page.getByRole("combobox", { name: "Type" }).selectOption("number");
    await page.getByRole("button", { name: "Add variation" }).click();
    await expect(field(page, "Variation 3 value")).toHaveAttribute(
      "inputmode",
      "decimal",
    );
    await page.getByRole("button", { name: "Remove variation 3" }).click();
    await expect(field(page, "Variation 3 value")).toHaveCount(0);
  });

  test("a taken key shows an error on Key", async ({ page }) => {
    await page.goto(NEW);
    await field(page, "Name").fill("Checkout again");
    await field(page, "Key").fill("checkout-v2");
    await page.getByRole("button", { name: "Create flag" }).click();

    const key = field(page, "Key");
    await expect(key).toHaveAttribute("aria-invalid", "true");
    await expect(key).toHaveAccessibleDescription(
      "A flag with this key already exists",
    );
    await expect(page).toHaveURL(new RegExp(`${NEW}$`));
  });

  test("New flag on the list leads here", async ({ page }) => {
    await page.goto(LIST);
    await page.getByRole("link", { name: "New flag" }).click();
    await expect(page).toHaveURL(new RegExp(`${NEW}$`));
    await expect(page.getByRole("heading", { name: "New flag" })).toBeVisible();
  });
});

test.describe("new flag on a phone", () => {
  test.use({ viewport: { width: 344, height: 800 } });

  test("the stepper checks each step and creates the flag", async ({
    page,
  }) => {
    const id = rand();
    await page.goto(NEW);
    await expect(page.getByText("Step 1 of 3")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Create flag" }),
    ).toBeHidden();

    await page.getByRole("button", { name: "Next" }).click();
    await expect(field(page, "Name")).toHaveAccessibleDescription(
      "Enter a name",
    );
    await expect(page.getByText("Step 1 of 3")).toBeVisible();

    await field(page, "Name").fill(`Phone ${id}`);
    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByText("Step 2 of 3")).toBeVisible();
    await expect(field(page, "Name")).toBeHidden();
    await expect(
      page.getByText("Boolean flags serve true or false."),
    ).toBeVisible();

    await page.getByRole("button", { name: "Next" }).click();
    await expect(page.getByText("Step 3 of 3")).toBeVisible();
    await page.getByRole("button", { name: "Create flag" }).click();
    await expect(page).toHaveURL(new RegExp(`/flags/phone-${id}$`));
  });

  test("a fold keeps the values", async ({ page }) => {
    await page.goto(NEW);
    await field(page, "Name").fill("Folded flag");
    await field(page, "Description").fill("Kept across sizes");

    await page.setViewportSize({ width: 1024, height: 800 });
    await expect(page.getByText("Step 1 of 3")).toBeHidden();
    await expect(page.getByRole("button", { name: "Next" })).toBeHidden();
    await expect(field(page, "Name")).toBeVisible();
    await expect(
      page.getByText("Boolean flags serve true or false."),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Create flag" }),
    ).toBeVisible();

    await page.setViewportSize({ width: 344, height: 800 });
    await expect(page.getByText("Step 1 of 3")).toBeVisible();
    await expect(field(page, "Name")).toHaveValue("Folded flag");
    await expect(field(page, "Key")).toHaveValue("folded-flag");
    await expect(field(page, "Description")).toHaveValue("Kept across sizes");
  });
});
