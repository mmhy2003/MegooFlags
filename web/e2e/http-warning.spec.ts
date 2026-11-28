import { expect, test } from "@playwright/test";

// DEP-6: the banner is decided on the server from the Host header, so it shows on sign-in too.
test.use({ storageState: { cookies: [], origins: [] } });

test("no plain-HTTP warning on localhost", async ({ page }) => {
  await page.goto("http://localhost:3000/sign-in");
  await expect(
    page.getByRole("heading", { name: "Sign in to MegooFlags" }),
  ).toBeVisible();
  await expect(page.getByText(/Plain HTTP/)).toHaveCount(0);
});

test("plain-HTTP warning on a non-loopback host", async ({ page }) => {
  await page.goto("http://127.0.0.2:3000/sign-in");
  const banner = page.getByRole("alert").filter({ hasText: "Plain HTTP" });
  await expect(banner).toBeVisible();
  await expect(banner.locator("svg")).toHaveCount(1);
});
