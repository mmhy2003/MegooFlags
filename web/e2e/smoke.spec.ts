import { expect, test } from "@playwright/test";

// Proves global setup signed the admin in and the saved storageState works.
test("a signed-in admin reaches /account/security", async ({ page }) => {
  await page.goto("/account/security");
  await expect(page).toHaveURL(/\/account\/security$/);
  await expect(page).not.toHaveURL(/\/sign-in/);
});
