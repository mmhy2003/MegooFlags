// Keeps the Store current: the stream, with polling while the stream keeps
// failing (or polling only, with disableStream).

import { parseRuleset } from "../engine/parse.js";
import { Backoff, sleep } from "../transport/backoff.js";
import {
  FatalError,
  RetryAfterError,
  sdkUrl,
  silentLogger,
  type FetchFn,
  type Logger,
} from "../transport/transport.js";
import type { Cache } from "./cache.js";
import { Poller } from "./poller.js";
import type { Snapshot, Source, Store } from "./store.js";
import { Streamer } from "./streamer.js";

/**
 * Status is the delivery state: ready (the stream is connected, or with
 * disableStream the last poll succeeded), stale (the stream or the poll
 * failed; the last ruleset is still served) or fatal (the key was refused;
 * Sync has stopped).
 */
export type Status = "ready" | "stale" | "fatal";

/** Tuning shortens the timings; only tests set it. */
export interface Tuning {
  deadTimeoutMs?: number;
  stableAfterMs?: number;
  retryBaseMs?: number;
  retryMaxMs?: number;
  /** pollAfterFailures is the stream failures in a row before polling (2). */
  pollAfterFailures?: number;
  /** pollIntervalMs replaces the poll interval without any clamp. */
  pollIntervalMs?: number;
}

export interface SyncOptions {
  baseUrl: string;
  sdkKey: string;
  fetch?: FetchFn;
  logger?: Logger;
  disableStream?: boolean;
  /** pollIntervalMs is used as given (the provider clamps it); 30 s. */
  pollIntervalMs?: number;
  store: Store;
  /** cache receives each ruleset from the stream or a poll. */
  cache?: Cache;
  onChange?: (old: Snapshot | undefined, next: Snapshot) => void;
  onStatus?: (status: Status, err?: unknown) => void;
  tuning?: Tuning;
}

const noop = () => {};

/**
 * Sync runs the stream and the poll loop as promises. Every loop catches
 * and logs, so nothing rejects unhandled. No callback runs after stop() or
 * after a fatal status.
 */
export class Sync {
  private readonly logger: Logger;
  private readonly disableStream: boolean;
  private readonly pollIntervalMs: number;
  private readonly pollAfterFailures: number;
  private readonly store: Store;
  private readonly cache: Cache | undefined;
  private readonly onChange: (
    old: Snapshot | undefined,
    next: Snapshot,
  ) => void;
  private readonly onStatus: (status: Status, err?: unknown) => void;
  private readonly streamer: Streamer;
  private readonly poller: Poller;

  private readonly controller = new AbortController();
  private readonly loops = new Set<Promise<void>>();
  private readonly logged = new Set<string>();
  private started = false;
  private fatal = false;
  private stopping: Promise<void> | undefined;
  private pollController: AbortController | undefined;
  /** nextPoll is the earliest next poll; kept across poll-loop restarts. */
  private nextPoll = 0;

  constructor(opts: SyncOptions) {
    const tuning = opts.tuning ?? {};
    this.logger = opts.logger ?? silentLogger;
    this.disableStream = opts.disableStream ?? false;
    this.pollIntervalMs =
      tuning.pollIntervalMs ?? opts.pollIntervalMs ?? 30_000;
    this.pollAfterFailures = tuning.pollAfterFailures ?? 2;
    this.store = opts.store;
    this.cache = opts.cache;
    this.onChange = opts.onChange ?? noop;
    this.onStatus = opts.onStatus ?? noop;
    const etag = () => this.store.load()?.etag ?? "";
    this.poller = new Poller({
      url: sdkUrl(opts.baseUrl, "ruleset"),
      sdkKey: opts.sdkKey,
      ...(opts.fetch === undefined ? {} : { fetch: opts.fetch }),
      etag,
      onRuleset: (raw, tag) => this.apply(raw, tag, "poll") === undefined,
    });
    this.streamer = new Streamer({
      url: sdkUrl(opts.baseUrl, "stream"),
      sdkKey: opts.sdkKey,
      ...(opts.fetch === undefined ? {} : { fetch: opts.fetch }),
      logger: this.logger,
      lastEventId: etag,
      onPut: (raw, id) => this.apply(raw, id, "stream") === undefined,
      onConnected: () => this.onConnected(),
      onDisconnected: (err, failures) => this.onDisconnected(err, failures),
      deadTimeoutMs: tuning.deadTimeoutMs ?? 45_000,
      stableAfterMs: tuning.stableAfterMs ?? 60_000,
      backoff: new Backoff(
        tuning.retryBaseMs ?? 3_000,
        tuning.retryMaxMs ?? 60_000,
      ),
    });
  }

  private get halted(): boolean {
    return this.fatal || this.controller.signal.aborted;
  }

  /**
   * apply parses raw and installs it. An invalid ruleset is logged and
   * returned, and the store keeps the previous one. A ruleset from the
   * stream or a poll is also saved to the cache; a save error is only
   * logged.
   */
  apply(raw: string, etag: string, source: Source): Error | undefined {
    const live = source === "stream" || source === "poll";
    if (live && this.halted) {
      return undefined;
    }
    let ruleset;
    try {
      ruleset = parseRuleset(raw);
    } catch (err) {
      this.logger.warn(
        "megooflags: invalid ruleset; keeping the previous one",
        { source, error: String(err) },
      );
      return err instanceof Error ? err : new Error(String(err));
    }
    const next: Snapshot = { ruleset, etag, source };
    const old = this.store.swap(next);
    if (live) {
      try {
        this.cache?.save(raw, etag);
      } catch (err) {
        this.logger.warn("megooflags: could not save the ruleset cache", {
          error: String(err),
        });
      }
    }
    for (const w of ruleset.warnings) {
      if (!this.logged.has(w)) {
        this.logged.add(w);
        this.logger.warn(`megooflags: ruleset warning: ${w}`);
      }
    }
    this.call(() => this.onChange(old, next));
    return undefined;
  }

  /**
   * start runs the stream, or only the poll loop with disableStream, and
   * returns at once. Only the first call does anything.
   */
  start(): void {
    if (this.started || this.halted) {
      return;
    }
    this.started = true;
    if (this.disableStream) {
      this.startPolling();
      return;
    }
    this.track(
      this.streamer.run(this.controller.signal).then((fatal) => {
        if (fatal !== undefined) {
          this.fail(fatal);
        }
      }),
    );
  }

  /**
   * stop aborts the stream and the poll loop and resolves when they have
   * ended. It is idempotent and safe before start().
   */
  stop(): Promise<void> {
    if (this.stopping === undefined) {
      this.controller.abort();
      this.stopping = this.settle();
    }
    return this.stopping;
  }

  private async settle(): Promise<void> {
    while (this.loops.size > 0) {
      await Promise.all(this.loops);
    }
  }

  private track(loop: Promise<void>): void {
    const p = loop
      .catch((err: unknown) =>
        this.logger.error("megooflags: sync loop failed", err),
      )
      .finally(() => this.loops.delete(p));
    this.loops.add(p);
  }

  private call(fn: () => void): void {
    try {
      fn();
    } catch (err) {
      this.logger.error("megooflags: sync callback failed", err);
    }
  }

  private onConnected(): void {
    this.stopPolling();
    this.status("ready");
  }

  private onDisconnected(err: unknown, failures: number): void {
    this.status("stale", err);
    if (failures >= this.pollAfterFailures) {
      this.startPolling();
    }
  }

  /** status reports st unless Sync is stopping or has failed. */
  private status(st: Status, err?: unknown): void {
    if (this.halted) {
      return;
    }
    this.call(() => this.onStatus(st, err));
  }

  /** fail reports a refused key once and stops everything. */
  private fail(err: FatalError): void {
    if (this.halted) {
      return;
    }
    this.fatal = true;
    this.controller.abort();
    this.call(() => this.onStatus("fatal", err));
  }

  private startPolling(): void {
    if (this.pollController !== undefined || this.halted) {
      return;
    }
    const pc = new AbortController();
    this.pollController = pc;
    const parent = this.controller.signal;
    const abort = () => pc.abort();
    parent.addEventListener("abort", abort, { once: true });
    this.track(
      this.pollLoop(pc.signal).finally(() =>
        parent.removeEventListener("abort", abort),
      ),
    );
  }

  private stopPolling(): void {
    this.pollController?.abort();
    this.pollController = undefined;
  }

  private async pollLoop(signal: AbortSignal): Promise<void> {
    for (;;) {
      if (signal.aborted) {
        return;
      }
      const now = Date.now();
      const wait = this.nextPoll - now;
      if (wait > 0) {
        if (!(await sleep(wait, signal))) {
          return;
        }
        continue;
      }
      this.nextPoll = now + this.pollIntervalMs;
      let err: unknown;
      try {
        await this.poller.poll(signal);
      } catch (e) {
        err = e;
      }
      if (signal.aborted) {
        return;
      }
      if (err instanceof FatalError) {
        this.fail(err);
        return;
      }
      if (err instanceof RetryAfterError) {
        this.nextPoll = now + Math.max(err.afterMs, this.pollIntervalMs);
      }
      if (err !== undefined) {
        this.logger.debug("megooflags: poll failed", { error: String(err) });
      }
      if (this.disableStream) {
        if (err === undefined) {
          this.status("ready");
        } else {
          this.status("stale", err);
        }
      }
    }
  }
}
