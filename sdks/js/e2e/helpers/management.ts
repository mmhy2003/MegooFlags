// The Node.js side of the end-to-end tests: management API calls with the
// API token (never from a browser page), polling, a closed port, and the
// project every client SDK test starts from. The React and React Native e2e
// tests import it too.

import { randomBytes } from "node:crypto";
import { createServer } from "node:net";

export const BASE = (process.env.MEGOOFLAGS_E2E_URL ?? "").replace(/\/+$/, "");
export const TOKEN = process.env.MEGOOFLAGS_API_TOKEN ?? "";
/** Whether the end-to-end tests run: both variables are set. */
export const ENABLED = BASE !== "" && TOKEN !== "";

const REQUEST_TIMEOUT_MS = 10_000;

/** api calls the management API and returns the status and the JSON body. */
export async function api(
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
export async function mustApi(
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
export async function until(
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
export async function closedPort(): Promise<number> {
  const srv = createServer();
  await new Promise<void>((r) => srv.listen(0, "127.0.0.1", r));
  const addr = srv.address();
  await new Promise<void>((r) => srv.close(() => r()));
  if (addr === null || typeof addr === "string") {
    throw new Error("no port");
  }
  return addr.port;
}

export interface E2EProject {
  project: string;
  /** The flag's environment path: /api/projects/{p}/flags/e2e-flag/environments/e2e. */
  flagPath: string;
  /** A client key (mf_client_…) of the e2e environment. */
  sdkKey: string;
}

/**
 * setupProject creates project sdk<lang>-<8 hex> with environment e2e, the
 * boolean flag e2e-flag turned on (serving false to vip-user and true to
 * everyone else) and a client key.
 */
export async function setupProject(lang: string): Promise<E2EProject> {
  const project = `sdk${lang}-${randomBytes(4).toString("hex")}`;
  const flagPath = `/api/projects/${project}/flags/e2e-flag/environments/e2e`;
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
  const config = await mustApi(200, "GET", flagPath);
  await mustApi(200, "PUT", flagPath, {
    version: config.version,
    enabled: true,
    targets: [{ variation: "false", values: ["vip-user"] }],
    rules: [],
    fallthrough: { variation: "true" },
    prerequisites: [],
  });
  const created = await mustApi(
    201,
    "POST",
    `/api/projects/${project}/environments/e2e/sdk-keys`,
    { name: `${lang}-e2e`, kind: "client" },
  );
  const sdkKey = typeof created.key === "string" ? created.key : "";
  if (!sdkKey.startsWith("mf_client_")) {
    throw new Error("POST sdk-keys returned no client key");
  }
  return { project, flagPath, sdkKey };
}

/** teardown deletes the project; it never fails the test. */
export async function teardown(project: string): Promise<void> {
  await api("DELETE", `/api/projects/${project}`).catch(() => undefined);
}
