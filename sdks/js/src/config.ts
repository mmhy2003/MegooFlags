// The public options of the MegooFlags web provider.
import type { Logger } from "./internal/transport.js";
import type { StorageLike } from "./platform.js";

/** Options of the MegooFlags web provider. Invalid options fail at construction. */
export interface MegooFlagsOptions {
  /** A client key (mf_client_…). Server keys are refused. */
  sdkKey: string;
  /** The MegooFlags API URL (http or https); required unless offline. */
  baseUrl?: string;
  /** Results to start from, shaped like an evaluate response body. */
  bootstrap?: { results: unknown[] };
  /** Do not read or write the on-device cache. */
  disableCache?: boolean;
  /** Never call the network: serve the bootstrap or cached values only. */
  offline?: boolean;
  /** How long initialisation waits for the first evaluation; default 5000. */
  initTimeoutMs?: number;
  /** Poll instead of following the SSE stream. */
  disableStream?: boolean;
  /** The polling interval; default 30000, at least 15000. */
  pollIntervalMs?: number;
  /** How often exposures are sent; default 30000, within 10000…60000. */
  flushIntervalMs?: number;
  /** Send the evaluation context with exposures; default false. */
  sendContext?: boolean;
  /** Where the SDK logs; default: warnings and errors to the console. */
  logger?: Logger;
  /** The fetch used for every request; default: the global fetch. */
  fetch?: typeof fetch;
  /** Where the last values are cached; default: the platform's storage. */
  storage?: StorageLike;
}
