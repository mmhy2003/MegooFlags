// The last-known results on the device, one entry per SDK key. Storage may
// be synchronous (localStorage) or asynchronous (AsyncStorage); every call is
// awaited and guarded, so a broken storage never fails an evaluation.
import type { StorageLike } from "../platform.js";
import { parseResults } from "./options.js";
import { sha256Hex } from "./sha256.js";
import { storedFrom, type StoredResults } from "./store.js";
import type { Logger } from "./transport.js";

const VERSION = 1;

const errorName = (err: unknown) =>
  err instanceof Error ? err.name : typeof err;

export class Cache {
  /** The storage key: it never contains the SDK key itself. */
  readonly key: string;
  readonly #storage: StorageLike | null;
  readonly #logger: Logger;
  readonly #now: () => Date;

  constructor(
    sdkKey: string,
    storage: StorageLike | null,
    logger: Logger,
    now: () => Date = () => new Date(),
  ) {
    this.key = "megooflags-" + sha256Hex(sdkKey).slice(0, 16);
    this.#storage = storage;
    this.#logger = logger;
    this.#now = now;
  }

  /**
   * load returns the entry for contextHash with source "cache", or undefined
   * when it is missing, unreadable, of another version or another context.
   * The entry does not record the targeting key: the caller knows it from
   * the context whose hash matched.
   */
  async load(contextHash: string): Promise<StoredResults | undefined> {
    if (!this.#storage) return undefined;
    let raw: string | null;
    try {
      raw = await this.#storage.getItem(this.key);
    } catch (err) {
      this.#logger.debug(`megooflags: cache read failed: ${errorName(err)}`);
      return undefined;
    }
    if (raw === null) return undefined;
    try {
      const entry: unknown = JSON.parse(raw);
      if (typeof entry !== "object" || entry === null || Array.isArray(entry))
        throw new Error("not an object");
      const { v, context_hash, etag } = entry as Record<string, unknown>;
      if (v !== VERSION) throw new Error(`version ${String(v)}`);
      if (context_hash !== contextHash) throw new Error("another context");
      if (etag !== null && typeof etag !== "string")
        throw new Error("etag: not a string or null");
      const results = parseResults(entry, "invalid entry");
      return storedFrom(results, {
        etag,
        source: "cache",
        contextHash,
        targetingKey: null,
      });
    } catch (err) {
      const why = err instanceof Error ? err.message : String(err);
      this.#logger.debug(`megooflags: cache entry ignored: ${why}`);
      return undefined;
    }
  }

  /** save writes s as the entry; a failure is logged at warn. */
  async save(s: StoredResults): Promise<void> {
    if (!this.#storage) return;
    try {
      const entry = {
        v: VERSION,
        context_hash: s.contextHash,
        etag: s.etag,
        results: [...s.results.values()],
        saved_at: this.#now().toISOString(),
      };
      await this.#storage.setItem(this.key, JSON.stringify(entry));
    } catch (err) {
      this.#logger.warn(`megooflags: cache write failed: ${errorName(err)}`);
    }
  }

  /** remove deletes the entry; a failure is logged at warn. */
  async remove(): Promise<void> {
    if (!this.#storage) return;
    try {
      await this.#storage.removeItem(this.key);
    } catch (err) {
      this.#logger.warn(`megooflags: cache remove failed: ${errorName(err)}`);
    }
  }
}
