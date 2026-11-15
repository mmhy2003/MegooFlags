// Follows the app to the background and back: flushes the exposures (with
// keepalive) when it is hidden, pauses the stream and the poller 30 s later
// (at once when the page is going away) and resumes them when it is visible
// again.
import type { BackgroundSignal, BackgroundState } from "../platform.js";
import { timerDelay } from "./backoff.js";
import type { Batcher } from "./batcher.js";
import type { Sync } from "./sync.js";

const HIDDEN_GRACE_MS = 30_000;

const noop = () => {};

export class BackgroundController {
  readonly #signal: BackgroundSignal | null;
  readonly #sync: Pick<Sync, "pause" | "resume">;
  readonly #batcher: Pick<Batcher, "flush"> | null;
  readonly #hiddenGraceMs: number;
  #unsubscribe: (() => void) | null = null;
  #timer: ReturnType<typeof setTimeout> | undefined;
  #paused = false;

  constructor(o: {
    signal: BackgroundSignal | null;
    sync: Pick<Sync, "pause" | "resume">;
    batcher: Pick<Batcher, "flush"> | null;
    hiddenGraceMs?: number;
  }) {
    this.#signal = o.signal;
    this.#sync = o.sync;
    this.#batcher = o.batcher;
    this.#hiddenGraceMs = o.hiddenGraceMs ?? HIDDEN_GRACE_MS;
  }

  /** start follows the signal; it is idempotent. */
  start(): void {
    if (this.#signal === null || this.#unsubscribe !== null) return;
    this.#unsubscribe = this.#signal.subscribe((state) => this.#on(state));
  }

  /** stop unsubscribes and cancels a pending pause. */
  stop(): void {
    this.#unsubscribe?.();
    this.#unsubscribe = null;
    this.#cancelTimer();
  }

  #on(state: BackgroundState): void {
    switch (state) {
      case "hidden":
        // keepalive: browsers fire hidden, then pagehide, on unload; the
        // terminated flush joins this one, so it must outlive the page too.
        void this.#batcher?.flush({ keepalive: true }).catch(noop);
        if (!this.#paused && this.#timer === undefined) {
          this.#timer = setTimeout(() => {
            this.#timer = undefined;
            this.#pause();
          }, timerDelay(this.#hiddenGraceMs));
        }
        break;
      case "terminated":
        void this.#batcher?.flush({ keepalive: true }).catch(noop);
        this.#cancelTimer();
        this.#pause();
        break;
      case "visible":
        this.#cancelTimer();
        if (this.#paused) {
          this.#paused = false;
          this.#sync.resume();
        }
        break;
    }
  }

  #pause(): void {
    if (this.#paused) return;
    this.#paused = true;
    this.#sync.pause();
  }

  #cancelTimer(): void {
    clearTimeout(this.#timer);
    this.#timer = undefined;
  }
}
