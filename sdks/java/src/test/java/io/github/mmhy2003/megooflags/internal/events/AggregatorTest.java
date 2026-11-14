package io.github.mmhy2003.megooflags.internal.events;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.github.mmhy2003.megooflags.internal.events.Aggregator.Batch;
import io.github.mmhy2003.megooflags.internal.events.Aggregator.Count;
import io.github.mmhy2003.megooflags.internal.events.Aggregator.Exposure;
import io.github.mmhy2003.megooflags.internal.json.Json;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class AggregatorTest {
    private static final Instant T0 = Instant.parse("2026-11-10T09:15:02Z");

    private static Evaluation served(String flag, String variant, String key, Instant at) {
        return new Evaluation(flag, variant, true, "SPLIT", key, Map.of("key", key), at);
    }

    private static Evaluation unserved(String flag, Instant at) {
        return new Evaluation(flag, "", false, "ERROR", "u1", null, at);
    }

    @Test
    void lruEvictsTheLeastRecentlySeenKey() {
        Aggregator.Lru<String> l = new Aggregator.Lru<>(2);
        assertFalse(l.seen("a"));
        assertFalse(l.seen("b"));
        assertTrue(l.seen("a"), "a not seen after insert");
        // a is now the most recent, so inserting c evicts b.
        assertFalse(l.seen("c"));
        assertTrue(l.seen("a"), "a was evicted, want b evicted");
        assertFalse(l.seen("b"), "b still present after eviction");
        assertEquals(2, l.size());
    }

    @Test
    void oneExposurePerKeyFlagVariantAndHour() {
        Aggregator a = new Aggregator(100, 100, false);
        a.add(served("f", "on", "u1", T0));
        a.add(served("f", "on", "u1", T0.plus(Duration.ofMinutes(10))));
        assertEquals(1, a.pendingExposures(), "same hour");
        a.add(served("f", "on", "u1", T0.plus(Duration.ofHours(1))));
        assertEquals(2, a.pendingExposures(), "next hour");

        Map<String, Evaluation> cases = Map.of(
                "another variant", served("f", "off", "u1", T0),
                "another key", served("f", "on", "u2", T0),
                "another flag", served("g", "on", "u1", T0));
        cases.forEach((name, ev) -> {
            Aggregator b = new Aggregator(100, 100, false);
            b.add(served("f", "on", "u1", T0));
            b.add(ev);
            assertEquals(2, b.pendingExposures(), name);
        });
    }

    @Test
    void anonymousContextsShareOneExposure() {
        Aggregator a = new Aggregator(100, 100, false);
        a.add(served("f", "on", "", T0));
        a.add(served("f", "on", "", T0));
        assertEquals(1, a.pendingExposures());
    }

    @Test
    void countsAddUpPerHour() {
        Aggregator a = new Aggregator(100, 100, false);
        for (int i = 0; i < 5; i++) {
            a.add(served("f", "on", "u" + i, T0));
        }
        a.add(served("f", "on", "u1", T0.plus(Duration.ofHours(1))));
        a.add(served("f", "off", "u1", T0));
        assertEquals(3, a.pendingCounts());
        Batch b = a.take(1000);
        assertEquals(
                List.of(
                        new Count("f", "on", "2026-11-10T09:00:00Z", 5),
                        new Count("f", "on", "2026-11-10T10:00:00Z", 1),
                        new Count("f", "off", "2026-11-10T09:00:00Z", 1)),
                b.counts);
        assertEquals(0, a.pendingExposures());
        assertEquals(0, a.pendingCounts());
    }

    @Test
    void unservedEvaluationsHaveANullVariantAndNoExposure() {
        Aggregator a = new Aggregator(100, 100, false);
        a.add(unserved("f", T0));
        a.add(new Evaluation("f", "", false, "ERROR", "u2", null, T0));
        Batch b = a.take(1000);
        assertEquals(0, b.exposures.size());
        assertEquals(List.of(new Count("f", null, "2026-11-10T09:00:00Z", 2)), b.counts);
    }

    @Test
    void contextOnlyWithSendContext() {
        Aggregator off = new Aggregator(100, 100, false);
        off.add(served("f", "on", "u1", T0));
        assertNull(off.take(1000).exposures.get(0).context);

        Aggregator on = new Aggregator(100, 100, true);
        on.add(served("f", "on", "u1", T0));
        assertEquals(Map.of("key", "u1"), on.take(1000).exposures.get(0).context);
    }

    @Test
    void exposureFieldsAndTimestampFormat() {
        Aggregator a = new Aggregator(100, 100, false);
        a.add(served("f", "on", "u1", T0.plusMillis(123)));
        Exposure e = a.take(1000).exposures.get(0);
        assertEquals("f", e.flagKey);
        assertEquals("on", e.variant);
        assertEquals("SPLIT", e.reason);
        assertEquals("2026-11-10T09:15:02.123Z", e.timestamp);
    }

    @Test
    void hourFormatIsTheUtcHour() {
        Aggregator a = new Aggregator(10, 10, false);
        a.add(unserved(
                "f", OffsetDateTime.parse("2026-11-10T11:59:59.999+02:00").toInstant()));
        assertEquals("2026-11-10T09:00:00Z", a.take(10).counts.get(0).hour);
    }

    @Test
    void lruEvictionAllowsAnExposureAgain() {
        Aggregator a = new Aggregator(2, 100, false);
        a.add(served("f", "on", "u1", T0));
        a.add(served("f", "on", "u2", T0));
        a.add(served("f", "on", "u3", T0)); // evicts u1
        a.add(served("f", "on", "u1", T0));
        assertEquals(4, a.pendingExposures());
    }

    @Test
    void maxPendingDropsExposuresButNotCounts() {
        Aggregator a = new Aggregator(100, 2, false);
        for (int i = 0; i < 5; i++) {
            a.add(served("f", "on", "u" + i, T0));
        }
        a.add(served("f", "on", "u0", T0)); // a duplicate is not a drop
        assertEquals(2, a.pendingExposures());
        assertEquals(3, a.dropped());
        assertEquals(List.of(new Count("f", "on", "2026-11-10T09:00:00Z", 6)), a.take(1000).counts);
    }

    @Test
    void takeLimitsTo1000() {
        Aggregator a = new Aggregator(10000, 10000, false);
        for (int i = 0; i < 1500; i++) {
            a.add(served("f" + i, "on", "u", T0));
        }
        Batch b = a.take(1000);
        assertEquals(1000, b.exposures.size());
        assertEquals(1000, b.counts.size());
        assertEquals("f0", b.exposures.get(0).flagKey);
        assertEquals("f999", b.counts.get(999).flagKey);
        b = a.take(1000);
        assertEquals(500, b.exposures.size());
        assertEquals(500, b.counts.size());
        assertEquals("f1000", b.exposures.get(0).flagKey);
        assertEquals("f1000", b.counts.get(0).flagKey);
        assertEquals(0, a.pendingExposures());
        assertEquals(0, a.pendingCounts());
    }

    @Test
    void takeSplitsACountOverOneBillion() {
        Aggregator a = new Aggregator(10, 10, false);
        a.addCount("f", "on", T0, 2_500_000_000L);
        assertEquals(List.of(1_000_000_000L, 1_000_000_000L, 500_000_000L), countsOf(a.take(1000)));

        // The item limit holds; the remainder stays pending for the next batch.
        a.addCount("f", "on", T0, 2_500_000_000L);
        assertEquals(2, a.take(2).counts.size());
        assertEquals(List.of(500_000_000L), countsOf(a.take(2)));
    }

    @Test
    void restoreMergesCountsAndKeepsOrder() {
        Aggregator a = new Aggregator(100, 100, false);
        a.add(served("f", "on", "u1", T0));
        a.add(served("f", "on", "u2", T0));
        a.add(unserved("f", T0));
        Batch sent = a.take(1000);

        a.add(served("f", "on", "u3", T0));
        a.add(served("g", "on", "u3", T0));
        a.restore(sent);

        Batch b = a.take(1000);
        assertEquals(4, b.exposures.size());
        assertEquals(sent.exposures.get(0), b.exposures.get(0));
        assertEquals(sent.exposures.get(1), b.exposures.get(1));
        assertEquals("f", b.exposures.get(2).flagKey);
        assertEquals("g", b.exposures.get(3).flagKey);
        assertEquals(
                List.of(
                        new Count("f", "on", "2026-11-10T09:00:00Z", 3),
                        new Count("f", null, "2026-11-10T09:00:00Z", 1),
                        new Count("g", "on", "2026-11-10T09:00:00Z", 1)),
                b.counts);
    }

    @Test
    void restoreJoinsSplitCounts() {
        Aggregator a = new Aggregator(10, 10, false);
        a.addCount("f", "on", T0, 2_500_000_000L);
        a.restore(a.take(1000));
        assertEquals(1, a.pendingCounts());
        assertEquals(List.of(1_000_000_000L, 1_000_000_000L, 500_000_000L), countsOf(a.take(1000)));
    }

    @Test
    void jsonShape() {
        Aggregator a = new Aggregator(10, 10, true);
        a.add(served("new-checkout", "on", "user-1", T0));
        a.add(unserved("new-checkout", T0));
        assertEquals(
                "{\"exposures\":[{\"flag_key\":\"new-checkout\",\"variant\":\"on\",\"reason\":\"SPLIT\","
                        + "\"timestamp\":\"2026-11-10T09:15:02.000Z\",\"context\":{\"key\":\"user-1\"}}],"
                        + "\"counts\":[{\"flag_key\":\"new-checkout\",\"variant\":\"on\","
                        + "\"hour\":\"2026-11-10T09:00:00Z\",\"count\":1},"
                        + "{\"flag_key\":\"new-checkout\",\"variant\":null,"
                        + "\"hour\":\"2026-11-10T09:00:00Z\",\"count\":1}]}",
                Json.write(a.take(10).toJson()));

        // Without a context the key is omitted, and an empty list is [].
        a = new Aggregator(10, 10, false);
        a.add(served("f", "on", "u", T0));
        Batch b = a.take(10);
        a.restore(new Batch(b.exposures, List.of()));
        assertEquals(
                "{\"exposures\":[{\"flag_key\":\"f\",\"variant\":\"on\",\"reason\":\"SPLIT\","
                        + "\"timestamp\":\"2026-11-10T09:15:02.000Z\"}],\"counts\":[]}",
                Json.write(a.take(10).toJson()));
        a = new Aggregator(10, 10, false);
        a.add(unserved("f", T0));
        assertEquals(
                "{\"exposures\":[],\"counts\":[{\"flag_key\":\"f\",\"variant\":null,"
                        + "\"hour\":\"2026-11-10T09:00:00Z\",\"count\":1}]}",
                Json.write(a.take(10).toJson()));
    }

    private static List<Long> countsOf(Batch b) {
        List<Long> out = new ArrayList<>();
        for (Count c : b.counts) {
            out.add(c.count);
        }
        return out;
    }
}
