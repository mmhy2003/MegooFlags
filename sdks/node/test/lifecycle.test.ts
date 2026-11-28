import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  ErrorCode,
  OpenFeature,
  ProviderEvents,
  ProviderFatalError,
  type Client,
  type EventDetails,
} from "@openfeature/server-sdk";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { MegooFlagsConfig } from "../src/config.js";
import { Cache } from "../src/internal/delivery/cache.js";
import type { Batch } from "../src/internal/events/aggregate.js";
import { MegooFlagsProvider, testHooks } from "../src/provider.js";
import { FakeServer } from "./helpers/fake-server.js";
import { changedProviderRuleset, providerRuleset } from "./helpers/ruleset.js";

const KEY = "mf_server_lifecycle-secret-value";
const STREAM = "/sdk/v1/stream";
const EVENTS = "/sdk/v1/events";

const quiet = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
};

function put(id: string, data: string): string {
  return `event: put\nid: ${id}\ndata: ${data}\n\n`;
}

/** until polls check every 10 ms until it holds or timeoutMs passes. */
async function until(check: () => boolean, timeoutMs = 3_000): Promise<void> {
  const end = Date.now() + timeoutMs;
  while (!check()) {
    if (Date.now() > end) {
      throw new Error("timed out waiting");
    }
    await new Promise((r) => setTimeout(r, 10));
  }
}

interface Seen {
  type: ProviderEvents;
  details: EventDetails | undefined;
}

let server: FakeServer;
let dir: string;
let domains = 0;

beforeEach(async () => {
  testHooks.syncTuning = {
    retryBaseMs: 20,
    retryMaxMs: 200,
    stableAfterMs: 100,
    deadTimeoutMs: 5_000,
  };
  server = await FakeServer.start();
  server.reply(STREAM, { stream: true });
  server.reply(EVENTS, { status: 202 });
  dir = mkdtempSync(join(tmpdir(), "megooflags-lifecycle-"));
});

afterEach(async () => {
  await OpenFeature.close();
  OpenFeature.clearHandlers();
  await server.stop();
  rmSync(dir, { recursive: true, force: true });
  testHooks.syncTuning = undefined;
});

function provider(cfg: Partial<MegooFlagsConfig> = {}): MegooFlagsProvider {
  return new MegooFlagsProvider({
    sdkKey: KEY,
    baseUrl: server.url,
    logger: quiet,
    ...cfg,
  });
}

/** Registration is a provider under its own domain, with its events. */
interface Registration {
  domain: string;
  client: Client;
  events: Seen[];
  /** ready resolves or rejects like OpenFeature.setProviderAndWait. */
  ready: Promise<void>;
  /** next waits for the n-th (1-based) event of type. */
  next(type: ProviderEvents, n?: number): Promise<Seen>;
}

function register(p: MegooFlagsProvider): Registration {
  const domain = `lifecycle-${++domains}`;
  const events: Seen[] = [];
  for (const type of Object.values(ProviderEvents)) {
    OpenFeature.addHandler(type, (details) => {
      if (details?.domain === domain) {
        events.push({ type, details });
      }
    });
  }
  const ready = OpenFeature.setProviderAndWait(domain, p);
  ready.catch(() => {});
  const ofType = (type: ProviderEvents) =>
    events.filter((e) => e.type === type);
  return {
    domain,
    client: OpenFeature.getClient(domain),
    events,
    ready,
    async next(type, n = 1) {
      await until(() => ofType(type).length >= n);
      return ofType(type)[n - 1] as Seen;
    },
  };
}

/** settled reports whether promise settled within ms. */
async function settled(promise: Promise<unknown>, ms: number) {
  let done = false;
  promise.then(
    () => (done = true),
    () => (done = true),
  );
  await new Promise((r) => setTimeout(r, ms));
  return done;
}

describe("initialize", () => {
  it("waits for the first put", async () => {
    const r = register(provider());
    const conn = await server.nextStream();
    expect(await settled(r.ready, 100)).toBe(false);
    conn.write(put("e1", providerRuleset()));
    await r.ready;
    expect(await r.client.getStringValue("string-flag", "x")).toBe("blue");
    expect(r.events.map((e) => e.type)).toEqual([ProviderEvents.Ready]);
  });

  it("times out, keeps retrying and emits READY with the ruleset", async () => {
    const r = register(provider({ initTimeoutMs: 200 }));
    const conn = await server.nextStream();
    const err = await r.ready.then(
      () => undefined,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(ProviderFatalError);
    expect((err as Error).message).toBe("megooflags: no ruleset within 200 ms");
    const d = await r.client.getStringDetails("string-flag", "x");
    expect(d).toMatchObject({
      value: "x",
      errorCode: ErrorCode.PROVIDER_NOT_READY,
    });

    conn.write(put("e1", providerRuleset()));
    await r.next(ProviderEvents.Ready);
    expect(await r.client.getStringValue("string-flag", "x")).toBe("blue");
  });

  it("does not let an initTimeoutMs beyond the timer limit wrap to 1 ms", async () => {
    const r = register(provider({ initTimeoutMs: 2 ** 40 }));
    const conn = await server.nextStream();
    expect(await settled(r.ready, 100)).toBe(false);
    conn.write(put("e1", providerRuleset()));
    await r.ready;
    expect(await r.client.getStringValue("string-flag", "x")).toBe("blue");
  });

  it("is ready from the bootstrap file, then refreshed by the stream", async () => {
    const bootstrapFile = join(dir, "ruleset.json");
    writeFileSync(bootstrapFile, providerRuleset());
    const r = register(provider({ bootstrapFile }));
    await r.ready;
    expect(await r.client.getStringValue("string-flag", "x")).toBe("blue");

    const conn = await server.nextStream();
    conn.write(put("e2", changedProviderRuleset()));
    const changed = await r.next(ProviderEvents.ConfigurationChanged);
    expect(changed.details?.flagsChanged).toEqual([
      "new-flag",
      "string-flag",
      "targeted",
    ]);
    expect(changed.details?.message).toBeTruthy();
    expect(await r.client.getStringValue("string-flag", "x")).toBe("red");

    conn.write(put("e3", changedProviderRuleset()));
    const same = await r.next(ProviderEvents.ConfigurationChanged, 2);
    expect(same.details?.flagsChanged).toEqual([]);
  });

  it("is ready from the cache offline, without any request", async () => {
    const first = register(provider({ cacheDir: dir }));
    (await server.nextStream()).write(put("e2", changedProviderRuleset()));
    await first.ready;
    await OpenFeature.clearProviders();
    const before = server.requests.length;

    const r = register(provider({ cacheDir: dir, offline: true }));
    await r.ready;
    expect(await r.client.getStringValue("string-flag", "x")).toBe("red");
    await OpenFeature.close();
    expect(server.requests.length).toBe(before);
  });

  it("prefers the bootstrap file to the cache", async () => {
    new Cache(dir, KEY).save(changedProviderRuleset(), "e2");
    const bootstrapFile = join(dir, "ruleset.json");
    writeFileSync(bootstrapFile, providerRuleset());
    const r = register(
      provider({ bootstrapFile, cacheDir: dir, offline: true }),
    );
    await r.ready;
    expect(await r.client.getStringValue("string-flag", "x")).toBe("blue");

    const missing = register(
      provider({
        bootstrapFile: join(dir, "missing.json"),
        cacheDir: dir,
        offline: true,
      }),
    );
    await missing.ready;
    expect(await missing.client.getStringValue("string-flag", "x")).toBe("red");
  });

  it("fails offline without a ruleset", async () => {
    const r = register(provider({ cacheDir: dir, offline: true }));
    const err = await r.ready.then(
      () => undefined,
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(Error);
    expect(err).not.toBeInstanceOf(ProviderFatalError);
  });

  it("is fatal for a client key", async () => {
    const r = register(provider({ sdkKey: "mf_client_abc" }));
    await expect(r.ready).rejects.toBeInstanceOf(ProviderFatalError);
    expect(server.requests).toHaveLength(0);
  });

  it("is fatal when the server refuses the key", async () => {
    server = await restartWith({ status: 401 });
    const r = register(provider());
    await expect(r.ready).rejects.toBeInstanceOf(ProviderFatalError);
    const d = await r.client.getStringDetails("string-flag", "x");
    expect(d).toMatchObject({
      value: "x",
      errorCode: ErrorCode.PROVIDER_FATAL,
    });
    await new Promise((r) => setTimeout(r, 100));
    expect(server.received(STREAM)).toHaveLength(1);
  });

  it("fails after shutdown", async () => {
    const bootstrapFile = join(dir, "ruleset.json");
    writeFileSync(bootstrapFile, providerRuleset());
    const p = provider({ bootstrapFile });
    await p.onClose();
    await expect(p.initialize()).rejects.toThrow("shut down");
  });
});

/** restartWith replaces the server with one that answers the stream with reply. */
async function restartWith(reply: { status: number }): Promise<FakeServer> {
  await server.stop();
  const s = await FakeServer.start();
  s.reply(STREAM, reply);
  s.reply(EVENTS, { status: 202 });
  return s;
}

describe("events", () => {
  it("emits the fatal error on revoked and keeps serving", async () => {
    const r = register(provider());
    const conn = await server.nextStream();
    conn.write(put("e2", changedProviderRuleset()));
    await r.ready;
    await r.client.getStringValue("string-flag", "x");

    conn.write("event: revoked\ndata: {}\n\n");
    const ev = await r.next(ProviderEvents.Error);
    expect(ev.details?.errorCode).toBe(ErrorCode.PROVIDER_FATAL);
    expect(ev.details?.message).toBeTruthy();
    expect(ev.details?.message).not.toContain(KEY);
    // The provider keeps serving the last ruleset; the OpenFeature SDK puts
    // a provider that reported PROVIDER_FATAL in the FATAL state and returns
    // the default itself.
    const p = OpenFeature.getProvider(r.domain) as MegooFlagsProvider;
    const direct = await p.resolveStringEvaluation("string-flag", "x", {});
    expect(direct).toMatchObject({ value: "red", variant: "red" });
    const d = await r.client.getStringDetails("string-flag", "x");
    expect(d).toMatchObject({
      value: "x",
      errorCode: ErrorCode.PROVIDER_FATAL,
    });

    await new Promise((r) => setTimeout(r, 200));
    expect(server.received(STREAM)).toHaveLength(1);
    await OpenFeature.close();
    expect(server.received(EVENTS)).toHaveLength(0);
    expect(
      r.events.filter((e) => e.type === ProviderEvents.Error),
    ).toHaveLength(1);
  });

  it("emits STALE on a disconnect and READY on reconnect", async () => {
    const r = register(provider());
    const conn = await server.nextStream();
    conn.write(put("e1", providerRuleset()));
    await r.ready;

    conn.destroy();
    await r.next(ProviderEvents.Stale);
    expect(await r.client.getStringValue("string-flag", "x")).toBe("blue");
    await r.next(ProviderEvents.Ready, 2);
    await new Promise((r) => setTimeout(r, 100));
    expect(r.events.map((e) => e.type)).toEqual([
      ProviderEvents.Ready,
      ProviderEvents.Stale,
      ProviderEvents.Ready,
    ]);
  });

  it("emits nothing after shutdown", async () => {
    const r = register(provider());
    const conn = await server.nextStream();
    conn.write(put("e1", providerRuleset()));
    await r.ready;
    const p = OpenFeature.getProvider(r.domain) as MegooFlagsProvider;
    const seen: string[] = [];
    p.events.addHandler(ProviderEvents.ConfigurationChanged, () =>
      seen.push("changed"),
    );
    await p.onClose();
    conn.write(put("e2", changedProviderRuleset()));
    await new Promise((r) => setTimeout(r, 100));
    expect(seen).toEqual([]);
  });
});

describe("shutdown", () => {
  for (const sendContext of [false, true]) {
    it(`sends the events (sendContext ${sendContext})`, async () => {
      const r = register(provider({ sendContext }));
      (await server.nextStream()).write(put("e1", providerRuleset()));
      await r.ready;
      const alice = { targetingKey: "alice", plan: "pro" };
      for (let i = 0; i < 3; i++) {
        await r.client.getStringValue("string-flag", "x", alice);
      }
      await r.client.getStringValue("string-flag", "x", {
        targetingKey: "bob",
      });
      await r.client.getBooleanValue("bool-flag", false, alice);
      await r.client.getStringValue("missing", "x", alice);
      await OpenFeature.close();

      const batches = server
        .received(EVENTS)
        .map((req) => JSON.parse(req.body) as Batch);
      const counts: Record<string, number> = {};
      const exposures = batches.flatMap((b) => b.exposures);
      for (const c of batches.flatMap((b) => b.counts)) {
        const k = `${c.flag_key}/${c.variant}`;
        counts[k] = (counts[k] ?? 0) + c.count;
      }
      expect(counts).toEqual({
        "string-flag/blue": 4,
        "bool-flag/on": 1,
        "missing/null": 1,
      });
      expect(exposures.map((e) => [e.flag_key, e.variant])).toEqual([
        ["string-flag", "blue"],
        ["string-flag", "blue"],
        ["bool-flag", "on"],
      ]);
      if (sendContext) {
        expect(exposures[0]?.context).toMatchObject({
          key: "alice",
          plan: "pro",
        });
        expect(exposures[1]?.context).toMatchObject({ key: "bob" });
      } else {
        expect(exposures.every((e) => e.context === undefined)).toBe(true);
      }
      expect(server.received(EVENTS)[0]?.headers["authorization"]).toBe(
        `Bearer ${KEY}`,
      );
    });
  }

  it("is idempotent and keeps serving the last ruleset", async () => {
    const r = register(provider());
    (await server.nextStream()).write(put("e1", providerRuleset()));
    await r.ready;
    const p = OpenFeature.getProvider(r.domain) as MegooFlagsProvider;
    await r.client.getStringValue("string-flag", "x");
    await Promise.all([p.onClose(), p.onClose()]);
    await p.onClose();
    expect(server.received(EVENTS)).toHaveLength(1);

    const d = await p.resolveStringEvaluation("string-flag", "x", {
      targetingKey: "later",
    });
    expect(d.value).toBe("blue");
    await p.onClose();
    await new Promise((r) => setTimeout(r, 50));
    expect(server.received(EVENTS)).toHaveLength(1);
  });
});
