import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { Poller } from "../../src/internal/delivery/poller.js";
import {
  FatalError,
  HttpError,
  RetryAfterError,
  sdkUrl,
  USER_AGENT,
} from "../../src/internal/transport/transport.js";
import { FakeServer } from "../helpers/fake-server.js";

const KEY = "mf_server_poller-key-value";
const RULESET = "/sdk/v1/ruleset";

let server: FakeServer;
let etag: string;
let got: { raw: string; etag: string }[];

function poller(timeoutMs?: number): Poller {
  return new Poller({
    url: sdkUrl(server.url, "ruleset"),
    sdkKey: KEY,
    etag: () => etag,
    onRuleset: (raw, e) => {
      got.push({ raw, etag: e });
      if (raw === "invalid") {
        return false;
      }
      etag = e;
      return true;
    },
    ...(timeoutMs === undefined ? {} : { timeoutMs }),
  });
}

const poll = (p: Poller) => p.poll(new AbortController().signal);

beforeEach(async () => {
  server = await FakeServer.start();
  etag = "";
  got = [];
});

afterEach(async () => {
  await server.stop();
});

describe("Poller", () => {
  it("polls without If-None-Match first, then with the ETag", async () => {
    server.reply(
      RULESET,
      { status: 200, headers: { ETag: '"v1"' }, body: "{}" },
      { status: 304 },
    );
    const p = poller();
    await poll(p);
    await poll(p);
    const [first, second] = server.received(RULESET);
    expect(first!.headers["if-none-match"]).toBeUndefined();
    expect(first!.headers["authorization"]).toBe(`Bearer ${KEY}`);
    expect(first!.headers["accept"]).toBe("application/json");
    expect(first!.headers["user-agent"]).toBe(USER_AGENT);
    expect(first!.url).not.toContain(KEY);
    expect(second!.headers["if-none-match"]).toBe('"v1"');
  });

  it("hands a 200 body and its ETag to onRuleset", async () => {
    server.reply(RULESET, {
      status: 200,
      headers: { ETag: '"v2"' },
      body: '{"flags":{}}',
    });
    await poll(poller());
    expect(got).toEqual([{ raw: '{"flags":{}}', etag: '"v2"' }]);
  });

  it("treats 304 as success without calling onRuleset", async () => {
    etag = '"v1"';
    server.reply(RULESET, { status: 304 });
    await expect(poll(poller())).resolves.toBeUndefined();
    expect(got).toEqual([]);
  });

  it("fails on an invalid ruleset", async () => {
    server.reply(RULESET, { status: 200, body: "invalid" });
    await expect(poll(poller())).rejects.toThrow(/invalid ruleset/);
  });

  it.each([401, 403])("fails with a fatal error on %i", async (status) => {
    server.reply(RULESET, { status });
    const err = await poll(poller()).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(FatalError);
    expect((err as FatalError).status).toBe(status);
  });

  it("reports the Retry-After of a 429", async () => {
    server.reply(RULESET, { status: 429, headers: { "Retry-After": "7" } });
    const err = await poll(poller()).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RetryAfterError);
    expect((err as RetryAfterError).afterMs).toBe(7000);
  });

  it("fails with an HTTP error on 500", async () => {
    server.reply(RULESET, { status: 500, body: "oops" });
    const err = await poll(poller()).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(HttpError);
    expect((err as HttpError).status).toBe(500);
  });

  it("times out", async () => {
    // A held-open response never finishes its body.
    server.reply(RULESET, { stream: true });
    const started = Date.now();
    await expect(poll(poller(100))).rejects.toThrow(/timed out/);
    expect(Date.now() - started).toBeLessThan(2000);
    expect(got).toEqual([]);
  });

  it("stops when the signal aborts", async () => {
    server.reply(RULESET, { stream: true });
    const controller = new AbortController();
    const p = poller().poll(controller.signal);
    await server.nextStream();
    controller.abort();
    await expect(p).rejects.toThrow();
  });
});
