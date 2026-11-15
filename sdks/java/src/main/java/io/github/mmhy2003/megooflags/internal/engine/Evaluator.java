package io.github.mmhy2003.megooflags.internal.engine;

import java.util.List;
import java.util.Map;

/** Resolves flags of a {@link Ruleset} (evaluation.md). Pure: it takes no lock. */
public final class Evaluator {
    /**
     * The deepest prerequisite chain evaluate follows; a deeper chain, or a cycle, is an error
     * (evaluation.md §6).
     */
    public static final int MAX_PREREQUISITE_DEPTH = 32;

    private Evaluator() {}

    /**
     * evaluate resolves flagKey for context, which must be a Map. Errors return defaultValue with
     * reason ERROR. expectedType is "boolean", "string", "number" or "json", or null for no type
     * check; the check applies only to a served variation.
     */
    public static Result evaluate(
            Ruleset ruleset, String flagKey, Object context, Object defaultValue, String expectedType) {
        if (!(context instanceof Map)) {
            return error(defaultValue, Result.INVALID_CONTEXT, "context must be a JSON object");
        }
        Map<?, ?> ctx = (Map<?, ?>) context;
        Ruleset.Flag flag = ruleset.flags.get(flagKey);
        if (flag == null) {
            return error(defaultValue, Result.FLAG_NOT_FOUND, "flag '" + flagKey + "' not found");
        }
        Result res = evalFlag(ruleset, flag, ctx, defaultValue, 0);
        if (res == null) {
            return error(defaultValue, Result.GENERAL, "prerequisite chain is too deep or has a cycle");
        }
        if (expectedType != null && res.variant != null && !hasType(res.value, expectedType)) {
            return error(defaultValue, Result.TYPE_MISMATCH, "flag '" + flagKey + "' is not a " + expectedType);
        }
        return res;
    }

    /** evalFlag returns null when the prerequisite chain is deeper than MAX_PREREQUISITE_DEPTH. */
    private static Result evalFlag(Ruleset rs, Ruleset.Flag flag, Map<?, ?> ctx, Object def, int depth) {
        if (!flag.enabled) {
            return serveOff(flag, Result.DISABLED, def, null);
        }
        for (Ruleset.Prerequisite p : flag.prerequisites) {
            Boolean met = prerequisiteMet(rs, p, ctx, depth + 1);
            if (met == null) {
                return null;
            }
            if (!met) {
                return serveOff(flag, Result.PREREQUISITE_FAILED, def, p.flag);
            }
        }
        Object key = ctx.get("key");
        if (key instanceof String) {
            for (Ruleset.Target t : flag.targets) {
                if (t.values.contains(key)) {
                    return serve(flag, t.variation, Result.TARGETING_MATCH, def, null, null);
                }
            }
        }
        for (int i = 0; i < flag.rules.size(); i++) {
            Ruleset.Rule rule = flag.rules.get(i);
            if (allClausesMatch(rs, rule.clauses, ctx)) {
                return serveRule(flag, rule.serve, ctx, Result.TARGETING_MATCH, def, i);
            }
        }
        String reason = Result.DEFAULT;
        if (flag.fallthrough.rollout != null) {
            reason = Result.SPLIT;
        } else if (flag.targets.isEmpty() && flag.rules.isEmpty() && flag.prerequisites.isEmpty()) {
            reason = Result.STATIC;
        }
        return serveRule(flag, flag.fallthrough, ctx, reason, def, null);
    }

    /**
     * prerequisiteMet reports whether p's flag serves p.variation, or null when depth is over
     * MAX_PREREQUISITE_DEPTH.
     */
    private static Boolean prerequisiteMet(Ruleset rs, Ruleset.Prerequisite p, Map<?, ?> ctx, int depth) {
        if (depth > MAX_PREREQUISITE_DEPTH) {
            return null;
        }
        Ruleset.Flag pf = rs.flags.get(p.flag);
        if (pf == null || !pf.enabled) {
            return false;
        }
        Result res = evalFlag(rs, pf, ctx, null, depth);
        if (res == null) {
            return null;
        }
        return !Result.ERROR.equals(res.reason) && p.variation.equals(res.variant);
    }

    private static boolean allClausesMatch(Ruleset rs, List<Ruleset.Clause> clauses, Map<?, ?> ctx) {
        for (Ruleset.Clause c : clauses) {
            if (!clauseMatches(rs, c, ctx)) {
                return false;
            }
        }
        return true;
    }

    private static boolean clauseMatches(Ruleset rs, Ruleset.Clause c, Map<?, ?> ctx) {
        if (c.op == Ruleset.Operator.IN_SEGMENT) {
            boolean hit = false;
            for (Object k : c.prepared) {
                if (inSegment(rs, rs.segments.get(k), ctx)) {
                    hit = true;
                    break;
                }
            }
            return hit != c.negate;
        }
        Object v = ctx.get(c.attribute);
        if (v == null) { // missing or null: false, negate not applied
            return false;
        }
        boolean hit = false;
        if (v instanceof List) {
            for (Object e : (List<?>) v) {
                if (matchValue(c, e)) {
                    hit = true;
                    break;
                }
            }
        } else {
            hit = matchValue(c, v);
        }
        return hit != c.negate;
    }

    private static boolean inSegment(Ruleset rs, Ruleset.Segment s, Map<?, ?> ctx) {
        if (s == null) {
            return false;
        }
        Object key = ctx.get("key");
        if (key instanceof String) {
            if (s.excluded.contains(key)) {
                return false;
            }
            if (s.included.contains(key)) {
                return true;
            }
        }
        for (List<Ruleset.Clause> clauses : s.rules) {
            if (allClausesMatch(rs, clauses, ctx)) {
                return true;
            }
        }
        return false;
    }

    /**
     * matchValue reports whether one element of the attribute matches the clause's prepared values
     * (operators.py match_value).
     */
    private static boolean matchValue(Ruleset.Clause c, Object e) {
        switch (c.op) {
            case IN:
                for (Object p : c.prepared) {
                    if (Values.jsonEqual(e, p)) {
                        return true;
                    }
                }
                return false;
            case NOT_IN:
                if (!Values.isScalar(e)) {
                    return false;
                }
                for (Object p : c.prepared) {
                    if (Values.jsonEqual(e, p)) {
                        return false;
                    }
                }
                return true;
            case CONTAINS:
            case STARTS_WITH:
            case ENDS_WITH:
                if (!(e instanceof String)) {
                    return false;
                }
                for (Object p : c.prepared) {
                    if (stringMatch(c.op, (String) e, (String) p)) {
                        return true;
                    }
                }
                return false;
            case MATCHES:
                if (!(e instanceof String)) {
                    return false;
                }
                for (Object p : c.prepared) {
                    if (((PortableRegex) p).test((String) e)) {
                        return true;
                    }
                }
                return false;
            case LT:
            case LTE:
            case GT:
            case GTE:
                if (!Values.isNumber(e) || Values.isNaN(e)) {
                    return false;
                }
                for (Object p : c.prepared) {
                    if (Values.isNaN(p)) {
                        continue;
                    }
                    int d = Values.compareNumbers(e, p);
                    if (c.op == Ruleset.Operator.LT
                            ? d < 0
                            : c.op == Ruleset.Operator.LTE ? d <= 0 : c.op == Ruleset.Operator.GT ? d > 0 : d >= 0) {
                        return true;
                    }
                }
                return false;
            case SEMVER_EQ:
            case SEMVER_LT:
            case SEMVER_GT:
                if (!(e instanceof String)) {
                    return false;
                }
                Semver sv = Semver.parse((String) e);
                if (sv == null) {
                    return false;
                }
                int want = c.op == Ruleset.Operator.SEMVER_LT ? -1 : c.op == Ruleset.Operator.SEMVER_GT ? 1 : 0;
                for (Object p : c.prepared) {
                    if (Integer.signum(Semver.compare(sv, (Semver) p)) == want) {
                        return true;
                    }
                }
                return false;
            case BEFORE:
            case AFTER:
                Long t = Dates.toMillis(e);
                if (t == null) {
                    return false;
                }
                for (Object p : c.prepared) {
                    long ms = (Long) p;
                    if (c.op == Ruleset.Operator.BEFORE ? t < ms : t > ms) {
                        return true;
                    }
                }
                return false;
            default:
                return false; // in_segment is handled by clauseMatches
        }
    }

    /**
     * stringMatch is contains, starts_with or ends_with by code point: a match that would split a
     * surrogate pair of s does not count.
     */
    private static boolean stringMatch(Ruleset.Operator op, String s, String p) {
        int n = p.length();
        switch (op) {
            case STARTS_WITH:
                return s.startsWith(p) && !splitsPair(s, n);
            case ENDS_WITH:
                return s.endsWith(p) && !splitsPair(s, s.length() - n);
            default:
                for (int i = s.indexOf(p); i >= 0; i = s.indexOf(p, i + 1)) {
                    if (!splitsPair(s, i) && !splitsPair(s, i + n)) {
                        return true;
                    }
                }
                return false;
        }
    }

    /** splitsPair reports whether index i of s falls between the two halves of a surrogate pair. */
    private static boolean splitsPair(String s, int i) {
        return i > 0
                && i < s.length()
                && Character.isHighSurrogate(s.charAt(i - 1))
                && Character.isLowSurrogate(s.charAt(i));
    }

    /** hasType reports whether a served value has the OpenFeature type asked for. */
    private static boolean hasType(Object v, String expectedType) {
        switch (expectedType) {
            case "boolean":
                return v instanceof Boolean;
            case "string":
                return v instanceof String;
            case "number":
                return Values.isNumber(v); // a Boolean is not a number
            default:
                return true; // json accepts anything, null included
        }
    }

    private static Result serveOff(Ruleset.Flag flag, String reason, Object def, String prerequisite) {
        if (flag.offVariation == null) {
            return new Result(def, null, reason, null, "", null, prerequisite);
        }
        return serve(flag, flag.offVariation, reason, def, null, prerequisite);
    }

    private static Result serve(
            Ruleset.Flag flag, String variation, String reason, Object def, Integer ruleIndex, String prerequisite) {
        Ruleset.Variation v = variation == null ? null : flag.variations.get(variation);
        if (v == null) { // the parser guarantees known variations; this keeps a bad Ruleset from throwing
            return new Result(def, null, reason, null, "", ruleIndex, prerequisite);
        }
        return new Result(v.value, variation, reason, null, "", ruleIndex, prerequisite);
    }

    private static Result serveRule(
            Ruleset.Flag flag, Ruleset.Serve s, Map<?, ?> ctx, String reason, Object def, Integer ruleIndex) {
        if (s.rollout == null) {
            return serve(flag, s.variation, reason, def, ruleIndex, null);
        }
        int bucket = 0;
        String text = Bucketing.bucketText(ctx.get(s.rollout.bucketBy));
        if (text != null) {
            bucket = Bucketing.bucket(flag.key, text);
        }
        return serve(flag, Bucketing.pickVariation(s.rollout.weights, bucket), reason, def, ruleIndex, null);
    }

    private static Result error(Object def, String code, String message) {
        return new Result(def, null, Result.ERROR, code, message, null, null);
    }
}
