import {
  type APIRequestContext,
  expect,
  type Page,
  test,
} from "@playwright/test";

import { adminApi, createTestFlag, ok, SHOP } from "./support/api";

let api: APIRequestContext;

test.beforeAll(async () => {
  api = await adminApi();
});

test.afterAll(async () => {
  await api.dispose();
});

function detail(flag: string, query = ""): string {
  return `/projects/${SHOP}/flags/${flag}${query}`;
}

async function enabledIn(flag: string, env: string): Promise<boolean> {
  const response = await ok(
    await api.get(`/api/projects/${SHOP}/flags/${flag}/environments/${env}`),
    `read ${flag} in ${env}`,
  );
  return ((await response.json()) as { enabled: boolean }).enabled;
}

async function toggleViaApi(flag: string, env: string, enabled: boolean) {
  await ok(
    await api.post(
      `/api/projects/${SHOP}/flags/${flag}/environments/${env}/toggle`,
      { data: { enabled } },
    ),
    `toggle ${flag} in ${env}`,
  );
}

function card(page: Page, env: string) {
  return page
    .getByRole("region", { name: "Environments" })
    .getByRole("group", { name: env, exact: true });
}

test.describe("flag detail", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("an unknown flag is the not-found page", async ({ page }) => {
    await page.goto(detail("nope"));
    await expect(
      page.getByRole("heading", { name: "Page not found" }),
    ).toBeVisible();
  });

  test("the strip has a card per environment", async ({ page }) => {
    await page.goto(detail("checkout-v2"));
    await expect(
      page.getByRole("heading", { level: 1, name: "Checkout v2" }),
    ).toBeVisible();
    await expect(page.locator("header code")).toHaveText("checkout-v2");
    const strip = page.getByRole("region", { name: "Environments" });
    await expect(strip.getByRole("group")).toHaveCount(3);
    await expect(
      card(page, "Production").getByText("Needs approval"),
    ).toBeVisible();
    await expect(card(page, "Staging").getByText("Needs approval")).toHaveCount(
      0,
    );
    for (const name of ["Targeting", "Test", "History", "Insights", "Settings"])
      await expect(page.getByRole("tab", { name })).toBeVisible();
  });

  test("toggling on in Staging turns the flag on", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const toggle = page.getByRole("switch", { name: `${flag} in Staging` });
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    await expect(page.getByText("Turned on in Staging")).toBeVisible();
    await expect(toggle).toBeChecked();
    expect(await enabledIn(flag, "staging")).toBe(true);
  });

  test("toggling in Production requests a change", async ({ page }) => {
    const flag = await createTestFlag(api);
    await page.goto(detail(flag));
    const toggle = page.getByRole("switch", { name: `${flag} in Production` });
    await toggle.click();
    const dialog = page.getByRole("dialog", {
      name: "Request change in Production",
    });
    await expect(dialog.getByRole("textbox", { name: "Title" })).toHaveValue(
      `Turn on ${flag} in Production`,
    );
    await dialog
      .getByRole("textbox", { name: "Description" })
      .fill("Launch it");
    await dialog.getByRole("button", { name: "Request change" }).click();
    await expect(page.getByText("Change request created")).toBeVisible();
    await expect(
      page.getByRole("link", { name: "View change requests" }),
    ).toBeVisible();
    await expect(toggle).not.toBeChecked();

    const listed = await ok(
      await api.get(`/api/projects/${SHOP}/change-requests`, {
        params: { flag, env: "production" },
      }),
      "list change requests",
    );
    const { items } = (await listed.json()) as {
      items: { kind: string; title: string }[];
    };
    expect(items).toEqual([
      expect.objectContaining({
        kind: "toggle",
        title: `Turn on ${flag} in Production`,
      }),
    ]);
    expect(await enabledIn(flag, "production")).toBe(false);
  });

  test("the kill switch asks first", async ({ page }) => {
    const flag = await createTestFlag(api);
    await toggleViaApi(flag, "staging", true);
    await page.goto(detail(flag));
    const kill = page.getByRole("button", { name: "Kill switch in Staging" });
    const title = `Kill ${flag} in Staging?`;

    await kill.click();
    const dialog = page.getByRole("alertdialog", { name: title });
    await expect(dialog).toContainText(
      `This turns ${flag} off in Staging right away, without approval.`,
    );
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    await expect(
      page.getByRole("switch", { name: `${flag} in Staging` }),
    ).toBeChecked();
    expect(await enabledIn(flag, "staging")).toBe(true);

    await kill.click();
    await page
      .getByRole("alertdialog", { name: title })
      .getByRole("button", { name: "Kill switch" })
      .click();
    await expect(
      page.getByRole("switch", { name: `${flag} in Staging` }),
    ).not.toBeChecked();
    await expect(kill).toBeDisabled();
    expect(await enabledIn(flag, "staging")).toBe(false);
  });

  test("the kill switch skips approval in Production", async ({ page }) => {
    const flag = await createTestFlag(api);
    const production = `/api/projects/${SHOP}/environments/production`;
    await ok(
      await api.patch(production, { data: { approvals_required: 0 } }),
      "drop approvals",
    );
    try {
      await toggleViaApi(flag, "production", true);
    } finally {
      await ok(
        await api.patch(production, { data: { approvals_required: 1 } }),
        "restore approvals",
      );
    }
    await page.goto(detail(flag));
    await page
      .getByRole("button", { name: "Kill switch in Production" })
      .click();
    await page
      .getByRole("alertdialog", { name: `Kill ${flag} in Production?` })
      .getByRole("button", { name: "Kill switch" })
      .click();
    await expect(
      page.getByRole("switch", { name: `${flag} in Production` }),
    ).not.toBeChecked();
    expect(await enabledIn(flag, "production")).toBe(false);
    const listed = await ok(
      await api.get(`/api/projects/${SHOP}/change-requests`, {
        params: { flag },
      }),
      "list change requests",
    );
    expect(((await listed.json()) as { items: unknown[] }).items).toEqual([]);
  });

  test("the environment and tab live in the URL", async ({ page }) => {
    await page.goto(detail("checkout-v2"));
    const edit = page.getByRole("button", { name: "Edit Development" });
    await expect(edit).toHaveAttribute("aria-pressed", "true");

    await page.getByRole("button", { name: "Edit Staging" }).click();
    await expect(page).toHaveURL(/[?&]env=staging/);
    await page.reload();
    await expect(
      page.getByRole("button", { name: "Edit Staging" }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(edit).toHaveAttribute("aria-pressed", "false");

    await page.getByRole("tab", { name: "History" }).click();
    await expect(page).toHaveURL(/[?&]tab=history/);
    await expect(page).toHaveURL(/[?&]env=staging/);
    await page.reload();
    await expect(page.getByRole("tab", { name: "History" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
