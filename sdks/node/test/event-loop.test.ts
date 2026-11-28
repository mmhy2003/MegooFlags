// The SDK's timers are unref'd so they never keep a process alive, except
// while the application is waiting on the SDK: initialize and shutdown must
// settle even when nothing else holds the event loop open.

import { createServer } from "node:net";
import { afterAll, afterEach, beforeAll, beforeEach, expect, it } from "vitest";
import { FakeServer } from "./helpers/fake-server.js";
import { runChild, transpileSdk, type ChildSdk } from "./helpers/child.js";

const KEY = "mf_server_event-loop-key-value";
const EVENTS = "/sdk/v1/events";

let sdk: ChildSdk;
let server: FakeServer;

beforeAll(() => {
  sdk = transpileSdk();
});

afterAll(() => {
  sdk.remove();
});

beforeEach(async () => {
  server = await FakeServer.start();
});

afterEach(async () => {
  await server.stop();
});

/** closedPort returns a local port nothing listens on. */
async function closedPort(): Promise<number> {
  const s = createServer();
  await new Promise<void>((r) => s.listen(0, "127.0.0.1", r));
  const port = (s.address() as { port: number }).port;
  await new Promise((r) => s.close(r));
  return port;
}

it("rejects setProviderAndWait after initTimeoutMs when nothing else keeps the process alive", async () => {
  const port = await closedPort();
  const res = await runChild(
    sdk,
    `
import { OpenFeature } from "@openfeature/server-sdk";
import { MegooFlagsProvider } from "./src/index.js";
const quiet = { debug() {}, info() {}, warn() {}, error() {} };
const p = new MegooFlagsProvider({
  sdkKey: ${JSON.stringify(KEY)},
  baseUrl: "http://127.0.0.1:${port}",
  initTimeoutMs: 400,
  logger: quiet,
});
const started = Date.now();
try {
  await OpenFeature.setProviderAndWait(p);
  console.log("ready");
} catch (e) {
  console.log("rejected after " + (Date.now() - started) + ": " + e.message);
}
await OpenFeature.close();
console.log("closed");
`,
  );
  expect(res.stderr).toBe("");
  expect(res.code).toBe(0);
  expect(res.stdout).toMatch(
    /^rejected after \d+: megooflags: no ruleset within 400 ms\nclosed\n$/,
  );
  const waited = Number(/after (\d+)/.exec(res.stdout)![1]);
  expect(waited).toBeGreaterThanOrEqual(390);
});

it("shutdown waits out an events retry when nothing else keeps the process alive", async () => {
  server.reply(EVENTS, { status: 503 }, { status: 202 });
  const res = await runChild(
    sdk,
    `
import { Batcher } from "./src/internal/events/batcher.js";
const b = new Batcher({
  url: ${JSON.stringify(`${server.url}${EVENTS}`)},
  sdkKey: ${JSON.stringify(KEY)},
  flushIntervalMs: 3_600_000,
  retryBaseMs: 300,
  retryMaxMs: 300,
});
b.start();
b.record({
  flagKey: "f",
  variant: "on",
  served: true,
  reason: "STATIC",
  contextKey: "u1",
  at: Date.now(),
});
console.log("shutdown " + (await b.shutdown(3_000)));
`,
  );
  expect(res.stderr).toBe("");
  expect(res.code).toBe(0);
  expect(res.stdout).toBe("shutdown true\n");
  const sent = server.received(EVENTS);
  expect(sent.map((r) => r.at)).toHaveLength(2);
  expect(sent[1]!.at - sent[0]!.at).toBeGreaterThanOrEqual(250);
  expect(JSON.parse(sent[1]!.body)).toMatchObject({
    exposures: [{ flag_key: "f" }],
    counts: [{ flag_key: "f", count: 1 }],
  });
});
