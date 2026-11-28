package io.github.mmhy2003.megooflags.internal.events;

import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Buffers deduplicated exposures and hour-bucketed counts (spec/delivery.md §7). Not thread-safe:
 * the batcher's thread owns it.
 */
final class Aggregator {
    /** The largest count the server accepts in one count item. */
    static final long MAX_COUNT = 1_000_000_000L;

    private static final DateTimeFormatter TIMESTAMP =
            DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSS'Z'").withZone(ZoneOffset.UTC);
    private static final DateTimeFormatter HOUR =
            DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss'Z'").withZone(ZoneOffset.UTC);

    /** That a context saw a variant. */
    static final class Exposure {
        final String flagKey;
        final String variant;
        final String reason;
        final String timestamp;
        /** Null without sendContext. */
        final Map<String, Object> context;

        Exposure(String flagKey, String variant, String reason, String timestamp, Map<String, Object> context) {
            this.flagKey = flagKey;
            this.variant = variant;
            this.reason = reason;
            this.timestamp = timestamp;
            this.context = context;
        }

        Map<String, Object> toJson() {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("flag_key", flagKey);
            m.put("variant", variant);
            m.put("reason", reason);
            m.put("timestamp", timestamp);
            if (context != null) {
                m.put("context", context);
            }
            return m;
        }
    }

    /** The evaluations of one flag and variant (null when not served) in one UTC hour. */
    static final class Count {
        final String flagKey;
        final String variant;
        final String hour;
        final long count;

        Count(String flagKey, String variant, String hour, long count) {
            this.flagKey = flagKey;
            this.variant = variant;
            this.hour = hour;
            this.count = count;
        }

        Map<String, Object> toJson() {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("flag_key", flagKey);
            m.put("variant", variant);
            m.put("hour", hour);
            m.put("count", count);
            return m;
        }

        @Override
        public boolean equals(Object o) {
            if (!(o instanceof Count)) {
                return false;
            }
            Count c = (Count) o;
            return flagKey.equals(c.flagKey)
                    && Objects.equals(variant, c.variant)
                    && hour.equals(c.hour)
                    && count == c.count;
        }

        @Override
        public int hashCode() {
            return Objects.hash(flagKey, variant, hour, count);
        }

        @Override
        public String toString() {
            return toJson().toString();
        }
    }

    /** The body of POST /sdk/v1/events. */
    static final class Batch {
        final List<Exposure> exposures;
        final List<Count> counts;

        Batch(List<Exposure> exposures, List<Count> counts) {
            this.exposures = exposures;
            this.counts = counts;
        }

        int size() {
            return exposures.size() + counts.size();
        }

        /** The JSON tree; both lists are always present. */
        Map<String, Object> toJson() {
            List<Object> e = new ArrayList<>(exposures.size());
            for (Exposure x : exposures) {
                e.add(x.toJson());
            }
            List<Object> c = new ArrayList<>(counts.size());
            for (Count x : counts) {
                c.add(x.toJson());
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("exposures", e);
            m.put("counts", c);
            return m;
        }
    }

    /** A fixed-size set that forgets the least recently seen key. */
    static final class Lru<K> {
        private final Map<K, Boolean> items;

        Lru(int capacity) {
            int cap = Math.max(capacity, 1);
            items = new LinkedHashMap<K, Boolean>(16, 0.75f, true) {
                private static final long serialVersionUID = 1L;

                @Override
                protected boolean removeEldestEntry(Map.Entry<K, Boolean> eldest) {
                    return size() > cap;
                }
            };
        }

        /**
         * Reports whether k is present and marks it as the most recent. A new key is inserted,
         * evicting the oldest one above the capacity.
         */
        boolean seen(K k) {
            return items.put(k, Boolean.TRUE) != null;
        }

        int size() {
            return items.size();
        }
    }

    /** A context without a string key has ctxKey "", so anonymous contexts share an exposure. */
    private static final class DedupKey {
        final String ctxKey;
        final String flag;
        final String variant;
        final long hour;

        DedupKey(String ctxKey, String flag, String variant, long hour) {
            this.ctxKey = ctxKey;
            this.flag = flag;
            this.variant = variant;
            this.hour = hour;
        }

        @Override
        public boolean equals(Object o) {
            if (!(o instanceof DedupKey)) {
                return false;
            }
            DedupKey k = (DedupKey) o;
            return hour == k.hour && ctxKey.equals(k.ctxKey) && flag.equals(k.flag) && variant.equals(k.variant);
        }

        @Override
        public int hashCode() {
            return Objects.hash(ctxKey, flag, variant, hour);
        }
    }

    /** variant is null for unserved evaluations. */
    private static final class CountKey {
        final String flag;
        final String variant;
        final long hour;

        CountKey(String flag, String variant, long hour) {
            this.flag = flag;
            this.variant = variant;
            this.hour = hour;
        }

        @Override
        public boolean equals(Object o) {
            if (!(o instanceof CountKey)) {
                return false;
            }
            CountKey k = (CountKey) o;
            return hour == k.hour && flag.equals(k.flag) && Objects.equals(variant, k.variant);
        }

        @Override
        public int hashCode() {
            return Objects.hash(flag, variant, hour);
        }
    }

    private final Lru<DedupKey> dedup;
    /** In the order the buckets were first seen. */
    private LinkedHashMap<CountKey, Long> counts = new LinkedHashMap<>();

    private final ArrayDeque<Exposure> exposures = new ArrayDeque<>();
    private final boolean sendContext;
    private final int maxPending;
    private long dropped;

    Aggregator(int dedupSize, int maxPending, boolean sendContext) {
        this.dedup = new Lru<>(dedupSize);
        this.maxPending = maxPending;
        this.sendContext = sendContext;
    }

    private static long hourOf(Instant at) {
        return Math.floorDiv(at.getEpochSecond(), 3600) * 3600;
    }

    /**
     * Counts ev and, the first time a served variant is seen for its context key, flag and hour,
     * queues an exposure. An exposure beyond maxPending is dropped and counted; counts are never
     * dropped.
     */
    void add(Evaluation ev) {
        addCount(ev.flagKey, ev.served ? ev.variant : null, ev.at, 1);
        if (!ev.served || dedup.seen(new DedupKey(ev.contextKey, ev.flagKey, ev.variant, hourOf(ev.at)))) {
            return;
        }
        if (exposures.size() >= maxPending) {
            dropped++;
            return;
        }
        exposures.add(new Exposure(
                ev.flagKey, ev.variant, ev.reason, TIMESTAMP.format(ev.at), sendContext ? ev.context : null));
    }

    /** Adds n to the bucket of flag, variant (null when not served) and the hour of at. */
    void addCount(String flag, String variant, Instant at, long n) {
        counts.merge(new CountKey(flag, variant, hourOf(at)), n, Long::sum);
    }

    int pendingExposures() {
        return exposures.size();
    }

    /** The number of count buckets. */
    int pendingCounts() {
        return counts.size();
    }

    /** Exposures dropped above maxPending. */
    long dropped() {
        return dropped;
    }

    /**
     * Removes and returns up to limit exposures (oldest first) and up to limit count items (in the
     * order the buckets were first seen). A bucket above MAX_COUNT becomes several items; what does
     * not fit stays for the next batch.
     */
    Batch take(int limit) {
        List<Exposure> e = new ArrayList<>(Math.min(limit, exposures.size()));
        while (e.size() < limit && !exposures.isEmpty()) {
            e.add(exposures.poll());
        }
        List<Count> c = new ArrayList<>();
        Iterator<Map.Entry<CountKey, Long>> it = counts.entrySet().iterator();
        while (it.hasNext() && c.size() < limit) {
            Map.Entry<CountKey, Long> entry = it.next();
            CountKey k = entry.getKey();
            String hour = HOUR.format(Instant.ofEpochSecond(k.hour));
            long left = entry.getValue();
            while (left > 0 && c.size() < limit) {
                long n = Math.min(left, MAX_COUNT);
                c.add(new Count(k.flag, k.variant, hour, n));
                left -= n;
            }
            if (left > 0) {
                entry.setValue(left);
                break;
            }
            it.remove();
        }
        return new Batch(e, c);
    }

    /**
     * Puts an unsent batch back in front of what is pending, so a retry sends it first. Its counts
     * add to any newer count for the same bucket.
     */
    void restore(Batch b) {
        List<Exposure> back = new ArrayList<>(b.exposures);
        Collections.reverse(back);
        for (Exposure x : back) {
            exposures.addFirst(x);
        }
        LinkedHashMap<CountKey, Long> order = new LinkedHashMap<>();
        for (Count c : b.counts) {
            order.merge(new CountKey(c.flagKey, c.variant, Instant.parse(c.hour).getEpochSecond()), c.count, Long::sum);
        }
        for (Map.Entry<CountKey, Long> e : counts.entrySet()) {
            order.merge(e.getKey(), e.getValue(), Long::sum);
        }
        counts = order;
    }
}
