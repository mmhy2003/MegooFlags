// The polling fallback: refreshes the values on an interval while the stream
// is down, or always with disableStream.
import { timerDelay } from "./backoff.js";

/** The shortest poll interval. */
const MIN_INTERVAL_MS = 15_000;

export class Poller {
  readonly #intervalMs: number;
  readonly #tick: () => Promise<void>;
  #timer: ReturnType<typeof setInterval> | undefined;

  /** tick runs every intervalMs (at least 15 s); a rejection is ignored. */
  constructor(intervalMs: number, tick: () => Promise<void>) {
    this.#intervalMs = Math.max(intervalMs, MIN_INTERVAL_MS);
    this.#tick = tick;
  }

  get running(): boolean {
    return this.#timer !== undefined;
  }

  /** start schedules the first tick one interval from now; it is idempotent. */
  start(): void {
    if (this.#timer !== undefined) return;
    this.#timer = setInterval(
      () => void this.#tick().catch(() => {}),
      timerDelay(this.#intervalMs),
    );
    (this.#timer as unknown as { unref?: () => void }).unref?.();
  }

  stop(): void {
    clearInterval(this.#timer);
    this.#timer = undefined;
  }
}
