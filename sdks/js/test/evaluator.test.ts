import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Evaluator } from "../src/internal/evaluator.js";
import {
  FatalError,
  HttpError,
  RetryAfterError,
} from "../src/internal/transport.js";
import { EVALUATE, evaluated, FakeServer } from "./helpers/fake-server.js";

const KEY = "mf_client_secret123";

const RESULT = {
  key: "new-checkout",
  value: true,
  variant: "on",
  reason: "TARGETING_MATCH",
  error_code: null,
  error_message: null,
};

let server: FakeServer;
let evaluator: Evaluator;

beforeEach(async () => {
  server = await FakeServer.start();
  evaluator = new Evaluator({ baseUrl: server.url, sdkKey: KEY, fetch });
});

afterEach(async () => {
  await server.stop();
});

describe("Evaluator", () => {
  it("returns the results and the ETag of a 200", async () => {
    server.reply(EVALUATE, evaluated([RESULT], { etag: '"e1"' }));

    const out = await evaluator.evaluate({ key: "user-1" }, null);

    expect(out).toEqual({ kind: "ok", results: [RESULT], etag: '"e1"' });
  });

  it("returns a null ETag when the response has none", async () => {
    server.reply(EVALUATE, evaluated([RESULT]));

    const out = await evaluator.evaluate({ key: "user-1" }, null);

    expect(out).toEqual({ kind: "ok", results: [RESULT], etag: null });
  });

  it("posts the context, without flags, with the key as a bearer token", async () => {
    server.reply(EVALUATE, evaluated([]));

    await evaluator.evaluate({ key: "user-1", plan: "pro" }, null);

    const [req] = server.received(EVALUATE);
    expect(req?.method).toBe("POST");
    expect(JSON.parse(req?.body ?? "")).toEqual({
      context: { key: "user-1", plan: "pro" },
    });
    expect(req?.headers.authorization).toBe(`Bearer ${KEY}`);
    expect(req?.headers["content-type"]).toBe("application/json");
    expect(req?.headers.accept).toBe("application/json");
    expect(req?.headers["if-none-match"]).toBeUndefined();
  });

  it("sends If-None-Match when an ETag is given", async () => {
    server.reply(EVALUATE, evaluated([]));

    await evaluator.evaluate({}, '"e1"');

    expect(server.received(EVALUATE)[0]?.headers["if-none-match"]).toBe('"e1"');
  });

  it("reports a 304 as not modified", async () => {
    server.reply(EVALUATE, { status: 304, headers: { ETag: '"e1"' } });

    const out = await evaluator.evaluate({}, '"e1"');

    expect(out).toEqual({ kind: "notModified", etag: '"e1"' });
  });

  it.each([401, 403])("throws FatalError on %i", async (status) => {
    server.reply(EVALUATE, { status });

    const err = await evaluator.evaluate({}, null).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(FatalError);
    expect((err as FatalError).status).toBe(status);
  });

  it("throws RetryAfterError with the Retry-After seconds on 429", async () => {
    server.reply(EVALUATE, { status: 429, headers: { "Retry-After": "7" } });

    const err = await evaluator.evaluate({}, null).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(RetryAfterError);
    expect((err as RetryAfterError).seconds).toBe(7);
  });

  it("throws HttpError on 503", async () => {
    server.reply(EVALUATE, { status: 503 });

    const err = await evaluator.evaluate({}, null).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(503);
  });

  it.each([
    ["not JSON", "<html>"],
    ["results missing", "{}"],
    ["an invalid result", JSON.stringify({ results: [{ key: 1 }] })],
  ])("throws HttpError(0) on a body with %s", async (_, body) => {
    server.reply(EVALUATE, { status: 200, body });

    const err = await evaluator.evaluate({}, null).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(0);
    expect((err as Error).message).toMatch(/^megooflags: /);
    expect((err as Error).message).not.toContain(KEY);
  });

  it("is cancelled by the abort signal", async () => {
    server.reply(EVALUATE, evaluated([RESULT], { delayMs: 5000 }));
    const ctl = new AbortController();

    const pending = evaluator.evaluate({}, null, ctl.signal);
    setTimeout(() => ctl.abort(), 20);
    const err = await pending.catch((e: unknown) => e);

    expect((err as Error).name).toBe("AbortError");
  });
});
