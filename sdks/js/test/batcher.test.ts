import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Batcher } from "../src/internal/batcher.js";
import { Exposures } from "../src/internal/exposures.js";
import type { Logger } from "../src/internal/transport.js";

const KEY = "mf_client_secret123";
const BASE = "https://flags.example.com";
const EVENTS = `${BASE}/sdk/v1/events`;
const USER1 = { key: "user-1" };

interface Call {
  url: string;
  init: RequestInit;
  /** exposures is the parsed body's exposures list. */
  body: { exposures: { flag_key: string }[] } & Record<string, unknown>;
}

type Answer = number | { status: number; headers?: Record<string, string> };

/**
 * A fake events server behind fetch: it answers each request from its queue
 * (the last answer repeats), "network" rejects like a failed connection and
 * "hang" never answers until the request is aborted.
 */
function fakeServer(...answers: (Answer | "network" | "hang")[]) {
  const calls: Call[] = [];
  const fetchFn = ((input: RequestInfo | URL, init: RequestInit = {}) => {
    calls.push({
      url: String(input),
      init,
      body: JSON.parse(String(init.body)) as Call["body"],
    });
    const a = answers.length > 1 ? answers.shift()! : answers[0]!;
    if (a === "network") {
      return Promise.reject(new TypeError("fetch failed"));
    }
    if (a === "hang") {
      return new Promise<Response>((_, reject) => {
        init.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        );
      });
    }
    const { status, headers = {} } = typeof a === "number" ? { status: a } : a;
    return Promise.resolve(new Response(null, { status, headers }));
  }) as typeof fetch;
  const sizes = () => calls.map((c) => c.body.exposures.length);
  return { calls, fetch: fetchFn, sizes };
}

function recordingLogger() {
  const lines: [string, string][] = [];
  const logger: Logger = {
    debug: (m) => lines.push(["debug", m]),
    info: (m) => lines.push(["info", m]),
    warn: (m) => lines.push(["warn", m]),
    error: (m) => lines.push(["error", m]),
  };
  return { lines, logger };
}

const advance = (ms: number) => vi.advanceTimersByTimeAsync(ms);

let logs: ReturnType<typeof recordingLogger>;
let exposures: Exposures;
let fatal: number[];
let batcher: Batcher | undefined;

function make(server: ReturnType<typeof fakeServer>, flushIntervalMs = 30_000) {
  batcher = new Batcher({
    baseUrl: BASE,
    sdkKey: KEY,
    fetch: server.fetch,
    exposures,
    flushIntervalMs,
    logger: logs.logger,
    onFatal: (s) => fatal.push(s),
  });
  return batcher;
}

/** fill records n distinct exposures. */
function fill(n: number, prefix = "f") {
  for (let i = 0; i < n; i++) {
    exposures.record(`${prefix}${i}`, "on", "STATIC", USER1);
  }
}

beforeEach(() => {
  vi.useFakeTimers({ now: Date.UTC(2026, 10, 10, 9, 15, 2) });
  logs = recordingLogger();
  exposures = new Exposures({ sendContext: false, logger: logs.logger });
  fatal = [];
});

afterEach(async () => {
  // A close already running may wait out its 5 s deadline.
  const closed = batcher?.close(0);
  await advance(5_000);
  await closed;
  batcher = undefined;
  vi.useRealTimers();
});

describe("Batcher", () => {
  it("flushes after the interval", async () => {
    const server = fakeServer(202);
    make(server).start();
    fill(3);

    await advance(29_999);
    expect(server.calls).toHaveLength(0);
    await advance(1);

    expect(server.sizes()).toEqual([3]);
    expect(exposures.size).toBe(0);
  });

  it("posts exposures only, with the key and without credentials", async () => {
    const server = fakeServer(202);
    make(server);
    fill(1);

    await batcher!.flush();

    const [call] = server.calls;
    expect(call?.url).toBe(EVENTS);
    expect(call?.init.method).toBe("POST");
    expect(call?.init.credentials).toBe("omit");
    expect(call?.init.keepalive).toBeUndefined();
    expect(call?.init.headers).toMatchObject({
      Authorization: `Bearer ${KEY}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    });
    expect(Object.keys(call!.body)).toEqual(["exposures"]);
    expect(call?.body.exposures[0]).toEqual({
      flag_key: "f0",
      variant: "on",
      reason: "STATIC",
      timestamp: "2026-11-10T09:15:02.000Z",
    });
  });

  it("logs the count it sent after a 202", async () => {
    make(fakeServer(202));
    fill(2);

    await batcher!.flush();

    expect(logs.lines).toContainEqual([
      "debug",
      "megooflags: sent 2 exposures",
    ]);
  });

  it("does not send an empty batch", async () => {
    const server = fakeServer(202);
    make(server);

    await batcher!.flush();

    expect(server.calls).toHaveLength(0);
  });

  it("flushes at once when 1000 exposures are buffered", async () => {
    const server = fakeServer(202);
    make(server).start();

    fill(999);
    await advance(0);
    expect(server.calls).toHaveLength(0);
    fill(1, "last");
    await advance(0);

    expect(server.sizes()).toEqual([1000]);
  });

  it("sends at most 1000 exposures per batch", async () => {
    const server = fakeServer(202);
    make(server);
    fill(2500);

    await batcher!.flush();

    expect(server.sizes()).toEqual([1000, 1000, 500]);
  });

  it("keeps one send in flight", async () => {
    const server = fakeServer("hang");
    make(server);
    fill(1);

    void batcher!.flush();
    void batcher!.flush();
    await advance(0);

    expect(server.calls).toHaveLength(1);
  });

  it("splits a 413 batch of 1000 into 500 and 500", async () => {
    const server = fakeServer(413, 202, 202);
    make(server);
    fill(1000);

    await batcher!.flush();

    expect(server.sizes()).toEqual([1000, 500, 500]);
    expect(exposures.size).toBe(0);
  });

  it("splits recursively and drops a single item that is still 413", async () => {
    const server = fakeServer(413, 202, 413, 413, 202);
    make(server);
    fill(3);

    await batcher!.flush();

    // [3] 413 → [1] 202, [2] 413 → [1] 413 dropped, [1] 202
    expect(server.sizes()).toEqual([3, 1, 2, 1, 1]);
    expect(logs.lines.filter((l) => l[0] === "warn")).toHaveLength(1);
    expect(exposures.size).toBe(0);
  });

  it("drops a 422 batch with a warning", async () => {
    const server = fakeServer(422, 202);
    make(server);
    fill(2);

    await batcher!.flush();
    await batcher!.flush();

    expect(server.sizes()).toEqual([2]);
    expect(exposures.size).toBe(0);
    expect(logs.lines).toContainEqual([
      "warn",
      "megooflags: the server refused 2 exposures (HTTP 422); dropping them",
    ]);
  });

  it("retries a 429 after Retry-After, keeping the items in front", async () => {
    const server = fakeServer(
      { status: 429, headers: { "Retry-After": "7" } },
      202,
    );
    make(server);
    fill(2);

    await batcher!.flush();
    fill(1, "later");
    expect(exposures.size).toBe(3);

    await batcher!.flush(); // still waiting: nothing sent
    await advance(6_999);
    expect(server.calls).toHaveLength(1);
    await advance(1);

    expect(server.calls).toHaveLength(2);
    expect(server.calls[1]?.body.exposures.map((e) => e.flag_key)).toEqual([
      "f0",
      "f1",
      "later0",
    ]);
  });

  it("retries a 503 with a backoff from 1 s", async () => {
    const server = fakeServer(503, 503, 202);
    make(server);
    fill(1);

    await batcher!.flush();
    await advance(999);
    expect(server.calls).toHaveLength(1);
    await advance(1);
    expect(server.calls).toHaveLength(2);
    // The second wait is 2 s with up to 20 % jitter off.
    await advance(1_599);
    expect(server.calls).toHaveLength(2);
    await advance(401);

    expect(server.calls).toHaveLength(3);
    expect(exposures.size).toBe(0);
  });

  it("retries after a network failure", async () => {
    const server = fakeServer("network", 202);
    make(server);
    fill(1);

    await batcher!.flush();
    expect(exposures.size).toBe(1);
    await advance(1_000);

    expect(server.calls).toHaveLength(2);
    expect(exposures.size).toBe(0);
  });

  it.each([401, 403])("calls onFatal on %i and stops", async (status) => {
    const server = fakeServer(status, 202);
    make(server).start();
    fill(1);

    await batcher!.flush();
    fill(1, "later");
    await batcher!.flush();
    await advance(120_000);

    expect(fatal).toEqual([status]);
    expect(server.calls).toHaveLength(1);
  });

  it("sets keepalive for a small body", async () => {
    const server = fakeServer(202);
    make(server);
    fill(10);

    await batcher!.flush({ keepalive: true });

    expect(server.calls[0]?.init.keepalive).toBe(true);
  });

  it("sends a 70 KiB body as a normal request", async () => {
    const server = fakeServer(202);
    make(server);
    for (let i = 0; i < 70; i++) {
      exposures.record(`${"x".repeat(1024)}-${i}`, "on", "STATIC", USER1);
    }

    await batcher!.flush({ keepalive: true });

    const init = server.calls[0]!.init;
    expect(String(init.body).length).toBeGreaterThan(70 * 1024);
    expect(init.keepalive).toBeUndefined();
  });

  it("close resolves within 5 s when the server never answers", async () => {
    const server = fakeServer("hang");
    make(server).start();
    fill(1);

    let done = false;
    void batcher!.close(5_000).then(() => (done = true));
    await advance(4_999);
    expect(done).toBe(false);
    await advance(1);

    expect(done).toBe(true);
    expect(server.calls).toHaveLength(1);
  });

  it("does not start another send while closing", async () => {
    const server = fakeServer("hang");
    make(server);
    fill(1);

    void batcher!.close(5_000);
    await advance(0);
    fill(1, "late");
    void batcher!.flush();
    await advance(0);

    expect(server.calls).toHaveLength(1);
  });

  it("close flushes once; a second close returns at once", async () => {
    const server = fakeServer(202);
    make(server).start();
    fill(1);

    await batcher!.close(5_000);
    expect(server.sizes()).toEqual([1]);

    fill(1, "late");
    let done = false;
    void batcher!.close(5_000).then(() => (done = true));
    await Promise.resolve();
    await Promise.resolve();

    expect(done).toBe(true);
    await advance(60_000);
    expect(server.calls).toHaveLength(1);
  });

  it("stop sends nothing more, even on close", async () => {
    const server = fakeServer(202);
    make(server).start();
    fill(2);

    batcher!.stop();
    await advance(60_000);
    await batcher!.close(5_000);

    expect(server.calls).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});
