package io.github.mmhy2003.megooflags.internal.engine;

/**
 * JSON values as the engine sees them. An integer (Long, Integer, Short or Byte) keeps its exact
 * value, so two integers compare exactly beyond 2^53; any other pair of numbers compares as
 * doubles. A Boolean is never a number.
 */
public final class Values {
    private Values() {}

    /** isNumber reports whether v is a number. A boolean is never a number. */
    public static boolean isNumber(Object v) {
        return isInteger(v) || v instanceof Double || v instanceof Float;
    }

    /** isScalar reports whether v is a string, a boolean or a number. */
    public static boolean isScalar(Object v) {
        return v instanceof String || v instanceof Boolean || isNumber(v);
    }

    /**
     * jsonEqual is JSON scalar equality: a boolean only equals a boolean, numbers compare
     * numerically, a string equals a string. Anything else, null included, is never equal.
     */
    public static boolean jsonEqual(Object a, Object b) {
        if (a instanceof Boolean || b instanceof Boolean) {
            return a instanceof Boolean && a.equals(b);
        }
        if (isNumber(a)) {
            return isNumber(b) && !isNaN(a) && !isNaN(b) && compareNumbers(a, b) == 0;
        }
        if (a instanceof String) {
            return a.equals(b);
        }
        return false;
    }

    /**
     * isNaN reports whether v is a NaN Double or Float. NaN equals nothing, itself included, and is
     * unordered: every comparison with it is false.
     */
    public static boolean isNaN(Object v) {
        return (v instanceof Double || v instanceof Float) && Double.isNaN(((Number) v).doubleValue());
    }

    /**
     * compareNumbers returns -1, 0 or 1. Both arguments must be numbers (see {@link #isNumber}) and
     * neither may be NaN (see {@link #isNaN}). Two integers compare exactly; otherwise as doubles.
     */
    public static int compareNumbers(Object a, Object b) {
        if (isInteger(a) && isInteger(b)) {
            return Long.compare(((Number) a).longValue(), ((Number) b).longValue());
        }
        double x = ((Number) a).doubleValue();
        double y = ((Number) b).doubleValue();
        return x < y ? -1 : x > y ? 1 : 0;
    }

    static boolean isInteger(Object v) {
        return v instanceof Long || v instanceof Integer || v instanceof Short || v instanceof Byte;
    }
}
