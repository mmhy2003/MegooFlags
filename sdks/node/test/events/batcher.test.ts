import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Batch, Evaluation } from "../../src/internal/events/aggregate.js";
import {
  Batcher,
  type BatcherOptions,
} from "../../src/internal/events/batcher.js";
import {
  FatalError,
  sdkUrl,
  USER_AGENT,
} from "../../src/internal/transport/transport.js";
import { FakeServer, type Received } from "../helpers/fake-server.js";

const KEY = "mf_server_batcher-key-value";
const EVENTS = "/sdk/v1/events";

let server: FakeServer;
let batcher: Batcher | undefined;
let releases: (() => void)[];

function newBatcher(opts: Partial<BatcherOptions> = {}): Batcher {
  batcher = new Batcher({
    url: sdkUrl(server.url, "events"),
    sdkKey: KEY,
    flushIntervalMs: 50,
    retryBaseMs: 10,
    retryMaxMs: 40,
    ...opts,
  });
  batcher.start();
  return batcher;
}

function ev(flag: string, ctxKey: string, context?: object): Evaluation {
  return {
    flagKey: flag,
    variant: "on",
    served: true,
    reason: "STATIC",
    contextKey: ctxKey,
    ...(context === undefined ? {} : { context }),
    at: Date.now(),
  };
}

const body = (r: Received) => JSON.parse(r.body) as Batch;
const items = (b: Batch) => b.exposures.length + b.counts.length;
const sent = () => server.received(EVENTS);
const accept = () => server.reply(EVENTS, { status: 202 });

/** respond answers each events request by its body. */
function respond(status: (b: Batch, n: number) => number | Promise<number>) {
  let n = 0;
  server.reply(EVENTS, async (r) => ({ status: await status(body(r), ++n) }));
}

/** totals sums what the server accepted (2xx). */
function totals(accepted: Batch[]) {
  const counts: Record<string, number> = {};
  let exposures = 0;
  for (const b of accepted) {
    exposures += b.exposures.length;
    for (const c of b.counts) {
      counts[c.flag_key] = (counts[c.flag_key] ?? 0) + c.count;
    }
  }
  return { exposures, counts };
}

beforeEach(async () => {
  server = await FakeServer.start();
  releases = [];
});

afterEach(async () => {
  await batcher?.stop();
  batcher = undefined;
  for (const release of releases) {
    release();
  }
  await server.stop();
});

describe("Batcher", () => {
  it("posts JSON with the bearer key and the user agent", async () => {
    accept();
    const b = newBatcher();
    b.record(ev("f", "u1"));
    await vi.waitFor(() => expect(sent()).toHaveLength(1));
    const r = sent()[0]!;
    expect(r.method).toBe("POST");
    expect(r.headers["authorization"]).toBe(`Bearer ${KEY}`);
    expect(r.headers["user-agent"]).toBe(USER_AGENT);
    expect(r.headers["content-type"]).toBe("application/json");
    expect(r.url).not.toContain(KEY);
    const got = body(r);
    expect(got.exposures).toEqual([
      expect.objectContaining({ flag_key: "f", variant: "on" }),
    ]);
    expect(got.counts).toEqual([
      expect.objectContaining({ flag_key: "f", variant: "on", count: 1 }),
    ]);
  });

  it("flushes on the interval", async () => {
    accept();
    const b = newBatcher();
    b.record(ev("f", "u1"));
    expect(sent()).toHaveLength(0);
    await vi.waitFor(() => expect(sent()).toHaveLength(1));
    b.record(ev("g", "u1"));
    await vi.waitFor(() => expect(sent()).toHaveLength(2));
    expect(totals(sent().map(body))).toEqual({
      exposures: 2,
      counts: { f: 1, g: 1 },
    });
  });

  it("flushes when a list reaches 1000 items", async () => {
    accept();
    const b = newBatcher({ flushIntervalMs: 3_600_000 });
    for (let i = 0; i < 999; i++) {
      b.record(ev("f", `u${i}`));
    }
    await new Promise((r) => setTimeout(r, 50));
    expect(sent()).toHaveLength(0);
    b.record(ev("f", "u999"));
    await vi.waitFor(() => expect(sent()).toHaveLength(1));
    expect(body(sent()[0]!).exposures).toHaveLength(1000);
  });

  it("runs one flush at a time", async () => {
    let inFlight = 0;
    let most = 0;
    respond(async () => {
      most = Math.max(most, ++inFlight);
      await new Promise((r) => setTimeout(r, 30));
      inFlight--;
      return 202;
    });
    const b = newBatcher({ flushIntervalMs: 5 });
    for (let i = 0; i < 2500; i++) {
      b.record(ev(`f${i}`, "u"));
    }
    expect(await b.shutdown(5_000)).toBe(true);
    expect(sent().length).toBeGreaterThanOrEqual(3);
    expect(most).toBe(1);
  });

  it("sends everything on shutdown, once", async () => {
    accept();
    const b = newBatcher({ flushIntervalMs: 3_600_000 });
    b.record(ev("f", "u1"));
    b.record(ev("f", "u2"));
    b.record(ev("g", "u1"));
    expect(await b.shutdown(5_000)).toBe(true);
    expect(totals(sent().map(body))).toEqual({
      exposures: 3,
      counts: { f: 2, g: 1 },
    });
    expect(await b.shutdown(5_000)).toBe(true);
    expect(sent()).toHaveLength(1);
  });

  it("gives up at the shutdown deadline", async () => {
    server.reply(
      EVENTS,
      () =>
        new Promise((resolve) => releases.push(() => resolve({ status: 202 }))),
    );
    const b = newBatcher({ flushIntervalMs: 3_600_000 });
    b.record(ev("f", "u1"));
    const start = Date.now();
    expect(await b.shutdown(200)).toBe(false);
    expect(Date.now() - start).toBeLessThan(1_000);
  });

  it("does not wait for a retry delay that ends after the deadline", async () => {
    server.reply(EVENTS, { status: 429, headers: { "Retry-After": "30" } });
    const b = newBatcher({ flushIntervalMs: 3_600_000 });
    b.record(ev("f", "u1"));
    const start = Date.now();
    expect(await b.shutdown(2_000)).toBe(false);
    expect(Date.now() - start).toBeLessThan(1_000);
    expect(sent()).toHaveLength(1);
  });

  it("sends nothing after stop", async () => {
    accept();
    const b = newBatcher({ flushIntervalMs: 3_600_000 });
    b.record(ev("f", "u1"));
    await b.stop();
    await b.stop();
    expect(await b.shutdown(5_000)).toBe(false);
    expect(sent()).toHaveLength(0);
  });

  it("drops and counts records over the bucket cap without waiting", () => {
    const b = newBatcher({ maxBuckets: 1 });
    expect(b.record(ev("f", "u1"))).toBeUndefined();
    b.record(ev("g", "u1"));
    expect(b.dropped).toBe(1);
  });

  it("drops records after shutdown or stop", async () => {
    accept();
    const b = newBatcher();
    await b.shutdown(5_000);
    b.record(ev("f", "u1"));
    await b.stop();
    b.record(ev("f", "u2"));
    expect(b.dropped).toBe(2);
    expect(sent()).toHaveLength(0);
  });

  it("stops for good on 401 and calls onFatal once", async () => {
    server.reply(EVENTS, { status: 401 });
    const fatals: unknown[] = [];
    const b = newBatcher({ onFatal: (err) => fatals.push(err) });
    b.record(ev("f", "u1"));
    await vi.waitFor(() => expect(fatals).toHaveLength(1));
    b.record(ev("g", "u1"));
    await new Promise((r) => setTimeout(r, 200));
    expect(await b.shutdown(5_000)).toBe(false);
    expect(sent()).toHaveLength(1);
    expect(fatals).toHaveLength(1);
    expect(fatals[0]).toBeInstanceOf(FatalError);
    expect((fatals[0] as FatalError).status).toBe(401);
  });

  it("lets onFatal stop and shut down the batcher", async () => {
    server.reply(EVENTS, { status: 403 });
    let done: Promise<boolean> | undefined;
    const b = newBatcher({
      onFatal: () => {
        done = b.stop().then(() => b.shutdown(5_000));
      },
    });
    b.record(ev("f", "u1"));
    await vi.waitFor(() => expect(done).toBeDefined());
    await expect(done).resolves.toBe(false);
  });

  it("splits a batch the server refuses with 413", async () => {
    respond((b) => (items(b) > 10 ? 413 : 202));
    const accepted: Batch[] = [];
    const b = newBatcher({ flushIntervalMs: 3_600_000 });
    for (let i = 0; i < 25; i++) {
      b.record(ev("f", `u${i}`));
    }
    await b.shutdown(5_000);
    for (const r of sent()) {
      const got = body(r);
      if (items(got) <= 10) {
        accepted.push(got);
        expect(items(got)).toBeGreaterThan(0);
      }
    }
    expect(totals(accepted)).toEqual({ exposures: 25, counts: { f: 25 } });
  });

  it("splits a body over 1 MB before sending it", async () => {
    accept();
    const b = newBatcher({ flushIntervalMs: 3_600_000, sendContext: true });
    const big = { blob: "x".repeat(2000) };
    for (let i = 0; i < 1000; i++) {
      b.record(ev("f", `u${i}`, big));
    }
    await b.shutdown(5_000);
    expect(sent().length).toBeGreaterThanOrEqual(2);
    for (const r of sent()) {
      expect(Buffer.byteLength(r.body)).toBeLessThanOrEqual(1_000_000);
    }
    expect(totals(sent().map(body))).toEqual({
      exposures: 1000,
      counts: { f: 1000 },
    });
  });

  it("drops a single item that is too large", async () => {
    accept();
    const warnings: string[] = [];
    const b = newBatcher({
      flushIntervalMs: 3_600_000,
      sendContext: true,
      logger: {
        debug: () => {},
        info: () => {},
        warn: (msg: string) => warnings.push(msg),
        error: () => {},
      },
    });
    b.record(ev("f", "u1", { blob: "x".repeat(1_100_000) }));
    expect(await b.shutdown(5_000)).toBe(true);
    // The exposure is dropped; its count goes on its own.
    expect(
      sent().map((r) => [body(r).exposures.length, body(r).counts.length]),
    ).toEqual([[0, 1]]);
    expect(warnings.join("\n")).toContain("too large");
  });

  it("drops a batch the server refuses with 422", async () => {
    server.reply(EVENTS, { status: 422 });
    const b = newBatcher();
    b.record(ev("f", "u1"));
    await vi.waitFor(() => expect(sent()).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 200)); // four intervals
    expect(await b.shutdown(5_000)).toBe(true);
    expect(sent()).toHaveLength(1);
  });

  it("resends a batch after the 429 Retry-After", async () => {
    server.reply(
      EVENTS,
      { status: 429, headers: { "Retry-After": "1" } },
      { status: 202 },
    );
    const b = newBatcher();
    b.record(ev("f", "u1"));
    await vi.waitFor(() => expect(sent()).toHaveLength(2), { timeout: 3_000 });
    const [first, second] = sent();
    expect(second!.at - first!.at).toBeGreaterThanOrEqual(1_000);
    expect(body(second!)).toEqual(body(first!));
  });

  it("retries a 5xx with backoff and keeps the counts", async () => {
    const b = newBatcher();
    respond((_, n) => {
      if (n === 1) {
        // Recorded while the first batch is in flight: merged on restore.
        for (let i = 0; i < 3; i++) {
          b.record(ev("f", "u1"));
        }
      }
      return n <= 2 ? 503 : 202;
    });
    for (let i = 0; i < 5; i++) {
      b.record(ev("f", "u1"));
    }
    await vi.waitFor(() => expect(sent()).toHaveLength(3), { timeout: 3_000 });
    expect(await b.shutdown(5_000)).toBe(true);
    expect(totals([body(sent()[2]!)])).toEqual({
      exposures: 1,
      counts: { f: 8 },
    });
  });

  it("stops splitting after a fatal status", async () => {
    respond((b) => (items(b) > 10 ? 413 : 401));
    let attempts = 0;
    const b = newBatcher({
      flushIntervalMs: 3_600_000,
      fetch: (input, init) => {
        attempts++;
        return fetch(input, init);
      },
    });
    for (let i = 0; i < 25; i++) {
      b.record(ev("f", `u${i}`));
    }
    await b.shutdown(5_000);
    // 26 items: 413; 13: 413; 6: 401, and no attempt after it.
    expect(attempts).toBe(3);
  });
});
