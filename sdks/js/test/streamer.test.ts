import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Streamer, type StreamerHooks } from "../src/internal/streamer.js";
import {
  HttpError,
  RetryAfterError,
  type Logger,
} from "../src/internal/transport.js";
import type { StreamOpener } from "../src/platform.js";

const KEY = "mf_client_secret123";
const BASE = "https://flags.example.com";

/** An async queue of body chunks the test writes to. */
class Chunks implements AsyncIterable<Uint8Array> {
  returned = false;
  private readonly items: (Uint8Array | Error | null)[] = [];
  private waiter: (() => void) | undefined;

  write(text: string): void {
    this.push(new TextEncoder().encode(text));
  }

  end(): void {
    this.push(null);
  }

  fail(err: Error): void {
    this.push(err);
  }

  private push(item: Uint8Array | Error | null): void {
    this.items.push(item);
    this.waiter?.();
  }

  [Symbol.asyncIterator](): AsyncIterator<Uint8Array> {
    return {
      next: async () => {
        while (this.items.length === 0) {
          await new Promise<void>((resolve) => (this.waiter = resolve));
        }
        const item = this.items.shift()!;
        if (item === null) return { done: true, value: undefined };
        if (item instanceof Error) throw item;
        return { done: false, value: item };
      },
      return: async () => {
        this.returned = true;
        return { done: true, value: undefined };
      },
    };
  }
}

interface Opened {
  url: string;
  headers: Record<string, string>;
  signal: AbortSignal;
  body: Chunks;
}

/** A StreamOpener answering 200 unless a status is queued in replies. */
function fakeOpener() {
  const calls: Opened[] = [];
  const replies: { status: number; headers?: Record<string, string> }[] = [];
  const open: StreamOpener = async (url, headers, signal) => {
    const r = replies.shift() ?? { status: 200 };
    const body = new Chunks();
    calls.push({ url, headers, signal, body });
    const lower = Object.fromEntries(
      Object.entries(r.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v]),
    );
    return {
      status: r.status,
      header: (n) => lower[n.toLowerCase()] ?? null,
      body,
    };
  };
  return { open, calls, replies };
}

function recordingHooks() {
  const log: unknown[][] = [];
  const hooks: StreamerHooks = {
    onConnected: () => log.push(["connected"]),
    onChanged: (id) => log.push(["changed", id]),
    onFatal: (reason) => log.push(["fatal", reason]),
    onFailure: (n, err) => log.push(["failure", n, err]),
  };
  return { log, hooks };
}

function recordingLogger() {
  const lines: string[] = [];
  const logger: Logger = {
    debug: (m) => lines.push(`debug ${m}`),
    info: (m) => lines.push(`info ${m}`),
    warn: (m) => lines.push(`warn ${m}`),
    error: (m) => lines.push(`error ${m}`),
  };
  return { lines, logger };
}

const tick = () => vi.advanceTimersByTimeAsync(0);
const advance = (ms: number) => vi.advanceTimersByTimeAsync(ms);

let opener: ReturnType<typeof fakeOpener>;
let hooks: ReturnType<typeof recordingHooks>;
let logs: ReturnType<typeof recordingLogger>;
let streamer: Streamer;

function make() {
  streamer = new Streamer({
    baseUrl: BASE,
    sdkKey: KEY,
    open: opener.open,
    logger: logs.logger,
    hooks: hooks.hooks,
    rand: () => 1,
  });
  return streamer;
}

const last = () => opener.calls[opener.calls.length - 1]!;
const names = () => hooks.log.map((e) => e[0]);

beforeEach(() => {
  vi.useFakeTimers();
  opener = fakeOpener();
  hooks = recordingHooks();
  logs = recordingLogger();
  make();
});

afterEach(() => {
  streamer.stop();
  vi.useRealTimers();
});

describe("Streamer", () => {
  it("opens the stream with the SDK headers and reports the connection", async () => {
    streamer.start();
    await tick();

    expect(opener.calls).toHaveLength(1);
    expect(last().url).toBe(`${BASE}/sdk/v1/stream`);
    expect(last().headers).toEqual({
      Authorization: `Bearer ${KEY}`,
      Accept: "text/event-stream",
    });
    expect(hooks.log).toEqual([["connected"]]);
    expect(logs.lines).toContain("debug megooflags: stream connected");
  });

  it("start is called once: a second start opens nothing", async () => {
    streamer.start();
    streamer.start();
    await tick();
    expect(opener.calls).toHaveLength(1);
  });

  it("calls onChanged with the id and resumes from it on the next connection", async () => {
    streamer.start();
    await tick();
    last().body.write(
      'event: changed\nid: "e2"\ndata: {"etag":"\\"e2\\""}\n\n',
    );
    await tick();

    expect(hooks.log).toContainEqual(["changed", '"e2"']);
    expect(streamer.lastEventId).toBe('"e2"');

    last().body.end();
    await advance(3000);
    expect(opener.calls).toHaveLength(2);
    expect(last().headers["Last-Event-ID"]).toBe('"e2"');
  });

  it("ignores unknown events and comments", async () => {
    streamer.start();
    await tick();
    last().body.write("event: other\ndata: x\n\n: ping\n\ndata: y\n\n");
    await tick();

    expect(hooks.log).toEqual([["connected"]]);
    expect(streamer.lastEventId).toBeNull();
    expect(opener.calls).toHaveLength(1);
  });

  it("retry: sets the next wait", async () => {
    streamer.start();
    await tick();
    last().body.write("retry: 5000\n\n");
    last().body.end();
    await advance(4999);
    expect(opener.calls).toHaveLength(1);
    await advance(1);
    expect(opener.calls).toHaveLength(2);
  });

  it("retry: never waits less than 1 s", async () => {
    streamer.start();
    await tick();
    last().body.write("retry: 0\n\n");
    last().body.end();
    await advance(999);
    expect(opener.calls).toHaveLength(1);
    await advance(1);
    expect(opener.calls).toHaveLength(2);
  });

  it("reconnects after 45 s without a line", async () => {
    streamer.start();
    await tick();
    const first = last();
    await advance(44_999);
    expect(first.signal.aborted).toBe(false);
    await advance(1);
    expect(first.signal.aborted).toBe(true);
    expect(hooks.log[1]?.slice(0, 2)).toEqual(["failure", 1]);
    expect(logs.lines).toContain(
      "debug megooflags: stream failed: DeadConnectionError",
    );
    await advance(3000);
    expect(opener.calls).toHaveLength(2);
  });

  it("a line every 15 s keeps the connection open", async () => {
    streamer.start();
    await tick();
    for (let i = 0; i < 6; i++) {
      await advance(15_000);
      last().body.write(": ping\n");
    }
    await advance(44_000);
    expect(opener.calls).toHaveLength(1);
    expect(names()).toEqual(["connected"]);
  });

  it("backs off 3 s, 6 s, 12 s … up to 60 s", async () => {
    opener.replies.push(...Array(8).fill({ status: 500 }));
    streamer.start();
    await tick();
    expect(opener.calls).toHaveLength(1);
    for (const wait of [3000, 6000, 12_000, 24_000, 48_000, 60_000, 60_000]) {
      const before = opener.calls.length;
      await advance(wait - 1);
      expect(opener.calls).toHaveLength(before);
      await advance(1);
      expect(opener.calls).toHaveLength(before + 1);
    }
  });

  it("jitters later waits between 80 % and 100 %", async () => {
    streamer.stop();
    streamer = new Streamer({
      baseUrl: BASE,
      sdkKey: KEY,
      open: opener.open,
      logger: logs.logger,
      hooks: hooks.hooks,
      rand: () => 0,
    });
    opener.replies.push({ status: 500 }, { status: 500 });
    streamer.start();
    await advance(3000);
    expect(opener.calls).toHaveLength(2);
    await advance(4799);
    expect(opener.calls).toHaveLength(2);
    await advance(1);
    expect(opener.calls).toHaveLength(3);
  });

  it("resets the backoff after a connection that lasted 60 s", async () => {
    opener.replies.push({ status: 500 }, { status: 500 });
    streamer.start();
    await advance(3000 + 6000);
    expect(opener.calls).toHaveLength(3);
    for (let i = 0; i < 4; i++) {
      await advance(15_000);
      last().body.write(": ping\n");
    }
    last().body.end();
    await advance(3000);
    expect(opener.calls).toHaveLength(4);
  });

  it("keeps the backoff after a short connection", async () => {
    opener.replies.push({ status: 500 }, { status: 500 });
    streamer.start();
    await advance(3000 + 6000);
    last().body.end();
    await advance(11_999);
    expect(opener.calls).toHaveLength(3);
    await advance(1);
    expect(opener.calls).toHaveLength(4);
  });

  it("onFailure counts consecutive failures and the count resets on connect", async () => {
    opener.replies.push({ status: 500 }, { status: 503 });
    streamer.start();
    await advance(3000 + 6000);
    last().body.end();
    await tick();

    const failures = hooks.log.filter((e) => e[0] !== "connected");
    expect(names()).toEqual(["failure", "failure", "connected", "failure"]);
    expect(failures.map((e) => e[1])).toEqual([1, 2, 1]);
    expect(failures[0]![2]).toBeInstanceOf(HttpError);
    expect((failures[1]![2] as HttpError).status).toBe(503);
    expect(logs.lines).toContain("debug megooflags: stream failed: HttpError");
  });

  it("a read error is a failure", async () => {
    streamer.start();
    await tick();
    last().body.fail(new TypeError("network"));
    await tick();
    expect(hooks.log[1]?.slice(0, 2)).toEqual(["failure", 1]);
    expect(hooks.log[1]?.[2]).toBeInstanceOf(TypeError);
  });

  it("an open that gets no response headers in 45 s is a failure", async () => {
    streamer.stop();
    const signals: AbortSignal[] = [];
    streamer = new Streamer({
      baseUrl: BASE,
      sdkKey: KEY,
      open: (...args) => {
        const signal = args[2];
        if (signals.push(signal) > 1) return opener.open(...args);
        // A half-open socket: nothing until the SDK aborts.
        return new Promise((_, reject) =>
          signal.addEventListener("abort", () => reject(signal.reason)),
        );
      },
      logger: logs.logger,
      hooks: hooks.hooks,
      rand: () => 1,
    });
    streamer.start();
    await advance(44_999);
    expect(signals[0]?.aborted).toBe(false);
    await advance(1);

    expect(signals[0]?.aborted).toBe(true);
    expect(hooks.log[0]?.slice(0, 2)).toEqual(["failure", 1]);
    await advance(3000);
    expect(names()).toEqual(["failure", "connected"]);
  });

  it("an opener that throws is a failure", async () => {
    streamer.stop();
    let n = 0;
    streamer = new Streamer({
      baseUrl: BASE,
      sdkKey: KEY,
      open: async (...args) => {
        if (n++ === 0) throw new TypeError("offline");
        return opener.open(...args);
      },
      logger: logs.logger,
      hooks: hooks.hooks,
      rand: () => 1,
    });
    streamer.start();
    await tick();
    expect(hooks.log[0]?.slice(0, 2)).toEqual(["failure", 1]);
    await advance(3000);
    expect(names()).toEqual(["failure", "connected"]);
  });

  for (const status of [401, 403]) {
    it(`${status} calls onFatal once and never reconnects`, async () => {
      opener.replies.push({ status });
      streamer.start();
      await advance(600_000);
      expect(hooks.log).toEqual([["fatal", status]]);
      expect(opener.calls).toHaveLength(1);
      expect(opener.calls[0]!.body.returned).toBe(true);
    });
  }

  it("revoked calls onFatal once and never reconnects", async () => {
    streamer.start();
    await tick();
    last().body.write("event: revoked\ndata: {}\n\n");
    await advance(600_000);
    expect(hooks.log).toEqual([["connected"], ["fatal", "revoked"]]);
    expect(opener.calls).toHaveLength(1);
    expect(last().signal.aborted).toBe(true);
  });

  it("429 waits Retry-After seconds", async () => {
    opener.replies.push({ status: 429, headers: { "Retry-After": "20" } });
    streamer.start();
    await tick();
    expect(hooks.log[0]?.[2]).toBeInstanceOf(RetryAfterError);
    await advance(19_999);
    expect(opener.calls).toHaveLength(1);
    await advance(1);
    expect(opener.calls).toHaveLength(2);
  });

  it("pause closes the connection and does not reconnect", async () => {
    streamer.start();
    await tick();
    streamer.pause();
    await tick();
    expect(last().signal.aborted).toBe(true);
    expect(last().body.returned).toBe(true);
    await advance(600_000);
    expect(opener.calls).toHaveLength(1);
    expect(names()).toEqual(["connected"]);
  });

  it("pause during a backoff wait cancels the reconnect", async () => {
    opener.replies.push({ status: 500 });
    streamer.start();
    await tick();
    streamer.pause();
    await advance(600_000);
    expect(opener.calls).toHaveLength(1);
  });

  it("resume opens at once with Last-Event-ID", async () => {
    streamer.start();
    await tick();
    last().body.write("event: changed\nid: e7\ndata: {}\n\n");
    await tick();
    streamer.pause();
    await advance(60_000);
    streamer.resume();
    await tick();
    expect(opener.calls).toHaveLength(2);
    expect(last().headers["Last-Event-ID"]).toBe("e7");
    expect(names()).toEqual(["connected", "changed", "connected"]);
  });

  it("resume without pause opens nothing", async () => {
    streamer.start();
    await tick();
    streamer.resume();
    await tick();
    expect(opener.calls).toHaveLength(1);
  });

  it("stop is final", async () => {
    streamer.start();
    await tick();
    streamer.stop();
    streamer.resume();
    streamer.start();
    await advance(600_000);
    expect(opener.calls).toHaveLength(1);
    expect(last().signal.aborted).toBe(true);
    expect(names()).toEqual(["connected"]);
  });

  it("stop before the open answers runs no hook", async () => {
    streamer.stop();
    let answer: (() => void) | undefined;
    streamer = new Streamer({
      baseUrl: BASE,
      sdkKey: KEY,
      open: (...args) =>
        new Promise((resolve) => {
          answer = () => resolve(opener.open(...args));
        }),
      logger: logs.logger,
      hooks: hooks.hooks,
    });
    streamer.start();
    await tick();
    streamer.stop();
    answer!();
    await advance(600_000);
    expect(hooks.log).toEqual([]);
    expect(last().body.returned).toBe(true);
  });

  it("a hook that throws is logged and the stream goes on", async () => {
    streamer.stop();
    streamer = new Streamer({
      baseUrl: BASE,
      sdkKey: KEY,
      open: opener.open,
      logger: logs.logger,
      hooks: {
        ...hooks.hooks,
        onChanged: () => {
          throw new Error("boom");
        },
      },
    });
    streamer.start();
    await tick();
    last().body.write("event: changed\nid: e1\ndata: {}\n\n");
    await tick();
    expect(logs.lines).toContain("error megooflags: stream callback failed");
    expect(opener.calls).toHaveLength(1);
    expect(last().signal.aborted).toBe(false);
  });
});
