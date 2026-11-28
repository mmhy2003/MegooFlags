// Runs the web provider in Chromium against a running MegooFlags stack, from
// a page on another origin than the API (so CORS is real). Set
// MEGOOFLAGS_E2E_URL (for example http://localhost:8080) and
// MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh prints one) to run it.

import { expect, test } from "@playwright/test";
import {
  BASE,
  ENABLED,
  closedPort,
  mustApi,
  setupProject,
  teardown,
  until,
  type E2EProject,
} from "./helpers/management.js";
import type { MegooWindow } from "./page/entry.js";

test.skip(!ENABLED, "MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are not set");

let setup: E2EProject;

test.beforeAll(async () => {
  setup = await setupProject("js");
});

test.afterAll(async () => {
  if (setup !== undefined) {
    await teardown(setup.project);
  }
});

test("evaluates, follows toggles and context, sends exposures and serves the cache", async ({
  page,
}) => {
  test.setTimeout(60_000);
  const { project, flagPath, sdkKey } = setup;
  const urls: string[] = [];
  page.on("request", (req) => urls.push(req.url()));
  await page.goto("/");
  await page.waitForFunction(() => "megoo" in window);

  /** read evaluates e2e-flag in the page for domain. */
  const read = (domain: string) =>
    page.evaluate((d) => {
      const w = window as unknown as MegooWindow;
      const details = w.megoo.OpenFeature.getClient(d).getBooleanDetails(
        "e2e-flag",
        false,
      );
      return { value: details.value, reason: details.reason };
    }, domain);

  await page.evaluate(
    async ({ sdkKey, baseUrl, domain }) => {
      const w = window as unknown as MegooWindow;
      const { OpenFeature, ProviderEvents, MegooFlagsWebProvider, logger } =
        w.megoo;
      const provider = new MegooFlagsWebProvider({
        sdkKey,
        baseUrl,
        flushIntervalMs: 10_000,
        logger,
      });
      w.megooState.provider = provider;
      w.megooState.changes = 0;
      provider.events.addHandler(ProviderEvents.ConfigurationChanged, () => {
        w.megooState.changes = (w.megooState.changes as number) + 1;
      });
      await OpenFeature.setProviderAndWait(domain, provider, {
        targetingKey: "user-1",
      });
    },
    { sdkKey, baseUrl: BASE, domain: project },
  );

  // Check 1: the server evaluates the flag for the context.
  expect((await read(project)).value).toBe(true);

  // Check 2: toggles arrive over SSE within 5 s.
  await mustApi(200, "POST", `${flagPath}/toggle`, { enabled: false });
  await until(
    "e2e-flag false after the toggle",
    async () => (await read(project)).value === false,
    5_000,
  );
  await until(
    "a configuration-changed event",
    async () =>
      page.evaluate(
        () =>
          ((window as unknown as MegooWindow).megooState.changes as number) > 0,
      ),
    5_000,
  );
  await mustApi(200, "POST", `${flagPath}/toggle`, { enabled: true });
  await until(
    "e2e-flag true after the second toggle",
    async () => (await read(project)).value === true,
    5_000,
  );

  // Check 3: a new targeting key is evaluated by the server.
  await page.evaluate(
    (d) =>
      (window as unknown as MegooWindow).megoo.OpenFeature.setContext(d, {
        targetingKey: "vip-user",
      }),
    project,
  );
  expect(await read(project)).toEqual({
    value: false,
    reason: "TARGETING_MATCH",
  });

  // Check 4: exposures are sent at shutdown and counted.
  for (let i = 0; i < 5; i++) {
    await read(project);
  }
  await page.evaluate(async () => {
    const w = window as unknown as MegooWindow;
    const provider = w.megooState.provider as { onClose(): Promise<void> };
    await provider.onClose();
  });
  const logs = await page.evaluate(
    () => (window as unknown as MegooWindow).megooLogs,
  );
  expect(logs.some((l) => l.includes("megooflags: sent "))).toBe(true);
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
    10_000,
    500,
  );

  // Check 5: with the API unreachable, a new provider serves the cache that
  // the first one left in localStorage.
  await page.evaluate(
    async ({ sdkKey, baseUrl }) => {
      const w = window as unknown as MegooWindow;
      const { OpenFeature, MegooFlagsWebProvider, logger } = w.megoo;
      const offline = new MegooFlagsWebProvider({
        sdkKey,
        baseUrl,
        initTimeoutMs: 1_000,
        logger,
      });
      w.megooState.offline = offline;
      await OpenFeature.setProviderAndWait("offline", offline, {
        targetingKey: "vip-user",
      });
    },
    { sdkKey, baseUrl: `http://127.0.0.1:${await closedPort()}` },
  );
  expect(await read("offline")).toEqual({ value: false, reason: "CACHED" });
  await page.evaluate(async () => {
    const w = window as unknown as MegooWindow;
    await (w.megooState.offline as { onClose(): Promise<void> }).onClose();
  });

  // The key travels in a header only, never in a URL.
  expect(urls.length).toBeGreaterThan(0);
  expect(urls.filter((u) => u.includes(sdkKey))).toEqual([]);
});
