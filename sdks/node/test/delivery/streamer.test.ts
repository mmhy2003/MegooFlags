import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  Streamer,
  type StreamerOptions,
} from "../../src/internal/delivery/streamer.js";
import { Backoff } from "../../src/internal/transport/backoff.js";
import {
  FatalError,
  RetryAfterError,
  sdkUrl,
  USER_AGENT,
} from "../../src/internal/transport/transport.js";
import { FakeServer } from "../helpers/fake-server.js";

const KEY = "mf_server_streamer-key-value";
const STREAM = "/sdk/v1/stream";

let server: FakeServer;
let controller: AbortController;
let running: Promise<FatalError | undefined> | undefined;

interface Seen {
  puts: { raw: string; id: string }[];
  connected: number;
  disconnects: { err: unknown; failures: number }[];
}

function startStreamer(opts: Partial<StreamerOptions> = {}): Seen {
  const seen: Seen = { puts: [], connected: 0, disconnects: [] };
  const streamer = new Streamer({
    url: sdkUrl(server.url, "stream"),
    sdkKey: KEY,
    onPut: (raw, id) => {
      seen.puts.push({ raw, id });
      return raw !== "invalid";
    },
    onConnected: () => seen.connected++,
    onDisconnected: (err, failures) => seen.disconnects.push({ err, failures }),
    backoff: new Backoff(10, 100, () => 1),
    ...opts,
  });
  running = streamer.run(controller.signal);
  return seen;
}

beforeEach(async () => {
  server = await FakeServer.start();
  controller = new AbortController();
  running = undefined;
});

afterEach(async () => {
  controller.abort();
  await running;
  await server.stop();
});

describe("Streamer", () => {
  it("connects with the stream headers and hands put events to onPut", async () => {
    server.reply(STREAM, { stream: true });
    const seen = startStreamer();
    const conn = await server.nextStream();
    conn.write('event: put\nid: "e1"\ndata: {"a":1}\n\n');
    await vi.waitFor(() => expect(seen.puts).toHaveLength(1));
    expect(seen.puts[0]).toEqual({ raw: '{"a":1}', id: '"e1"' });
    expect(seen.connected).toBe(1);
    const req = conn.request;
    expect(req.method).toBe("GET");
    expect(req.headers["authorization"]).toBe(`Bearer ${KEY}`);
    expect(req.headers["accept"]).toBe("text/event-stream");
    expect(req.headers["cache-control"]).toBe("no-cache");
    expect(req.headers["user-agent"]).toBe(USER_AGENT);
    expect(req.headers["last-event-id"]).toBeUndefined();
    expect(req.url).toBe(STREAM);
    expect(req.url).not.toContain(KEY);
  });

  it("sends Last-Event-ID when there is an ETag", async () => {
    server.reply(STREAM, { stream: true });
    startStreamer({ lastEventId: () => '"etag-7"' });
    const conn = await server.nextStream();
    expect(conn.request.headers["last-event-id"]).toBe('"etag-7"');
  });

  it("stays connected while pings arrive", async () => {
    server.reply(STREAM, { stream: true });
    const seen = startStreamer({ deadTimeoutMs: 150 });
    const conn = await server.nextStream();
    for (let i = 0; i < 8; i++) {
      await new Promise((r) => setTimeout(r, 50));
      conn.write(": ping\n");
    }
    expect(server.received(STREAM)).toHaveLength(1);
    expect(seen.disconnects).toEqual([]);
  });

  it("reconnects a connection that sends nothing", async () => {
    server.reply(STREAM, { stream: true });
    const seen = startStreamer({ deadTimeoutMs: 50 });
    await server.nextStream();
    await server.nextStream();
    expect(seen.disconnects[0]?.failures).toBe(1);
    expect(String(seen.disconnects[0]?.err)).toContain("dead");
  });

  it("ignores unknown events and keeps reading after an invalid put", async () => {
    server.reply(STREAM, { stream: true });
    const seen = startStreamer();
    const conn = await server.nextStream();
    conn.write("event: mystery\ndata: x\n\n");
    conn.write("data: unnamed\n\n");
    conn.write("event: put\nid: 1\ndata: invalid\n\n");
    conn.write("event: put\nid: 2\ndata: {}\n\n");
    await vi.waitFor(() => expect(seen.puts).toHaveLength(2));
    expect(seen.puts.map((p) => p.id)).toEqual(["1", "2"]);
    expect(seen.disconnects).toEqual([]);
    expect(server.received(STREAM)).toHaveLength(1);
  });

  it("stops with a fatal error on revoked", async () => {
    server.reply(STREAM, { stream: true });
    const seen = startStreamer();
    const conn = await server.nextStream();
    conn.write("event: revoked\ndata: {}\n\n");
    const err = await running;
    expect(err).toBeInstanceOf(FatalError);
    expect(err?.reason).toBe("revoked");
    expect(seen.disconnects).toEqual([]);
    await conn.closed;
    expect(server.received(STREAM)).toHaveLength(1);
  });

  it.each([401, 403])("stops with a fatal error on %i", async (status) => {
    server.reply(STREAM, { status });
    const seen = startStreamer();
    const err = await running;
    expect(err).toBeInstanceOf(FatalError);
    expect(err?.status).toBe(status);
    expect(seen.connected).toBe(0);
    expect(seen.disconnects).toEqual([]);
    expect(server.received(STREAM)).toHaveLength(1);
  });

  it("waits the Retry-After of a 429", async () => {
    server.reply(
      STREAM,
      { status: 429, headers: { "Retry-After": "1" } },
      { stream: true },
    );
    const seen = startStreamer();
    await server.nextStream();
    const [first, second] = server.received(STREAM);
    expect(second!.at - first!.at).toBeGreaterThanOrEqual(950);
    expect(seen.disconnects[0]?.err).toBeInstanceOf(RetryAfterError);
  });

  it("backs off with growing waits on 5xx", async () => {
    server.reply(
      STREAM,
      { status: 500 },
      { status: 502 },
      { status: 503 },
      { status: 500 },
      { stream: true },
    );
    const seen = startStreamer({ backoff: new Backoff(40, 1000, () => 1) });
    await server.nextStream();
    const at = server.received(STREAM).map((r) => r.at);
    const gaps = at.slice(1).map((t, i) => t - at[i]!);
    expect(gaps).toHaveLength(4);
    // 40, 80, 160, 320 ms, with some slack for the event loop.
    [40, 80, 160, 320].forEach((want, i) => {
      expect(gaps[i]).toBeGreaterThanOrEqual(want - 5);
    });
    expect(seen.disconnects.map((d) => d.failures)).toEqual([1, 2, 3, 4]);
  });

  it("uses the server's retry field as the backoff base", async () => {
    server.reply(STREAM, { stream: true });
    const seen = startStreamer({ backoff: new Backoff(5000, 60000, () => 1) });
    const conn = await server.nextStream();
    conn.write("retry: 20\n\n");
    await vi.waitFor(() => expect(seen.connected).toBe(1));
    conn.end();
    const started = Date.now();
    await server.nextStream();
    expect(Date.now() - started).toBeLessThan(1000);
  });

  it("resets the backoff after a stable connection", async () => {
    server.reply(
      STREAM,
      { status: 500 },
      { status: 500 },
      { stream: true },
      { status: 500 },
      { stream: true },
    );
    const seen = startStreamer({ stableAfterMs: 50 });
    const conn = await server.nextStream();
    await new Promise((r) => setTimeout(r, 80));
    conn.end();
    await server.nextStream();
    expect(seen.disconnects.map((d) => d.failures)).toEqual([1, 2, 1, 2]);
  });

  it("returns promptly when stopped", async () => {
    server.reply(STREAM, { stream: true });
    const seen = startStreamer();
    const conn = await server.nextStream();
    const started = Date.now();
    controller.abort();
    expect(await running).toBeUndefined();
    expect(Date.now() - started).toBeLessThan(500);
    await conn.closed;
    expect(seen.disconnects).toEqual([]);
  });

  it("returns at once when stopped while waiting to reconnect", async () => {
    server.reply(STREAM, { status: 500 });
    const seen = startStreamer({ backoff: new Backoff(60000, 60000) });
    await vi.waitFor(() => expect(seen.disconnects).toHaveLength(1));
    controller.abort();
    expect(await running).toBeUndefined();
  });
});
