import {
  type APIRequestContext,
  expect,
  type Locator,
  type Page,
  test,
} from "@playwright/test";

import { flagPath, projectPath } from "../lib/routes";

import { noHorizontalScroll } from "./support/checks";
import { adminApi, ok, SHOP } from "./support/api";
import { VIEWER } from "./support/users";

// This run's own project, so reordering never disturbs the shared shop project.
const RUN = `e2e-${Date.now().toString(36)}`;
const LIST = projectPath(RUN, "environments");

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
  await ok(
    await api.post("/api/projects", { data: { key: RUN, name: RUN } }),
    `create project ${RUN}`,
  );
  await ok(
    await api.post(`/api/projects/${RUN}/flags`, {
      data: { key: "f1", name: "f1", type: "boolean" },
    }),
    "create flag f1",
  );
});

test.afterAll(async () => {
  await ok(await api.delete(`/api/projects/${RUN}`), `delete project ${RUN}`);
  await api.dispose();
});

/** The table rows of the environments, in page order. */
function rows(page: Page): Locator {
  return page
    .getByRole("table", { name: "Environments" })
    .getByRole("rowgroup")
    .nth(1)
    .getByRole("row");
}

/** Puts the environments back in the default order, so each test starts from it. */
async function resetOrder(): Promise<void> {
  await ok(
    await api.put(`/api/projects/${RUN}/environment-order`, {
      data: { environments: ["development", "staging", "production"] },
    }),
    "reset the environment order",
  );
}

test.describe("as an admin", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  test.beforeEach(resetOrder);

  test("lists in order with approvals", async ({ page }) => {
    await ok(
      await api.patch(`/api/projects/${RUN}/environments/production`, {
        data: { approvals_required: 2 },
      }),
      "protect production",
    );
    await page.goto(LIST);
    await expect(
      page.getByRole("heading", { level: 1, name: "Environments" }),
    ).toBeVisible();
    await expect(rows(page)).toHaveText([
      /Development/,
      /Staging/,
      /Production/,
    ]);
    await expect(rows(page).nth(0)).toContainText("development");
    await expect(rows(page).nth(0)).toContainText("Direct changes");
    await expect(rows(page).nth(1)).toContainText("Direct changes");
    await expect(rows(page).nth(2)).toContainText("Needs 2 approvals");
  });

  test("reorder", async ({ page }) => {
    await page.goto(LIST);
    await expect(rows(page)).toHaveCount(3);
    await page.getByRole("button", { name: "Move Production up" }).click();
    await expect(rows(page)).toHaveText([
      /Development/,
      /Production/,
      /Staging/,
    ]);
    await expect(page.locator(":focus")).toHaveAccessibleName(
      "Move Production up",
    );
    await page.keyboard.press("Enter");
    await expect(rows(page)).toHaveText([
      /Production/,
      /Development/,
      /Staging/,
    ]);
    // At the top, up is disabled, so focus moves to the other button.
    await expect(page.locator(":focus")).toHaveAccessibleName(
      "Move Production down",
    );

    await page.reload();
    await expect(rows(page)).toHaveText([
      /Production/,
      /Development/,
      /Staging/,
    ]);

    await page.goto(flagPath(RUN, "f1"));
    await expect(
      page
        .getByRole("region", { name: "Environments" })
        .getByRole("group")
        .first(),
    ).toHaveAccessibleName("Production");
  });

  test("reorder refused", async ({ page }) => {
    await page.route(`**/api/projects/${RUN}/environment-order`, (route) =>
      route.request().method() === "PUT"
        ? route.fulfill({
            status: 422,
            json: {
              code: "validation_error",
              message:
                "environments must list every environment of the project exactly once",
              details: [],
            },
          })
        : route.fallback(),
    );
    await page.goto(LIST);
    await expect(rows(page)).toHaveCount(3);
    await page.getByRole("button", { name: "Move Staging up" }).click();
    await expect(
      page.getByText(
        "The environments changed meanwhile. The list was reloaded.",
      ),
    ).toBeVisible();
    await expect(rows(page)).toHaveText([
      /Development/,
      /Staging/,
      /Production/,
    ]);
  });

  test("cards under 768 px", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto(LIST);
    const list = page.getByRole("list", { name: "Environments" });
    await expect(list.getByRole("article")).toHaveCount(3);
    await expect(page.getByRole("table")).toBeHidden();
    await expect(
      list.getByRole("button", { name: "Move Staging down" }),
    ).toBeVisible();
    expect(await noHorizontalScroll(page)).toBe(true);
  });
});

test.describe("as a viewer", () => {
  test.use({ storageState: VIEWER.storageState });

  test("the list is read-only", async ({ page }) => {
    await page.goto(projectPath(SHOP, "environments"));
    await expect(
      page.getByRole("table", { name: "Environments" }).getByRole("row"),
    ).not.toHaveCount(0);
    await expect(page.getByText("Production").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /^Move / })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Edit" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Delete" })).toHaveCount(0);
  });
});
