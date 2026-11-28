// The OpenFeature web provider: wires options, store, cache, evaluator,
// streamer, sync, exposures, batcher and background signal together, maps
// the sync events to provider events and serves flag reads from the store.
import {
  ErrorCode,
  GeneralError,
  OpenFeatureEventEmitter,
  ProviderEvents,
  ProviderFatalError,
  type EvaluationContext,
  type JsonValue,
  type Provider,
  type ResolutionDetails,
} from "@openfeature/web-sdk";
import type { MegooFlagsOptions } from "./config.js";
import { BackgroundController } from "./internal/background.js";
import { Batcher } from "./internal/batcher.js";
import { Cache } from "./internal/cache.js";
import { Evaluator } from "./internal/evaluator.js";
import { Exposures } from "./internal/exposures.js";
import { resolveOptions } from "./internal/options.js";
import { type FlagType, resolveFlag } from "./internal/resolve.js";
import { Store } from "./internal/store.js";
import { Streamer } from "./internal/streamer.js";
import { Sync } from "./internal/sync.js";
import { FatalError } from "./internal/transport.js";
import type { Platform } from "./platform.js";
import { webPlatform } from "./web-platform.js";

const CLOSE_TIMEOUT_MS = 5_000;

const fatalMessage = (reason: number | "revoked") =>
  new FatalError(reason).message;

/**
 * MegooFlagsWebProvider evaluates flags remotely with a client key and
 * serves them synchronously from the last values for the current context.
 */
export class MegooFlagsWebProvider implements Provider {
  readonly metadata = { name: "MegooFlags" } as const;
  readonly runsOn = "client";
  readonly events = new OpenFeatureEventEmitter();

  readonly #store = new Store();
  readonly #sync: Sync;
  readonly #exposures: Exposures | null;
  readonly #batcher: Batcher | null;
  readonly #background: BackgroundController;
  #closing: Promise<void> | undefined;

  /** The constructor throws on invalid options, a server key included. */
  constructor(options: MegooFlagsOptions, platform?: Platform) {
    const opts = resolveOptions(options);
    const plat = platform ?? webPlatform(options.fetch);
    const fetchFn = opts.fetch ?? globalThis.fetch.bind(globalThis);
    const storage = opts.disableCache ? null : (opts.storage ?? plat.storage);
    const cache = new Cache(opts.sdkKey, storage, opts.logger);
    const evaluator = new Evaluator({
      baseUrl: opts.baseUrl,
      sdkKey: opts.sdkKey,
      fetch: fetchFn,
    });

    let streamer: Streamer | null = null;
    this.#sync = new Sync({
      opts,
      store: this.#store,
      cache,
      evaluator,
      streamer: {
        start: () => streamer?.start(),
        pause: () => streamer?.pause(),
        resume: () => streamer?.resume(),
        stop: () => streamer?.stop(),
      },
      events: {
        ready: () => this.events.emit(ProviderEvents.Ready),
        stale: () => this.events.emit(ProviderEvents.Stale),
        changed: (flagsChanged) =>
          this.events.emit(ProviderEvents.ConfigurationChanged, {
            flagsChanged,
          }),
        fatal: (reason) => this.#emitFatal(reason),
        // The sync logs and retries; a failed context change is reported
        // by the web SDK through the rejected onContextChange.
        error: () => {},
      },
      onFatalStop: () => this.#stopDelivery(),
    });
    if (!opts.offline) {
      streamer = new Streamer({
        baseUrl: opts.baseUrl,
        sdkKey: opts.sdkKey,
        open: plat.openStream,
        logger: opts.logger,
        hooks: this.#sync.streamerHooks,
      });
    }

    this.#exposures = opts.offline
      ? null
      : new Exposures({ sendContext: opts.sendContext, logger: opts.logger });
    this.#batcher =
      this.#exposures === null
        ? null
        : new Batcher({
            baseUrl: opts.baseUrl,
            sdkKey: opts.sdkKey,
            fetch: fetchFn,
            exposures: this.#exposures,
            flushIntervalMs: opts.flushIntervalMs,
            logger: opts.logger,
            onFatal: (status) => {
              this.#sync.stop();
              this.#stopDelivery();
              this.#emitFatal(status);
            },
          });
    this.#background = new BackgroundController({
      signal: plat.background,
      sync: this.#sync,
      batcher: this.#batcher,
    });
  }

  /**
   * initialize serves bootstrap or cached values at once, or waits for the
   * first evaluation up to initTimeoutMs. A refused key throws
   * ProviderFatalError; a timeout or another failure throws GeneralError
   * and ready is emitted when values arrive.
   */
  async initialize(context?: EvaluationContext): Promise<void> {
    this.#batcher?.start();
    this.#background.start();
    try {
      await this.#sync.start(context ?? {});
    } catch (err) {
      if (err instanceof FatalError) {
        throw new ProviderFatalError(err.message);
      }
      throw new GeneralError(err instanceof Error ? err.message : String(err));
    }
  }

  /** onContextChange fetches the values for newContext; the web SDK emits Reconciling and ContextChanged. */
  onContextChange(
    _oldContext: EvaluationContext,
    newContext: EvaluationContext,
  ): Promise<void> {
    return this.#sync.setContext(newContext);
  }

  /** onClose stops the sync and sends the last exposures within 5 s; it is idempotent. */
  onClose(): Promise<void> {
    if (this.#closing === undefined) {
      this.#sync.stop();
      this.#background.stop();
      this.#closing =
        this.#batcher?.close(CLOSE_TIMEOUT_MS) ?? Promise.resolve();
    }
    return this.#closing;
  }

  resolveBooleanEvaluation(
    flagKey: string,
    defaultValue: boolean,
  ): ResolutionDetails<boolean> {
    return this.#resolve(flagKey, "boolean", defaultValue);
  }

  resolveStringEvaluation(
    flagKey: string,
    defaultValue: string,
  ): ResolutionDetails<string> {
    return this.#resolve(flagKey, "string", defaultValue);
  }

  resolveNumberEvaluation(
    flagKey: string,
    defaultValue: number,
  ): ResolutionDetails<number> {
    return this.#resolve(flagKey, "number", defaultValue);
  }

  resolveObjectEvaluation<T extends JsonValue>(
    flagKey: string,
    defaultValue: T,
  ): ResolutionDetails<T> {
    return this.#resolve(flagKey, "object", defaultValue);
  }

  /** resolve reads the store and records an exposure for a served value. */
  #resolve<T>(
    flagKey: string,
    type: FlagType,
    defaultValue: T,
  ): ResolutionDetails<T> {
    const details = resolveFlag(this.#store.get(), flagKey, type, defaultValue);
    if (
      this.#exposures !== null &&
      details.errorCode === undefined &&
      details.variant !== undefined
    ) {
      this.#exposures.record(
        flagKey,
        details.variant,
        details.reason ?? "",
        this.#sync.body,
      );
    }
    return details;
  }

  /** stopDelivery stops the exposures and the background signal for a refused key. */
  #stopDelivery(): void {
    this.#batcher?.stop();
    this.#background.stop();
  }

  #emitFatal(reason: number | "revoked"): void {
    this.events.emit(ProviderEvents.Error, {
      errorCode: ErrorCode.PROVIDER_FATAL,
      message: fatalMessage(reason),
    });
  }
}
