// One download of GET /sdk/v1/ruleset. The poll loop and its interval
// belong to Sync.

import { timerDelay } from "../transport/backoff.js";
import {
  buildHeaders,
  HttpError,
  statusError,
  type FetchFn,
} from "../transport/transport.js";

export interface PollerOptions {
  /** url is the full ruleset URL. */
  url: string;
  sdkKey: string;
  fetch?: FetchFn;
  /** timeoutMs bounds the whole request, body included (10 s). */
  timeoutMs?: number;
  /** etag returns the ETag sent as If-None-Match when not empty. */
  etag?: () => string;
  /** onRuleset receives a 200 body; false means an invalid ruleset. */
  onRuleset?: (raw: string, etag: string) => boolean;
}

/** Poller downloads the ruleset. */
export class Poller {
  private readonly url: string;
  private readonly sdkKey: string;
  private readonly fetch: FetchFn;
  private readonly timeoutMs: number;
  private readonly etag: () => string;
  private readonly onRuleset: (raw: string, etag: string) => boolean;

  constructor(opts: PollerOptions) {
    this.url = opts.url;
    this.sdkKey = opts.sdkKey;
    this.fetch = opts.fetch ?? globalThis.fetch;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.etag = opts.etag ?? (() => "");
    this.onRuleset = opts.onRuleset ?? (() => true);
  }

  /**
   * poll downloads the ruleset once. A 304 keeps what we have; a 200 goes
   * to onRuleset. It rejects with statusError's error for other statuses,
   * with a timeout error after timeoutMs, and with the fetch error for a
   * network failure or an aborted signal.
   */
  async poll(signal: AbortSignal): Promise<void> {
    const req = new AbortController();
    const abort = () => req.abort(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      req.abort();
    }, timerDelay(this.timeoutMs));
    timer.unref();
    try {
      if (signal.aborted) {
        throw signal.reason;
      }
      const headers = buildHeaders(this.sdkKey);
      const etag = this.etag();
      if (etag !== "") {
        headers["If-None-Match"] = etag;
      }
      const res = await this.fetch(this.url, {
        method: "GET",
        headers,
        signal: req.signal,
      });
      if (res.status !== 200) {
        await res.body?.cancel().catch(() => {});
        if (res.status === 304) {
          return;
        }
        throw (
          statusError(res.status, res.headers, Date.now()) ??
          new HttpError(res.status)
        );
      }
      const raw = await res.text();
      if (!this.onRuleset(raw, res.headers.get("ETag") ?? "")) {
        throw new Error("megooflags: invalid ruleset");
      }
    } catch (err) {
      if (timedOut) {
        throw new Error(
          `megooflags: ruleset poll timed out after ${this.timeoutMs / 1000}s`,
        );
      }
      throw err;
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
    }
  }
}
