package io.github.mmhy2003.megooflags;

import java.text.MessageFormat;
import java.util.ArrayList;
import java.util.List;
import java.util.ResourceBundle;

/** A System.Logger that keeps every message, as "LEVEL message". */
public final class RecordingLogger implements System.Logger {
    private final List<String> lines = new ArrayList<>();

    /** Every message logged so far. */
    public synchronized List<String> lines() {
        return new ArrayList<>(lines);
    }

    /** The messages logged at level. */
    public synchronized List<String> at(Level level) {
        List<String> out = new ArrayList<>();
        for (String l : lines) {
            if (l.startsWith(level.getName() + " ")) {
                out.add(l.substring(level.getName().length() + 1));
            }
        }
        return out;
    }

    @Override
    public String getName() {
        return "test";
    }

    @Override
    public boolean isLoggable(Level level) {
        return true;
    }

    @Override
    public synchronized void log(Level level, ResourceBundle bundle, String msg, Throwable thrown) {
        lines.add(level.getName() + " " + msg + (thrown == null ? "" : ": " + thrown));
    }

    @Override
    public synchronized void log(Level level, ResourceBundle bundle, String format, Object... params) {
        String msg = params == null || params.length == 0 ? format : MessageFormat.format(format, params);
        lines.add(level.getName() + " " + msg);
    }
}
