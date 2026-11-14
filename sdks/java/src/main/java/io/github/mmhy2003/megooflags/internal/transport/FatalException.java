package io.github.mmhy2003.megooflags.internal.transport;

import java.io.IOException;

/**
 * The server refused the SDK key (401, 403, or the stream's revoked event). Retrying with the same
 * key cannot succeed.
 */
public final class FatalException extends IOException {
    private static final long serialVersionUID = 1L;

    private final int status;
    private final String reason;

    private FatalException(int status, String reason) {
        super("megooflags: SDK key refused (" + (status != 0 ? Integer.toString(status) : reason) + ")");
        this.status = status;
        this.reason = reason;
    }

    /** A refusal by HTTP status (401 or 403). */
    public FatalException(int status) {
        this(status, "");
    }

    /** The stream's revoked event. */
    public static FatalException revoked() {
        return new FatalException(0, "revoked");
    }

    /** The HTTP status, or 0 when the refusal came as a reason. */
    public int status() {
        return status;
    }

    /** The reason ("revoked"), or "" for a refusal by status. */
    public String reason() {
        return reason;
    }
}
