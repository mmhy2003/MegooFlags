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

const DEFAULT_ORDER = ["development", "staging", "production"];

/** Puts the default environments back first, in their order, so each test starts from it. */
async function resetOrder(): Promise<void> {
  const res = await api.get(`/api/projects/${RUN}/environments`);
  await ok(res, "list the environments");
  const keys = ((await res.json()) as { key: string }[]).map((e) => e.key);
  await ok(
    await api.put(`/api/projects/${RUN}/environment-order`, {
      data: {
        environments: [
          ...DEFAULT_ORDER,
          ...keys.filter((k) => !DEFAULT_ORDER.includes(k)),
        ],
      },
    }),
    "reset the environment order",
  );
}

/** The dialog to edit an environment, opened from its row. */
async function openEdit(page: Page, name: string): Promise<Locator> {
  await page.getByRole("button", { name: `Edit ${name}` }).click();
  return page.getByRole("dialog", { name: "Edit environment" });
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

  test("create", async ({ page }) => {
    // A retry starts without the environment the first try created.
    await api.delete(`/api/projects/${RUN}/environments/qa`);
    await page.goto(LIST);
    await expect(rows(page)).toHaveCount(3);
    await page.getByRole("button", { name: "New environment" }).click();
    const dialog = page.getByRole("dialog", { name: "New environment" });
    await dialog.getByLabel("Name").fill("QA");
    await expect(dialog.getByLabel("Key")).toHaveValue("qa");
    await expect(dialog).toContainText(
      "Every flag gets this environment, turned off.",
    );
    await expect(dialog).toContainText("It's added last.");
    await dialog.getByLabel("Description").fill("Manual testing");
    await dialog.getByRole("button", { name: "Create environment" }).click();

    await expect(dialog).toBeHidden();
    await expect(rows(page)).toHaveText([
      /Development/,
      /Staging/,
      /Production/,
      /QA/,
    ]);
    const res = await api.get(`/api/projects/${RUN}/flags/f1/environments/qa`);
    await ok(res, "read f1 in qa");
    expect(((await res.json()) as { enabled: boolean }).enabled).toBe(false);
  });

  test("duplicate key", async ({ page }) => {
    // Present whether or not "create" ran first.
    await api.post(`/api/projects/${RUN}/environments`, {
      data: { key: "qa", name: "QA" },
    });
    await page.goto(LIST);
    await page.getByRole("button", { name: "New environment" }).click();
    const dialog = page.getByRole("dialog", { name: "New environment" });
    await dialog.getByLabel("Name").fill("Another QA");
    await dialog.getByLabel("Key").fill("qa");
    await dialog.getByRole("button", { name: "Create environment" }).click();
    await expect(dialog.getByLabel("Key")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await expect(dialog).toContainText("environment 'qa' already exists");
  });

  test("set and clear approvals", async ({ page }) => {
    await ok(
      await api.patch(`/api/projects/${RUN}/environments/staging`, {
        data: { approvals_required: 0 },
      }),
      "unprotect staging",
    );
    await page.goto(LIST);
    let dialog = await openEdit(page, "Staging");
    await expect(dialog.getByLabel("Key")).toHaveValue("staging");
    await expect(dialog.getByLabel("Key")).toHaveAttribute("readonly", "");
    await expect(dialog.getByLabel("Approvals needed")).toBeHidden();
    await dialog.getByLabel("Changes need approval").check();
    await dialog.getByLabel("Approvals needed").fill("2");
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toBeHidden();
    await expect(rows(page).nth(1)).toContainText("Needs 2 approvals");

    await page.goto(flagPath(RUN, "f1"));
    await expect(
      page
        .getByRole("region", { name: "Environments" })
        .getByRole("group", { name: "Staging" }),
    ).toContainText("Needs approval");

    await page.goto(LIST);
    dialog = await openEdit(page, "Staging");
    await expect(dialog.getByLabel("Changes need approval")).toBeChecked();
    await expect(dialog.getByLabel("Approvals needed")).toHaveValue("2");
    const note = "Open change requests stay open and still need one approval.";
    await expect(dialog.getByText(note)).toBeHidden();
    await dialog.getByLabel("Changes need approval").uncheck();
    await expect(dialog.getByText(note)).toBeVisible();
    await dialog.getByRole("button", { name: "Save" }).click();
    await expect(dialog).toBeHidden();
    await expect(rows(page).nth(1)).toContainText("Direct changes");
  });

  test("approvals range", async ({ page }) => {
    await page.goto(LIST);
    const dialog = await openEdit(page, "Development");
    await dialog.getByLabel("Changes need approval").check();
    await dialog.getByLabel("Approvals needed").fill("11");
    await expect(dialog.getByLabel("Approvals needed")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    await expect(dialog).toContainText("Enter 1 to 10");
    await expect(dialog.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  test("delete with the typed key", async ({ page }) => {
    // Present whether or not "create" ran first.
    await api.post(`/api/projects/${RUN}/environments`, {
      data: { key: "qa", name: "QA" },
    });
    await page.goto(LIST);
    await expect(rows(page)).toHaveCount(4);
    await page.getByRole("button", { name: "Delete QA" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Delete QA?" });
    await expect(dialog).toContainText(
      "This deletes QA and, with it, every flag's state, targeting, schedules and rollouts there, its SDK keys, change requests and role assignments. It can't be undone.",
    );
    const confirm = dialog.getByRole("button", { name: "Delete environment" });
    await expect(confirm).toBeDisabled();
    const typed = dialog.getByLabel("Type qa to confirm");
    await expect(typed).toHaveAttribute("autocomplete", "off");
    await typed.fill("q");
    await expect(confirm).toBeDisabled();
    await typed.fill("qa");
    await confirm.click();

    await expect(dialog).toBeHidden();
    await expect(page.getByText("Environment deleted")).toBeVisible();
    await expect(rows(page)).toHaveText([
      /Development/,
      /Staging/,
      /Production/,
    ]);

    await page.goto(flagPath(RUN, "f1"));
    const strip = page.getByRole("region", { name: "Environments" });
    await expect(
      strip.getByRole("group", { name: "Production" }),
    ).toBeVisible();
    await expect(strip.getByRole("group", { name: "QA" })).toHaveCount(0);
  });

  test("a failed delete keeps the dialog open", async ({ page }) => {
    await api.post(`/api/projects/${RUN}/environments`, {
      data: { key: "qa", name: "QA" },
    });
    await page.route(`**/api/projects/${RUN}/environments/qa`, (route) =>
      route.request().method() === "DELETE"
        ? route.fulfill({
            status: 409,
            json: {
              code: "conflict",
              message: "the environment is busy",
              details: null,
            },
          })
        : route.fallback(),
    );
    await page.goto(LIST);
    await page.getByRole("button", { name: "Delete QA" }).click();
    const dialog = page.getByRole("alertdialog", { name: "Delete QA?" });
    await dialog.getByLabel("Type qa to confirm").fill("qa");
    const confirm = dialog.getByRole("button", { name: "Delete environment" });
    await confirm.click();
    await expect(page.getByText("the environment is busy")).toBeVisible();
    await expect(dialog).toBeVisible();
    await expect(confirm).toBeEnabled();

    // Closing resets what was typed.
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    await page.getByRole("button", { name: "Delete QA" }).click();
    await expect(dialog.getByLabel("Type qa to confirm")).toHaveValue("");
    await expect(confirm).toBeDisabled();
  });

  test("the last environment", async ({ page }) => {
    const single = `${RUN}-one`;
    await ok(
      await api.post("/api/projects", { data: { key: single, name: single } }),
      `create project ${single}`,
    );
    try {
      for (const env of ["staging", "production"])
        await ok(
          await api.delete(`/api/projects/${single}/environments/${env}`),
          `delete ${env}`,
        );
      await page.goto(projectPath(single, "environments"));
      await expect(rows(page)).toHaveCount(1);
      const remove = page
        .getByRole("table", { name: "Environments" })
        .getByRole("button", { name: "Delete Development" });
      await expect(remove).toBeDisabled();
      await expect(remove).toHaveAccessibleDescription(
        "A project needs at least one environment.",
      );
      await expect(
        page.getByText("A project needs at least one environment."),
      ).toBeVisible();
    } finally {
      await ok(
        await api.delete(`/api/projects/${single}`),
        `delete project ${single}`,
      );
    }
  });

  test("cards under 768 px", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await page.goto(LIST);
    const res = await api.get(`/api/projects/${RUN}/environments`);
    await ok(res, "list the environments");
    const count = ((await res.json()) as unknown[]).length;
    const list = page.getByRole("list", { name: "Environments" });
    await expect(list.getByRole("article")).toHaveCount(count);
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
