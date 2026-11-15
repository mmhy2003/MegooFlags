// Jittered exponential backoff, a cancellable sleep and the timer delay
// clamp.

/**
 * MAX_TIMER_MS is the longest delay setTimeout and setInterval honour: a
 * longer one (or NaN) fires after 1 ms instead.
 */
export const MAX_TIMER_MS = 2 ** 31 - 1;

/**
 * timerDelay clamps ms to what a timer can wait, so a huge Retry-After or
 * configured interval waits about 24.8 days instead of firing at once. A
 * value below 0 or NaN becomes 0.
 */
export function timerDelay(ms: number): number {
  return ms > 0 ? Math.min(ms, MAX_TIMER_MS) : 0;
}

/**
 * Backoff computes waits in milliseconds. The first wait is min(base, max);
 * the n-th is min(max, base * 2^n) scaled by a factor in [0.8, 1.0]. base
 * can be replaced, for the stream's retry field.
 */
export class Backoff {
  private n = 0;

  constructor(
    public base: number,
    readonly max: number,
    private readonly rand: () => number = Math.random,
  ) {}

  /** next returns the next wait and advances the attempt counter. */
  next(): number {
    if (this.n === 0) {
      this.n++;
      return Math.min(this.base, this.max);
    }
    const d = Math.min(this.max, this.base * 2 ** this.n);
    if (this.n < 64) {
      this.n++;
    }
    return d * (0.8 + 0.2 * this.rand());
  }

  /** reset makes the next wait base again. */
  reset(): void {
    this.n = 0;
  }
}

/**
 * sleep waits ms milliseconds (clamped by timerDelay) and resolves true, or
 * resolves false as soon as signal aborts. Where timers have unref (Node.js,
 * the tests) the timer is unref'd, so it never keeps the process alive,
 * unless ref is true; browser timers have none. It never rejects.
 */
export function sleep(
  ms: number,
  signal?: AbortSignal,
  ref = false,
): Promise<boolean> {
  if (signal?.aborted) {
    return Promise.resolve(false);
  }
  return new Promise((resolve) => {
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve(true);
    }, timerDelay(ms));
    if (!ref) {
      (timer as unknown as { unref?: () => void }).unref?.();
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
