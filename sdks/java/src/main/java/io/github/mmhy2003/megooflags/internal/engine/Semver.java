package io.github.mmhy2003.megooflags.internal.engine;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * A parsed SemVer 2.0.0 version. Build metadata is dropped, as it has no precedence. The core
 * parts are digit strings without leading zeros, so they compare by (length, text) without
 * overflow.
 */
public final class Semver {
    private final String[] core;
    /** null when the version has no prerelease. */
    private final List<String> pre;

    private Semver(String[] core, List<String> pre) {
        this.core = core;
        this.pre = pre;
    }

    /**
     * parse parses a strict SemVer 2.0.0 string, or returns null: ASCII only, no "v" prefix, no
     * surrounding whitespace, no leading zeros in numeric parts.
     */
    public static Semver parse(String s) {
        String rest = s;
        int plus = rest.indexOf('+');
        if (plus >= 0) {
            for (String id : split(rest.substring(plus + 1))) {
                if (!isIdent(id)) {
                    return null;
                }
            }
            rest = rest.substring(0, plus);
        }
        List<String> pre = null;
        int dash = rest.indexOf('-');
        if (dash >= 0) {
            pre = new ArrayList<>();
            for (String id : split(rest.substring(dash + 1))) {
                if (!isIdent(id) || (isDigits(id) && !isNumericPart(id))) {
                    return null;
                }
                pre.add(id);
            }
            pre = Collections.unmodifiableList(pre);
            rest = rest.substring(0, dash);
        }
        List<String> parts = split(rest);
        if (parts.size() != 3) {
            return null;
        }
        for (String p : parts) {
            if (!isNumericPart(p)) {
                return null;
            }
        }
        return new Semver(parts.toArray(new String[0]), pre);
    }

    /** compare returns -1, 0 or 1 by SemVer 2.0.0 precedence. */
    public static int compare(Semver a, Semver b) {
        for (int i = 0; i < 3; i++) {
            int c = compareDigits(a.core[i], b.core[i]);
            if (c != 0) {
                return c;
            }
        }
        if (a.pre == null) {
            return b.pre == null ? 0 : 1;
        }
        if (b.pre == null) {
            return -1;
        }
        int n = Math.min(a.pre.size(), b.pre.size());
        for (int i = 0; i < n; i++) {
            String x = a.pre.get(i);
            String y = b.pre.get(i);
            boolean xn = isDigits(x);
            boolean yn = isDigits(y);
            int c;
            if (xn && yn) {
                c = compareDigits(x, y);
            } else if (xn) {
                c = -1;
            } else if (yn) {
                c = 1;
            } else {
                c = Integer.signum(x.compareTo(y)); // ASCII, so UTF-16 order is ASCII order
            }
            if (c != 0) {
                return c;
            }
        }
        return Integer.compare(a.pre.size(), b.pre.size());
    }

    /** compareDigits compares two digit strings without leading zeros numerically. */
    private static int compareDigits(String a, String b) {
        if (a.length() != b.length()) {
            return Integer.compare(a.length(), b.length());
        }
        return Integer.signum(a.compareTo(b));
    }

    /** split splits on '.', keeping empty parts (String.split drops trailing ones). */
    private static List<String> split(String s) {
        List<String> out = new ArrayList<>();
        int start = 0;
        while (true) {
            int dot = s.indexOf('.', start);
            if (dot < 0) {
                out.add(s.substring(start));
                return out;
            }
            out.add(s.substring(start, dot));
            start = dot + 1;
        }
    }

    /** isNumericPart reports whether s is "0" or [1-9][0-9]*. */
    private static boolean isNumericPart(String s) {
        return isDigits(s) && (s.equals("0") || s.charAt(0) != '0');
    }

    /** isDigits reports whether s is a non-empty run of ASCII digits. */
    private static boolean isDigits(String s) {
        if (s.isEmpty()) {
            return false;
        }
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c < '0' || c > '9') {
                return false;
            }
        }
        return true;
    }

    /** isIdent reports whether s is a non-empty run of [0-9A-Za-z-]. */
    private static boolean isIdent(String s) {
        if (s.isEmpty()) {
            return false;
        }
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            boolean ok = (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '-';
            if (!ok) {
                return false;
            }
        }
        return true;
    }
}
