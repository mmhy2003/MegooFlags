package io.github.mmhy2003.megooflags.internal.engine;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.List;

/** Percentage rollout bucketing, identical in every MegooFlags SDK. */
public final class Bucketing {
    /** The number of rollout buckets; weights add up to it. */
    public static final int BUCKETS = 100000;

    private static final long MAX_SAFE_INTEGER = (1L << 53) - 1;

    private Bucketing() {}

    /** One variation's share of a rollout, out of {@link #BUCKETS}. */
    public static final class Weight {
        public final String variation;
        public final int weight;

        public Weight(String variation, int weight) {
            this.variation = variation;
            this.weight = weight;
        }
    }

    /**
     * bucketText is the text hashed for v, or null when v can't be bucketed (which counts as a
     * missing attribute). A string is used as is, an integral number with |n| <= 2^53-1 as its
     * base-10 text; anything else is refused.
     */
    public static String bucketText(Object v) {
        if (v instanceof String) {
            return (String) v;
        }
        if (Values.isInteger(v)) {
            long n = ((Number) v).longValue();
            return n >= -MAX_SAFE_INTEGER && n <= MAX_SAFE_INTEGER ? Long.toString(n) : null;
        }
        if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            if (d != Math.rint(d) || Math.abs(d) > MAX_SAFE_INTEGER) {
                return null; // NaN fails the first test, infinities the second
            }
            return Long.toString((long) d); // (long) -0.0 is 0
        }
        return null;
    }

    /** bucket is uint64_be(sha256(flagKey + "." + text)[0:8]) mod BUCKETS. */
    public static int bucket(String flagKey, String text) {
        byte[] sum = sha256().digest((flagKey + "." + text).getBytes(StandardCharsets.UTF_8));
        int r = 0;
        for (int i = 0; i < 8; i++) {
            r = (r * 256 + (sum[i] & 0xff)) % BUCKETS;
        }
        return r;
    }

    /**
     * pickVariation returns the first variation whose running total of weights is greater than
     * bucket, or null when there is none. The parser guarantees the weights add up to BUCKETS.
     */
    public static String pickVariation(List<Weight> weights, int bucket) {
        long total = 0;
        for (Weight w : weights) {
            total += w.weight;
            if (total > bucket) {
                return w.variation;
            }
        }
        return null;
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            // Every Java platform must provide SHA-256.
            throw new IllegalStateException(e);
        }
    }
}
