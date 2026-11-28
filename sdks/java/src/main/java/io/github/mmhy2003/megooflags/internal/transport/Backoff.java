package io.github.mmhy2003.megooflags.internal.transport;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;
import java.util.function.DoubleSupplier;

/**
 * Jittered exponential backoff. The first wait is min(base, max); the n-th is min(max, base * 2^n)
 * scaled by a factor in [0.8, 1.0]. The base can be replaced, for the stream's retry field. Not
 * thread-safe: one worker owns it.
 */
public final class Backoff {
    private Duration base;
    private final Duration max;
    private final DoubleSupplier rand;
    private int n;

    public Backoff(Duration base, Duration max) {
        this(base, max, () -> ThreadLocalRandom.current().nextDouble());
    }

    /** rand returns a number in [0, 1]; tests pass a constant. */
    public Backoff(Duration base, Duration max, DoubleSupplier rand) {
        this.base = base;
        this.max = max;
        this.rand = rand;
    }

    /** Replaces the base; the attempt counter is kept. */
    public void setBase(Duration base) {
        this.base = base;
    }

    /** Returns the next wait and advances the attempt counter. */
    public Duration next() {
        if (n == 0) {
            n++;
            return min(base, max);
        }
        // Double step by step so a large n cannot overflow.
        Duration d = base;
        for (int i = 0; i < n && d.compareTo(max) < 0; i++) {
            d = d.multipliedBy(2);
        }
        d = min(d, max);
        if (n < 64) {
            n++;
        }
        return Duration.ofNanos(Math.round(d.toNanos() * (0.8 + 0.2 * rand.getAsDouble())));
    }

    /** Makes the next wait the base again. */
    public void reset() {
        n = 0;
    }

    /**
     * Waits for d and returns true, or returns false as soon as cancel is counted down or the
     * thread is interrupted (the interrupt flag is kept).
     */
    public static boolean sleep(Duration d, CountDownLatch cancel) {
        try {
            return !cancel.await(d.toNanos(), TimeUnit.NANOSECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private static Duration min(Duration a, Duration b) {
        return a.compareTo(b) <= 0 ? a : b;
    }
}
