// Jittered exponential backoff and a cancellable sleep.

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
 * sleep waits ms milliseconds and resolves true, or resolves false as soon
 * as signal aborts. Its timer is unref'd, so it never keeps the process
 * alive, and it never rejects.
 */
export function sleep(ms: number, signal?: AbortSignal): Promise<boolean> {
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
    }, ms);
    timer.unref();
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
