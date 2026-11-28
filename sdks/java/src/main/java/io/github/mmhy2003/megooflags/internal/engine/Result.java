package io.github.mmhy2003.megooflags.internal.engine;

import java.util.LinkedHashMap;
import java.util.Map;

/** The resolution of one flag (evaluation.md). */
public final class Result {
    // Reasons.
    public static final String STATIC = "STATIC";
    public static final String DEFAULT = "DEFAULT";
    public static final String TARGETING_MATCH = "TARGETING_MATCH";
    public static final String SPLIT = "SPLIT";
    public static final String DISABLED = "DISABLED";
    public static final String PREREQUISITE_FAILED = "PREREQUISITE_FAILED";
    public static final String ERROR = "ERROR";

    // Error codes of a result with reason ERROR.
    public static final String FLAG_NOT_FOUND = "FLAG_NOT_FOUND";
    public static final String TYPE_MISMATCH = "TYPE_MISMATCH";
    public static final String PARSE_ERROR = "PARSE_ERROR";
    public static final String INVALID_CONTEXT = "INVALID_CONTEXT";
    public static final String GENERAL = "GENERAL";

    /** The served variation's value, or the default when no variation was served. */
    public final Object value;
    /** The served variation, or null when the default was returned. */
    public final String variant;

    public final String reason;
    /** null unless reason is ERROR. */
    public final String errorCode;
    /** Empty unless reason is ERROR. */
    public final String errorMessage;
    /** The matching rule, or null. */
    public final Integer ruleIndex;
    /** The failed prerequisite flag, or null. */
    public final String prerequisite;

    Result(
            Object value,
            String variant,
            String reason,
            String errorCode,
            String errorMessage,
            Integer ruleIndex,
            String prerequisite) {
        this.value = value;
        this.variant = variant;
        this.reason = reason;
        this.errorCode = errorCode;
        this.errorMessage = errorMessage;
        this.ruleIndex = ruleIndex;
        this.prerequisite = prerequisite;
    }

    /**
     * metadata is {"rule_index": n} and {"prerequisite": key} when set, empty otherwise. It is
     * built on each call, so the evaluation itself does not allocate it.
     */
    public Map<String, Object> metadata() {
        Map<String, Object> m = new LinkedHashMap<>();
        if (ruleIndex != null) {
            m.put("rule_index", ruleIndex);
        }
        if (prerequisite != null) {
            m.put("prerequisite", prerequisite);
        }
        return m;
    }
}
