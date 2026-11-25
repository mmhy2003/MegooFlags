import { type APIRequestContext, expect, test } from "@playwright/test";

import {
  adminApi,
  createTestFlag,
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
