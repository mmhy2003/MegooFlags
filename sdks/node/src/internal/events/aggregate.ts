// Evaluation counts per UTC hour and deduplicated exposures, waiting to be
// sent to POST /sdk/v1/events (spec/delivery.md §7).

/** MAX_COUNT is the largest count the server accepts in one count item. */
const MAX_COUNT = 1_000_000_000;

const HOUR_MS = 3_600_000;

/** Evaluation is one flag evaluation as recorded by the provider. */
export interface Evaluation {
  flagKey: string;
  variant: string;
  /** served is false when the default was returned (error, not ready). */
  served: boolean;
  reason: string;
  /** contextKey is "" when the context key is not a string. */
  contextKey: string;
  context?: object;
  /** at is the evaluation time in epoch milliseconds. */
  at: number;
}

/** Exposure records that a context saw a variant. */
export interface Exposure {
  flag_key: string;
  variant: string | null;
  reason: string;
  timestamp: string;
  context?: object;
}

/** Count is the evaluations of one flag and variant in one UTC hour. */
export interface Count {
  flag_key: string;
  variant: string | null;
  hour: string;
  count: number;
}

/** Batch is the body of POST /sdk/v1/events. */
export interface Batch {
  exposures: Exposure[];
  counts: Count[];
}

/** Lru is a fixed-size set that forgets the least recently seen key. */
export class Lru {
  private readonly keys = new Set<string>();
  private readonly cap: number;

  constructor(capacity: number) {
    this.cap = Math.max(capacity, 1);
  }

  get size(): number {
    return this.keys.size;
  }

  /**
   * seen reports whether key is present and marks it as the most recent. A
   * new key is inserted, evicting the oldest one above the capacity.
   */
  seen(key: string): boolean {
    if (this.keys.delete(key)) {
      this.keys.add(key);
      return true;
    }
    this.keys.add(key);
    if (this.keys.size > this.cap) {
      this.keys.delete(this.keys.values().next().value as string);
    }
    return false;
  }
}

interface Bucket {
  flag: string;
  /** variant is null for evaluations that served no variant. */
  variant: string | null;
  hour: number;
  count: number;
}

export interface AggregatorOptions {
  /** dedupSize is the number of exposure keys remembered (10 000). */
  dedupSize?: number;
  /** maxPending caps the exposures waiting to be sent (10 000). */
  maxPending?: number;
  /**
   * maxBuckets caps the count buckets waiting to be sent (10 000). It stands
   * in for the bounded queue of multi-threaded runtimes.
   */
  maxBuckets?: number;
  sendContext?: boolean;
}

const bucketKey = (flag: string, variant: string | null, hour: number) =>
  JSON.stringify([flag, variant, hour]);

const hourString = (hour: number) =>
  new Date(hour).toISOString().replace(/\.\d{3}Z$/, "Z");

/**
 * Aggregator buffers exposures and hour-bucketed counts. It is synchronous
 * and does no I/O, so recording an evaluation never waits.
 */
export class Aggregator {
  /** dropped counts the evaluations and exposures that did not fit. */
  dropped = 0;

  private readonly dedup: Lru;
  private readonly maxPending: number;
  private readonly maxBuckets: number;
  private readonly sendContext: boolean;
  private exposures: Exposure[] = [];
  /** buckets keeps the order in which the buckets were first seen. */
  private buckets = new Map<string, Bucket>();

  constructor(opts: AggregatorOptions = {}) {
    this.dedup = new Lru(opts.dedupSize ?? 10_000);
    this.maxPending = opts.maxPending ?? 10_000;
    this.maxBuckets = opts.maxBuckets ?? 10_000;
    this.sendContext = opts.sendContext ?? false;
  }

  /**
   * add counts ev and, the first time a served variant is seen for its
   * context key, flag and hour, queues an exposure. An evaluation that needs
   * a new bucket beyond maxBuckets is dropped whole; an exposure beyond
   * maxPending is dropped alone. Both are counted in dropped.
   */
  add(ev: Evaluation): void {
    const hour = Math.floor(ev.at / HOUR_MS) * HOUR_MS;
    const variant = ev.served ? ev.variant : null;
    const key = bucketKey(ev.flagKey, variant, hour);
    const bucket = this.buckets.get(key);
    if (bucket !== undefined) {
      bucket.count++;
    } else if (this.buckets.size >= this.maxBuckets) {
      this.dropped++;
      return;
    } else {
      this.buckets.set(key, { flag: ev.flagKey, variant, hour, count: 1 });
    }

    if (
      !ev.served ||
      this.dedup.seen(
        JSON.stringify([ev.contextKey, ev.flagKey, ev.variant, hour]),
      )
    ) {
      return;
    }
    if (this.exposures.length >= this.maxPending) {
      this.dropped++;
      return;
    }
    const e: Exposure = {
      flag_key: ev.flagKey,
      variant: ev.variant,
      reason: ev.reason,
      timestamp: new Date(ev.at).toISOString(),
    };
    if (this.sendContext && ev.context !== undefined) {
      e.context = ev.context;
    }
    this.exposures.push(e);
  }

  pending(): { exposures: number; counts: number } {
    return { exposures: this.exposures.length, counts: this.buckets.size };
  }

  /**
   * take removes and returns up to limit exposures (oldest first) and up to
   * limit count items (buckets in first-seen order). A bucket above 1e9
   * becomes several items; what does not fit stays for the next batch.
   */
  take(limit: number): Batch {
    const batch: Batch = {
      exposures: this.exposures.splice(0, limit),
      counts: [],
    };
    for (const [key, b] of this.buckets) {
      if (batch.counts.length >= limit) {
        break;
      }
      while (b.count > 0 && batch.counts.length < limit) {
        const count = Math.min(b.count, MAX_COUNT);
        b.count -= count;
        batch.counts.push({
          flag_key: b.flag,
          variant: b.variant,
          hour: hourString(b.hour),
          count,
        });
      }
      if (b.count > 0) {
        break;
      }
      this.buckets.delete(key);
    }
    return batch;
  }

  /**
   * restore puts an unsent batch back in front of what is pending, so a
   * retry sends it first. Its counts add to any newer count for the same
   * bucket.
   */
  restore(batch: Batch): void {
    this.exposures = [...batch.exposures, ...this.exposures];
    const buckets = new Map<string, Bucket>();
    for (const c of batch.counts) {
      const hour = Date.parse(c.hour);
      const key = bucketKey(c.flag_key, c.variant, hour);
      const b = buckets.get(key);
      if (b !== undefined) {
        b.count += c.count;
      } else {
        buckets.set(key, {
          flag: c.flag_key,
          variant: c.variant,
          hour,
          count: c.count,
        });
      }
    }
    for (const [key, b] of this.buckets) {
      const restored = buckets.get(key);
      if (restored !== undefined) {
        restored.count += b.count;
      } else {
        buckets.set(key, b);
      }
    }
    this.buckets = buckets;
  }
}
