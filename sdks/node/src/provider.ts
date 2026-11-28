// The MegooFlags OpenFeature provider: local evaluation of the ruleset the
// sync keeps current, and its lifecycle and events.

import { readFileSync } from "node:fs";
import {
  ErrorCode,
  OpenFeatureEventEmitter,
  ProviderEvents,
  ProviderFatalError,
  type EvaluationContext,
  type JsonValue,
  type Provider,
  type ResolutionDetails,
} from "@openfeature/server-sdk";
import {
  validateConfig,
  withDefaults,
  type MegooFlagsConfig,
  type ResolvedConfig,
} from "./config.js";
import { Cache } from "./internal/delivery/cache.js";
import type { Snapshot } from "./internal/delivery/store.js";
import { Store } from "./internal/delivery/store.js";
import { Sync, type Status, type Tuning } from "./internal/delivery/sync.js";
import {
  evaluate,
  metadata,
  type ErrorCode as EngineErrorCode,
  type ExpectedType,
} from "./internal/engine/evaluate.js";
import { Batcher } from "./internal/events/batcher.js";
import { timerDelay } from "./internal/transport/backoff.js";
import { sdkUrl } from "./internal/transport/transport.js";

/** SHUTDOWN_DEADLINE_MS bounds the final events flush. */
const SHUTDOWN_DEADLINE_MS = 5_000;

/** testHooks lets the tests shorten Sync's timings; unset in production. */
export const testHooks: { syncTuning: Tuning | undefined } = {
  syncTuning: undefined,
};

/** How a wait for the first ruleset ended. */
type Outcome = "ready" | "fatal" | "closed" | "timeout";

const ERROR_CODES: Record<EngineErrorCode, ErrorCode> = {
  FLAG_NOT_FOUND: ErrorCode.FLAG_NOT_FOUND,
  TYPE_MISMATCH: ErrorCode.TYPE_MISMATCH,
  PARSE_ERROR: ErrorCode.PARSE_ERROR,
  INVALID_CONTEXT: ErrorCode.INVALID_CONTEXT,
  GENERAL: ErrorCode.GENERAL,
};

/**
 * MegooFlagsProvider evaluates flags against the ruleset it downloaded,
 * with no network call per evaluation. The constructor never touches the
 * network: it loads the bootstrap file and, when that gave no ruleset, the
 * cache. An invalid config is logged, and every evaluation then returns the
 * default with PROVIDER_FATAL.
 */
export class MegooFlagsProvider implements Provider {
  readonly metadata = { name: "MegooFlags" } as const;
  readonly runsOn = "server";
  readonly events = new OpenFeatureEventEmitter();

  private readonly cfg: ResolvedConfig;
  private readonly configError: Error | undefined;
  private readonly store = new Store();
  private readonly sync: Sync | undefined;
  private readonly batcher: Batcher | undefined;

  private started = false;
  private closing: Promise<void> | undefined;
  private fatalError: Error | undefined;
  /** initTimedOut: initialize gave up, so the first ruleset emits READY. */
  private initTimedOut = false;
  /** stale: STALE was emitted and not yet followed by READY. */
  private stale = false;
  /** waiters are the initialize calls waiting for the first ruleset. */
  private readonly waiters = new Set<(o: Outcome) => void>();

  constructor(config: MegooFlagsConfig) {
    const cfg = withDefaults(config);
    this.cfg = cfg;
    this.configError = validateConfig(cfg);
    if (this.configError !== undefined) {
      cfg.logger.error("megooflags: invalid config", {
        error: this.configError.message,
      });
      return;
    }
    const cache = new Cache(cfg.cacheDir, cfg.sdkKey);
    // Offline uses Sync only to apply the bootstrap file and the cache;
    // initialize never starts it.
    this.sync = new Sync({
      baseUrl: cfg.baseUrl,
      sdkKey: cfg.sdkKey,
      fetch: cfg.fetch,
      logger: cfg.logger,
      disableStream: cfg.disableStream,
      pollIntervalMs: cfg.pollIntervalMs,
      store: this.store,
      cache,
      onChange: (old, next) => this.onChange(old, next),
      onStatus: (st, err) => this.onStatus(st, err),
      ...(testHooks.syncTuning === undefined
        ? {}
        : { tuning: testHooks.syncTuning }),
    });
    if (!cfg.offline) {
      this.batcher = new Batcher({
        url: sdkUrl(cfg.baseUrl, "events"),
        sdkKey: cfg.sdkKey,
        fetch: cfg.fetch,
        logger: cfg.logger,
        flushIntervalMs: cfg.flushIntervalMs,
        sendContext: cfg.sendContext,
        onFatal: (err) => this.onFatal(err),
      });
    }
    if (cfg.bootstrapFile !== "") {
      try {
        // apply logs an invalid ruleset
        this.sync.apply(
          readFileSync(cfg.bootstrapFile, "utf8"),
          "",
          "bootstrap",
        );
      } catch (err) {
        cfg.logger.warn("megooflags: could not read the bootstrap file", {
          error: String(err),
        });
      }
    }
    if (this.store.load() === undefined) {
      try {
        const cached = cache.load();
        if (cached !== undefined) {
          this.sync.apply(JSON.stringify(cached.ruleset), cached.etag, "cache");
        }
      } catch (err) {
        cfg.logger.warn("megooflags: could not read the ruleset cache", {
          error: String(err),
        });
      }
    }
  }

  /**
   * initialize starts the event sender and, unless offline, the sync. With
   * a ruleset from the bootstrap file or the cache it resolves at once.
   * Otherwise it waits up to initTimeoutMs for the first ruleset; on
   * timeout it rejects, keeps retrying, and emits READY when a ruleset
   * arrives. A refused key or an invalid config rejects with
   * ProviderFatalError.
   */
  async initialize(): Promise<void> {
    if (this.configError !== undefined) {
      throw new ProviderFatalError(this.configError.message);
    }
    if (this.closing !== undefined) {
      throw new Error("megooflags: the provider was shut down");
    }
    if (this.fatalError !== undefined) {
      throw new ProviderFatalError(this.fatalError.message);
    }
    if (!this.started) {
      this.started = true;
      this.batcher?.start();
      if (!this.cfg.offline) {
        this.sync?.start();
      }
    }
    if (this.store.load() !== undefined) {
      return;
    }
    if (this.cfg.offline) {
      throw new Error(
        "megooflags: offline, and neither the bootstrap file nor the cache gave a valid ruleset",
      );
    }
    switch (await this.waitFirstRuleset()) {
      case "ready":
        return;
      case "fatal":
        throw new ProviderFatalError(this.fatalMessage());
      case "closed":
        throw new Error("megooflags: the provider was shut down");
      case "timeout":
        throw new Error(
          `megooflags: no ruleset within ${this.cfg.initTimeoutMs} ms`,
        );
    }
  }

  /**
   * onClose stops the sync and sends the buffered events within 5 s. It is
   * idempotent. The last ruleset keeps being served.
   */
  onClose(): Promise<void> {
    if (this.closing === undefined) {
      this.notify("closed");
      this.closing = this.shutdown();
    }
    return this.closing;
  }

  async resolveBooleanEvaluation(
    flagKey: string,
    defaultValue: boolean,
    context: EvaluationContext,
  ): Promise<ResolutionDetails<boolean>> {
    return this.resolve(flagKey, defaultValue, context, "boolean");
  }

  async resolveStringEvaluation(
    flagKey: string,
    defaultValue: string,
    context: EvaluationContext,
  ): Promise<ResolutionDetails<string>> {
    return this.resolve(flagKey, defaultValue, context, "string");
  }

  async resolveNumberEvaluation(
    flagKey: string,
    defaultValue: number,
    context: EvaluationContext,
  ): Promise<ResolutionDetails<number>> {
    return this.resolve(flagKey, defaultValue, context, "number");
  }

  async resolveObjectEvaluation<T extends JsonValue>(
    flagKey: string,
    defaultValue: T,
    context: EvaluationContext,
  ): Promise<ResolutionDetails<T>> {
    return this.resolve(flagKey, defaultValue, context, "json");
  }

  /**
   * resolve runs the engine on the current ruleset. The context is copied,
   * with targetingKey mapped to key. No I/O.
   */
  private resolve<T>(
    flagKey: string,
    defaultValue: T,
    context: EvaluationContext,
    type: ExpectedType,
  ): ResolutionDetails<T> {
    const snap = this.store.load();
    if (snap === undefined) {
      return this.notReady(defaultValue);
    }
    const ctx: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(context)) {
      // defineProperty, so __proto__ is an attribute like any other
      Object.defineProperty(ctx, k, {
        value: v,
        enumerable: true,
        writable: true,
        configurable: true,
      });
    }
    if (context.targetingKey !== undefined) {
      ctx["key"] = context.targetingKey;
    }
    const res = evaluate(snap.ruleset, flagKey, ctx, defaultValue, type);
    const served = res.variant !== null && res.errorCode === null;
    this.batcher?.record({
      flagKey,
      variant: res.variant ?? "",
      served,
      reason: res.reason,
      contextKey: typeof ctx["key"] === "string" ? ctx["key"] : "",
      ...(this.cfg.sendContext ? { context: ctx } : {}),
      at: Date.now(),
    });
    const details: ResolutionDetails<T> = {
      value: res.errorCode === null ? (res.value as T) : defaultValue,
      reason: res.reason,
      flagMetadata: { ...metadata(res) },
    };
    if (res.variant !== null) {
      details.variant = res.variant;
    }
    if (res.errorCode !== null) {
      details.errorCode = ERROR_CODES[res.errorCode];
      details.errorMessage = res.errorMessage;
    }
    return details;
  }

  /** fatalMessage describes the fatal error that stopped the provider. */
  private fatalMessage(): string {
    return this.fatalError?.message ?? "megooflags: the SDK key was refused";
  }

  /** notReady is the result while there is no ruleset. */
  private notReady<T>(defaultValue: T): ResolutionDetails<T> {
    if (this.configError !== undefined || this.fatalError !== undefined) {
      return {
        value: defaultValue,
        reason: "ERROR",
        errorCode: ErrorCode.PROVIDER_FATAL,
        errorMessage: this.configError?.message ?? this.fatalMessage(),
      };
    }
    return {
      value: defaultValue,
      reason: "ERROR",
      errorCode: ErrorCode.PROVIDER_NOT_READY,
      errorMessage: "megooflags: no ruleset is loaded yet",
    };
  }

  /** waitFirstRuleset waits for the first ruleset, a fatal status, close or the timeout. */
  private waitFirstRuleset(): Promise<Outcome> {
    return new Promise((resolve) => {
      // Not unref'd: the caller is waiting and the sync's retry sleeps are
      // unref'd, so this timer keeps the process alive until initialize
      // settles. done clears it when that happens first.
      const timer = setTimeout(() => {
        this.waiters.delete(done);
        this.initTimedOut = true;
        resolve("timeout");
      }, timerDelay(this.cfg.initTimeoutMs));
      const done = (o: Outcome) => {
        clearTimeout(timer);
        resolve(o);
      };
      this.waiters.add(done);
    });
  }

  /** notify ends every wait for the first ruleset with o. */
  private notify(o: Outcome): void {
    for (const w of this.waiters) {
      w(o);
    }
    this.waiters.clear();
  }

  private async shutdown(): Promise<void> {
    await this.sync?.stop();
    if (this.batcher !== undefined) {
      const all = await this.batcher.shutdown(SHUTDOWN_DEADLINE_MS);
      if (!all) {
        this.cfg.logger.warn(
          "megooflags: could not send all events before shutdown",
        );
      }
    }
  }

  /**
   * onChange runs after each apply. The first ruleset ends the wait in
   * initialize (and emits READY when that wait had timed out); a later one
   * is a configuration change.
   */
  private onChange(old: Snapshot | undefined, next: Snapshot): void {
    if (old === undefined) {
      this.notify("ready");
      if (this.initTimedOut) {
        this.emit(ProviderEvents.Ready, {
          message: "megooflags: ruleset loaded",
        });
      }
      return;
    }
    this.emit(ProviderEvents.ConfigurationChanged, {
      message: "megooflags: ruleset updated",
      flagsChanged: flagChanges(old, next),
    });
  }

  /**
   * onStatus maps the delivery status to events: STALE while disconnected
   * with a ruleset, READY again on reconnect.
   */
  private onStatus(st: Status, err: unknown): void {
    switch (st) {
      case "stale":
        if (
          !this.stale &&
          this.store.load() !== undefined &&
          this.fatalError === undefined
        ) {
          this.stale = true;
          const why = err === undefined ? "" : `: ${errorMessage(err)}`;
          this.emit(ProviderEvents.Stale, {
            message: `megooflags: disconnected; serving the last ruleset${why}`,
          });
        }
        return;
      case "ready":
        if (this.stale) {
          this.stale = false;
          this.emit(ProviderEvents.Ready, {
            message: "megooflags: reconnected",
          });
        }
        return;
      case "fatal":
        this.onFatal(err);
    }
  }

  /**
   * onFatal handles a refused key once: it stops event sending and the
   * sync and emits PROVIDER_ERROR with PROVIDER_FATAL. A waiting initialize
   * rejects instead of the event. The last ruleset keeps being served.
   */
  private onFatal(err: unknown): void {
    if (this.fatalError !== undefined) {
      return;
    }
    const fatal =
      err instanceof Error
        ? err
        : new Error("megooflags: the SDK key was refused");
    this.fatalError = fatal;
    this.cfg.logger.error(
      "megooflags: the server refused the SDK key; serving the last ruleset",
      { error: fatal.message },
    );
    void this.batcher?.stop();
    void this.sync?.stop();
    if (this.waiters.size > 0) {
      this.notify("fatal");
      return;
    }
    this.emit(ProviderEvents.Error, {
      message: fatal.message,
      errorCode: ErrorCode.PROVIDER_FATAL,
    });
  }

  /** emit sends an event unless the provider was closed. */
  private emit(
    type: ProviderEvents,
    details: {
      message: string;
      flagsChanged?: string[];
      errorCode?: ErrorCode;
    },
  ): void {
    if (this.closing === undefined) {
      this.events.emit(type, details);
    }
  }
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * flagChanges returns the sorted keys of the flags that were added,
 * removed, or have a different version.
 */
function flagChanges(old: Snapshot, next: Snapshot): string[] {
  const keys: string[] = [];
  for (const [k, f] of next.ruleset.flags) {
    if (old.ruleset.flags.get(k)?.version !== f.version) {
      keys.push(k);
    }
  }
  for (const k of old.ruleset.flags.keys()) {
    if (!next.ruleset.flags.has(k)) {
      keys.push(k);
    }
  }
  return keys.sort();
}
