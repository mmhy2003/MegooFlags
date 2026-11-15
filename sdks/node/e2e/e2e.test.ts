// Runs the provider against a running MegooFlags stack. Set
// MEGOOFLAGS_E2E_URL (for example http://localhost:8080) and
// MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh prints one) to run it.

import { randomBytes } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { OpenFeature, ProviderEvents } from "@openfeature/server-sdk";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { MegooFlagsProvider } from "../src/index.js";

const BASE = (process.env.MEGOOFLAGS_E2E_URL ?? "").replace(/\/+$/, "");
const TOKEN = process.env.MEGOOFLAGS_API_TOKEN ?? "";
const REQUEST_TIMEOUT_MS = 10_000;

/** api calls the management API and returns the status and the JSON body. */
async function api(
  method: string,
  path: string,
  body?: unknown,
): Promise<{ status: number; json: Record<string, unknown> }> {
  const res = await fetch(BASE + path, {
    method,
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  const text = await res.text();
  return {
    status: res.status,
    json: text === "" ? {} : (JSON.parse(text) as Record<string, unknown>),
  };
}

/** mustApi calls api and fails unless the status is want. */
async function mustApi(
  want: number,
  method: string,
  path: string,
  body?: unknown,
): Promise<Record<string, unknown>> {
  const { status, json } = await api(method, path, body);
  if (status !== want) {
    throw new Error(
      `${method} ${path}: status ${status}, want ${want}: ${JSON.stringify(json)}`,
    );
  }
  return json;
}

/** until polls check every intervalMs until it holds or timeoutMs passes. */
async function until(
  what: string,
  check: () => Promise<boolean> | boolean,
  timeoutMs: number,
  intervalMs = 50,
): Promise<void> {
  const end = Date.now() + timeoutMs;
  while (!(await check())) {
    if (Date.now() > end) {
      throw new Error(`${what}: not within ${timeoutMs} ms`);
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

/** closedPort returns a local port nothing listens on. */
async function closedPort(): Promise<number> {
  const srv = createServer();
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r));
  const addr = srv.address();
  await new Promise<void>((r) => srv.close(() => r()));
  if (addr === null || typeof addr === "string") {
    throw new Error("no port");
  }
  return addr.port;
}

const log = {
  debug: () => {},
  info: (msg: string, ...a: unknown[]) => console.info(msg, ...a),
  warn: (msg: string, ...a: unknown[]) => console.warn(msg, ...a),
  error: (msg: string, ...a: unknown[]) => console.error(msg, ...a),
};

describe.skipIf(BASE === "" || TOKEN === "")("end to end", () => {
  const project = `sdknode-${randomBytes(4).toString("hex")}`;
  const flagPath = `/api/projects/${project}/flags/e2e-flag/environments/e2e`;
  let cacheDir = "";
  let sdkKey = "";

  beforeAll(async () => {
    cacheDir = mkdtempSync(join(tmpdir(), "megooflags-e2e-"));
    await mustApi(201, "POST", "/api/projects", {
      key: project,
      name: project,
      environments: [{ key: "e2e", name: "E2E" }],
    });
    await mustApi(201, "POST", `/api/projects/${project}/flags`, {
      key: "e2e-flag",
      name: "E2E",
      type: "boolean",
    });
    await mustApi(200, "POST", `${flagPath}/toggle`, { enabled: true });
    const created = await mustApi(
      201,
      "POST",
      `/api/projects/${project}/environments/e2e/sdk-keys`,
      { name: "node-e2e", kind: "server" },
    );
    sdkKey = typeof created.key === "string" ? created.key : "";
    expect(sdkKey, "POST sdk-keys returned no key").not.toBe("");
  }, 30_000);

  afterAll(async () => {
    await OpenFeature.close();
    await api("DELETE", `/api/projects/${project}`);
    rmSync(cacheDir, { recursive: true, force: true });
  }, 30_000);

  it("evaluates, follows a toggle, sends counts and serves the cache", async () => {
    const provider = new MegooFlagsProvider({
      sdkKey,
      baseUrl: BASE,
      cacheDir,
      flushIntervalMs: 10_000,
      logger: log,
    });
    let changes = 0;
    provider.events.addHandler(ProviderEvents.ConfigurationChanged, () => {
      changes++;
    });
    await OpenFeature.setProviderAndWait(project, provider);
    const client = OpenFeature.getClient(project);
    const ctx = { targetingKey: "user-1" };

    // Check 1: the downloaded ruleset is evaluated.
    expect(await client.getBooleanValue("e2e-flag", false, ctx)).toBe(true);

    // Check 2: a toggle arrives over SSE within 5 s.
    await mustApi(200, "POST", `${flagPath}/toggle`, { enabled: false });
    await until(
      "e2e-flag false after the toggle",
      async () => !(await client.getBooleanValue("e2e-flag", false, ctx)),
      5_000,
    );
    await until("a configuration-changed event", () => changes > 0, 5_000);

    // Check 3: evaluation counts are sent at shutdown and accepted.
    for (let i = 0; i < 20; i++) {
      await client.getBooleanValue("e2e-flag", false, ctx);
    }
    await provider.onClose();
    let total: unknown;
    await until(
      "evaluations total >= 21",
      async () => {
        const out = await mustApi(
          200,
          "GET",
          `${flagPath}/evaluations?range=24h`,
        );
        total = out.total;
        return typeof total === "number" && total >= 21;
      },
      10_000,
      500,
    );

    // Check 4: with the API unreachable, a new provider serves the cache.
    const offline = new MegooFlagsProvider({
      sdkKey,
      baseUrl: `http://127.0.0.1:${await closedPort()}`,
      cacheDir,
      initTimeoutMs: 1_000,
      logger: log,
    });
    await OpenFeature.setProviderAndWait(`${project}-offline`, offline);
    const value = await OpenFeature.getClient(
      `${project}-offline`,
    ).getBooleanValue("e2e-flag", true, ctx);
    expect(value).toBe(false);
  }, 60_000);
});
