// The SSE client for GET /sdk/v1/stream: reconnects with backoff until it is
// stopped or the server refuses the key.

import { Backoff, sleep } from "../transport/backoff.js";
import {
  buildHeaders,
  FatalError,
  HttpError,
  RetryAfterError,
  silentLogger,
  statusError,
  type FetchFn,
  type Logger,
} from "../transport/transport.js";
import { readSse, type SseEvent } from "./sse.js";

/** DeadConnectionError reports a stream silent for the dead timeout. */
export class DeadConnectionError extends Error {
  override readonly name = "DeadConnectionError";

  constructor(ms: number) {
    super(
      `megooflags: stream sent nothing for ${ms / 1000}s (dead connection)`,
    );
  }
}

export interface StreamerOptions {
  /** url is the full stream URL. */
  url: string;
  sdkKey: string;
  fetch?: FetchFn;
  logger?: Logger;
  /** lastEventId returns the ETag to resume from, or "". */
  lastEventId?: () => string;
  /** onPut receives a put event; false means an invalid ruleset. */
  onPut?: (raw: string, id: string) => boolean;
  onConnected?: () => void;
  onDisconnected?: (err: unknown, failures: number) => void;
  /** deadTimeoutMs is the longest silence before reconnecting (45 s). */
  deadTimeoutMs?: number;
  /** stableAfterMs is how long a connection must last to reset the backoff (60 s). */
  stableAfterMs?: number;
  /** backoff is 3 s to 60 s by default; the server's retry: replaces its base. */
  backoff?: Backoff;
}

const noop = () => {};

/** Streamer follows the ruleset stream. */
export class Streamer {
  private readonly url: string;
  private readonly sdkKey: string;
  private readonly fetch: FetchFn;
  private readonly logger: Logger;
  private readonly lastEventId: () => string;
  private readonly onPut: (raw: string, id: string) => boolean;
  private readonly onConnected: () => void;
  private readonly onDisconnected: (err: unknown, failures: number) => void;
  private readonly deadTimeoutMs: number;
  private readonly stableAfterMs: number;
  private readonly backoff: Backoff;

  constructor(opts: StreamerOptions) {
    this.url = opts.url;
    this.sdkKey = opts.sdkKey;
    this.fetch = opts.fetch ?? globalThis.fetch;
    this.logger = opts.logger ?? silentLogger;
    this.lastEventId = opts.lastEventId ?? (() => "");
    this.onPut = opts.onPut ?? (() => true);
    this.onConnected = opts.onConnected ?? noop;
    this.onDisconnected = opts.onDisconnected ?? noop;
    this.deadTimeoutMs = opts.deadTimeoutMs ?? 45_000;
    this.stableAfterMs = opts.stableAfterMs ?? 60_000;
    this.backoff = opts.backoff ?? new Backoff(3_000, 60_000);
  }

  /**
   * run streams until signal aborts, resolving undefined, or until the
   * server refuses the key, resolving the FatalError. It never rejects.
   */
  async run(signal: AbortSignal): Promise<FatalError | undefined> {
    let failures = 0;
    for (;;) {
      const { connectedAt, err } = await this.connect(signal);
      if (signal.aborted) {
        return undefined;
      }
      if (err instanceof FatalError) {
        return err;
      }
      if (
        connectedAt !== undefined &&
        Date.now() - connectedAt >= this.stableAfterMs
      ) {
        this.backoff.reset();
        failures = 0;
      }
      failures++;
      let wait = this.backoff.next();
      if (err instanceof RetryAfterError) {
        wait = Math.max(err.afterMs, wait);
      }
      this.logger.debug("megooflags: stream disconnected", {
        error: String(err),
        failures,
        retryInMs: Math.round(wait),
      });
      try {
        this.onDisconnected(err, failures);
      } catch (cbErr) {
        this.logger.error("megooflags: stream callback failed", cbErr);
      }
      if (!(await sleep(wait, signal))) {
        return undefined;
      }
    }
  }

  /**
   * connect opens one stream and reads it until it ends. connectedAt is set
   * when the server answered 200. It never rejects.
   */
  private async connect(
    signal: AbortSignal,
  ): Promise<{ connectedAt: number | undefined; err: unknown }> {
    const conn = new AbortController();
    const abort = () => conn.abort();
    signal.addEventListener("abort", abort, { once: true });
    let connectedAt: number | undefined;
    let dead = false;
    let timer: NodeJS.Timeout | undefined;
    try {
      const headers = buildHeaders(this.sdkKey, { stream: true });
      const id = this.lastEventId();
      if (id !== "") {
        headers["Last-Event-ID"] = id;
      }
      const res = await this.fetch(this.url, {
        method: "GET",
        headers,
        signal: conn.signal,
      });
      const bad =
        statusError(res.status, res.headers, Date.now()) ??
        (res.status === 200 ? undefined : new HttpError(res.status));
      if (bad !== undefined || res.body === null) {
        await res.body?.cancel().catch(noop);
        return { connectedAt, err: bad ?? new HttpError(res.status) };
      }
      connectedAt = Date.now();
      this.onConnected();
      timer = setTimeout(() => {
        dead = true;
        conn.abort();
      }, this.deadTimeoutMs);
      timer.unref();
      const t = timer;
      await readSse(res.body, {
        onLine: () => t.refresh(),
        onRetry: (ms) => {
          this.backoff.base = ms;
        },
        onEvent: (ev) => this.onEvent(ev),
      });
      return { connectedAt, err: new Error("megooflags: stream ended") };
    } catch (err) {
      if (dead && !signal.aborted) {
        return {
          connectedAt,
          err: new DeadConnectionError(this.deadTimeoutMs),
        };
      }
      return { connectedAt, err };
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      conn.abort();
    }
  }

  private onEvent(ev: SseEvent): void {
    switch (ev.name) {
      case "put":
        if (!this.onPut(ev.data, ev.id)) {
          this.logger.warn(
            "megooflags: stream sent an invalid ruleset; keeping the previous one",
            { eventId: ev.id },
          );
        }
        break;
      case "revoked":
        throw new FatalError(undefined, "revoked");
    }
  }
}
