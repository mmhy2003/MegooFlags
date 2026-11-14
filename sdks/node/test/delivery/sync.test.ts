import { mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Cache } from "../../src/internal/delivery/cache.js";
import { Store, type Snapshot } from "../../src/internal/delivery/store.js";
import {
  Sync,
  type Status,
  type SyncOptions,
} from "../../src/internal/delivery/sync.js";
import type { Logger } from "../../src/internal/transport/transport.js";
import { FatalError } from "../../src/internal/transport/transport.js";
import { cl, ruleFlag, rulesetJson } from "../helpers/ruleset.js";
import { FakeServer } from "../helpers/fake-server.js";

const KEY = "mf_server_sync-key-value";
const STREAM = "/sdk/v1/stream";
const RULESET = "/sdk/v1/ruleset";

function raw(env: string): string {
  return JSON.stringify({ ...rulesetJson([]), environment: env });
}

function put(id: string, data: string): string {
  return `event: put\nid: ${id}\ndata: ${data}\n\n`;
}

interface Seen {
  changes: { old: Snapshot | undefined; next: Snapshot }[];
  statuses: { status: Status; err: unknown }[];
  warnings: string[];
}

let server: FakeServer;
let dir: string;
let store: Store;
let cache: Cache;
let sync: Sync | undefined;
let seen: Seen;

const logger: Logger = {
  debug: () => {},
  info: () => {},
  warn: (msg: string, ...args: unknown[]) =>
    seen.warnings.push([msg, ...args.map(String)].join(" ")),
  error: () => {},
};

function newSync(opts: Partial<SyncOptions> = {}): Sync {
  sync = new Sync({
    baseUrl: server.url,
    sdkKey: KEY,
    logger,
    store,
    cache,
    onChange: (old, next) => seen.changes.push({ old, next }),
    onStatus: (status, err) => seen.statuses.push({ status, err }),
    tuning: { retryBaseMs: 10, retryMaxMs: 50, pollIntervalMs: 20 },
    ...opts,
  });
  return sync;
}

const statuses = () => seen.statuses.map((s) => s.status);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

beforeEach(async () => {
  server = await FakeServer.start();
  dir = mkdtempSync(join(tmpdir(), "megooflags-sync-"));
  store = new Store();
  cache = new Cache(dir, KEY);
  sync = undefined;
  seen = { changes: [], statuses: [], warnings: [] };
});

afterEach(async () => {
  await sync?.stop();
  await server.stop();
  rmSync(dir, { recursive: true, force: true });
});

describe("Sync.apply", () => {
  it("does not write the cache for a bootstrap ruleset", () => {
    const err = newSync().apply(raw("boot"), "", "bootstrap");
    expect(err).toBeUndefined();
    expect(store.load()?.source).toBe("bootstrap");
    expect(store.load()?.ruleset.environment).toBe("boot");
    expect(readdirSync(dir)).toEqual([]);
    expect(seen.changes).toHaveLength(1);
    expect(seen.changes[0]?.old).toBeUndefined();
  });

  it("keeps the previous ruleset when the new one is invalid", () => {
    const s = newSync();
    s.apply(raw("one"), '"1"', "poll");
    const err = s.apply('{"schema_version":99}', '"2"', "poll");
    expect(err).toBeInstanceOf(Error);
    expect(store.load()?.etag).toBe('"1"');
    expect(seen.changes).toHaveLength(1);
    expect(seen.warnings.join("\n")).toMatch(/invalid ruleset/);
  });

  it("logs each ruleset warning once", () => {
    const bad = JSON.stringify(
      rulesetJson([ruleFlag("re", cl("email", "matches", false, "(?<n>x)"))]),
    );
    const s = newSync();
    s.apply(bad, '"1"', "stream");
    s.apply(bad, '"2"', "stream");
    const regex = seen.warnings.filter((w) => w.includes("regex"));
    expect(regex).toHaveLength(1);
    expect(regex[0]).toContain("flags.re");
  });
});

describe("Sync", () => {
  it("updates the store and the cache from a stream put", async () => {
    server.reply(STREAM, { stream: true });
    newSync().start();
    const conn = await server.nextStream();
    conn.write(put('"e1"', raw("streamed")));
    await vi.waitFor(() => expect(seen.changes).toHaveLength(1));
    expect(store.load()).toMatchObject({ etag: '"e1"', source: "stream" });
    expect(cache.load()).toEqual({
      etag: '"e1"',
      ruleset: JSON.parse(raw("streamed")),
    });
    expect(statuses()).toEqual(["ready"]);
  });

  it("sends the store's ETag as Last-Event-ID", async () => {
    server.reply(STREAM, { stream: true });
    const s = newSync();
    s.apply(raw("boot"), '"boot-etag"', "bootstrap");
    s.start();
    const conn = await server.nextStream();
    expect(conn.request.headers["last-event-id"]).toBe('"boot-etag"');
  });

  it("keeps the previous ruleset and the stream after an invalid put", async () => {
    server.reply(STREAM, { stream: true });
    newSync().start();
    const conn = await server.nextStream();
    conn.write(put('"1"', raw("one")));
    conn.write(put('"2"', "{nope"));
    conn.write(put('"3"', raw("three")));
    await vi.waitFor(() => expect(seen.changes).toHaveLength(2));
    expect(store.load()?.etag).toBe('"3"');
    expect(server.received(STREAM)).toHaveLength(1);
  });

  it("polls after two stream failures and stops polling on reconnect", async () => {
    server.reply(STREAM, { status: 500 }, { status: 500 }, { stream: true });
    server.reply(RULESET, {
      status: 200,
      headers: { ETag: '"p1"' },
      body: raw("polled"),
    });
    newSync({
      tuning: { retryBaseMs: 60, retryMaxMs: 60, pollIntervalMs: 10 },
    }).start();
    await server.nextStream();
    await vi.waitFor(() => expect(statuses()).toContain("ready"));
    const polls = server.received(RULESET);
    expect(polls.length).toBeGreaterThanOrEqual(1);
    // The first poll came after the second stream failure.
    const streams = server.received(STREAM);
    expect(polls[0]!.at).toBeGreaterThanOrEqual(streams[1]!.at);
    expect(store.load()?.source).toBe("poll");
    expect(cache.load()?.etag).toBe('"p1"');
    expect(statuses()).toEqual(["stale", "stale", "ready"]);
    const count = polls.length;
    await sleep(100);
    expect(server.received(RULESET)).toHaveLength(count);
  });

  it("does not poll after a single stream failure", async () => {
    server.reply(STREAM, { status: 500 }, { stream: true });
    server.reply(RULESET, { status: 200, body: raw("polled") });
    newSync().start();
    await server.nextStream();
    await sleep(50);
    expect(server.received(RULESET)).toHaveLength(0);
  });

  it("keeps polls at least the poll interval apart", async () => {
    server.reply(RULESET, { status: 304 });
    newSync({ disableStream: true, tuning: { pollIntervalMs: 100 } }).start();
    await sleep(350);
    const at = server.received(RULESET).map((r) => r.at);
    expect(at.length).toBeGreaterThanOrEqual(3);
    expect(at.length).toBeLessThanOrEqual(4);
    at.slice(1).forEach((t, i) => {
      expect(t - at[i]!).toBeGreaterThanOrEqual(95);
    });
  });

  it("waits the Retry-After of a poll", async () => {
    server.reply(
      RULESET,
      { status: 429, headers: { "Retry-After": "1" } },
      { status: 304 },
    );
    newSync({ disableStream: true }).start();
    await vi.waitFor(() => expect(server.received(RULESET)).toHaveLength(2), {
      timeout: 3000,
    });
    const [first, second] = server.received(RULESET);
    expect(second!.at - first!.at).toBeGreaterThanOrEqual(950);
  });

  it("with disableStream polls only and reports stale then ready", async () => {
    server.reply(
      RULESET,
      { status: 500 },
      { status: 200, headers: { ETag: '"v1"' }, body: raw("polled") },
      { status: 304 },
    );
    newSync({ disableStream: true }).start();
    await vi.waitFor(() =>
      expect(statuses().slice(0, 3)).toEqual(["stale", "ready", "ready"]),
    );
    expect(server.received(STREAM)).toHaveLength(0);
    expect(store.load()?.etag).toBe('"v1"');
  });

  it("reports revoked as fatal once and stops", async () => {
    server.reply(STREAM, { stream: true });
    server.reply(RULESET, { status: 200, body: raw("polled") });
    newSync().start();
    const conn = await server.nextStream();
    conn.write("event: revoked\ndata: {}\n\n");
    await vi.waitFor(() => expect(statuses()).toContain("fatal"));
    await sleep(80);
    expect(statuses()).toEqual(["ready", "fatal"]);
    const fatal = seen.statuses[1]?.err;
    expect(fatal).toBeInstanceOf(FatalError);
    expect((fatal as FatalError).reason).toBe("revoked");
    expect(server.received(STREAM)).toHaveLength(1);
    expect(server.received(RULESET)).toHaveLength(0);
  });

  it("reports a 403 on a poll as fatal and stops polling", async () => {
    server.reply(RULESET, { status: 403 });
    newSync({ disableStream: true }).start();
    await vi.waitFor(() => expect(statuses()).toContain("fatal"));
    await sleep(80);
    expect(statuses()).toEqual(["fatal"]);
    expect((seen.statuses[0]?.err as FatalError).status).toBe(403);
    expect(server.received(RULESET)).toHaveLength(1);
  });

  it("stops the stream when polling hits a fatal status", async () => {
    server.reply(STREAM, { status: 500 });
    server.reply(RULESET, { status: 401 });
    newSync({
      tuning: { retryBaseMs: 30, retryMaxMs: 30, pollIntervalMs: 10 },
    }).start();
    await vi.waitFor(() => expect(statuses()).toContain("fatal"));
    const streams = server.received(STREAM).length;
    await sleep(100);
    expect(server.received(STREAM)).toHaveLength(streams);
    expect(statuses().filter((s) => s === "fatal")).toHaveLength(1);
  });

  it("stop is safe before start and idempotent", async () => {
    const s = newSync();
    await s.stop();
    await s.stop();
  });

  it("stop ends the loops and silences callbacks", async () => {
    server.reply(STREAM, { stream: true });
    const s = newSync();
    s.start();
    const conn = await server.nextStream();
    const started = Date.now();
    await Promise.all([s.stop(), s.stop()]);
    expect(Date.now() - started).toBeLessThan(500);
    await conn.closed;
    const before = seen.statuses.length;
    s.start();
    await sleep(50);
    expect(seen.statuses).toHaveLength(before);
    expect(server.received(STREAM)).toHaveLength(1);
  });
});
