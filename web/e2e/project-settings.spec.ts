import { type APIRequestContext, expect, test } from "@playwright/test";

import { projectPath } from "../lib/routes";

import { adminApi, ok, SHOP } from "./support/api";
import { VIEWER } from "./support/users";

// This run's own project, so renaming never disturbs the shared projects.
const RUN = `e2e-${Date.now().toString(36)}`;
const KEY = `${RUN}-p`;
const SETTINGS = projectPath(KEY, "settings");

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
  await ok(
    await api.post("/api/projects", { data: { key: KEY, name: KEY } }),
    `create project ${KEY}`,
  );
});

test.afterAll(async () => {
  const res = await api.get(`/api/projects/${KEY}`);
  if (res.status() !== 404)
    await ok(await api.delete(`/api/projects/${KEY}`), `delete project ${KEY}`);
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
});
