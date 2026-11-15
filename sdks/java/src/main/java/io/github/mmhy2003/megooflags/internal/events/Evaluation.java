package io.github.mmhy2003.megooflags.internal.events;

import java.time.Instant;
import java.util.Map;

/** One flag evaluation as the provider records it. */
public final class Evaluation {
    public final String flagKey;
    /** The variant served; ignored when served is false. */
    public final String variant;
    /** False when the default was returned (an error, not ready). */
    public final boolean served;

    public final String reason;
    /** The context's key, or "" when it has no string key. */
    public final String contextKey;
    /** The context as a JSON tree; sent only with sendContext. May be null. */
    public final Map<String, Object> context;

    public final Instant at;

    public Evaluation(
            String flagKey,
            String variant,
            boolean served,
            String reason,
            String contextKey,
            Map<String, Object> context,
            Instant at) {
        this.flagKey = flagKey;
        this.variant = variant;
        this.served = served;
        this.reason = reason;
        this.contextKey = contextKey;
        this.context = context;
        this.at = at;
    }
}
