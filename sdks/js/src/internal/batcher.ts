// Sends the buffered exposures to POST /sdk/v1/events in the background: on
// an interval, at 1000 buffered items, on request (the app was hidden) and on
// close. No counts are ever sent: the server counts remote evaluations.
import { Backoff, timerDelay } from "./backoff.js";
import { BATCH_SIZE, type Exposure, type Exposures } from "./exposures.js";
import {
  buildHeaders,
  FatalError,
  HttpError,
  type Logger,
  RetryAfterError,
  sdkUrl,
  statusError,
} from "./transport.js";

/** Browsers refuse keepalive bodies over 64 KiB in total; stay under 60. */
const KEEPALIVE_MAX_BYTES = 60 * 1024;

/** The longest one request may take before it is aborted and retried. */
const REQUEST_TIMEOUT_MS = 10_000;

/** The items of a batch to put back for a retry; null when none. */
type Unsent = Exposure[] | null;

export class Batcher {
  readonly #url: string;
  readonly #sdkKey: string;
  readonly #fetch: typeof fetch;
  readonly #exposures: Exposures;
  readonly #flushIntervalMs: number;
  readonly #logger: Logger;
  readonly #onFatal: (status: number) => void;
  readonly #backoff = new Backoff(1_000, 60_000);
  /** Aborts the request in flight when the batcher stops. */
  readonly #controller = new AbortController();
  #interval: ReturnType<typeof setInterval> | undefined;
  #retry: ReturnType<typeof setTimeout> | undefined;
  #flushing: Promise<void> | undefined;
  #closing: Promise<void> | undefined;
  /** The epoch milliseconds before which a flush sends nothing. */
  #nextAttempt = 0;
  #stopped = false;

  constructor(o: {
    baseUrl: string;
    sdkKey: string;
    fetch: typeof fetch;
    exposures: Exposures;
    flushIntervalMs: number;
    logger: Logger;
    onFatal: (status: number) => void;
  }) {
    this.#url = sdkUrl(o.baseUrl, "events");
    this.#sdkKey = o.sdkKey;
    this.#fetch = o.fetch;
    this.#exposures = o.exposures;
    this.#flushIntervalMs = o.flushIntervalMs;
    this.#logger = o.logger;
    this.#onFatal = o.onFatal;
    o.exposures.onFull = () => void this.flush();
  }

  /** start runs the flush timer; it is idempotent. */
  start(): void {
    if (this.#interval !== undefined || this.#stopped || this.#closing) return;
    this.#interval = setInterval(
      () => void this.flush(),
      timerDelay(this.#flushIntervalMs),
    );
    unref(this.#interval);
  }

  /**
   * flush sends the buffered exposures in batches of at most 1000, joining
   * the flush in flight if there is one. It sends nothing while a retry
   * wait runs and stops at the first batch that must be retried. keepalive
   * lets a request outlive the page when its body is small enough. It never
   * rejects.
   */
  flush(opts: { keepalive?: boolean } = {}): Promise<void> {
    if (this.#closing !== undefined) {
      // close sends the last batches itself.
      return this.#closing;
    }
    if (this.#flushing === undefined) {
      this.#flushing = this.#run(opts.keepalive ?? false, false).finally(
        () => (this.#flushing = undefined),
      );
    }
    return this.#flushing;
  }

  /**
   * close stops the timers and flushes once, ignoring a retry wait, then
   * stops for good; it resolves within timeoutMs, aborting a request still
   * in flight. It is idempotent.
   */
  close(timeoutMs = 5_000): Promise<void> {
    if (this.#closing === undefined) {
      this.#clearTimers();
      let timer: ReturnType<typeof setTimeout> | undefined;
      const deadline = new Promise<void>((resolve) => {
        timer = setTimeout(resolve, timerDelay(timeoutMs));
      });
      const final = (async () => {
        await this.#flushing;
        await this.#run(false, true);
      })();
      this.#closing = Promise.race([final, deadline]).then(() => {
        clearTimeout(timer);
        this.#stop();
      });
    }
    return this.#closing;
  }

  async #run(keepalive: boolean, final: boolean): Promise<void> {
    if (!final && Date.now() < this.#nextAttempt) return;
    while (!this.#stopped && this.#exposures.size > 0) {
      const unsent = await this.#send(
        this.#exposures.drain(BATCH_SIZE),
        keepalive,
      );
      if (unsent !== null) {
        if (!this.#stopped) this.#exposures.restore(unsent);
        return;
      }
    }
  }

  /**
   * send posts batch, splitting it on 413, and returns what must be
   * retried, after scheduling the retry.
   */
  async #send(batch: Exposure[], keepalive: boolean): Promise<Unsent> {
    if (this.#stopped) return null;
    const body = JSON.stringify({ exposures: batch });
    const small = new TextEncoder().encode(body).length < KEEPALIVE_MAX_BYTES;
    const request = new AbortController();
    const abort = () => request.abort();
    this.#controller.signal.addEventListener("abort", abort);
    const timeout = setTimeout(abort, REQUEST_TIMEOUT_MS);
    unref(timeout);
    let err: Error | undefined;
    try {
      const res = await this.#fetch(this.#url, {
        method: "POST",
        headers: buildHeaders({
          sdkKey: this.#sdkKey,
          accept: "application/json",
          json: true,
        }),
        body,
        credentials: "omit",
        ...(keepalive && small ? { keepalive: true } : {}),
        signal: request.signal,
      });
      await res.body?.cancel().catch(() => {});
      err = statusError(res.status, (name) => res.headers.get(name));
    } catch (e) {
      err = e instanceof Error ? e : new Error(String(e));
    } finally {
      clearTimeout(timeout);
      this.#controller.signal.removeEventListener("abort", abort);
    }
    if (this.#stopped) return null;

    if (err === undefined) {
      this.#backoff.reset();
      this.#logger.debug(`megooflags: sent ${batch.length} exposures`);
      return null;
    }
    if (err instanceof FatalError) {
      this.#stop();
      this.#onFatal(err.status as number);
      return null;
    }
    if (err instanceof HttpError && err.status === 413) {
      return this.#split(batch, keepalive);
    }
    if (err instanceof HttpError && err.status < 500) {
      this.#logger.warn(
        `megooflags: the server refused ${batch.length} exposures (HTTP ${err.status}); dropping them`,
      );
      return null;
    }
    const wait =
      err instanceof RetryAfterError
        ? err.seconds * 1000
        : this.#backoff.next();
    this.#logger.debug(
      `megooflags: sending exposures failed (${err.message}); will retry`,
    );
    this.#retryIn(wait);
    return batch;
  }

  /**
   * split sends batch as two halves; a single item cannot be split and is
   * dropped. When the first half must be retried, the second is kept
   * behind it.
   */
  async #split(batch: Exposure[], keepalive: boolean): Promise<Unsent> {
    if (batch.length <= 1) {
      this.#logger.warn(
        "megooflags: the server refused an exposure as too large (HTTP 413); dropping it",
      );
      return null;
    }
    const half = Math.floor(batch.length / 2);
    const second = batch.slice(half);
    const unsent = await this.#send(batch.slice(0, half), keepalive);
    if (unsent !== null) {
      return [...unsent, ...second];
    }
    return this.#send(second, keepalive);
  }

  #retryIn(wait: number): void {
    // Whole milliseconds, so the timer never fires before nextAttempt.
    const ms = Math.ceil(wait);
    this.#nextAttempt = Date.now() + ms;
    clearTimeout(this.#retry);
    this.#retry = setTimeout(() => void this.flush(), timerDelay(ms));
    unref(this.#retry);
  }

  #clearTimers(): void {
    clearInterval(this.#interval);
    clearTimeout(this.#retry);
    this.#interval = undefined;
    this.#retry = undefined;
  }

  #stop(): void {
    this.#stopped = true;
    this.#clearTimers();
    this.#controller.abort();
  }
}

/** unref keeps a Node.js timer (the tests) from holding the process open. */
function unref(timer: unknown): void {
  (timer as { unref?: () => void }).unref?.();
}
