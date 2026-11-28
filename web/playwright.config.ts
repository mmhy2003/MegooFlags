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
  // The standalone server, laid out as the web image does (Dockerfile), so tests run what ships.
  webServer: {
    command:
      "cp -r public .next/standalone/ && cp -r .next/static .next/standalone/.next/ && node .next/standalone/server.js",
    url: "http://localhost:3000/sign-in",
    reuseExistingServer: !CI,
    env: {
      PORT: "3000",
      HOSTNAME: "0.0.0.0",
      MEGOOFLAGS_UI_KIT: "1",
      MEGOOFLAGS_API_URL:
        process.env.MEGOOFLAGS_API_URL ?? "http://localhost:8000",
    },
  },
});
