package io.github.mmhy2003.megooflags.internal.delivery;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;

/** A text/event-stream parser over a byte stream (the response body of the stream request). */
public final class SseReader {
    /** One dispatched server-sent event. The name is "message" when the stream sent none. */
    public static final class Event {
        private final String name;
        private final String data;
        private final String id;

        public Event(String name, String data, String id) {
            this.name = name;
            this.data = data;
            this.id = id;
        }

        public String name() {
            return name;
        }

        public String data() {
            return data;
        }

        /** The last id field of the event, or "". */
        public String id() {
            return id;
        }

        @Override
        public boolean equals(Object o) {
            if (!(o instanceof Event)) {
                return false;
            }
            Event e = (Event) o;
            return name.equals(e.name) && data.equals(e.data) && id.equals(e.id);
        }

        @Override
        public int hashCode() {
            return Objects.hash(name, data, id);
        }

        @Override
        public String toString() {
            return "Event{name=" + name + ", id=" + id + ", data=" + data + "}";
        }
    }

    /** What the reader reports. An exception thrown by onEvent stops reading. */
    public interface Callbacks {
        /** Runs for every line, comments included (the dead-connection timer). */
        void onLine();

        /** Receives a valid retry field, in milliseconds. */
        void onRetry(long millis);

        /** Receives each event. */
        void onEvent(Event event) throws IOException;
    }

    private static final byte LF = '\n';
    private static final byte CR = '\r';

    private final Callbacks cb;
    private String name = "";
    private String id = "";
    private final List<String> data = new ArrayList<>();
    private boolean seen;

    private SseReader(Callbacks cb) {
        this.cb = cb;
    }

    /**
     * Parses in until it ends. Lines are split on the LF byte before decoding UTF-8, so a character
     * split across reads decodes whole, and a line of any length is accepted. An unterminated last
     * line or event is discarded. A read error, or an exception thrown by onEvent, is passed
     * through unchanged.
     */
    public static void read(InputStream in, Callbacks cb) throws IOException {
        SseReader r = new SseReader(cb);
        byte[] buf = new byte[64 * 1024];
        byte[] pending = new byte[0];
        int pendingLength = 0;
        for (int n; (n = in.read(buf)) >= 0; ) {
            int start = 0;
            for (int i = 0; i < n; i++) {
                if (buf[i] != LF) {
                    continue;
                }
                if (pendingLength > 0) {
                    pending = append(pending, pendingLength, buf, start, i - start);
                    pendingLength += i - start;
                    r.line(pending, 0, pendingLength);
                    pendingLength = 0;
                } else {
                    r.line(buf, start, i - start);
                }
                start = i + 1;
            }
            if (start < n) {
                pending = append(pending, pendingLength, buf, start, n - start);
                pendingLength += n - start;
            }
        }
    }

    private static byte[] append(byte[] pending, int length, byte[] src, int off, int len) {
        byte[] out = pending;
        if (length + len > out.length) {
            out = Arrays.copyOf(pending, Math.max(length + len, out.length * 2));
        }
        System.arraycopy(src, off, out, length, len);
        return out;
    }

    private void line(byte[] bytes, int off, int len) throws IOException {
        if (len > 0 && bytes[off + len - 1] == CR) {
            len--;
        }
        String line = new String(bytes, off, len, StandardCharsets.UTF_8);
        cb.onLine();
        if (line.isEmpty()) {
            dispatch();
            return;
        }
        if (line.charAt(0) == ':') {
            return;
        }
        String field = line;
        String value = "";
        int colon = line.indexOf(':');
        if (colon >= 0) {
            field = line.substring(0, colon);
            value = line.substring(colon + 1);
            if (value.startsWith(" ")) {
                value = value.substring(1);
            }
        }
        switch (field) {
            case "event":
                name = value;
                break;
            case "data":
                data.add(value);
                seen = true;
                break;
            case "id":
                if (value.indexOf('\0') < 0) {
                    id = value;
                }
                break;
            case "retry":
                Long ms = digits(value);
                if (ms != null) {
                    cb.onRetry(ms);
                }
                break;
            default:
                break;
        }
    }

    private void dispatch() throws IOException {
        Event event = seen ? new Event(name.isEmpty() ? "message" : name, String.join("\n", data), id) : null;
        name = "";
        id = "";
        data.clear();
        seen = false;
        if (event != null) {
            cb.onEvent(event);
        }
    }

    /** A non-empty string of ASCII digits that fits a long, else null. */
    private static Long digits(String s) {
        if (s.isEmpty()) {
            return null;
        }
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c < '0' || c > '9') {
                return null;
            }
        }
        try {
            return Long.parseLong(s);
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
