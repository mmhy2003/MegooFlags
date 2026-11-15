import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MegooFlagsWebProvider } from "@megooflags/js";
import { reactNativePlatform } from "../src/platform.js";
import { loadAsyncStorage } from "../src/storage.js";
import {
  EVALUATE,
  evaluated,
  FakeServer,
  STREAM,
} from "../../js/test/helpers/fake-server.js";
import { FetchXhr } from "./helpers/fetch-xhr.js";
import { FakeAsyncStorage, silent } from "./helpers/memory.js";

const KEY = "mf_client_secret123";

/** A React Native platform without a storage of its own. */
const platform = () =>
  reactNativePlatform({
    appState: {
      currentState: "active",
      addEventListener: () => ({ remove: () => {} }),
    },
    asyncStorage: null,
    Xhr: FetchXhr as unknown as typeof XMLHttpRequest,
    logger: silent,
  });

let server: FakeServer;

beforeEach(async () => {
  server = await FakeServer.start();
});

afterEach(async () => {
  await server.stop();
});

describe("loadAsyncStorage", () => {
  it("returns null when the module cannot be loaded (Node)", () => {
    expect(loadAsyncStorage()).toBeNull();
  });
});

describe("an AsyncStorage passed as storage", () => {
  it("keeps the values for a provider that cannot reach the server", async () => {
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
    const storage = new FakeAsyncStorage();

    const online = new MegooFlagsWebProvider(
      {
        sdkKey: KEY,
        baseUrl: server.url,
        storage,
        logger: silent,
      },
      platform(),
    );
    await online.initialize({ targetingKey: "user-1" });
    await vi.waitFor(() => expect(storage.items.size).toBe(1));
    await online.onClose();
    await server.stop();

    const offline = new MegooFlagsWebProvider(
      {
        sdkKey: KEY,
        baseUrl: server.url,
        storage,
        initTimeoutMs: 500,
        logger: silent,
      },
      platform(),
    );
    await offline.initialize({ targetingKey: "user-1" });
    const details = offline.resolveBooleanEvaluation("f", false);
    await offline.onClose();

    expect(details.value).toBe(true);
    expect(details.reason).toBe("CACHED");
  });
});
