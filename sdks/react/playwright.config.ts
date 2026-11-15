import { defineConfig, devices } from "@playwright/test";

// The end-to-end test runs against a MegooFlags stack (MEGOOFLAGS_E2E_URL,
// MEGOOFLAGS_API_TOKEN) from a page served on another origin.
export default defineConfig({
  testDir: "e2e",
  testMatch: "*.e2e.ts",
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["github"]] : "list",
  use: {
    baseURL: "http://127.0.0.1:4174",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "node --experimental-strip-types e2e/serve.ts",
    url: "http://127.0.0.1:4174",
    reuseExistingServer: !process.env.CI,
  },
});
