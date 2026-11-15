// Runs <MegooFlagsProvider> in Chromium against a running MegooFlags stack,
// from a page on another origin than the API (so CORS is real), and checks
// what the page renders. Set MEGOOFLAGS_E2E_URL (for example
// http://localhost:8080) and MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh
// prints one) to run it.

import { expect, test, type Page } from "@playwright/test";
import {
  BASE,
  ENABLED,
  closedPort,
  mustApi,
  setupProject,
  teardown,
  until,
  type E2EProject,
} from "../../js/e2e/helpers/management.js";
import type { MegooReactWindow, MountProps } from "./page/entry.js";

test.skip(!ENABLED, "MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are not set");

let setup: E2EProject;

test.beforeAll(async () => {
  setup = await setupProject("react");
});

test.afterAll(async () => {
  if (setup !== undefined) {
    await teardown(setup.project);
  }
});

const mount = (page: Page, props: MountProps) =>
  page.evaluate(
    (p) => (window as unknown as MegooReactWindow).megooReact.mount(p),
    props,
  );

test("renders evaluated values, follows toggles and context, sends exposures and serves the cache", async ({
  page,
}) => {
  test.setTimeout(60_000);
  const { flagPath, sdkKey } = setup;
  const urls: string[] = [];
  page.on("request", (req) => urls.push(req.url()));
  await page.goto("/");
  await page.waitForFunction(() => "megooReact" in window);
  const flag = page.getByTestId("flag");

  // Check 1: the server evaluates the flag for the context (user-1 falls
  // through: reason DEFAULT; before the provider is ready the hook renders
  // the default value with reason ERROR).
  await mount(page, {
    sdkKey,
    baseUrl: BASE,
    context: { targetingKey: "user-1" },
  });
  await expect(flag).toHaveAttribute("data-reason", "DEFAULT");
  await expect(flag).toHaveAttribute("data-value", "true");

  // Check 2: toggles arrive over SSE and re-render within 5 s.
  await mustApi(200, "POST", `${flagPath}/toggle`, { enabled: false });
  await expect(flag).toHaveAttribute("data-value", "false", { timeout: 5_000 });
  await mustApi(200, "POST", `${flagPath}/toggle`, { enabled: true });
  await expect(flag).toHaveAttribute("data-value", "true", { timeout: 5_000 });

  // Check 3: a new context prop is evaluated by the server.
  await page.evaluate(() =>
    (window as unknown as MegooReactWindow).megooReact.setContext({
      targetingKey: "vip-user",
    }),
  );
  await expect(flag).toHaveAttribute("data-value", "false");
  await expect(flag).toHaveAttribute("data-reason", "TARGETING_MATCH");

  // Check 4: unmounting closes the provider, which sends the exposures.
  await page.evaluate(() =>
    (window as unknown as MegooReactWindow).megooReact.unmount(),
  );
  await until(
    "the logger captured megooflags: sent",
    async () =>
      (
        await page.evaluate(
          () => (window as unknown as MegooReactWindow).megooLogs,
        )
      ).some((l) => l.includes("megooflags: sent ")),
    5_000,
  );
  // The API writes its counts every 10 s, so allow one interval and a margin.
  await until(
    "evaluations total >= 1",
    async () => {
      const out = await mustApi(
        200,
        "GET",
        `${flagPath}/evaluations?range=24h`,
      );
      return typeof out.total === "number" && out.total >= 1;
    },
    15_000,
    500,
  );

  // Check 5: with the API unreachable, a new mount serves the cache that the
  // first provider left in localStorage.
  await mount(page, {
    sdkKey,
    baseUrl: `http://127.0.0.1:${await closedPort()}`,
    context: { targetingKey: "vip-user" },
    initTimeoutMs: 1_000,
  });
  await expect(flag).toHaveAttribute("data-reason", "CACHED");
  await expect(flag).toHaveAttribute("data-value", "false");
  await page.evaluate(() =>
    (window as unknown as MegooReactWindow).megooReact.unmount(),
  );

  // The key travels in a header only, never in a URL.
  expect(urls.length).toBeGreaterThan(0);
  expect(urls.filter((u) => u.includes(sdkKey))).toEqual([]);
});
