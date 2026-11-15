package io.github.mmhy2003.megooflags.internal.transport;

import java.io.IOException;
import java.time.Duration;

/** A 429. after is the server's Retry-After, or zero. */
public final class RetryAfterException extends IOException {
    private static final long serialVersionUID = 1L;

    private final Duration after;

    public RetryAfterException(Duration after) {
        super("megooflags: rate limited, retry after " + seconds(after) + "s");
        this.after = after;
    }

    /** The wait the server asked for; zero when it gave none. */
    public Duration after() {
        return after;
    }

    private static String seconds(Duration d) {
        long millis = d.toMillis();
        return millis % 1000 == 0 ? Long.toString(millis / 1000) : Double.toString(millis / 1000.0);
    }
}
