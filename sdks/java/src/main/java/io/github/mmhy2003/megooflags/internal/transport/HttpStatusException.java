package io.github.mmhy2003.megooflags.internal.transport;

import java.io.IOException;

/** Any other unexpected HTTP status. */
public final class HttpStatusException extends IOException {
    private static final long serialVersionUID = 1L;

    private final int status;

    public HttpStatusException(int status) {
        super("megooflags: HTTP " + status);
        this.status = status;
    }

    public int status() {
        return status;
    }
}
