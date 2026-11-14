package io.github.mmhy2003.megooflags.internal.json;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * A strict RFC 8259 JSON reader and writer over a plain tree: {@code LinkedHashMap<String,Object>},
 * {@code ArrayList<Object>}, {@code String}, {@code Long} (an integer literal that fits) or
 * {@code Double}, {@code Boolean} and {@code null}.
 */
public final class Json {
    /** The deepest nesting of arrays and objects the reader accepts. */
    public static final int MAX_DEPTH = 512;

    private final String text;
    private int pos;
    private int depth;

    private Json(String text) {
        this.text = text;
    }

    /**
     * Parses text as one JSON value. Trailing data, trailing commas, leading zeros, NaN and
     * Infinity, unescaped control characters and bad escapes are refused. Duplicate keys: the last
     * one wins. Lone surrogate escapes become U+FFFD.
     */
    public static Object parse(String text) {
        Json p = new Json(text);
        p.skipWhitespace();
        Object v = p.readValue();
        p.skipWhitespace();
        if (p.pos != text.length()) {
            throw p.error("trailing data");
        }
        return v;
    }

    /**
     * Writes a tree as compact JSON. Maps need String keys; any Collection is an array; numbers
     * must be finite. Non-ASCII characters are written as is.
     */
    public static String write(Object value) {
        StringBuilder out = new StringBuilder();
        writeValue(out, value, 0);
        return out.toString();
    }

    private Object readValue() {
        if (pos >= text.length()) {
            throw error("unexpected end of input");
        }
        char c = text.charAt(pos);
        switch (c) {
            case '{':
                return readObject();
            case '[':
                return readArray();
            case '"':
                return readString();
            case 't':
                return readLiteral("true", Boolean.TRUE);
            case 'f':
                return readLiteral("false", Boolean.FALSE);
            case 'n':
                return readLiteral("null", null);
            default:
                if (c == '-' || (c >= '0' && c <= '9')) {
                    return readNumber();
                }
                throw error("unexpected character");
        }
    }

    private Object readLiteral(String word, Object value) {
        if (!text.startsWith(word, pos)) {
            throw error("invalid literal");
        }
        pos += word.length();
        return value;
    }

    private Map<String, Object> readObject() {
        enter();
        pos++; // {
        Map<String, Object> map = new LinkedHashMap<>();
        skipWhitespace();
        if (peek() == '}') {
            pos++;
            depth--;
            return map;
        }
        while (true) {
            skipWhitespace();
            if (peek() != '"') {
                throw error("expected a string key");
            }
            String key = readString();
            skipWhitespace();
            expect(':');
            skipWhitespace();
            map.put(key, readValue());
            skipWhitespace();
            char c = peek();
            pos++;
            if (c == '}') {
                depth--;
                return map;
            }
            if (c != ',') {
                throw error("expected ',' or '}'");
            }
        }
    }

    private ArrayList<Object> readArray() {
        enter();
        pos++; // [
        ArrayList<Object> list = new ArrayList<>();
        skipWhitespace();
        if (peek() == ']') {
            pos++;
            depth--;
            return list;
        }
        while (true) {
            skipWhitespace();
            list.add(readValue());
            skipWhitespace();
            char c = peek();
            pos++;
            if (c == ']') {
                depth--;
                return list;
            }
            if (c != ',') {
                throw error("expected ',' or ']'");
            }
        }
    }

    private void enter() {
        if (++depth > MAX_DEPTH) {
            throw error("nesting deeper than " + MAX_DEPTH);
        }
    }

    private String readString() {
        pos++; // opening quote
        StringBuilder sb = new StringBuilder();
        while (true) {
            if (pos >= text.length()) {
                throw error("unterminated string");
            }
            char c = text.charAt(pos++);
            if (c == '"') {
                return sb.toString();
            }
            if (c < 0x20) {
                throw error("unescaped control character in string");
            }
            if (c != '\\') {
                sb.append(c);
                continue;
            }
            if (pos >= text.length()) {
                throw error("unterminated string");
            }
            char e = text.charAt(pos++);
            switch (e) {
                case '"':
                case '\\':
                case '/':
                    sb.append(e);
                    break;
                case 'b':
                    sb.append('\b');
                    break;
                case 'f':
                    sb.append('\f');
                    break;
                case 'n':
                    sb.append('\n');
                    break;
                case 'r':
                    sb.append('\r');
                    break;
                case 't':
                    sb.append('\t');
                    break;
                case 'u':
                    appendUnicodeEscape(sb);
                    break;
                default:
                    throw error("invalid escape");
            }
        }
    }

    // Called after "\\u". A high surrogate followed by a low surrogate escape is one code point;
    // any other surrogate escape becomes U+FFFD.
    private void appendUnicodeEscape(StringBuilder sb) {
        char c = (char) readHex4();
        if (Character.isHighSurrogate(c) && text.startsWith("\\u", pos)) {
            int save = pos;
            pos += 2;
            char low = (char) readHex4();
            if (Character.isLowSurrogate(low)) {
                sb.append(c).append(low);
                return;
            }
            pos = save; // read the next escape on its own
        }
        sb.append(Character.isSurrogate(c) ? '�' : c);
    }

    private int readHex4() {
        if (pos + 4 > text.length()) {
            throw error("invalid \\u escape");
        }
        int v = 0;
        for (int i = 0; i < 4; i++) {
            char c = text.charAt(pos + i);
            int d;
            if (c >= '0' && c <= '9') {
                d = c - '0';
            } else if (c >= 'a' && c <= 'f') {
                d = c - 'a' + 10;
            } else if (c >= 'A' && c <= 'F') {
                d = c - 'A' + 10;
            } else {
                throw error("invalid \\u escape");
            }
            v = v * 16 + d;
        }
        pos += 4;
        return v;
    }

    private Object readNumber() {
        int start = pos;
        if (peek() == '-') {
            pos++;
        }
        char c = peek();
        if (c == '0') {
            pos++;
        } else if (c >= '1' && c <= '9') {
            skipDigits();
        } else {
            throw error("invalid number");
        }
        boolean integer = true;
        if (peek() == '.') {
            integer = false;
            pos++;
            if (!isDigit(peek())) {
                throw error("invalid number");
            }
            skipDigits();
        }
        c = peek();
        if (c == 'e' || c == 'E') {
            integer = false;
            pos++;
            c = peek();
            if (c == '+' || c == '-') {
                pos++;
            }
            if (!isDigit(peek())) {
                throw error("invalid number");
            }
            skipDigits();
        }
        String literal = text.substring(start, pos);
        if (integer) {
            try {
                return Long.parseLong(literal);
            } catch (NumberFormatException tooBig) {
                // falls through to a double, as for any other literal
            }
        }
        return Double.parseDouble(literal);
    }

    private void skipDigits() {
        while (isDigit(peek())) {
            pos++;
        }
    }

    private static boolean isDigit(char c) {
        return c >= '0' && c <= '9';
    }

    private void skipWhitespace() {
        while (pos < text.length()) {
            char c = text.charAt(pos);
            if (c != ' ' && c != '\t' && c != '\n' && c != '\r') {
                return;
            }
            pos++;
        }
    }

    // peek returns the current character, or NUL at the end (which no rule accepts).
    private char peek() {
        return pos < text.length() ? text.charAt(pos) : '\0';
    }

    private void expect(char c) {
        if (peek() != c) {
            throw error("expected '" + c + "'");
        }
        pos++;
    }

    private JsonException error(String reason) {
        return new JsonException(reason + " at offset " + pos);
    }

    private static void writeValue(StringBuilder out, Object v, int depth) {
        if (depth > MAX_DEPTH) {
            throw new JsonException("nesting deeper than " + MAX_DEPTH);
        }
        if (v == null) {
            out.append("null");
        } else if (v instanceof String) {
            writeString(out, (String) v);
        } else if (v instanceof Boolean) {
            out.append(((Boolean) v).booleanValue());
        } else if (v instanceof Long || v instanceof Integer || v instanceof Short || v instanceof Byte) {
            out.append(((Number) v).longValue());
        } else if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) {
                throw new JsonException("non-finite number");
            }
            out.append(d);
        } else if (v instanceof Map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!(e.getKey() instanceof String)) {
                    throw new JsonException("object key is not a string");
                }
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeString(out, (String) e.getKey());
                out.append(':');
                writeValue(out, e.getValue(), depth + 1);
            }
            out.append('}');
        } else if (v instanceof Collection) {
            out.append('[');
            boolean first = true;
            for (Object item : (Collection<?>) v) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                writeValue(out, item, depth + 1);
            }
            out.append(']');
        } else {
            throw new JsonException("can't write " + v.getClass().getName() + " as JSON");
        }
    }

    private static void writeString(StringBuilder out, String s) {
        out.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':
                    out.append("\\\"");
                    break;
                case '\\':
                    out.append("\\\\");
                    break;
                case '\b':
                    out.append("\\b");
                    break;
                case '\f':
                    out.append("\\f");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        out.append('"');
    }
}
