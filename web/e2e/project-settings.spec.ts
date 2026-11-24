import { type APIRequestContext, expect, test } from "@playwright/test";

import { projectPath } from "../lib/routes";

import { adminApi, ok, SHOP } from "./support/api";
import { VIEWER } from "./support/users";

// This run's own project, so renaming never disturbs the shared projects.
const RUN = `e2e-${Date.now().toString(36)}`;
const KEY = `${RUN}-p`;
const SETTINGS = projectPath(KEY, "settings");
// Created from the switcher, then deleted from its settings.
const NEW_NAME = `${RUN} New`;
const NEW_KEY = `${RUN}-new`;
// Created while the read of it is made to fail.
const HIDDEN_NAME = `${RUN} Hidden`;
const HIDDEN_KEY = `${RUN}-hidden`;

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
  await ok(
    await api.post("/api/projects", { data: { key: KEY, name: KEY } }),
    `create project ${KEY}`,
  );
});

test.afterAll(async () => {
  for (const key of [KEY, NEW_KEY, HIDDEN_KEY]) {
    const res = await api.get(`/api/projects/${key}`);
    if (res.status() !== 404)
      await ok(
        await api.delete(`/api/projects/${key}`),
        `delete project ${key}`,
      );
  }
  await api.dispose();
});

test.describe("as an admin", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("the menu item opens the project settings", async ({ page }) => {
    await page.goto(projectPath(KEY));
    const nav = page.getByRole("navigation", { name: "Main" });
    await nav.getByRole("link", { name: "Project settings" }).click();
    await expect(page).toHaveURL(SETTINGS);
    await expect(
      page.getByRole("heading", { level: 1, name: "Project settings" }),
    ).toBeVisible();
    await expect(page.getByLabel("Key")).toHaveValue(KEY);
  });

  test("rename and describe", async ({ page }) => {
    await page.goto(SETTINGS);
    const details = page.getByRole("region", { name: "Details" });
    const name = `${RUN} Renamed`;
    await details.getByLabel("Name").fill(name);
    await details.getByLabel("Description").fill("Checkout experiments");
    await details.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Project saved")).toBeVisible();
    await expect(
      page.getByRole("button", { name: `Project: ${name}` }),
    ).toBeVisible();

    const res = await api.get(`/api/projects/${KEY}`);
    await ok(res, "read the project");
    expect(await res.json()).toMatchObject({
      name,
      description: "Checkout experiments",
    });
  });

  test("stale after days", async ({ page }) => {
    await page.goto(SETTINGS);
    const stale = page.getByRole("region", { name: "Stale flags" });
    const days = stale.getByLabel("Stale after (days)");
    const save = stale.getByRole("button", { name: "Save" });
    await expect(days).toHaveAccessibleDescription(
      /A flag no environment has evaluated for this many days is marked stale\./,
    );

    for (const value of ["0", "366"]) {
      await days.fill(value);
      await expect(stale.getByText("Enter 1 to 365")).toBeVisible();
      await expect(save).toBeDisabled();
    }

    await days.fill("45");
    await expect(stale.getByText("Enter 1 to 365")).toHaveCount(0);
    await save.click();
    await expect(page.getByText("Project saved")).toBeVisible();
    await expect
      .poll(async () => {
        const res = await api.get(`/api/projects/${KEY}`);
        return ((await res.json()) as { stale_after_days: number })
          .stale_after_days;
      })
      .toBe(45);
  });

  test("create from the switcher", async ({ page }) => {
    await page.goto(projectPath(KEY));
    await page.getByRole("button", { name: /^Project: / }).click();
    await page.getByRole("menuitem", { name: "New project" }).click();
    const dialog = page.getByRole("dialog", { name: "New project" });
    await expect(dialog).toContainText(
      "It starts with Development, Staging and Production.",
    );
    await dialog.getByLabel("Name").fill(NEW_NAME);
    await expect(dialog.getByLabel("Key")).toHaveValue(NEW_KEY);
    await dialog.getByLabel("Description").fill("Made in a test");
    await dialog.getByRole("button", { name: "Create project" }).click();

    await expect(page).toHaveURL(projectPath(NEW_KEY));
    await expect(
      page.getByRole("button", { name: `Project: ${NEW_NAME}` }),
    ).toBeVisible();
  });

  test("delete with the typed key", async ({ page }) => {
    // Present whether or not "create from the switcher" ran first.
    await api.post("/api/projects", {
      data: { key: NEW_KEY, name: NEW_NAME },
    });
    await page.goto(projectPath(NEW_KEY, "settings"));
    const danger = page.getByRole("region", { name: "Danger zone" });
    await danger.getByRole("button", { name: "Delete project" }).click();
    const dialog = page.getByRole("alertdialog", {
      name: `Delete ${NEW_NAME}?`,
    });
    await expect(dialog).toContainText(
      `This deletes ${NEW_NAME} with all its flags, segments, environments, SDK keys, change requests and history. It can't be undone.`,
    );
    const confirm = dialog.getByRole("button", { name: "Delete project" });
    await expect(confirm).toBeDisabled();
    await dialog.getByLabel(`Type ${NEW_KEY} to confirm`).fill(NEW_KEY);
    await confirm.click();

    await expect(page).toHaveURL(projectPath("demo"));
    expect((await api.get(`/api/projects/${NEW_KEY}`)).status()).toBe(404);
  });

  test("created without access", async ({ page }) => {
    await page.route(`**/api/projects/${HIDDEN_KEY}`, (route) =>
      route.request().method() === "GET"
        ? route.fulfill({
            status: 404,
            contentType: "application/json",
            body: JSON.stringify({
              code: "not_found",
              message: "Project not found",
            }),
          })
        : route.fallback(),
    );
    await page.goto(SETTINGS);
    await page.getByRole("button", { name: /^Project: / }).click();
    await page.getByRole("menuitem", { name: "New project" }).click();
    const dialog = page.getByRole("dialog", { name: "New project" });
    await dialog.getByLabel("Name").fill(HIDDEN_NAME);
    await dialog.getByRole("button", { name: "Create project" }).click();

    await expect(
      page.getByText(
        "Project created, but you have no access to it. Ask an admin for a role in it.",
      ),
    ).toBeVisible();
    await expect(page).toHaveURL(SETTINGS);
    await ok(
      await api.get(`/api/projects/${HIDDEN_KEY}`),
      "the project exists",
    );
  });
});

test.describe("as a viewer", () => {
  test.use({
    storageState: VIEWER.storageState,
    viewport: { width: 1440, height: 900 },
  });

  test("the settings are read-only", async ({ page }) => {
    await page.goto(projectPath(SHOP, "settings"));
    const details = page.getByRole("region", { name: "Details" });
    await expect(details.getByLabel("Name")).not.toBeEditable();
    await expect(details.getByLabel("Description")).not.toBeEditable();
    await expect(
      page
        .getByRole("region", { name: "Stale flags" })
        .getByLabel("Stale after (days)"),
    ).not.toBeEditable();
    await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Danger zone" }),
    ).toHaveCount(0);
    await expect(
      page
        .getByRole("navigation", { name: "Main" })
        .getByRole("link", { name: "Project settings" }),
    ).toHaveCount(0);
  });

  test("no New project", async ({ page }) => {
    await page.goto(projectPath(SHOP));
    await page.getByRole("button", { name: /^Project: / }).click();
    await expect(page.getByRole("menuitem").first()).toBeVisible();
    await expect(
      page.getByRole("menuitem", { name: "New project" }),
    ).toHaveCount(0);
  });
});
