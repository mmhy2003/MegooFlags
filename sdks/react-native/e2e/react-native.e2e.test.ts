// Runs the web provider on the React Native platform in Node against a
// running MegooFlags stack: the stream goes through XMLHttpRequest (a shim
// over node:http), evaluate and events through Node's fetch, the cache
// through an in-memory AsyncStorage and the background signal through a fake
// AppState. Set MEGOOFLAGS_E2E_URL (for example http://localhost:8080) and
// MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh prints one) to run it.

import type { Logger } from "@megooflags/js";
import { MegooFlagsWebProvider } from "@megooflags/js";
import { OpenFeature, ProviderEvents } from "@openfeature/web-sdk";
import { afterAll, beforeAll, describe, expect, test, vi } from "vitest";
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
import type { AppStateLike } from "../src/app-state.js";
import { reactNativePlatform } from "../src/index.js";
import { FakeAsyncStorage } from "../test/helpers/memory.js";
import { ShimXhr } from "./xhr-shim.js";

/** An AppState the test drives. */
class FakeAppState implements AppStateLike {
  currentState = "active";
  readonly #listeners = new Set<(state: string) => void>();
  addEventListener(_type: "change", listener: (state: string) => void) {
    this.#listeners.add(listener);
    return { remove: () => void this.#listeners.delete(listener) };
  }
  set(state: string): void {
    this.currentState = state;
    for (const listener of [...this.#listeners]) listener(state);
  }
}

const logs: string[] = [];
const capture =
  (level: string) =>
  (message: string, ...args: unknown[]) => {
    logs.push([`${level}:`, message, ...args.map(String)].join(" "));
  };
const logger: Logger = {
  debug: capture("debug"),
  info: capture("info"),
  warn: capture("warn"),
  error: capture("error"),
};
const count = (line: string) => logs.filter((l) => l.includes(line)).length;

describe.skipIf(!ENABLED)("React Native platform end to end", () => {
  let setup: E2EProject;
  const memoryAsyncStorage = new FakeAsyncStorage();

  beforeAll(async () => {
    setup = await setupProject("reactnative");
  });

  afterAll(async () => {
    await OpenFeature.close();
    if (setup !== undefined) {
      await teardown(setup.project);
    }
  });

  test("evaluates, follows toggles, context and AppState, sends exposures and serves the cache", async () => {
    const { project, flagPath, sdkKey } = setup;
    const client = OpenFeature.getClient(project);
    const read = () => {
      const d = client.getBooleanDetails("e2e-flag", false);
      return { value: d.value, reason: d.reason };
    };
    const fakeAppState = new FakeAppState();
    const provider = new MegooFlagsWebProvider(
      { sdkKey, baseUrl: BASE, flushIntervalMs: 10_000, logger },
      reactNativePlatform({
        appState: fakeAppState,
        asyncStorage: memoryAsyncStorage,
        Xhr: ShimXhr as unknown as typeof XMLHttpRequest,
        logger,
      }),
    );
    let changes = 0;
    provider.events.addHandler(ProviderEvents.ConfigurationChanged, () => {
      changes++;
    });
    await OpenFeature.setProviderAndWait(project, provider, {
      targetingKey: "user-1",
    });

    // Check 1: the server evaluates the flag for the context.
    expect(read().value).toBe(true);

    // Check 2: toggles arrive over the XMLHttpRequest stream within 5 s.
    await until(
      "the stream connected",
      () => count("megooflags: stream connected") > 0,
      5_000,
    );
    await mustApi(200, "POST", `${flagPath}/toggle`, { enabled: false });
    await until("e2e-flag false after the toggle", () => !read().value, 5_000);
    await until("a configuration-changed event", () => changes > 0, 5_000);
    await mustApi(200, "POST", `${flagPath}/toggle`, { enabled: true });
    await until(
      "e2e-flag true after the second toggle",
      () => read().value,
      5_000,
    );

    // AppState: 30 s in the background pause the stream (the grace timer is
    // faked, the network stays real); back to active, it reconnects.
    const streams = ShimXhr.all.length;
    const connected = count("megooflags: stream connected");
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      fakeAppState.set("background");
      vi.advanceTimersByTime(30_000);
    } finally {
      vi.useRealTimers();
    }
    expect(ShimXhr.all.at(-1)?.aborted).toBe(true);
    fakeAppState.set("active");
    await until(
      "the stream reconnected",
      () => count("megooflags: stream connected") > connected,
      5_000,
    );
    expect(ShimXhr.all.length).toBe(streams + 1);

    // Check 3: a new targeting key is evaluated by the server.
    await OpenFeature.setContext(project, { targetingKey: "vip-user" });
    expect(read()).toEqual({ value: false, reason: "TARGETING_MATCH" });

    // Check 4: exposures are sent at shutdown and counted.
    for (let i = 0; i < 5; i++) {
      read();
    }
    await provider.onClose();
    expect(count("megooflags: sent ")).toBeGreaterThan(0);
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

    // Check 5: with the API unreachable, a new provider serves the values
    // the first one left in the same AsyncStorage.
    const offline = new MegooFlagsWebProvider(
      {
        sdkKey,
        baseUrl: `http://127.0.0.1:${await closedPort()}`,
        initTimeoutMs: 1_000,
        logger,
      },
      reactNativePlatform({
        appState: new FakeAppState(),
        asyncStorage: memoryAsyncStorage,
        Xhr: ShimXhr as unknown as typeof XMLHttpRequest,
        logger,
      }),
    );
    await OpenFeature.setProviderAndWait("offline", offline, {
      targetingKey: "vip-user",
    });
    expect(
      OpenFeature.getClient("offline").getBooleanDetails("e2e-flag", true),
    ).toMatchObject({ value: false, reason: "CACHED" });
    await offline.onClose();
  }, 60_000);
});
