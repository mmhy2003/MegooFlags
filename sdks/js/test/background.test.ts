import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BackgroundController } from "../src/internal/background.js";
import { Cache } from "../src/internal/cache.js";
import type { EvaluateOutcome } from "../src/internal/evaluator.js";
import { resolveOptions } from "../src/internal/options.js";
import { Store } from "../src/internal/store.js";
import { Sync } from "../src/internal/sync.js";
import type { Logger } from "../src/internal/transport.js";
import type { BackgroundSignal, BackgroundState } from "../src/platform.js";

const silent: Logger = {
  debug: () => {},
  info: () => {},
  warn: () => {},
  error: () => {},
};

class FakeSignal implements BackgroundSignal {
  readonly listeners = new Set<(state: BackgroundState) => void>();
  state: BackgroundState = "visible";
  current(): BackgroundState {
    return this.state;
  }
  subscribe(listener: (state: BackgroundState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  emit(state: BackgroundState): void {
    this.state = state;
    for (const l of this.listeners) l(state);
  }
}

class FakeStreamer {
  pauses = 0;
  resumes = 0;
  start() {}
  pause() {
    this.pauses++;
  }
  resume() {
    this.resumes++;
  }
  stop() {}
}

/** setup gives a started Sync whose evaluator answers at once and counts its calls. */
async function setup() {
  const opts = resolveOptions({
    sdkKey: "mf_client_secret123",
    baseUrl: "https://flags.example.com",
    logger: silent,
  });
  const evaluator = {
    calls: 0,
    evaluate(): Promise<EvaluateOutcome> {
      this.calls++;
      return Promise.resolve({ kind: "ok", results: [], etag: null });
    },
  };
  const streamer = new FakeStreamer();
  const sync = new Sync({
    opts,
    store: new Store(),
    cache: new Cache(opts.sdkKey, null, silent),
    evaluator,
    streamer,
    events: {
      ready: () => {},
      stale: () => {},
      changed: () => {},
      fatal: () => {},
      error: () => {},
    },
    onFatalStop: () => {},
  });
  await sync.start({ targetingKey: "user-1" });
  await vi.advanceTimersByTimeAsync(0);
  const signal = new FakeSignal();
  const batcher = { flush: vi.fn(() => Promise.resolve()) };
  const controller = new BackgroundController({ signal, sync, batcher });
  controller.start();
  return { sync, evaluator, streamer, signal, batcher, controller };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("BackgroundController", () => {
  it("hidden flushes at once and pauses after 30 s, not before", async () => {
    const t = await setup();

    t.signal.emit("hidden");
    expect(t.batcher.flush).toHaveBeenCalledTimes(1);
    expect(t.batcher.flush).toHaveBeenCalledWith();

    await vi.advanceTimersByTimeAsync(29_999);
    expect(t.streamer.pauses).toBe(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(t.streamer.pauses).toBe(1);
  });

  it("visible before 30 s cancels the pause", async () => {
    const t = await setup();

    t.signal.emit("hidden");
    await vi.advanceTimersByTimeAsync(10_000);
    t.signal.emit("visible");
    await vi.advanceTimersByTimeAsync(60_000);

    expect(t.streamer.pauses).toBe(0);
    expect(t.streamer.resumes).toBe(0);
  });

  it("a second hidden does not restart the 30 s timer", async () => {
    const t = await setup();

    t.signal.emit("hidden");
    await vi.advanceTimersByTimeAsync(20_000);
    t.signal.emit("hidden");
    await vi.advanceTimersByTimeAsync(10_000);

    expect(t.streamer.pauses).toBe(1);
  });

  it("terminated flushes with keepalive and pauses at once", async () => {
    const t = await setup();

    t.signal.emit("terminated");

    expect(t.batcher.flush).toHaveBeenCalledWith({ keepalive: true });
    expect(t.streamer.pauses).toBe(1);
  });

  it("visible after a pause resumes the stream once; the poller stays off when it was off", async () => {
    const t = await setup();
    t.signal.emit("hidden");
    await vi.advanceTimersByTimeAsync(30_000);
    const calls = t.evaluator.calls;

    t.signal.emit("visible");
    t.signal.emit("visible");
    await vi.advanceTimersByTimeAsync(120_000);

    expect(t.streamer.resumes).toBe(1);
    expect(t.evaluator.calls).toBe(calls);
  });

  it("visible after a pause brings the poller back when it was running", async () => {
    const t = await setup();
    t.sync.streamerHooks.onFailure(2, new Error("down"));
    t.signal.emit("terminated");
    await vi.advanceTimersByTimeAsync(120_000);
    const calls = t.evaluator.calls;

    t.signal.emit("visible");
    await vi.advanceTimersByTimeAsync(60_000);

    expect(t.streamer.resumes).toBe(1);
    expect(t.evaluator.calls).toBe(calls + 2);
  });

  it("stop unsubscribes and cancels a pending pause", async () => {
    const t = await setup();
    t.signal.emit("hidden");

    t.controller.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    t.signal.emit("terminated");

    expect(t.signal.listeners.size).toBe(0);
    expect(t.streamer.pauses).toBe(0);
    expect(t.batcher.flush).toHaveBeenCalledTimes(1);
  });

  it("pauses without a batcher (offline, no exposures)", async () => {
    const t = await setup();
    const signal = new FakeSignal();
    new BackgroundController({ signal, sync: t.sync, batcher: null }).start();
    t.controller.stop();

    signal.emit("terminated");

    expect(t.streamer.pauses).toBe(1);
  });

  it("does nothing without a signal", async () => {
    const t = await setup();
    const c = new BackgroundController({
      signal: null,
      sync: t.sync,
      batcher: t.batcher,
    });

    c.start();
    c.stop();

    expect(t.batcher.flush).not.toHaveBeenCalled();
  });
});
