package io.github.mmhy2003.megooflags.internal.delivery;

import io.github.mmhy2003.megooflags.internal.engine.Ruleset;
import java.util.Locale;

/** One parsed ruleset with the ETag and the source it came from. Immutable. */
public final class Snapshot {
    /** Where a ruleset came from. */
    public enum Source {
        BOOTSTRAP,
        CACHE,
        STREAM,
        POLL;

        /** The lowercase name: bootstrap, cache, stream or poll. */
        @Override
        public String toString() {
            return name().toLowerCase(Locale.ROOT);
        }
    }

    private final Ruleset ruleset;
    private final String etag;
    private final Source source;

    public Snapshot(Ruleset ruleset, String etag, Source source) {
        this.ruleset = ruleset;
        this.etag = etag;
        this.source = source;
    }

    public Ruleset ruleset() {
        return ruleset;
    }

    /** The ETag, or "" when the source gave none. */
    public String etag() {
        return etag;
    }

    public Source source() {
        return source;
    }
}
