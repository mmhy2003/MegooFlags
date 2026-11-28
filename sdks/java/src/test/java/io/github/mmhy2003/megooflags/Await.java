package io.github.mmhy2003.megooflags;

import java.time.Duration;
import java.util.function.BooleanSupplier;

/** Waits for a condition the background threads make true. */
public final class Await {
    private Await() {}

    /** Polls cond every 5 ms and fails when it is still false after timeout. */
    public static void until(Duration timeout, BooleanSupplier cond) throws InterruptedException {
        long deadline = System.nanoTime() + timeout.toNanos();
        while (!cond.getAsBoolean()) {
            if (System.nanoTime() > deadline) {
                throw new AssertionError("condition still false after " + timeout);
            }
            Thread.sleep(5);
        }
    }

    /** until with a 3 s timeout. */
    public static void until(BooleanSupplier cond) throws InterruptedException {
        until(Duration.ofSeconds(3), cond);
    }
}
