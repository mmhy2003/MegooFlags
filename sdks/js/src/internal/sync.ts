// Keeps the store current for the current context: the first values
// (bootstrap, cache, then the network), refreshes on stream changes, the
// polling fallback, context changes and the stop rules.
import type { EvaluationContext, JsonValue } from "@openfeature/web-sdk";
import type { Cache } from "./cache.js";
import { contextHash, targetingKeyOf, toRequestContext } from "./context.js";
import type { EvaluateOutcome, Evaluator } from "./evaluator.js";
import { Backoff, sleep } from "./backoff.js";
import type { ResolvedOptions } from "./options.js";
import { Poller } from "./poller.js";
import { changedFlags, type Store, storedFrom } from "./store.js";
import type { Streamer, StreamerHooks } from "./streamer.js";
import {
  FatalError,
  HttpError,
  type Logger,
  RetryAfterError,
} from "./transport.js";

export interface SyncEvents {
  /** ready: values arrived after start failed, or the stream is back after stale. */
  ready(): void;
  /** stale: the stream, connected before, keeps failing. */
  stale(): void;
  /** changed: a refresh changed these flags of the previous values. */
  changed(flags: string[]): void;
  /** fatal: the key was refused after start; everything has stopped. */
  fatal(reason: number | "revoked"): void;
  /** error: a refresh failed; it is retried. */
  error(e: unknown): void;
}

/** The part of Streamer that Sync drives; its hooks are Sync.streamerHooks. */
export type StreamControl = Pick<
  Streamer,
  "start" | "pause" | "resume" | "stop"
>;

const OFFLINE_MESSAGE =
  "megooflags: offline mode needs bootstrap values or a cache entry";

/** A refresh() caller waiting for the first run numbered run or later. */
interface Waiter {
  run: number;
  resolve(): void;
  reject(err: unknown): void;
}

const noop = () => {};

const errorName = (err: unknown) =>
  err instanceof Error ? err.name : String(err);

export class Sync {
  /** The hooks the streamer must be built with. */
  readonly streamerHooks: StreamerHooks;
  readonly #opts: ResolvedOptions;
  readonly #store: Store;
  readonly #cache: Cache;
  readonly #evaluator: Pick<Evaluator, "evaluate"> | null;
  readonly #streamer: StreamControl | null;
  readonly #events: SyncEvents;
  readonly #onFatalStop: () => void;
  readonly #logger: Logger;
  readonly #poller: Poller;
  readonly #backoff = new Backoff(1_000, 60_000);

  #body: Record<string, JsonValue> = {};
  #hash = contextHash({});
  /** Incremented by every context change; older answers are dropped. */
  #gen = 0;
  /** The number of evaluate runs started. */
  #runs = 0;
  #inFlight: AbortController | null = null;
  #queued = false;
  #waiters: Waiter[] = [];
  #retry: AbortController | null = null;
  #cacheOps: Promise<void> = Promise.resolve();

  /** Set once stopped or fatal: the error later refresh() calls reject with. */
  #halted: Error | null = null;
  #starting = false;
  /** start failed without a fatal error: emit ready when values arrive. */
  #readyPending = false;
  #deliveryStarted = false;
  #connected = false;
  #stale = false;
  #paused = false;
  #pollerWasRunning = false;

  constructor(o: {
    opts: ResolvedOptions;
    store: Store;
    cache: Cache;
    evaluator: Pick<Evaluator, "evaluate"> | null;
    streamer: StreamControl | null;
    events: SyncEvents;
    onFatalStop: () => void;
  }) {
    this.#opts = o.opts;
    this.#store = o.store;
    this.#cache = o.cache;
    this.#evaluator = o.opts.offline ? null : o.evaluator;
    this.#streamer = o.opts.offline ? null : o.streamer;
    this.#events = o.events;
    this.#onFatalStop = o.onFatalStop;
    this.#logger = o.opts.logger;
    this.#poller = new Poller(o.opts.pollIntervalMs, () => this.refresh());
    this.streamerHooks = {
      onConnected: () => this.#onConnected(),
      onChanged: () => void this.refresh().catch(noop),
      onFatal: (reason) => this.#stopAll(new FatalError(reason)),
      onFailure: (consecutive) => this.#onStreamFailure(consecutive),
    };
  }

  /** body is the request context of the current evaluation context. */
  get body(): Record<string, JsonValue> {
    return this.#body;
  }

  /**
   * start serves bootstrap values, then a cache entry for context. With
   * values it resolves at once and refreshes in the background; without, it
   * waits for the first refresh up to initTimeoutMs. It rejects with the
   * FatalError of a refused key, or an Error on timeout, another failure or
   * offline without values; refresh keeps retrying unless fatal.
   */
  async start(context: EvaluationContext): Promise<void> {
    this.#starting = true;
    try {
      await this.#start(toRequestContext(context));
    } finally {
      this.#starting = false;
    }
  }

  async #start(body: Record<string, JsonValue>): Promise<void> {
    this.#setBody(body);
    const hash = this.#hash;
    const targetingKey = targetingKeyOf(body);
    if (this.#opts.bootstrap !== null) {
      this.#store.set(
        storedFrom(this.#opts.bootstrap, {
          etag: null,
          source: "bootstrap",
          contextHash: hash,
          targetingKey,
        }),
      );
    }
    const cached = await this.#cache.load(hash);
    if (cached !== undefined && this.#hash === hash) {
      this.#store.set({ ...cached, targetingKey });
    }
    if (this.#evaluator === null) {
      if (this.#store.get() === undefined) throw new Error(OFFLINE_MESSAGE);
      return;
    }
    const first = this.refresh();
    const deliver = () => this.#startDelivery();
    first.then(deliver, deliver);
    if (this.#store.get() !== undefined) {
      first.catch(noop);
      return;
    }
    const timeout = new AbortController();
    const ms = this.#opts.initTimeoutMs;
    try {
      const inTime = await Promise.race([
        first.then(() => true),
        sleep(ms, timeout.signal).then(() => false),
      ]);
      if (!inTime) {
        throw new Error(`megooflags: no flag values within ${ms / 1000}s`);
      }
    } catch (err) {
      this.#readyPending = this.#halted === null;
      throw err;
    } finally {
      timeout.abort();
    }
  }

  /**
   * refresh evaluates the current context. At most one evaluate is in
   * flight and one queued; calls while one is queued share it. It resolves
   * when a run started after the call stores values for the current context
   * and rejects when it fails (a retry follows unless fatal). Offline it
   * resolves at once.
   */
  refresh(): Promise<void> {
    if (this.#halted !== null) return Promise.reject(this.#halted);
    if (this.#evaluator === null) return Promise.resolve();
    const done = new Promise<void>((resolve, reject) => {
      this.#waiters.push({ run: this.#runs + 1, resolve, reject });
    });
    this.#kick();
    return done;
  }

  /**
   * setContext switches to context. A new targeting key clears the store
   * and the cache entry at once; other changes keep serving the old values
   * until the new ones arrive. It resolves or rejects as refresh() does for
   * the new context. Offline it only records the context.
   */
  setContext(context: EvaluationContext): Promise<void> {
    const body = toRequestContext(context);
    if (this.#evaluator === null || this.#halted !== null) {
      this.#setBody(body);
      return this.refresh();
    }
    const keyChanged = targetingKeyOf(body) !== targetingKeyOf(this.#body);
    this.#gen++;
    this.#setBody(body);
    this.#inFlight?.abort();
    if (keyChanged) {
      this.#store.set(undefined);
      this.#cacheOp(() => this.#cache.remove());
    }
    return this.refresh();
  }

  /** pause closes the stream and stops the poller (the app is hidden). */
  pause(): void {
    if (this.#halted !== null || this.#paused) return;
    this.#paused = true;
    this.#pollerWasRunning = this.#poller.running;
    this.#poller.stop();
    this.#streamer?.pause();
  }

  /** resume reopens the stream, and the poller when it was running. */
  resume(): void {
    if (this.#halted !== null || !this.#paused) return;
    this.#paused = false;
    if (!this.#deliveryStarted) {
      this.#startDelivery();
      return;
    }
    this.#streamer?.resume();
    if (this.#pollerWasRunning) this.#poller.start();
  }

  /** stop ends the stream, the poller and every refresh for good. */
  stop(): void {
    this.#halt(new Error("megooflags: provider shut down"));
  }

  #setBody(body: Record<string, JsonValue>): void {
    this.#body = body;
    this.#hash = contextHash(body);
  }

  #kick(): void {
    if (this.#inFlight !== null) {
      this.#queued = true;
      return;
    }
    void this.#run();
  }

  async #run(): Promise<void> {
    this.#retry?.abort();
    this.#retry = null;
    const run = ++this.#runs;
    const gen = this.#gen;
    const body = this.#body;
    const hash = this.#hash;
    const ctl = new AbortController();
    this.#inFlight = ctl;
    const stored = this.#store.get();
    const etag = stored?.contextHash === hash ? stored.etag : null;
    let outcome: EvaluateOutcome | undefined;
    let error: unknown;
    try {
      outcome = await this.#evaluator!.evaluate(body, etag, ctl.signal);
    } catch (err) {
      error = err;
    }
    this.#inFlight = null;
    if (this.#halted !== null) return;
    if (gen === this.#gen) {
      if (outcome !== undefined) {
        error = this.#apply(outcome, body, hash);
      }
      if (error === undefined) {
        this.#settle(run);
      } else {
        this.#failed(run, error);
      }
    }
    if (this.#queued && this.#halted === null) {
      this.#queued = false;
      void this.#run();
    }
  }

  /** apply stores an answer for the current context; it returns an error for a 304 it cannot use. */
  #apply(
    outcome: EvaluateOutcome,
    body: Record<string, JsonValue>,
    hash: string,
  ): Error | undefined {
    const old = this.#store.get();
    if (outcome.kind === "notModified") {
      if (old?.contextHash !== hash) {
        return new HttpError(304, "megooflags: unexpected 304 without values");
      }
      this.#store.set({
        ...old,
        etag: outcome.etag ?? old.etag,
        source: "network",
      });
    } else {
      const next = storedFrom(outcome.results, {
        etag: outcome.etag,
        source: "network",
        contextHash: hash,
        targetingKey: targetingKeyOf(body),
      });
      this.#store.set(next);
      this.#cacheOp(() => this.#cache.save(next));
      if (this.#readyPending) {
        this.#readyPending = false;
        this.#call(() => this.#events.ready());
      } else if (old !== undefined) {
        const changed = changedFlags(old, next);
        if (changed.length > 0) {
          this.#call(() => this.#events.changed(changed));
        }
      }
    }
    this.#backoff.reset();
    return undefined;
  }

  #failed(run: number, err: unknown): void {
    if (err instanceof FatalError) {
      this.#stopAll(err);
      return;
    }
    this.#logger.debug(`megooflags: evaluate failed: ${errorName(err)}`);
    this.#call(() => this.#events.error(err));
    this.#settle(run, err);
    if (this.#queued) return;
    const wait =
      err instanceof RetryAfterError
        ? err.seconds * 1000
        : this.#backoff.next();
    const retry = new AbortController();
    this.#retry = retry;
    void sleep(wait, retry.signal).then((elapsed) => {
      if (!elapsed || this.#retry !== retry) return;
      this.#retry = null;
      this.#kick();
    });
  }

  /** settle resolves, or rejects with err, the waiters served by run. */
  #settle(run: number, err?: unknown): void {
    const served = this.#waiters.filter((w) => w.run <= run);
    this.#waiters = this.#waiters.filter((w) => w.run > run);
    for (const w of served) {
      if (err === undefined) w.resolve();
      else w.reject(err);
    }
  }

  #startDelivery(): void {
    if (this.#halted !== null || this.#deliveryStarted || this.#paused) return;
    this.#deliveryStarted = true;
    if (this.#opts.disableStream || this.#streamer === null) {
      this.#poller.start();
    } else {
      this.#streamer.start();
    }
  }

  #onConnected(): void {
    if (this.#halted !== null) return;
    this.#poller.stop();
    this.#connected = true;
    if (this.#stale) {
      this.#stale = false;
      this.#call(() => this.#events.ready());
    }
  }

  #onStreamFailure(consecutive: number): void {
    if (this.#halted !== null || consecutive < 2) return;
    if (!this.#paused) this.#poller.start();
    if (this.#connected && !this.#stale) {
      this.#stale = true;
      this.#call(() => this.#events.stale());
    }
  }

  /**
   * stopAll stops everything for a refused key and keeps the store. While
   * start runs, its rejection reports the error instead of a fatal event.
   */
  #stopAll(err: FatalError): void {
    if (this.#halted !== null) return;
    this.#halt(err);
    this.#call(this.#onFatalStop);
    if (!this.#starting) this.#call(() => this.#events.fatal(err.status));
  }

  #halt(err: Error): void {
    if (this.#halted !== null) return;
    this.#halted = err;
    this.#inFlight?.abort();
    this.#retry?.abort();
    this.#retry = null;
    this.#queued = false;
    this.#poller.stop();
    this.#streamer?.stop();
    const waiters = this.#waiters;
    this.#waiters = [];
    for (const w of waiters) w.reject(err);
  }

  /** cacheOp runs op after the previous cache write, so writes keep their order. */
  #cacheOp(op: () => Promise<void>): void {
    this.#cacheOps = this.#cacheOps.then(op);
  }

  /** call runs an event callback; an error it throws is logged. */
  #call(fn: () => void): void {
    try {
      fn();
    } catch (err) {
      this.#logger.error("megooflags: event callback failed", err);
    }
  }
}
