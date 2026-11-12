import { expect, request, test } from "@playwright/test";

import { PASSWORD, PREFS } from "./support/users";

const ORIGIN = "http://localhost:3000";

// These tests change PREFS's stored preferences, so they run one after another.
test.describe.configure({ mode: "serial" });
test.use({
  storageState: PREFS.storageState,
  viewport: { width: 1440, height: 900 },
});

test.beforeEach(async ({ page }) => {
  const r = await page.request.patch("/api/account/preferences", {
    data: { theme: "system", sidebar_collapsed: false },
    headers: { Origin: ORIGIN },
  });
  expect(r.ok()).toBe(true);
});

test.afterAll(async () => {
  // The last test signs PREFS out, which ends the session saved in its storage state.
  const ctx = await request.newContext({
    baseURL: ORIGIN,
    extraHTTPHeaders: { Origin: ORIGIN },
  });
  try {
    const r = await ctx.post("/api/auth/sign-in", {
      data: { email: PREFS.email, password: PASSWORD },
    });
    expect(r.ok()).toBe(true);
    await ctx.storageState({ path: PREFS.storageState });
  } finally {
    await ctx.dispose();
  }
});

test("the collapsed sidebar survives a reload and a new browser", async ({
  page,
  browser,
}) => {
  await page.goto("/projects/demo/flags");
  const sidebar = page.locator("[data-slot=sidebar][data-state]");
  await expect(sidebar).toHaveAttribute("data-state", "expanded");
  const saved = page.waitForResponse(
    (r) => r.url().endsWith("/api/account/preferences") && r.ok(),
  );
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(sidebar).toHaveAttribute("data-state", "collapsed");
  await saved;

  await page.reload();
  await expect(sidebar).toHaveAttribute("data-state", "collapsed");

  const other = await browser.newContext({
    storageState: PREFS.storageState,
    viewport: { width: 1440, height: 900 },
  });
  try {
    const fresh = await other.newPage();
    await fresh.goto("/projects/demo/flags");
    expect(
      (await other.cookies()).some((c) => c.name === "sidebar_state"),
    ).toBe(false);
    await expect(
      fresh.locator("[data-slot=sidebar][data-state]"),
    ).toHaveAttribute("data-state", "collapsed");
  } finally {
    await other.close();
  }
});

test("the stored theme applies on first paint, with no flash", async ({
  page,
  browser,
}) => {
  const r = await page.request.patch("/api/account/preferences", {
    data: { theme: "dark" },
    headers: { Origin: ORIGIN },
  });
  expect(r.ok()).toBe(true);

  const other = await browser.newContext({
    colorScheme: "light",
    storageState: PREFS.storageState,
  });
  try {
    await other.addInitScript(() => {
      const w = window as unknown as { __classes: string[] };
      w.__classes = [];
      document.addEventListener("DOMContentLoaded", () => {
        w.__classes.push(document.documentElement.className);
      });
      // The init script runs before <html> exists, so watch the document for it.
      new MutationObserver((records) => {
        for (const r of records) {
          if (r.target === document.documentElement && r.type === "attributes")
            w.__classes.push(document.documentElement.className);
        }
      }).observe(document, {
        subtree: true,
        attributes: true,
        attributeFilter: ["class"],
      });
    });
    const fresh = await other.newPage();
    await fresh.goto("/projects/demo/flags");
    await expect(fresh.locator("html")).toHaveClass(/\bdark\b/);
    const classes = await fresh.evaluate(
      () => (window as unknown as { __classes: string[] }).__classes,
    );
    expect(classes.length).toBeGreaterThan(0);
    expect(classes.filter((c) => /\blight\b/.test(c))).toEqual([]);
  } finally {
    await other.close();
  }
});

test("the theme switcher applies and stores the theme", async ({ page }) => {
  await page.goto("/projects/demo/flags");
  await page.getByRole("button", { name: "Theme" }).click();
  await page.getByRole("menuitemradio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  await expect
    .poll(async () => {
      const me = await page.request.get("/api/auth/me");
      return (await me.json()).preferences?.theme;
    })
    .toBe("dark");
});

test("the user menu shows initials and name, and links to the account", async ({
  page,
}) => {
  await page.goto("/projects/demo/flags");
  const account = page.getByRole("button", { name: /^Account/ });
  await expect(account).toContainText("PP");
  await expect(account.getByText("Pat Prefs")).toBeVisible();

  await account.click();
  await page.getByRole("menuitem", { name: "Profile & security" }).click();
  await expect(page).toHaveURL(/\/account\/security$/);

  await account.click();
  await page.getByRole("menuitem", { name: "API tokens" }).click();
  await expect(page).toHaveURL(/\/account\/tokens$/);

  await account.click();
  await expect(page.getByRole("menuitem", { name: "Sign out" })).toBeVisible();
});

test.describe("phone", () => {
  test.use({ viewport: { width: 390, height: 800 } });

  test("the user menu shows only the initials", async ({ page }) => {
    await page.goto("/projects/demo/flags");
    const account = page.getByRole("button", { name: /^Account/ });
    await expect(account).toContainText("PP");
    await expect(account.getByText("Pat Prefs")).toBeHidden();
  });
});

test("a failed save says so", async ({ page }) => {
  await page.goto("/projects/demo/flags");
  await page.route("**/api/account/preferences", (r) =>
    r.fulfill({ status: 500 }),
  );
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(page.getByText("Couldn't save this preference")).toBeVisible();
});

test("sign out lands on the sign-in page", async ({ page }) => {
  await page.goto("/projects/demo/flags");
  await page.getByRole("button", { name: /^Account/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
});
