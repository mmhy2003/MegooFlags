package io.github.mmhy2003.megooflags.internal.json;

/** Thrown when text is not strict JSON or a value can't be written as JSON. */
public final class JsonException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    public JsonException(String message) {
        super(message);
    }
}
