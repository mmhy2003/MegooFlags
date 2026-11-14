// Sends the aggregated counts and exposures to POST /sdk/v1/events in the
// background: on an interval, when a list reaches 1000 items, and on
// shutdown.

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
import { Aggregator, type Batch, type Evaluation } from "./aggregate.js";

/** MAX_BODY_BYTES is the largest events body the server accepts. */
const MAX_BODY_BYTES = 1_000_000;

export interface BatcherOptions {
  /** url is the full events URL. */
  url: string;
  sdkKey: string;
  fetch?: FetchFn;
  logger?: Logger;
  /** flushIntervalMs is used as given (the provider clamps it); 30 s. */
  flushIntervalMs?: number;
  dedupSize?: number;
  maxPending?: number;
  maxBuckets?: number;
  /** maxItems is the most items of each list in one batch (1000). */
  maxItems?: number;
  sendContext?: boolean;
  /** onFatal is called once when the server refuses the key. */
  onFatal?: (err: FatalError) => void;
  /** timeoutMs bounds one POST (10 s). */
  timeoutMs?: number;
  retryBaseMs?: number;
  retryMaxMs?: number;
}

/** The result of sending a batch: retry means unsent must be kept. */
interface Sent {
  unsent: Batch;
  retry: boolean;
}

const none = (): Sent => ({
  unsent: { exposures: [], counts: [] },
  retry: false,
});

/**
 * Batcher aggregates evaluations and posts them in the background. Only one
 * flush runs at a time; nothing it does rejects unhandled.
 */
export class Batcher {
  private readonly url: string;
  private readonly sdkKey: string;
  private readonly fetch: FetchFn;
  private readonly logger: Logger;
  private readonly flushIntervalMs: number;
  private readonly maxItems: number;
  private readonly onFatal: (err: FatalError) => void;
  private readonly timeoutMs: number;
  private readonly agg: Aggregator;
  private readonly backoff: Backoff;

  /** controller aborts a send in flight on stop(), fatal or the deadline. */
  private readonly controller = new AbortController();
  private timer: NodeJS.Timeout | undefined;
  private flushing: Promise<void> | undefined;
  private shutdownResult: Promise<boolean> | undefined;
  /** closed: record() drops from now on. */
  private closed = false;
  /** stopped: stop(), a fatal status or the deadline ended sending. */
  private stopped = false;
  private nextAttempt = 0;
  private droppedRecords = 0;

  constructor(opts: BatcherOptions) {
    this.url = opts.url;
    this.sdkKey = opts.sdkKey;
    this.fetch = opts.fetch ?? globalThis.fetch;
    this.logger = opts.logger ?? silentLogger;
    this.flushIntervalMs = opts.flushIntervalMs ?? 30_000;
    this.maxItems = opts.maxItems ?? 1000;
    this.onFatal = opts.onFatal ?? (() => {});
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.agg = new Aggregator({
      ...(opts.dedupSize === undefined ? {} : { dedupSize: opts.dedupSize }),
      ...(opts.maxPending === undefined ? {} : { maxPending: opts.maxPending }),
      ...(opts.maxBuckets === undefined ? {} : { maxBuckets: opts.maxBuckets }),
      sendContext: opts.sendContext ?? false,
    });
    this.backoff = new Backoff(
      opts.retryBaseMs ?? 2_000,
      opts.retryMaxMs ?? 60_000,
    );
  }

  /** dropped counts the evaluations and exposures that were not kept. */
  get dropped(): number {
    return this.droppedRecords + this.agg.dropped;
  }

  /** start runs the flush timer; a no-op once started, stopped or shut. */
  start(): void {
    if (this.timer !== undefined || this.closed) {
      return;
    }
    this.timer = setInterval(() => void this.flush(), this.flushIntervalMs);
    this.timer.unref();
  }

  /**
   * record adds ev to the aggregator. It is synchronous and does no I/O;
   * after stop or shutdown it drops and counts ev. Reaching maxItems
   * pending items starts a flush without waiting for it.
   */
  record(ev: Evaluation): void {
    if (this.closed) {
      this.droppedRecords++;
      return;
    }
    this.agg.add(ev);
    const p = this.agg.pending();
    if (p.exposures >= this.maxItems || p.counts >= this.maxItems) {
      void this.flush();
    }
  }

  /**
   * flush sends pending items in batches, joining the flush in progress if
   * there is one. It does nothing before the retry time and stops at the
   * first failure.
   */
  flush(): Promise<void> {
    if (this.flushing === undefined) {
      this.flushing = this.runFlush().finally(() => {
        this.flushing = undefined;
      });
    }
    return this.flushing;
  }

  /**
   * shutdown stops accepting records and sends what is pending within
   * deadlineMs, waiting out a retry delay when it fits. It resolves true
   * when everything pending was handled, false when it was stopped or ran
   * out of time. It is idempotent.
   */
  shutdown(deadlineMs = 5_000): Promise<boolean> {
    if (this.shutdownResult === undefined) {
      this.close();
      this.shutdownResult = this.drain(deadlineMs);
    }
    return this.shutdownResult;
  }

  /** stop ends sending without sending what is pending; idempotent. */
  async stop(): Promise<void> {
    this.halt();
    await this.flushing;
  }

  private close(): void {
    this.closed = true;
    clearInterval(this.timer);
  }

  private halt(): void {
    this.close();
    this.stopped = true;
    this.controller.abort();
  }

  private async drain(deadlineMs: number): Promise<boolean> {
    const deadline = Date.now() + deadlineMs;
    const timer = setTimeout(() => this.halt(), deadlineMs);
    timer.unref();
    try {
      await this.flushing;
      while (!this.stopped && this.hasPending()) {
        const wait = this.nextAttempt - Date.now();
        if (wait > 0) {
          if (Date.now() + wait > deadline) {
            this.halt();
            break;
          }
          await sleep(wait, this.controller.signal);
        }
        await this.flush();
      }
      return !this.stopped;
    } finally {
      clearTimeout(timer);
    }
  }

  private hasPending(): boolean {
    const p = this.agg.pending();
    return p.exposures > 0 || p.counts > 0;
  }

  private async runFlush(): Promise<void> {
    if (Date.now() < this.nextAttempt) {
      return;
    }
    while (!this.stopped && this.hasPending()) {
      const { unsent, retry } = await this.send(this.agg.take(this.maxItems));
      if (retry) {
        if (!this.stopped) {
          this.agg.restore(unsent);
        }
        return;
      }
    }
  }

  /** send posts batch, splitting it when it is too large. */
  private async send(batch: Batch): Promise<Sent> {
    if (this.stopped) {
      return none();
    }
    const body = JSON.stringify(batch);
    if (Buffer.byteLength(body) > MAX_BODY_BYTES) {
      return this.split(batch, "the body is over 1 MB");
    }

    let err: Error | undefined;
    const signal = AbortSignal.any([
      this.controller.signal,
      AbortSignal.timeout(this.timeoutMs),
    ]);
    try {
      const res = await this.fetch(this.url, {
        method: "POST",
        headers: buildHeaders(this.sdkKey, { body: true }),
        body,
        signal,
      });
      await res.body?.cancel().catch(() => {});
      err = statusError(res.status, res.headers, Date.now());
    } catch (e) {
      this.retryIn(this.backoff.next());
      this.logger.debug("megooflags: sending events failed; will retry", e);
      return { unsent: batch, retry: true };
    }

    if (err === undefined) {
      this.backoff.reset();
      return none();
    }
    if (err instanceof FatalError) {
      this.halt();
      try {
        this.onFatal(err);
      } catch (e) {
        this.logger.error("megooflags: onFatal threw", e);
      }
      return none();
    }
    if (err instanceof RetryAfterError) {
      this.retryIn(Math.max(err.afterMs, this.backoff.next()));
      this.logger.debug("megooflags: events rate limited; will retry");
      return { unsent: batch, retry: true };
    }
    if (err instanceof HttpError && err.status === 413) {
      return this.split(batch, "the server refused the body as too large");
    }
    if (err instanceof HttpError && err.status >= 500) {
      this.retryIn(this.backoff.next());
      this.logger.debug(
        `megooflags: sending events failed (HTTP ${err.status}); will retry`,
      );
      return { unsent: batch, retry: true };
    }
    this.logger.warn(
      `megooflags: the server refused an events batch (${err.message}); ` +
        `dropping ${batch.exposures.length} exposures and ${batch.counts.length} counts`,
    );
    return none();
  }

  /**
   * split sends batch as two halves, exposures first. A single item cannot
   * be split and is dropped. When the first half must be retried, the
   * second is kept unsent too.
   */
  private async split(batch: Batch, why: string): Promise<Sent> {
    const n = batch.exposures.length + batch.counts.length;
    if (n <= 1) {
      this.logger.warn(
        `megooflags: an events item is too large (${why}); dropping it`,
      );
      return none();
    }
    const h = Math.floor(n / 2);
    const ne = Math.min(h, batch.exposures.length);
    const first: Batch = {
      exposures: batch.exposures.slice(0, ne),
      counts: batch.counts.slice(0, h - ne),
    };
    const second: Batch = {
      exposures: batch.exposures.slice(ne),
      counts: batch.counts.slice(h - ne),
    };
    const sent = await this.send(first);
    if (this.stopped) {
      return none();
    }
    if (sent.retry) {
      return {
        unsent: {
          exposures: [...sent.unsent.exposures, ...second.exposures],
          counts: [...sent.unsent.counts, ...second.counts],
        },
        retry: true,
      };
    }
    return this.send(second);
  }

  private retryIn(ms: number): void {
    this.nextAttempt = Date.now() + ms;
  }
}
