// The provider's options, their defaults and their validation.

import type { FetchFn, Logger } from "./internal/transport/transport.js";

/**
 * MegooFlagsConfig configures a MegooFlagsProvider. Only sdkKey and baseUrl
 * (or, with offline, a bootstrapFile or cacheDir) are required. Durations
 * are in milliseconds.
 */
export interface MegooFlagsConfig {
  /** A server SDK key (mf_server_…); a client key is refused. */
  sdkKey: string;
  /** The MegooFlags URL, for example https://flags.example.com. */
  baseUrl?: string;
  /** A ruleset JSON file loaded at construction. */
  bootstrapFile?: string;
  /** Where the last-known ruleset is read at start and written on change. */
  cacheDir?: string;
  /** No network: the bootstrap file and the cache only. */
  offline?: boolean;
  /** How long initialize waits for the first ruleset; 5 s. */
  initTimeoutMs?: number;
  /** Poll only, without the stream. */
  disableStream?: boolean;
  /** The time between polls; 30 s, at least 15 s. */
  pollIntervalMs?: number;
  /** The time between event batches; 30 s, clamped to 10 to 60 s. */
  flushIntervalMs?: number;
  /** Include the evaluation context in exposures. */
  sendContext?: boolean;
  /** Where the SDK logs; console. */
  logger?: Logger;
  /** The fetch used for every request; the global fetch. */
  fetch?: FetchFn;
}

/** ResolvedConfig is a config with its defaults filled in. */
export interface ResolvedConfig {
  sdkKey: string;
  baseUrl: string;
  bootstrapFile: string;
  cacheDir: string;
  offline: boolean;
  initTimeoutMs: number;
  disableStream: boolean;
  pollIntervalMs: number;
  flushIntervalMs: number;
  sendContext: boolean;
  logger: Logger;
  fetch: FetchFn;
}

const DEFAULT_INIT_TIMEOUT_MS = 5_000;
const DEFAULT_POLL_INTERVAL_MS = 30_000;
const MIN_POLL_INTERVAL_MS = 15_000;
const DEFAULT_FLUSH_INTERVAL_MS = 30_000;
const MIN_FLUSH_INTERVAL_MS = 10_000;
const MAX_FLUSH_INTERVAL_MS = 60_000;

/** positive returns ms when it is a positive number, else fallback. */
const positive = (ms: number | undefined, fallback: number) =>
  ms !== undefined && ms > 0 ? ms : fallback;

/** withDefaults fills in the defaults and clamps the intervals. */
export function withDefaults(c: MegooFlagsConfig): ResolvedConfig {
  const flush = positive(c.flushIntervalMs, DEFAULT_FLUSH_INTERVAL_MS);
  return {
    sdkKey: c.sdkKey,
    baseUrl: (c.baseUrl ?? "").replace(/\/+$/, ""),
    bootstrapFile: c.bootstrapFile ?? "",
    cacheDir: c.cacheDir ?? "",
    offline: c.offline ?? false,
    initTimeoutMs: positive(c.initTimeoutMs, DEFAULT_INIT_TIMEOUT_MS),
    disableStream: c.disableStream ?? false,
    pollIntervalMs: Math.max(
      positive(c.pollIntervalMs, DEFAULT_POLL_INTERVAL_MS),
      MIN_POLL_INTERVAL_MS,
    ),
    flushIntervalMs: Math.min(
      Math.max(flush, MIN_FLUSH_INTERVAL_MS),
      MAX_FLUSH_INTERVAL_MS,
    ),
    sendContext: c.sendContext ?? false,
    logger: c.logger ?? console,
    fetch: c.fetch ?? globalThis.fetch,
  };
}

/**
 * validateConfig returns the reason the provider cannot run with c, or
 * undefined. The messages never include the key.
 */
export function validateConfig(c: ResolvedConfig): Error | undefined {
  const key = typeof c.sdkKey === "string" ? c.sdkKey : "";
  if (key.startsWith("mf_client_")) {
    return new Error(
      "megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)",
    );
  }
  if (!key.startsWith("mf_server_")) {
    return new Error(
      "megooflags: invalid SDK key; use a server key (mf_server_…)",
    );
  }
  if (c.offline) {
    if (c.bootstrapFile === "" && c.cacheDir === "") {
      return new Error(
        "megooflags: offline needs a bootstrapFile or a cacheDir",
      );
    }
    return undefined;
  }
  let url: URL | undefined;
  try {
    url = new URL(c.baseUrl);
  } catch {
    url = undefined;
  }
  if (
    url === undefined ||
    (url.protocol !== "http:" && url.protocol !== "https:") ||
    url.hostname === ""
  ) {
    return new Error("megooflags: baseUrl must be an http or https URL");
  }
  return undefined;
}
