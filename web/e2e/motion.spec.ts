import { expect, test, type Page } from "@playwright/test";

// UI-6: decorative motion stops when the user asks for reduced motion.
test.use({ viewport: { width: 1024, height: 900 } });

const running = (page: Page) =>
  page.evaluate(
    () =>
      document.getAnimations().filter((a) => a.playState === "running").length,
  );

test("nothing animates with reduced motion, even an opening dialog", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/dev/kit");
  await page.getByRole("button", { name: "Open sample dialog" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.waitForTimeout(100);
  expect(await running(page)).toBe(0);
});

test("without reduced motion the live dot pulses", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.goto("/dev/kit");
  const pill = page
    .getByRole("region", { name: "Status pills" })
    .getByText("On", { exact: true })
    .locator("..");
  const dot = pill.locator(".animate-pulse, [class*='animate-pulse']");
  await expect(dot).toHaveCount(1);
  expect(
    await dot.evaluate(
      (el) =>
        el.getAnimations().filter((a) => a.playState === "running").length,
    ),
  ).toBeGreaterThan(0);
});
