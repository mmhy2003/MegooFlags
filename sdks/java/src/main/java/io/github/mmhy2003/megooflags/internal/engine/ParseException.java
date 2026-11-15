package io.github.mmhy2003.megooflags.internal.engine;

/** An invalid ruleset. The message is {@code <json path>: <reason>} for the first problem. */
public final class ParseException extends Exception {
    private static final long serialVersionUID = 1L;

    private final String path;
    private final String reason;

    public ParseException(String path, String reason) {
        super(path + ": " + reason);
        this.path = path;
        this.reason = reason;
    }

    /** path is the JSON path of the problem, for example {@code flags.f.rules[0].serve}. */
    public String path() {
        return path;
    }

    public String reason() {
        return reason;
    }
}
