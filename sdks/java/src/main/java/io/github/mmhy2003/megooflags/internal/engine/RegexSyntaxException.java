package io.github.mmhy2003.megooflags.internal.engine;

/** Thrown when a pattern is outside the portable regex subset, or the host engine refused it. */
public final class RegexSyntaxException extends Exception {
    private static final long serialVersionUID = 1L;

    public RegexSyntaxException(String reason) {
        super(reason);
    }
}
