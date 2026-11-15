// Options validation and defaults, and the evaluate result shape shared by
// bootstrap, the cache and the evaluate response. Messages never contain the
// SDK key.
import type { JsonValue } from "@openfeature/web-sdk";
import type { MegooFlagsOptions } from "../config.js";
import type { StorageLike } from "../platform.js";
import { defaultLogger, type Logger } from "./transport.js";

/** One evaluated flag, as POST /sdk/v1/evaluate returns it. */
export interface EvaluateResult {
  key: string;
  value: JsonValue;
  variant: string | null;
  reason: string;
  error_code: string | null;
  error_message: string | null;
}

/** MegooFlagsOptions with every default filled and every value checked. */
export interface ResolvedOptions {
  sdkKey: string;
  /** Without trailing slashes; empty when offline without a baseUrl. */
  baseUrl: string;
  bootstrap: EvaluateResult[] | null;
  disableCache: boolean;
  offline: boolean;
  initTimeoutMs: number;
  disableStream: boolean;
  pollIntervalMs: number;
  flushIntervalMs: number;
  sendContext: boolean;
  logger: Logger;
  /** null: use the platform's fetch. */
  fetch: typeof fetch | null;
  /** null: use the platform's storage. */
  storage: StorageLike | null;
}

const CLIENT_PREFIX = "mf_client_";
const SERVER_PREFIX = "mf_server_";
const MIN_POLL_MS = 15000;
const MIN_FLUSH_MS = 10000;
const MAX_FLUSH_MS = 60000;

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const isStringOrNull = (v: unknown) => v === null || typeof v === "string";

/**
 * parseResults checks an evaluate response body ({results: [...]}) and
 * returns its results. It throws Error("<prefix>: <path>: <why>"); a missing
 * error_message becomes null, extra fields are dropped and duplicate keys are
 * refused.
 */
export function parseResults(
  raw: unknown,
  prefix = "megooflags: invalid results",
): EvaluateResult[] {
  const fail = (path: string, why: string): never => {
    throw new Error(path ? `${prefix}: ${path}: ${why}` : `${prefix}: ${why}`);
  };
  if (!isObject(raw)) fail("", "not an object");
  const items = (raw as Record<string, unknown>).results;
  if (!Array.isArray(items)) return fail("results", "not an array");
  const seen = new Set<string>();
  return items.map((item: unknown, i) => {
    const path = `results[${i}]`;
    if (!isObject(item)) return fail(path, "not an object");
    const { key, value, variant, reason, error_code } = item;
    const message = item.error_message ?? null;
    if (typeof key !== "string") fail(`${path}.key`, "not a string");
    if (value === undefined) fail(`${path}.value`, "missing");
    if (!isStringOrNull(variant))
      fail(`${path}.variant`, "not a string or null");
    if (typeof reason !== "string") fail(`${path}.reason`, "not a string");
    if (!isStringOrNull(error_code))
      fail(`${path}.error_code`, "not a string or null");
    if (!isStringOrNull(message))
      fail(`${path}.error_message`, "not a string or null");
    if (seen.has(key as string)) fail(`${path}.key`, "duplicate key");
    seen.add(key as string);
    return {
      key: key as string,
      value: value as JsonValue,
      variant: variant as string | null,
      reason: reason as string,
      error_code: error_code as string | null,
      error_message: message as string | null,
    };
  });
}

function checkKey(sdkKey: unknown): string {
  if (typeof sdkKey === "string" && sdkKey.startsWith(SERVER_PREFIX)) {
    throw new Error(
      "megooflags: a server key must never ship in an app; use a client key (mf_client_…)",
    );
  }
  if (typeof sdkKey !== "string" || !sdkKey.startsWith(CLIENT_PREFIX)) {
    throw new Error("megooflags: sdkKey must be a client key (mf_client_…)");
  }
  return sdkKey;
}

function checkBaseUrl(baseUrl: string | undefined, offline: boolean): string {
  if (baseUrl === undefined || baseUrl === "") {
    if (offline) return "";
    throw new Error("megooflags: baseUrl is required unless offline");
  }
  let url: URL;
  try {
    url = new URL(baseUrl);
  } catch {
    throw new Error("megooflags: baseUrl is not a valid URL");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("megooflags: baseUrl must be an http or https URL");
  }
  return url.href.replace(/\/+$/, "");
}

/** resolveOptions checks the options and fills the defaults; it throws on invalid options. */
export function resolveOptions(o: MegooFlagsOptions): ResolvedOptions {
  const sdkKey = checkKey(o.sdkKey);
  const offline = o.offline ?? false;
  const baseUrl = checkBaseUrl(o.baseUrl, offline);
  const initTimeoutMs = o.initTimeoutMs ?? 5000;
  if (!(initTimeoutMs > 0)) {
    throw new Error("megooflags: initTimeoutMs must be greater than 0");
  }
  const pollIntervalMs = Math.max(o.pollIntervalMs ?? 30000, MIN_POLL_MS);
  const flushIntervalMs = Math.min(
    Math.max(o.flushIntervalMs ?? 30000, MIN_FLUSH_MS),
    MAX_FLUSH_MS,
  );
  const bootstrap =
    o.bootstrap === undefined
      ? null
      : parseResults(o.bootstrap, "megooflags: invalid bootstrap");
  return {
    sdkKey,
    baseUrl,
    bootstrap,
    disableCache: o.disableCache ?? false,
    offline,
    initTimeoutMs,
    disableStream: o.disableStream ?? false,
    pollIntervalMs,
    flushIntervalMs,
    sendContext: o.sendContext ?? false,
    logger: o.logger ?? defaultLogger,
    fetch: o.fetch ?? null,
    storage: o.storage ?? null,
  };
}
