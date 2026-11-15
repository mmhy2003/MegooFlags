import type { EvaluationContext, JsonValue } from "@openfeature/web-sdk";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MegooFlagsOptions } from "../src/config.js";
import { Cache } from "../src/internal/cache.js";
import { contextHash, toRequestContext } from "../src/internal/context.js";
import type { EvaluateOutcome } from "../src/internal/evaluator.js";
import {
  type EvaluateResult,
  resolveOptions,
} from "../src/internal/options.js";
import { Poller } from "../src/internal/poller.js";
import { resolveFlag } from "../src/internal/resolve.js";
import { Store, storedFrom } from "../src/internal/store.js";
import { Sync, type SyncEvents } from "../src/internal/sync.js";
import {
  FatalError,
  HttpError,
  type Logger,
  RetryAfterError,
} from "../src/internal/transport.js";
import type { StorageLike } from "../src/platform.js";

const KEY = "mf_client_secret123";
const USER1: EvaluationContext = { targetingKey: "user-1" };

const result = (o: Partial<EvaluateResult> = {}): EvaluateResult => ({
  key: "f",
  value: true,
  variant: "on",
  reason: "TARGETING_MATCH",
  error_code: null,
  error_message: null,
  ...o,
});

const ok = (
  results: EvaluateResult[],
  etag: string | null = '"e1"',
): EvaluateOutcome => ({ kind: "ok", results, etag });

const silent: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
};

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

interface Call {
  body: Record<string, JsonValue>;
  etag: string | null;
  answer(o: EvaluateOutcome): void;
  fail(e: unknown): void;
}

/** An evaluator whose answers the test gives; it ignores the abort signal. */
class FakeEvaluator {
  readonly calls: Call[] = [];

  evaluate(
    body: Record<string, JsonValue>,
    etag: string | null,
  ): Promise<EvaluateOutcome> {
    return new Promise((resolve, reject) => {
      this.calls.push({ body, etag, answer: resolve, fail: reject });
    });
  }

  last(): Call {
    const c = this.calls.at(-1);
    if (c === undefined) throw new Error("no evaluate call");
    return c;
  }
}

class FakeStreamer {
  starts = 0;
  pauses = 0;
  resumes = 0;
  stops = 0;
  start() {
    this.starts++;
  }
  pause() {
    this.pauses++;
  }
  resume() {
    this.resumes++;
  }
  stop() {
    this.stops++;
  }
}

type Event =
  | "ready"
  | "stale"
  | ["changed", string[]]
  | ["fatal", number | "revoked"]
  | ["error", unknown];

function setup(
  o: Partial<MegooFlagsOptions> = {},
  parts: { storage?: MemoryStorage; offline?: boolean } = {},
) {
  const opts = resolveOptions({
    sdkKey: KEY,
    baseUrl: "https://flags.example.com",
    logger: silent,
    ...o,
  });
  const storage = parts.storage ?? new MemoryStorage();
  const store = new Store();
  const cache = new Cache(KEY, storage, silent);
  const evaluator = new FakeEvaluator();
  const streamer = new FakeStreamer();
  const events: Event[] = [];
  const sink: SyncEvents = {
    ready: () => events.push("ready"),
    stale: () => events.push("stale"),
    changed: (flags) => events.push(["changed", flags]),
    fatal: (reason) => events.push(["fatal", reason]),
    error: (e) => events.push(["error", e]),
  };
  let fatalStops = 0;
  const offline = opts.offline;
  const sync = new Sync({
    opts,
    store,
    cache,
    evaluator: offline ? null : evaluator,
    streamer: offline ? null : streamer,
    events: sink,
    onFatalStop: () => fatalStops++,
  });
  return {
    sync,
    store,
    cache,
    storage,
    evaluator,
    streamer,
    events,
    fatalStops: () => fatalStops,
    read: (flag = "f") => resolveFlag(store.get(), flag, "boolean", false),
  };
}

/** flush runs every pending microtask and zero-delay timer. */
const flush = () => vi.advanceTimersByTimeAsync(0);

async function saveCacheFor(
  storage: MemoryStorage,
  ctx: EvaluationContext,
  results: EvaluateResult[],
  etag: string | null,
) {
  const cache = new Cache(KEY, storage, silent);
  await cache.save(
    storedFrom(results, {
      etag,
      source: "network",
      contextHash: contextHash(toRequestContext(ctx)),
      targetingKey: null,
    }),
  );
}

/** started gives a Sync for USER1 whose first refresh answered f=true. */
async function started(o: Partial<MegooFlagsOptions> = {}) {
  const t = setup({ bootstrap: { results: [result()] }, ...o });
  await t.sync.start(USER1);
  t.evaluator.last().answer(ok([result()]));
  await flush();
  return t;
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Sync.start", () => {
  it("returns at once with bootstrap values, then a refresh replaces them", async () => {
    const t = setup({
      bootstrap: {
        results: [result({ value: false, variant: "off", reason: "STATIC" })],
      },
    });

    await t.sync.start(USER1);

    expect(t.read()).toEqual({
      value: false,
      variant: "off",
      reason: "STATIC",
    });
    expect(t.store.get()?.source).toBe("bootstrap");
    expect(t.evaluator.calls).toHaveLength(1);
    expect(t.evaluator.last().body).toEqual({ key: "user-1" });
    expect(t.streamer.starts).toBe(0);

    t.evaluator.last().answer(ok([result()]));
    await flush();

    expect(t.read()).toEqual({
      value: true,
      variant: "on",
      reason: "TARGETING_MATCH",
    });
    expect(t.store.get()?.source).toBe("network");
    expect(t.events).toEqual([["changed", ["f"]]]);
    expect(t.storage.items.get(t.cache.key)).toContain('"etag":"\\"e1\\""');
    expect(t.streamer.starts).toBe(1);
  });

  it("serves a matching cache entry over bootstrap, sends its ETag and a 304 makes it fresh", async () => {
    const storage = new MemoryStorage();
    await saveCacheFor(
      storage,
      USER1,
      [result({ value: false, variant: "off" })],
      '"c1"',
    );
    const t = setup(
      { bootstrap: { results: [result({ reason: "STATIC" })] } },
      { storage },
    );

    await t.sync.start(USER1);

    expect(t.read()).toEqual({
      value: false,
      variant: "off",
      reason: "CACHED",
    });
    expect(t.store.get()?.targetingKey).toBe("user-1");
    expect(t.evaluator.last().etag).toBe('"c1"');

    t.evaluator.last().answer({ kind: "notModified", etag: '"c1"' });
    await flush();

    expect(t.read()).toEqual({
      value: false,
      variant: "off",
      reason: "TARGETING_MATCH",
    });
    expect(t.store.get()?.source).toBe("network");
    expect(t.events).toEqual([]);
  });

  it("ignores a cache entry for another context", async () => {
    const storage = new MemoryStorage();
    await saveCacheFor(storage, { targetingKey: "user-2" }, [result()], '"c1"');
    const t = setup({}, { storage });

    let done = false;
    void t.sync.start(USER1).then(() => (done = true));
    await flush();

    expect(done).toBe(false);
    expect(t.store.get()).toBeUndefined();
    expect(t.evaluator.last().etag).toBeNull();
  });

  it("waits for the first evaluate when there are no values", async () => {
    const t = setup();

    let done = false;
    const start = t.sync.start(USER1).then(() => (done = true));
    await flush();
    expect(done).toBe(false);
    expect(t.read().errorCode).toBe("PROVIDER_NOT_READY");

    t.evaluator.last().answer(ok([result()]));
    await start;

    expect(t.read().value).toBe(true);
    expect(t.events).toEqual([]);
    expect(t.streamer.starts).toBe(1);
  });

  it("rejects after initTimeoutMs and emits ready when values arrive later", async () => {
    const t = setup();

    const start = t.sync.start(USER1);
    const rejected = expect(start).rejects.toThrow(
      "megooflags: no flag values within 5s",
    );
    await vi.advanceTimersByTimeAsync(4999);
    expect(t.events).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    await rejected;

    t.evaluator.last().answer(ok([result()]));
    await flush();

    expect(t.read().value).toBe(true);
    expect(t.events).toEqual(["ready"]);
  });

  it("rejects with a fatal error on 401 and retries nothing", async () => {
    const t = setup();

    const start = t.sync.start(USER1);
    const rejected = expect(start).rejects.toBeInstanceOf(FatalError);
    await flush();
    t.evaluator.last().fail(new FatalError(401));
    await rejected;
    await vi.advanceTimersByTimeAsync(300_000);

    expect(t.evaluator.calls).toHaveLength(1);
    expect(t.streamer.starts).toBe(0);
    expect(t.fatalStops()).toBe(1);
  });

  it("rejects offline without bootstrap or cache", async () => {
    const t = setup({ offline: true });

    await expect(t.sync.start(USER1)).rejects.toThrow(
      "megooflags: offline mode needs bootstrap values or a cache entry",
    );
  });

  it("starts offline from bootstrap without any network", async () => {
    const t = setup({ offline: true, bootstrap: { results: [result()] } });

    await t.sync.start(USER1);
    await t.sync.refresh();
    await vi.advanceTimersByTimeAsync(120_000);

    expect(t.read().value).toBe(true);
    expect(t.evaluator.calls).toHaveLength(0);
    expect(t.streamer.starts).toBe(0);
  });
});

describe("Sync.refresh", () => {
  it("runs at most one evaluate in flight and one queued", async () => {
    const t = await started();
    expect(t.evaluator.calls).toHaveLength(1);

    t.sync.streamerHooks.onChanged("1");
    t.sync.streamerHooks.onChanged("2");
    t.sync.streamerHooks.onChanged("3");
    await flush();
    expect(t.evaluator.calls).toHaveLength(2);

    t.evaluator.last().answer(ok([result()]));
    await flush();
    expect(t.evaluator.calls).toHaveLength(3);
    t.evaluator.last().answer(ok([result()]));
    await vi.advanceTimersByTimeAsync(120_000);

    expect(t.evaluator.calls).toHaveLength(3);
  });

  it("sends the stored ETag and emits no change when nothing changed", async () => {
    const t = await started();

    t.sync.streamerHooks.onChanged("1");
    await flush();
    expect(t.evaluator.last().etag).toBe('"e1"');
    t.evaluator.last().answer(ok([result()], '"e2"'));
    await flush();

    expect(t.store.get()?.etag).toBe('"e2"');
    expect(t.events).toEqual([]);
  });

  it("retries a failure with backoff from 1 s", async () => {
    const t = await started();

    const refresh = t.sync.refresh();
    const rejected = expect(refresh).rejects.toBeInstanceOf(HttpError);
    await flush();
    t.evaluator.last().fail(new HttpError(500));
    await rejected;
    expect(t.events).toEqual([["error", new HttpError(500)]]);

    await vi.advanceTimersByTimeAsync(999);
    expect(t.evaluator.calls).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(t.evaluator.calls).toHaveLength(3);

    t.evaluator.last().fail(new TypeError("fetch failed"));
    await vi.advanceTimersByTimeAsync(1599);
    expect(t.evaluator.calls).toHaveLength(3);
    await vi.advanceTimersByTimeAsync(401);
    expect(t.evaluator.calls).toHaveLength(4);

    t.evaluator.last().answer(ok([result()]));
    await vi.advanceTimersByTimeAsync(300_000);
    expect(t.evaluator.calls).toHaveLength(4);
  });

  it("waits Retry-After after a 429", async () => {
    const t = await started();

    t.sync.streamerHooks.onChanged("1");
    await flush();
    t.evaluator.last().fail(new RetryAfterError(7));
    await vi.advanceTimersByTimeAsync(6999);
    expect(t.evaluator.calls).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);

    expect(t.evaluator.calls).toHaveLength(3);
  });

  it("stops everything on a fatal answer after start", async () => {
    const t = await started();

    t.sync.streamerHooks.onChanged("1");
    await flush();
    t.evaluator.last().fail(new FatalError(403));
    await vi.advanceTimersByTimeAsync(300_000);

    expect(t.events).toEqual([["fatal", 403]]);
    expect(t.streamer.stops).toBe(1);
    expect(t.fatalStops()).toBe(1);
    expect(t.evaluator.calls).toHaveLength(2);
    expect(t.read().value).toBe(true);
  });

  it("stops everything when the stream reports the key revoked", async () => {
    const t = await started();

    t.sync.streamerHooks.onFatal("revoked");
    t.sync.streamerHooks.onChanged("1");
    await vi.advanceTimersByTimeAsync(300_000);

    expect(t.events).toEqual([["fatal", "revoked"]]);
    expect(t.fatalStops()).toBe(1);
    expect(t.evaluator.calls).toHaveLength(1);
  });
});

describe("Sync.setContext", () => {
  it("clears the store and the cache entry at once when the targeting key changes", async () => {
    const t = await started();
    expect(t.storage.items.has(t.cache.key)).toBe(true);

    let done = false;
    const change = t.sync
      .setContext({ targetingKey: "user-2" })
      .then(() => (done = true));

    expect(t.read().errorCode).toBe("PROVIDER_NOT_READY");
    await flush();
    expect(t.storage.items.has(t.cache.key)).toBe(false);
    expect(t.evaluator.last().body).toEqual({ key: "user-2" });
    expect(t.evaluator.last().etag).toBeNull();
    expect(done).toBe(false);

    t.evaluator.last().answer(ok([result({ value: false, variant: "off" })]));
    await change;

    expect(t.read().value).toBe(false);
    expect(t.sync.body).toEqual({ key: "user-2" });
    expect(t.events).toEqual([]);
  });

  it("keeps serving the old values when only attributes change", async () => {
    const t = await started();

    const change = t.sync.setContext({ targetingKey: "user-1", plan: "pro" });

    expect(t.read().value).toBe(true);
    await flush();
    expect(t.evaluator.last().body).toEqual({ key: "user-1", plan: "pro" });
    expect(t.evaluator.last().etag).toBeNull();
    t.evaluator.last().answer(ok([result({ value: false, variant: "off" })]));
    await change;

    expect(t.read().value).toBe(false);
    expect(t.events).toEqual([["changed", ["f"]]]);
  });

  it("ignores a late answer for the old context", async () => {
    const t = await started();
    t.sync.streamerHooks.onChanged("1");
    await flush();
    const old = t.evaluator.last();

    const change = t.sync.setContext({ targetingKey: "user-1", plan: "pro" });
    old.answer(ok([result({ key: "late" })], '"old"'));
    await flush();

    expect(t.store.get()?.results.has("late")).toBe(false);
    expect(t.evaluator.calls).toHaveLength(3);
    expect(t.evaluator.last().etag).toBeNull();
    t.evaluator.last().answer(ok([result({ value: false, variant: "off" })]));
    await change;

    expect(t.read().value).toBe(false);
    expect(t.store.get()?.results.has("late")).toBe(false);
  });

  it("clears the store and the cache entry on a key change offline", async () => {
    const storage = new MemoryStorage();
    await saveCacheFor(storage, USER1, [result()], '"e1"');
    const t = setup({ offline: true }, { storage });
    await t.sync.start(USER1);
    expect(t.read().value).toBe(true);

    await t.sync.setContext({ targetingKey: "user-2" });
    await flush();

    expect(t.read().errorCode).toBe("PROVIDER_NOT_READY");
    expect(t.storage.items.has(t.cache.key)).toBe(false);
    expect(t.sync.body).toEqual({ key: "user-2" });
  });

  it("clears the store and the cache entry on a key change after a fatal stop", async () => {
    const t = await started();
    t.sync.streamerHooks.onFatal(401);

    await expect(
      t.sync.setContext({ targetingKey: "user-2" }),
    ).rejects.toBeInstanceOf(FatalError);
    await flush();

    expect(t.read().errorCode).toBe("PROVIDER_NOT_READY");
    expect(t.storage.items.has(t.cache.key)).toBe(false);
    expect(t.evaluator.calls).toHaveLength(1);
  });

  it("keeps the values on an attribute change after a fatal stop", async () => {
    const t = await started();
    t.sync.streamerHooks.onFatal(401);

    await expect(
      t.sync.setContext({ targetingKey: "user-1", plan: "pro" }),
    ).rejects.toBeInstanceOf(FatalError);

    expect(t.read().value).toBe(true);
  });

  it("rejects when the evaluate for the new context fails, and keeps retrying", async () => {
    const t = await started();

    const change = t.sync.setContext({ targetingKey: "user-2" });
    const rejected = expect(change).rejects.toBeInstanceOf(HttpError);
    await flush();
    t.evaluator.last().fail(new HttpError(503));
    await rejected;
    await vi.advanceTimersByTimeAsync(1000);
    t.evaluator.last().answer(ok([result()]));
    await flush();

    expect(t.evaluator.calls).toHaveLength(3);
    expect(t.read().value).toBe(true);
  });
});

describe("Sync stream fallback", () => {
  it("polls after two stream failures, stops on reconnect, and emits stale then ready", async () => {
    const t = await started();
    const hooks = t.sync.streamerHooks;
    hooks.onConnected();

    hooks.onFailure(1, new Error("x"));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.evaluator.calls).toHaveLength(1);
    expect(t.events).toEqual([]);

    hooks.onFailure(2, new Error("x"));
    expect(t.events).toEqual(["stale"]);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(t.evaluator.calls).toHaveLength(2);
    t.evaluator.last().answer(ok([result()]));
    hooks.onFailure(3, new Error("x"));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(t.evaluator.calls).toHaveLength(3);
    t.evaluator.last().answer(ok([result()]));

    hooks.onConnected();
    await vi.advanceTimersByTimeAsync(120_000);

    expect(t.evaluator.calls).toHaveLength(3);
    expect(t.events).toEqual(["stale", "ready"]);
  });

  it("emits no stale for failures before the stream ever connected", async () => {
    const t = await started();

    t.sync.streamerHooks.onFailure(2, new Error("x"));
    t.sync.streamerHooks.onConnected();

    expect(t.events).toEqual([]);
  });

  it("polls from the start with disableStream", async () => {
    const t = await started({ disableStream: true });
    expect(t.streamer.starts).toBe(0);

    await vi.advanceTimersByTimeAsync(30_000);
    expect(t.evaluator.calls).toHaveLength(2);
    t.evaluator.last().answer(ok([result()]));
    await vi.advanceTimersByTimeAsync(30_000);

    expect(t.evaluator.calls).toHaveLength(3);
  });

  it("pause stops the stream and the poller; resume restarts what ran", async () => {
    const t = await started();
    t.sync.streamerHooks.onFailure(2, new Error("x"));

    t.sync.pause();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(t.streamer.pauses).toBe(1);
    expect(t.evaluator.calls).toHaveLength(1);

    t.sync.resume();
    await vi.advanceTimersByTimeAsync(30_000);

    expect(t.streamer.resumes).toBe(1);
    expect(t.evaluator.calls).toHaveLength(2);
  });

  it("resume before the first evaluate settles waits for it to start the stream", async () => {
    const t = setup({ bootstrap: { results: [result()] } });
    await t.sync.start(USER1);

    t.sync.pause();
    t.sync.resume();
    await flush();
    expect(t.streamer.starts).toBe(0);

    t.evaluator.last().answer(ok([result()]));
    await flush();

    expect(t.streamer.starts).toBe(1);
  });

  it("stop ends the stream, the poller and the retries", async () => {
    const t = await started();
    t.sync.streamerHooks.onFailure(2, new Error("x"));
    void t.sync.refresh().catch(() => {});
    await flush();
    t.evaluator.last().fail(new HttpError(500));

    t.sync.stop();
    await vi.advanceTimersByTimeAsync(300_000);

    expect(t.streamer.stops).toBe(1);
    expect(t.evaluator.calls).toHaveLength(2);
  });
});

describe("Poller", () => {
  it("calls tick every interval while running", async () => {
    const tick = vi.fn(() => Promise.resolve());
    const p = new Poller(30_000, tick);

    p.start();
    p.start();
    expect(p.running).toBe(true);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(tick).toHaveBeenCalledTimes(2);

    p.stop();
    expect(p.running).toBe(false);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(tick).toHaveBeenCalledTimes(2);
  });

  it("never ticks more often than every 15 s", async () => {
    const tick = vi.fn(() => Promise.resolve());
    const p = new Poller(1_000, tick);

    p.start();
    await vi.advanceTimersByTimeAsync(14_999);
    expect(tick).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(tick).toHaveBeenCalledTimes(1);
    p.stop();
  });

  it("survives a tick that rejects", async () => {
    const tick = vi.fn(() => Promise.reject(new Error("boom")));
    const p = new Poller(15_000, tick);

    p.start();
    await vi.advanceTimersByTimeAsync(30_000);

    expect(tick).toHaveBeenCalledTimes(2);
    p.stop();
  });
});
