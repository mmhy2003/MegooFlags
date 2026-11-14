package io.github.mmhy2003.megooflags.internal.transport;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

class BackoffTest {
    private static final Duration BASE = Duration.ofSeconds(3);
    private static final Duration MAX = Duration.ofSeconds(60);

    @Test
    void doublesFromBaseUpToMax() {
        Backoff b = new Backoff(BASE, MAX, () -> 1.0);
        List<Long> waits = new ArrayList<>();
        for (int i = 0; i < 7; i++) {
            waits.add(b.next().toSeconds());
        }
        assertEquals(List.of(3L, 6L, 12L, 24L, 48L, 60L, 60L), waits);
    }

    @Test
    void resetStartsOver() {
        Backoff b = new Backoff(BASE, MAX, () -> 1.0);
        b.next();
        b.next();
        b.reset();
        assertEquals(BASE, b.next());
    }

    @Test
    void jittersFromTheSecondWait() {
        Backoff b = new Backoff(BASE, MAX, () -> 0.0);
        assertEquals(Duration.ofMillis(3000), b.next());
        assertEquals(Duration.ofMillis(4800), b.next());
        assertEquals(Duration.ofMillis(9600), b.next());
    }

    @Test
    void firstWaitIsNeverAboveMax() {
        assertEquals(MAX, new Backoff(Duration.ofSeconds(90), MAX).next());
    }

    @Test
    void neverGoesAboveMax() {
        Backoff b = new Backoff(BASE, MAX);
        for (int i = 0; i < 200; i++) {
            Duration w = b.next();
            assertTrue(w.compareTo(Duration.ZERO) > 0);
            assertTrue(w.compareTo(MAX) <= 0, w.toString());
        }
    }

    @Test
    void baseCanBeReplaced() {
        Backoff b = new Backoff(BASE, MAX, () -> 1.0);
        b.setBase(Duration.ofSeconds(1));
        assertEquals(Duration.ofSeconds(1), b.next());
        assertEquals(Duration.ofSeconds(2), b.next());
    }

    @Test
    void sleepReturnsTrueAfterTheWait() {
        assertTrue(Backoff.sleep(Duration.ofMillis(1), new CountDownLatch(1)));
    }

    @Test
    void sleepEndsEarlyWhenCancelled() {
        CountDownLatch cancel = new CountDownLatch(1);
        ScheduledExecutorService s = Executors.newSingleThreadScheduledExecutor();
        try {
            s.schedule(cancel::countDown, 10, TimeUnit.MILLISECONDS);
            long start = System.nanoTime();
            assertFalse(Backoff.sleep(Duration.ofHours(1), cancel));
            assertTrue(System.nanoTime() - start < TimeUnit.SECONDS.toNanos(5));
        } finally {
            s.shutdownNow();
        }
    }

    @Test
    void sleepReturnsFalseAtOnceWhenAlreadyCancelled() {
        CountDownLatch cancel = new CountDownLatch(1);
        cancel.countDown();
        assertFalse(Backoff.sleep(Duration.ofHours(1), cancel));
    }

    @Test
    void sleepReturnsFalseWhenInterruptedAndKeepsTheFlag() {
        Thread.currentThread().interrupt();
        try {
            assertFalse(Backoff.sleep(Duration.ofHours(1), new CountDownLatch(1)));
            assertTrue(Thread.currentThread().isInterrupted());
        } finally {
            Thread.interrupted();
        }
    }
}
