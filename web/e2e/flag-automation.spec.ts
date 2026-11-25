import { type APIRequestContext, expect, test } from "@playwright/test";

import {
  adminApi,
  createTestFlag,
  ok,
  putConfig,
  scheduleChange,
  SHOP,
  startRollout,
} from "./support/api";
import { ADMIN, VIEWER } from "./support/users";

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  await api.dispose();
});

function automation(flag: string, env = "development"): string {
  return `/projects/${SHOP}/flags/${flag}?env=${env}&tab=automation`;
}

function daysAhead(days: number): string {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
}

test.describe("flag automation", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("shows a running rollout", async ({ page }) => {
    const flag = await createTestFlag(api);
    await startRollout(api, flag, "development");

    await page.goto(automation(flag));
    await expect(
      page.getByRole("tab", { name: "Automation, 1 active" }),
    ).toBeVisible();
    const rollout = page.getByRole("region", { name: "Progressive rollout" });
    await expect(rollout.getByText("Running")).toBeVisible();
    await expect(rollout.getByText("false → true")).toBeVisible();
    await expect(
      rollout.getByRole("progressbar", { name: "Serving true" }),
    ).toHaveAttribute("aria-valuenow", "10");
    const steps = rollout.getByRole("list", { name: "Steps" });
    const current = steps.getByRole("listitem").filter({ hasText: "10 %" });
    await expect(current).toContainText("Current");
    const next = steps.getByRole("listitem").filter({ hasText: "100 %" });
    await expect(next).toContainText("Next");
    await expect(next.locator("time")).toHaveCount(1);

    await page.getByRole("tab", { name: "Targeting" }).click();
    await expect(
      page.getByText(
        "A progressive rollout is running: the default rule serves true to 10 % now.",
      ),
    ).toBeVisible();
    await page.getByRole("link", { name: "Open Automation" }).click();
    await expect(page).toHaveURL(/tab=automation/);
    await expect(rollout.getByText("false → true")).toBeVisible();
  });

  test("lists schedules", async ({ page }) => {
    const flag = await createTestFlag(api);
    await scheduleChange(api, flag, "development", {
      action: "enable",
      run_at: daysAhead(1),
    });

    await page.goto(automation(flag));
    await expect(
      page.getByRole("tab", { name: "Automation, 1 active" }),
    ).toBeVisible();
    const schedules = page.getByRole("region", { name: "Scheduled changes" });
    const upcoming = schedules.getByRole("list", { name: "Upcoming" });
    const row = upcoming.getByRole("listitem").filter({ hasText: "Turn on" });
    await expect(row).toBeVisible();
    await expect(row.locator("time").first()).toBeVisible();
    await expect(row).toContainText(`By ${ADMIN.email}`);
    const pastChanges = schedules.locator("details", {
      hasText: "Past changes",
    });
    await expect(pastChanges).toBeVisible();
    await expect(pastChanges).not.toHaveAttribute("open");
  });

  test("empty", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(automation(flag));
    await expect(page.getByRole("tab", { name: "Automation" })).toBeVisible();
    await expect(page.getByText("No rollout running")).toBeVisible();
    await expect(page.getByText("No scheduled changes")).toBeVisible();
  });

  test("start", async ({ page }) => {
    const flag = await createTestFlag(api);
    // A new flag serves its first variation, true; From follows what it serves now.
    await putConfig(api, flag, "development", (c) => {
      c.fallthrough = { variation: "false" };
    });
    await page.goto(automation(flag));
    await page.getByRole("button", { name: "Start a rollout" }).click();
    const dialog = page.getByRole("dialog", {
      name: "Start a rollout in Development",
    });
    await expect(dialog.getByRole("combobox", { name: "From" })).toHaveValue(
      "false",
    );
    await expect(dialog.getByRole("combobox", { name: "To" })).toHaveValue(
      "true",
    );
    for (const [n, percent] of [
      [1, "10"],
      [2, "25"],
      [3, "50"],
    ] as const) {
      await expect(
        dialog.getByRole("spinbutton", { name: `Step ${n} percent` }),
      ).toHaveValue(percent);
      await expect(
        dialog.getByRole("spinbutton", { name: `Step ${n} wait` }),
      ).toHaveValue("1");
      await expect(
        dialog.getByRole("combobox", { name: `Step ${n} wait unit` }),
      ).toHaveValue("hours");
    }
    await expect(dialog.getByText("Step 4: 100 %")).toBeVisible();
    await expect(
      dialog.getByText(
        "Only the default rule changes. Rules and individual targets stay as they are.",
      ),
    ).toBeVisible();

    await dialog.getByRole("spinbutton", { name: "Step 2 percent" }).fill("30");
    await dialog.getByRole("button", { name: "Remove step 3" }).click();
    await expect(
      dialog.getByRole("spinbutton", { name: "Step 3 percent" }),
    ).toHaveCount(0);
    await expect(
      dialog.getByText(
        "The first step (10 %) applies when you start. 100 % after 2 hours.",
      ),
    ).toBeVisible();
    await dialog.getByRole("button", { name: "Start rollout" }).click();

    await expect(
      page.getByText("Rollout started in Development"),
    ).toBeVisible();
    const rollout = page.getByRole("region", { name: "Progressive rollout" });
    await expect(rollout.getByText("Running")).toBeVisible();
    await expect(
      rollout.getByRole("progressbar", { name: "Serving true" }),
    ).toHaveAttribute("aria-valuenow", "10");
    await expect(
      rollout.getByRole("list", { name: "Steps" }).getByRole("listitem"),
    ).toHaveCount(3);

    await page.getByRole("tab", { name: /^Targeting/ }).click();
    const fallthrough = page.getByRole("region", { name: "Default rule" });
    await expect(
      fallthrough.getByRole("radio", { name: "Serve a rollout" }),
    ).toBeChecked();
    await expect(
      fallthrough.getByRole("spinbutton", { name: "Rollout percentage" }),
    ).toHaveValue("10");
  });

  test("checks", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(automation(flag));
    await page.getByRole("button", { name: "Start a rollout" }).click();
    const dialog = page.getByRole("dialog", {
      name: "Start a rollout in Development",
    });
    await expect(dialog.getByRole("combobox", { name: "From" })).toHaveValue(
      "true",
    );
    await dialog.getByRole("combobox", { name: "To" }).selectOption("true");
    await expect(
      dialog.getByText("Choose a variation other than From"),
    ).toBeVisible();
    await expect(
      dialog.getByRole("button", { name: "Start rollout" }),
    ).toBeDisabled();
  });

  test("pause, resume, abort", async ({ page }) => {
    const flag = await createTestFlag(api);
    await startRollout(api, flag, "development");
    await page.goto(automation(flag));
    const rollout = page.getByRole("region", { name: "Progressive rollout" });

    await rollout.getByRole("button", { name: "Pause" }).click();
    await expect(page.getByText("Rollout paused")).toBeVisible();
    await expect(rollout.getByText("Paused")).toBeVisible();
    await expect(
      rollout
        .getByRole("list", { name: "Steps" })
        .getByRole("listitem")
        .filter({ hasText: "100 %" }),
    ).toContainText("left");

    await rollout.getByRole("button", { name: "Resume" }).click();
    await expect(page.getByText("Rollout resumed")).toBeVisible();
    await expect(rollout.getByText("Running")).toBeVisible();

    await rollout.getByRole("button", { name: "Abort" }).click();
    const confirm = page.getByRole("alertdialog", {
      name: "Abort the rollout in Development?",
    });
    await expect(confirm).toContainText(
      "The default rule serves false to everyone again. Pause keeps the current split instead.",
    );
    await confirm.getByRole("button", { name: "Abort rollout" }).click();
    await expect(rollout.getByText("No rollout running")).toBeVisible();
    await expect(
      rollout.getByRole("list", { name: "Recent rollouts" }),
    ).toContainText("Aborted");

    await page.getByRole("tab", { name: /^Targeting/ }).click();
    const fallthrough = page.getByRole("region", { name: "Default rule" });
    await expect(
      fallthrough.getByRole("radio", { name: "Serve a variation" }),
    ).toBeChecked();
    await expect(
      fallthrough.getByRole("combobox", { name: "Variation" }),
    ).toHaveValue("false");
  });

  test("already running", async ({ page }) => {
    const flag = await createTestFlag(api);
    const message = `a rollout is already running for flag '${flag}' in 'development'`;
    await page.route(
      `**/api/projects/${SHOP}/flags/${flag}/environments/development/rollout`,
      (route) =>
        route.request().method() === "POST"
          ? route.fulfill({
              status: 409,
              json: { code: "invalid_state", message, details: null },
            })
          : route.fallback(),
    );
    await page.goto(automation(flag));
    await page.getByRole("button", { name: "Start a rollout" }).click();
    // Someone else started one meanwhile.
    await startRollout(api, flag, "development");
    await page
      .getByRole("dialog", { name: "Start a rollout in Development" })
      .getByRole("button", { name: "Start rollout" })
      .click();
    await expect(page.getByText(message)).toBeVisible();
    await expect(
      page
        .getByRole("region", { name: "Progressive rollout" })
        .getByText("Running"),
    ).toBeVisible();
  });

  test("protected production", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(automation(flag, "production"));
    await page.getByRole("button", { name: "Request a rollout" }).click();
    const dialog = page.getByRole("dialog", {
      name: "Request a rollout in Production",
    });
    await expect(dialog.getByRole("textbox", { name: "Title" })).toHaveValue(
      `Roll out false in ${flag} in Production`,
    );
    await dialog
      .getByRole("textbox", { name: "Description" })
      .fill("Slowly, please");
    await dialog.getByRole("button", { name: "Request rollout" }).click();
    await expect(page.getByText("Change request created")).toBeVisible();
    await expect(
      page
        .getByRole("region", { name: "Progressive rollout" })
        .getByText("No rollout running"),
    ).toBeVisible();

    const listed = await ok(
      await api.get(`/api/projects/${SHOP}/change-requests`, {
        params: { flag },
      }),
      "list change requests",
    );
    const { items } = (await listed.json()) as {
      items: { kind: string; environment: string; title: string }[];
    };
    expect(items).toEqual([
      expect.objectContaining({
        kind: "rollout",
        environment: "production",
        title: `Roll out false in ${flag} in Production`,
      }),
    ]);
  });

  test("approval_required meanwhile", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.route(
      `**/api/projects/${SHOP}/flags/${flag}/environments/development/rollout`,
      (route) =>
        route.request().method() === "POST"
          ? route.fulfill({
              status: 409,
              json: {
                code: "approval_required",
                message: "environment 'development' requires approval",
                details: null,
              },
            })
          : route.fallback(),
    );
    await page.goto(automation(flag));
    await page.getByRole("button", { name: "Start a rollout" }).click();
    await page
      .getByRole("dialog", { name: "Start a rollout in Development" })
      .getByRole("button", { name: "Start rollout" })
      .click();
    const dialog = page.getByRole("dialog", {
      name: "Request a rollout in Development",
    });
    await expect(dialog.getByRole("textbox", { name: "Title" })).toHaveValue(
      `Roll out false in ${flag} in Development`,
    );
    await expect(
      dialog.getByRole("button", { name: "Request rollout" }),
    ).toBeEnabled();
  });
});

test.describe("flag automation as a viewer", () => {
  test.use({
    storageState: VIEWER.storageState,
    viewport: { width: 1440, height: 900 },
  });

  test("viewer", async ({ page }) => {
    const flag = await createTestFlag(api);
    await startRollout(api, flag, "development");
    await scheduleChange(api, flag, "development", {
      action: "disable",
      run_at: daysAhead(2),
    });

    await page.goto(automation(flag));
    await expect(
      page.getByRole("tab", { name: "Automation, 2 active" }),
    ).toBeVisible();
    await expect(
      page
        .getByRole("region", { name: "Scheduled changes" })
        .getByText("Turn off"),
    ).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Progressive rollout" }),
    ).toContainText("Running");
    for (const name of [/start/i, /pause/i, /schedule/i, /cancel/i]) {
      await expect(page.getByRole("button", { name })).toHaveCount(0);
    }
  });
});
