import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { webStreamOpener } from "../src/web-platform.js";
import { FakeServer, STREAM } from "./helpers/fake-server.js";

let server: FakeServer;

beforeEach(async () => {
  server = await FakeServer.start();
});

afterEach(async () => {
  await server.stop();
});

const text = (chunk: Uint8Array | undefined) => new TextDecoder().decode(chunk);

describe("webStreamOpener", () => {
  it("streams chunks as the server writes them", async () => {
    server.reply(STREAM, { stream: true });
    const open = webStreamOpener(fetch);

    const res = await open(
      `${server.url}${STREAM}`,
      { Accept: "text/event-stream", "Last-Event-ID": "e1" },
      new AbortController().signal,
    );
    const conn = await server.nextStream();

    expect(res.status).toBe(200);
    expect(res.header("content-type")).toBe("text/event-stream");
    expect(conn.request.headers.accept).toBe("text/event-stream");
    expect(conn.lastEventId).toBe("e1");

    const it = res.body[Symbol.asyncIterator]();
    conn.send(": ping\n");
    expect(text((await it.next()).value)).toBe(": ping\n");
    conn.send("event: changed\n");
    expect(text((await it.next()).value)).toBe("event: changed\n");

    conn.end();
    expect((await it.next()).done).toBe(true);
  });

  it("returning from the body cancels the response", async () => {
    server.reply(STREAM, { stream: true });
    const res = await webStreamOpener(fetch)(
      `${server.url}${STREAM}`,
      {},
      new AbortController().signal,
    );
    const conn = await server.nextStream();

    const it = res.body[Symbol.asyncIterator]();
    await it.return?.();

    await conn.closed;
  });

  it("reports a non-200 status and its headers", async () => {
    server.reply(STREAM, {
      status: 429,
      headers: { "Retry-After": "20" },
      body: "",
    });
    const res = await webStreamOpener(fetch)(
      `${server.url}${STREAM}`,
      {},
      new AbortController().signal,
    );
    expect(res.status).toBe(429);
    expect(res.header("Retry-After")).toBe("20");
    await res.body[Symbol.asyncIterator]().return?.();
  });

  it("aborting the signal ends the body with an error", async () => {
    server.reply(STREAM, { stream: true });
    const ac = new AbortController();
    const res = await webStreamOpener(fetch)(
      `${server.url}${STREAM}`,
      {},
      ac.signal,
    );
    await server.nextStream();
    const it = res.body[Symbol.asyncIterator]();
    const next = it.next();
    ac.abort();
    await expect(next).rejects.toThrow();
  });
});
