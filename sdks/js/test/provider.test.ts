import {
  ClientProviderEvents,
  ErrorCode,
  OpenFeature,
  ProviderEvents,
  ProviderStatus,
  type EventDetails,
} from "@openfeature/web-sdk";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MegooFlagsWebProvider, type MegooFlagsOptions } from "../src/index.js";
import type { EvaluateResult } from "../src/internal/options.js";
import type { Logger } from "../src/internal/transport.js";
import type { Platform, StorageLike } from "../src/platform.js";
import { webStreamOpener } from "../src/web-platform.js";
import {
  EVALUATE,
  EVENTS,
  evaluated,
  FakeServer,
  type Received,
  type Scripted,
  STREAM,
} from "./helpers/fake-server.js";

const KEY = "mf_client_secret123";

const silent: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
};

const result = (o: Partial<EvaluateResult> = {}): EvaluateResult => ({
  key: "f",
  value: true,
  variant: "true",
  reason: "TARGETING_MATCH",
  error_code: null,
  error_message: null,
  ...o,
});

class MemoryStorage implements StorageLike {
  readonly items = new Map<string, string>();
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
}

const platform = (): Platform => ({
  openStream: webStreamOpener(globalThis.fetch.bind(globalThis)),
  storage: new MemoryStorage(),
  background: null,
});

/** contextKey is the `key` of an evaluate request's context. */
const contextKey = (req: Received) =>
  (JSON.parse(req.body) as { context: { key?: string } }).context.key;

/** nextEvent resolves with the details of the next event of this type. */
function nextEvent(event: ClientProviderEvents): Promise<EventDetails> {
  return new Promise((resolve) => {
    const handler = (details?: EventDetails) => {
      OpenFeature.removeHandler(event, handler);
      resolve(details as EventDetails);
    };
    OpenFeature.addHandler(event, handler);
  });
}

let server: FakeServer;

function provider(o: Partial<MegooFlagsOptions> = {}): MegooFlagsWebProvider {
  return new MegooFlagsWebProvider(
    { sdkKey: KEY, baseUrl: server.url, logger: silent, ...o },
    platform(),
  );
}

beforeEach(async () => {
  OpenFeature.setLogger(silent);
  server = await FakeServer.start();
});

afterEach(async () => {
  await OpenFeature.clearProviders();
  await OpenFeature.setContext({});
  await server.stop();
});

describe("MegooFlagsWebProvider", () => {
  it("refuses a server key at construction", () => {
    expect(
      () =>
        new MegooFlagsWebProvider(
          { sdkKey: "mf_server_secret", baseUrl: "https://flags.example.com" },
          platform(),
        ),
    ).toThrow("megooflags: a server key must never ship in an app");
  });

  it("runs on the client and is named MegooFlags", () => {
    const p = provider();
    expect(p.runsOn).toBe("client");
    expect(p.metadata.name).toBe("MegooFlags");
  });

  it("serves the evaluated value with its reason once ready", async () => {
    server.reply(EVALUATE, evaluated([result()], { etag: '"e1"' }));
    server.reply(STREAM, { stream: true });

    await OpenFeature.setProviderAndWait(provider(), {
      targetingKey: "user-1",
    });
    const details = OpenFeature.getClient().getBooleanDetails("f", false);

    expect(details).toMatchObject({
      value: true,
      variant: "true",
      reason: "TARGETING_MATCH",
    });
    expect(details.errorCode).toBeUndefined();
    expect(contextKey(server.received(EVALUATE)[0]!)).toBe("user-1");
  });

  it("is ready at once with bootstrap, then reports the flags the network changed", async () => {
    server.reply(
      EVALUATE,
      evaluated([result(), result({ key: "g", value: "x", variant: "x" })], {
        delayMs: 100,
      }),
    );
    server.reply(STREAM, { stream: true });
    const p = provider({
      bootstrap: {
        results: [
          result({ value: false, variant: "false", reason: "STATIC" }),
          result({ key: "g", value: "x", variant: "x" }),
        ],
      },
    });
    const changed = nextEvent(ProviderEvents.ConfigurationChanged);

    await OpenFeature.setProviderAndWait(p, { targetingKey: "user-1" });
    const client = OpenFeature.getClient();
    expect(client.getBooleanDetails("f", true)).toMatchObject({
      value: false,
      reason: "STATIC",
    });

    expect((await changed).flagsChanged).toEqual(["f"]);
    expect(client.getBooleanValue("f", false)).toBe(true);
  });

  it("follows a stream change with a new value and a configuration change", async () => {
    server.reply(
      EVALUATE,
      evaluated([result()], { etag: '"e1"' }),
      evaluated([result({ value: false, variant: "false" })], {
        etag: '"e2"',
      }),
    );
    server.reply(STREAM, { stream: true });
    await OpenFeature.setProviderAndWait(provider(), {
      targetingKey: "user-1",
    });
    const client = OpenFeature.getClient();
    expect(client.getBooleanValue("f", false)).toBe(true);
    const stream = await server.nextStream();
    const changed = nextEvent(ProviderEvents.ConfigurationChanged);

    stream.send("event: changed\nid: e2\ndata: {}\n\n");

    expect((await changed).flagsChanged).toEqual(["f"]);
    expect(client.getBooleanValue("f", true)).toBe(false);
  });

  it("reconciles a new targeting key, serving defaults until its values arrive", async () => {
    let release: (() => void) | undefined;
    server.reply(EVALUATE, (req): Scripted | Promise<Scripted> => {
      if (contextKey(req) === "a") return evaluated([result()]);
      return new Promise((resolve) => {
        release = () =>
          resolve(evaluated([result({ value: false, variant: "false" })]));
      });
    });
    server.reply(STREAM, { stream: true });
    await OpenFeature.setProviderAndWait(provider(), { targetingKey: "a" });
    const client = OpenFeature.getClient();
    expect(client.getBooleanValue("f", false)).toBe(true);
    const seen: string[] = [];
    client.addHandler(ProviderEvents.Reconciling, () =>
      seen.push("reconciling"),
    );
    client.addHandler(ProviderEvents.ContextChanged, () =>
      seen.push("contextChanged"),
    );

    const switched = OpenFeature.setContext({ targetingKey: "b" });
    await vi.waitFor(() => expect(release).toBeDefined());

    expect(seen).toEqual(["reconciling"]);
    expect(client.getBooleanDetails("f", true)).toMatchObject({
      value: true,
      errorCode: ErrorCode.PROVIDER_NOT_READY,
    });

    release!();
    await switched;

    expect(seen).toEqual(["reconciling", "contextChanged"]);
    expect(client.getBooleanDetails("f", true)).toMatchObject({
      value: false,
      reason: "TARGETING_MATCH",
    });
  });

  it("turns a refused key into a fatal status and stops calling the server", async () => {
    server.reply(EVALUATE, { status: 401 });
    server.reply(STREAM, { stream: true });

    await expect(
      OpenFeature.setProviderAndWait(provider(), { targetingKey: "user-1" }),
    ).rejects.toMatchObject({ code: ErrorCode.PROVIDER_FATAL });
    const client = OpenFeature.getClient();

    expect(client.providerStatus).toBe(ProviderStatus.FATAL);
    expect(client.getBooleanDetails("f", true)).toMatchObject({
      value: true,
      errorCode: ErrorCode.PROVIDER_FATAL,
    });
    await new Promise((r) => setTimeout(r, 200));
    expect(server.received(EVALUATE)).toHaveLength(1);
    expect(server.received(STREAM)).toHaveLength(0);
    expect(server.received(EVENTS)).toHaveLength(0);
  });

  it("emits a fatal error when the stream revokes the key", async () => {
    server.reply(EVALUATE, evaluated([result()]));
    server.reply(STREAM, { stream: true });
    await OpenFeature.setProviderAndWait(provider(), {
      targetingKey: "user-1",
    });
    const stream = await server.nextStream();
    const error = nextEvent(ProviderEvents.Error);

    stream.send("event: revoked\ndata: {}\n\n");

    expect(await error).toMatchObject({
      errorCode: ErrorCode.PROVIDER_FATAL,
    });
    expect(OpenFeature.getClient().providerStatus).toBe(ProviderStatus.FATAL);
  });

  it("sends one exposure for three reads of a flag on close", async () => {
    server.reply(EVALUATE, evaluated([result()]));
    server.reply(STREAM, { stream: true });
    server.reply(EVENTS, { status: 202 });
    await OpenFeature.setProviderAndWait(provider(), {
      targetingKey: "user-1",
    });
    const client = OpenFeature.getClient();

    client.getBooleanValue("f", false);
    client.getBooleanValue("f", false);
    client.getBooleanValue("f", false);
    client.getBooleanValue("missing", false);
    await OpenFeature.close();

    expect(server.bodies(EVENTS)).toEqual([
      {
        exposures: [
          {
            flag_key: "f",
            variant: "true",
            reason: "TARGETING_MATCH",
            timestamp: expect.any(String) as string,
          },
        ],
      },
    ]);
  });
});
