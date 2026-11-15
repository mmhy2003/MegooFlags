// Deduplicated exposures waiting to be sent to POST /sdk/v1/events. Recording
// runs on the read path, so it never does I/O.
import type { JsonValue } from "@openfeature/web-sdk";
import { targetingKeyOf } from "./context.js";
import type { Logger } from "./transport.js";

/** Exposure records that a context saw a variant. */
export interface Exposure {
  flag_key: string;
  variant: string | null;
  reason: string;
  timestamp: string;
  context?: Record<string, JsonValue>;
}

/** The buffered count that asks for an immediate flush. */
export const BATCH_SIZE = 1000;

export class Exposures {
  /**
   * onFull is called in a microtask when the buffer reaches BATCH_SIZE
   * items; the batcher sets it to flush.
   */
  onFull: (() => void) | undefined;

  readonly #sendContext: boolean;
  readonly #now: () => Date;
  readonly #maxKeys: number;
  readonly #maxBuffer: number;
  readonly #logger: Logger;
  /** The dedup keys, least recently seen first. */
  readonly #seen = new Map<string, true>();
  #buffer: Exposure[] = [];
  #warned = false;
  #fullScheduled = false;

  constructor(o: {
    sendContext: boolean;
    now?: () => Date;
    maxKeys?: number;
    maxBuffer?: number;
    logger: Logger;
  }) {
    this.#sendContext = o.sendContext;
    this.#now = o.now ?? (() => new Date());
    this.#maxKeys = Math.max(o.maxKeys ?? 10_000, 1);
    this.#maxBuffer = o.maxBuffer ?? 10_000;
    this.#logger = o.logger;
  }

  get size(): number {
    return this.#buffer.length;
  }

  /**
   * record buffers an exposure unless the same targeting key saw this flag
   * and variant in the current UTC hour. A full buffer drops it, warning
   * once until the next drain.
   */
  record(
    flag: string,
    variant: string,
    reason: string,
    body: Record<string, JsonValue>,
  ): void {
    const now = this.#now();
    const timestamp = now.toISOString();
    const key = `${targetingKeyOf(body) ?? ""}|${flag}|${variant}|${timestamp.slice(0, 13)}`;
    if (this.#seen.delete(key)) {
      this.#seen.set(key, true);
      return;
    }
    if (this.#buffer.length >= this.#maxBuffer) {
      this.#warnFull();
      return;
    }
    this.#seen.set(key, true);
    if (this.#seen.size > this.#maxKeys) {
      this.#seen.delete(this.#seen.keys().next().value as string);
    }
    this.#buffer.push({
      flag_key: flag,
      variant,
      reason,
      timestamp,
      ...(this.#sendContext ? { context: body } : {}),
    });
    if (this.#buffer.length >= BATCH_SIZE && !this.#fullScheduled) {
      this.#fullScheduled = true;
      queueMicrotask(() => {
        this.#fullScheduled = false;
        this.onFull?.();
      });
    }
  }

  /** drain removes and returns up to max of the oldest exposures. */
  drain(max: number): Exposure[] {
    this.#warned = false;
    return this.#buffer.splice(0, max);
  }

  /**
   * restore puts items that could not be sent back in front of the buffer;
   * the newest items over the bound are dropped.
   */
  restore(items: Exposure[]): void {
    this.#buffer = [...items, ...this.#buffer];
    if (this.#buffer.length > this.#maxBuffer) {
      this.#buffer.length = this.#maxBuffer;
      this.#warnFull();
    }
  }

  #warnFull(): void {
    if (!this.#warned) {
      this.#warned = true;
      this.#logger.warn(
        `megooflags: the exposure buffer is full (${this.#maxBuffer}); dropping new exposures`,
      );
    }
  }
}
