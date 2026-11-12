import { defineConfig, devices } from "@playwright/test";

import { ADMIN } from "./e2e/support/users";

const CI = !!process.env.CI;

// Runs against the built app (`pnpm build`) and a real API; see AGENTS.md §9.
export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  workers: 1,
  fullyParallel: false,
  forbidOnly: CI,
  retries: CI ? 1 : 0,
  reporter: [["html", { open: "never" }], ["list"]],
  use: {
    baseURL: "http://localhost:3000",
    storageState: ADMIN.storageState,
    trace: "retain-on-failure",
  },
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled" },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "pnpm start -H 0.0.0.0 -p 3000",
    url: "http://localhost:3000/sign-in",
    reuseExistingServer: !CI,
    env: {
      MEGOOFLAGS_UI_KIT: "1",
      MEGOOFLAGS_API_URL:
        process.env.MEGOOFLAGS_API_URL ?? "http://localhost:8000",
    },
  },
});
