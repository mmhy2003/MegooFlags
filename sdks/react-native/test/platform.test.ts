import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MegooFlagsWebProvider, type Logger } from "@megooflags/js";
import type { AppStateLike } from "../src/app-state.js";
import {
  EVALUATE,
  evaluated,
  FakeServer,
  STREAM,
} from "../../js/test/helpers/fake-server.js";
import { FetchXhr } from "./helpers/fetch-xhr.js";
import { FakeAsyncStorage, silent } from "./helpers/memory.js";

const KEY = "mf_client_secret123";

class FakeAppState implements AppStateLike {
  currentState = "active";
  readonly listeners = new Set<(s: string) => void>();
  addEventListener(_type: "change", l: (s: string) => void) {
    this.listeners.add(l);
    return { remove: () => this.listeners.delete(l) };
  }
  emit(state: string) {
    this.currentState = state;
    for (const l of [...this.listeners]) l(state);
  }
}

function capturing(): Logger & { warnings: string[] } {
  const warnings: string[] = [];
  return { ...silent, warnings, warn: (m: string) => warnings.push(m) };
}

/** A fresh module, so the once-per-process warning starts unsent. */
async function load() {
  vi.resetModules();
  return import("../src/platform.js");
}

const WARNING =
  "megooflags: @react-native-async-storage/async-storage is not installed; values are not cached across restarts";

describe("reactNativePlatform storage", () => {
  it("warns once per process when there is no AsyncStorage", async () => {
    const { reactNativePlatform } = await load();
    const logger = capturing();
    const app = new FakeAppState();

    const a = reactNativePlatform({
      appState: app,
      asyncStorage: null,
      logger,
    });
    const b = reactNativePlatform({ appState: app, logger });

    expect(a.storage).toBeNull();
    expect(b.storage).toBeNull();
    expect(logger.warnings).toEqual([WARNING]);
  });

  it("uses the AsyncStorage it is given, without a warning", async () => {
    const { reactNativePlatform } = await load();
    const logger = capturing();
    const storage = new FakeAsyncStorage();

    const p = reactNativePlatform({
      appState: new FakeAppState(),
      asyncStorage: storage,
      logger,
    });

    expect(p.storage).toBe(storage);
    expect(logger.warnings).toEqual([]);
  });
});

describe("reactNativePlatform background", () => {
  let server: FakeServer;

  beforeEach(async () => {
    server = await FakeServer.start();
    server.reply(
      EVALUATE,
      evaluated([
        {
          key: "f",
          value: true,
          variant: "true",
          reason: "TARGETING_MATCH",
          error_code: null,
        },
      ]),
    );
    server.reply(STREAM, { stream: true });
  });

  afterEach(async () => {
    vi.useRealTimers();
    await server.stop();
  });

  it("closes the stream after 30 s in the background and reopens it on active", async () => {
    const { reactNativePlatform } = await load();
    const app = new FakeAppState();
    vi.useFakeTimers({
      toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
    });
    const provider = new MegooFlagsWebProvider(
      { sdkKey: KEY, baseUrl: server.url, logger: silent },
      reactNativePlatform({
        appState: app,
        asyncStorage: new FakeAsyncStorage(),
        Xhr: FetchXhr as unknown as typeof XMLHttpRequest,
        logger: silent,
      }),
    );
    await provider.initialize({ targetingKey: "user-1" });
    const first = await server.nextStream();

    app.emit("background");
    await vi.advanceTimersByTimeAsync(29_999);
    const early = await Promise.race([
      first.closed.then(() => "closed"),
      new Promise((r) => setImmediate(() => r("open"))),
    ]);
    expect(early).toBe("open");

    await vi.advanceTimersByTimeAsync(1);
    await first.closed;

    app.emit("active");
    const second = await server.nextStream();
    expect(second.request.path).toBe(STREAM);

    await provider.onClose();
  });
});
