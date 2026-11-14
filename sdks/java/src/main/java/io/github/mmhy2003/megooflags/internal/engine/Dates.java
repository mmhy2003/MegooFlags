package io.github.mmhy2003.megooflags.internal.engine;

/**
 * RFC 3339 timestamps as integer epoch milliseconds, computed by hand rather than with java.time
 * so every SDK accepts and refuses the same strings.
 */
public final class Dates {
    private Dates() {}

    /**
     * parseRfc3339Millis parses YYYY-MM-DD, a T, t or space, hh:mm:ss, an optional fraction
     * (truncated to milliseconds) and a Z, z or ±hh:mm zone, ASCII digits only. Returns null for
     * anything else or an out-of-range field. The offset's minutes are not range-checked on their
     * own; only the whole offset must be under 24 h.
     */
    public static Long parseRfc3339Millis(String s) {
        int n = s.length();
        if (n < 20
                || s.charAt(4) != '-'
                || s.charAt(7) != '-'
                || "Tt ".indexOf(s.charAt(10)) < 0
                || s.charAt(13) != ':'
                || s.charAt(16) != ':') {
            return null;
        }
        int year = digits(s, 0, 4);
        int month = digits(s, 5, 2);
        int day = digits(s, 8, 2);
        int hour = digits(s, 11, 2);
        int minute = digits(s, 14, 2);
        int second = digits(s, 17, 2);
        if (year < 0 || month < 0 || day < 0 || hour < 0 || minute < 0 || second < 0) {
            return null;
        }
        int i = 19;
        int millis = 0;
        if (s.charAt(i) == '.') {
            i++;
            int start = i;
            while (i < n && isDigit(s.charAt(i))) {
                if (i - start < 3) {
                    millis = millis * 10 + (s.charAt(i) - '0');
                }
                i++;
            }
            int count = i - start;
            if (count == 0) {
                return null;
            }
            for (int k = count; k < 3; k++) {
                millis *= 10;
            }
        }
        if (i >= n) {
            return null;
        }
        int offset = 0; // minutes east of UTC
        char z = s.charAt(i);
        if (z == 'Z' || z == 'z') {
            if (i + 1 != n) {
                return null;
            }
        } else if (z == '+' || z == '-') {
            if (i + 6 != n || s.charAt(i + 3) != ':') {
                return null;
            }
            int oh = digits(s, i + 1, 2);
            int om = digits(s, i + 4, 2);
            if (oh < 0 || om < 0) {
                return null;
            }
            offset = oh * 60 + om;
            if (offset >= 24 * 60) {
                return null;
            }
            if (z == '-') {
                offset = -offset;
            }
        } else {
            return null;
        }
        if (year < 1
                || month < 1
                || month > 12
                || day < 1
                || day > daysIn(year, month)
                || hour > 23
                || minute > 59
                || second > 59) {
            return null;
        }
        long secs = daysFromCivil(year, month, day) * 86400L + hour * 3600L + minute * 60L + second - offset * 60L;
        return secs * 1000 + millis;
    }

    /**
     * toMillis is v as integer epoch milliseconds: a number is floored, a string is parsed as RFC
     * 3339. Anything else, and a non-finite number, is not a date (null).
     */
    public static Long toMillis(Object v) {
        if (v instanceof String) {
            return parseRfc3339Millis((String) v);
        }
        if (Values.isInteger(v)) {
            return ((Number) v).longValue();
        }
        if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) {
                return null;
            }
            return (long) Math.floor(d);
        }
        return null;
    }

    /** digits reads count ASCII digits at from, or returns -1. */
    private static int digits(String s, int from, int count) {
        int v = 0;
        for (int i = from; i < from + count; i++) {
            char c = s.charAt(i);
            if (!isDigit(c)) {
                return -1;
            }
            v = v * 10 + (c - '0');
        }
        return v;
    }

    private static boolean isDigit(char c) {
        return c >= '0' && c <= '9';
    }

    /** daysIn is the number of days in month of the proleptic Gregorian year. */
    private static int daysIn(int year, int month) {
        switch (month) {
            case 2:
                return year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) ? 29 : 28;
            case 4:
            case 6:
            case 9:
            case 11:
                return 30;
            default:
                return 31;
        }
    }

    /** daysFromCivil is the days since 1970-01-01 of a proleptic Gregorian date (year >= 1). */
    private static long daysFromCivil(int year, int month, int day) {
        long y = month <= 2 ? year - 1 : year;
        long era = Math.floorDiv(y, 400);
        long yoe = y - era * 400;
        long doy = (153L * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1;
        long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        return era * 146097 + doe - 719468;
    }
}
