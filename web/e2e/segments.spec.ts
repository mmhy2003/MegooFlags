import {
  type APIRequestContext,
  expect,
  type Locator,
  type Page,
  test,
} from "@playwright/test";

import { slugifyKey } from "../lib/flags/flag-form";
import { flagPath, segmentPath } from "../lib/routes";

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

/** Creates a test flag whose rule in ENV uses the segment, and returns its key. */
async function createUsingFlag(segment: string): Promise<string> {
  const flag = await createTestFlag(api);
  await putConfig(api, flag, ENV, (config) => {
    config.rules = [
      {
        description: "",
        clauses: [
          {
            attribute: "key",
            operator: "in_segment",
            values: [segment],
            negate: false,
          },
        ],
        variation: "true",
      },
    ];
  });
  usingFlags.push(flag);
  return flag;
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

  await createUsingFlag(key);
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

test.describe("a segment's page", () => {
  function unsaved(page: Page): Locator {
    return page.getByRole("status", { name: "Unsaved changes" });
  }

  function section(page: Page, name: string): Locator {
    return page.getByRole("region", { name });
  }

  /** Creates a RUN segment through the API and opens its page. */
  async function open(page: Page, suffix: string): Promise<string> {
    const key = `${RUN}-${suffix}`;
    await createSegment(key, `${RUN} ${suffix}`);
    await page.goto(segmentPath(SHOP, key));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      `${RUN} ${suffix}`,
    );
    return key;
  }

  test("edit keys and rules", async ({ page }) => {
    await open(page, "edit");
    const included = page.getByRole("textbox", { name: "Included keys" });
    await included.fill("u1\nu2");
    await included.blur();
    await expect(section(page, "Keys")).toContainText("2 keys");
    await expect(unsaved(page)).toBeVisible();

    const rules = section(page, "Rules");
    await rules.getByRole("button", { name: "Add rule" }).click();
    await rules.getByRole("textbox", { name: "Attribute" }).fill("plan");
    const operator = rules.getByRole("combobox", { name: "Operator" });
    await expect(operator.locator("option")).not.toContainText([
      "is in segment",
    ]);
    const values = rules.getByRole("textbox", { name: "Values" });
    await values.fill("pro");
    await values.blur();

    await unsaved(page).getByRole("button", { name: "Save" }).click();
    await expect(unsaved(page)).toHaveCount(0);

    await page.reload();
    await expect(
      page.getByRole("textbox", { name: "Included keys" }),
    ).toHaveValue("u1\nu2");
    await expect(rules.getByRole("textbox", { name: "Attribute" })).toHaveValue(
      "plan",
    );
    await expect(rules.getByRole("textbox", { name: "Values" })).toHaveValue(
      "pro",
    );
  });

  test("a key in both lists", async ({ page }) => {
    const key = await open(page, "both");
    let sent = 0;
    page.on("request", (request) => {
      if (
        request.method() === "PUT" &&
        request.url().endsWith(`/segments/${key}`)
      )
        sent += 1;
    });
    const included = page.getByRole("textbox", { name: "Included keys" });
    await included.fill("u1");
    await included.blur();
    const excluded = page.getByRole("textbox", { name: "Excluded keys" });
    await excluded.fill("u1");
    await excluded.blur();

    await unsaved(page).getByRole("button", { name: "Save" }).click();
    await expect(excluded).toHaveAttribute("aria-invalid", "true");
    await expect(excluded).toHaveAccessibleDescription(
      /u1 is both included and excluded/,
    );
    await expect(unsaved(page)).toBeVisible();
    expect(sent).toBe(0);
  });

  test("leaving with unsaved changes asks first", async ({ page }) => {
    const key = await open(page, "guard");
    const included = page.getByRole("textbox", { name: "Included keys" });
    await included.fill("u1");
    await included.blur();
    await expect(unsaved(page)).toBeVisible();

    let message = "";
    page.once("dialog", (dialog) => {
      message = dialog.message();
      void dialog.dismiss();
    });
    await page
      .getByRole("navigation", { name: "Breadcrumb" })
      .getByRole("link", { name: "Segments" })
      .click();
    await expect.poll(() => message).toBe("Leave without saving your changes?");
    await expect(page).toHaveURL(segmentPath(SHOP, key));
    await expect(included).toHaveValue("u1");
  });

  test("used by and confirmation", async ({ page }) => {
    const key = `${RUN}-usage`;
    const name = `${RUN} usage`;
    await createSegment(key, name);
    const flag = await createUsingFlag(key);
    await page.goto(segmentPath(SHOP, key));

    const usedBy = section(page, "Used by");
    await expect(usedBy.getByRole("link", { name: flag })).toHaveAttribute(
      "href",
      flagPath(SHOP, flag),
    );
    await expect(usedBy).toContainText(ENV);
    await expect(usedBy).toContainText(
      "A saved change applies to these flags at once",
    );

    const included = page.getByRole("textbox", { name: "Included keys" });
    await included.fill("u9");
    await included.blur();
    await unsaved(page).getByRole("button", { name: "Save" }).click();
    const confirm = page.getByRole("alertdialog", {
      name: `Save changes to ${name}?`,
    });
    await confirm.getByRole("button", { name: "Save changes" }).click();
    await expect(confirm).toBeHidden();
    await expect(unsaved(page)).toHaveCount(0);

    const saved = await ok(
      await api.get(`/api/projects/${SHOP}/segments/${key}`),
      "read the saved segment",
    );
    expect(((await saved.json()) as { included: string[] }).included).toEqual([
      "u9",
    ]);
  });

  test("an unused segment says so", async ({ page }) => {
    await open(page, "unused");
    await expect(section(page, "Used by")).toContainText(
      "No flag uses this segment.",
    );
  });

  test("edit details", async ({ page }) => {
    await open(page, "details");
    await page.getByRole("button", { name: "Edit details" }).click();
    const dialog = page.getByRole("dialog", { name: "Edit details" });
    await dialog.getByLabel("Name").fill(`${RUN} renamed`);
    await dialog.getByLabel("Description").fill("Renamed in a test");
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      `${RUN} renamed`,
    );
    await expect(page.getByText("Renamed in a test")).toBeVisible();
  });

  test("an unknown segment is not found", async ({ page }) => {
    await page.goto(segmentPath(SHOP, "nope"));
    await expect(
      page.getByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
  });
});

test.describe("as a viewer", () => {
  test.use({ storageState: VIEWER.storageState });

  test("a segment is read-only", async ({ page }) => {
    await page.goto(segmentPath(SHOP, "beta-testers"));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "Beta testers",
    );
    await expect(
      page.getByRole("textbox", { name: "Included keys" }),
    ).toHaveAttribute("readonly", "");
    await expect(
      page.getByRole("textbox", { name: "Excluded keys" }),
    ).toHaveAttribute("readonly", "");
    for (const name of ["Add rule", "Save", "Edit details"])
      await expect(page.getByRole("button", { name })).toHaveCount(0);
  });

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
