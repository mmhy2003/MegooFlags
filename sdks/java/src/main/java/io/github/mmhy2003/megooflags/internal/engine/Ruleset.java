package io.github.mmhy2003.megooflags.internal.engine;

import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * A parsed, validated environment ruleset (spec/ruleset.schema.json). Every model object is
 * immutable once {@link RulesetParser} returns it, so a Ruleset is safe for concurrent use.
 */
public final class Ruleset {
    public final String project;
    public final String environment;
    public final Map<String, Flag> flags;
    public final Map<String, Segment> segments;
    /** Problems that do not make the ruleset invalid, such as a pattern that does not compile. */
    public final List<String> warnings;

    Ruleset(
            String project,
            String environment,
            Map<String, Flag> flags,
            Map<String, Segment> segments,
            List<String> warnings) {
        this.project = project;
        this.environment = environment;
        this.flags = flags;
        this.segments = segments;
        this.warnings = warnings;
    }

    /** One flag of a ruleset. */
    public static final class Flag {
        public final String key;
        public final String type;
        public final boolean enabled;
        public final long version;
        /** By key, in the order of the ruleset. */
        public final Map<String, Variation> variations;
        /** null when off_variation is null. */
        public final String offVariation;

        public final List<Prerequisite> prerequisites;
        public final List<Target> targets;
        public final List<Rule> rules;
        public final Serve fallthrough;

        Flag(
                String key,
                String type,
                boolean enabled,
                long version,
                Map<String, Variation> variations,
                String offVariation,
                List<Prerequisite> prerequisites,
                List<Target> targets,
                List<Rule> rules,
                Serve fallthrough) {
            this.key = key;
            this.type = type;
            this.enabled = enabled;
            this.version = version;
            this.variations = variations;
            this.offVariation = offVariation;
            this.prerequisites = prerequisites;
            this.targets = targets;
            this.rules = rules;
            this.fallthrough = fallthrough;
        }
    }

    /**
     * One possible value of a flag. value is plain JSON; isInt and intValue keep the exact value of
     * a number with an integral value that fits a long.
     */
    public static final class Variation {
        public final String key;
        public final Object value;
        public final boolean isInt;
        public final long intValue;

        Variation(String key, Object value, boolean isInt, long intValue) {
            this.key = key;
            this.value = value;
            this.isInt = isInt;
            this.intValue = intValue;
        }
    }

    /** Requires flag {@code flag} to serve variation {@code variation}. */
    public static final class Prerequisite {
        public final String flag;
        public final String variation;

        Prerequisite(String flag, String variation) {
            this.flag = flag;
            this.variation = variation;
        }
    }

    /** Serves variation to the context keys in values. */
    public static final class Target {
        public final String variation;
        public final Set<String> values;

        Target(String variation, Set<String> values) {
            this.variation = variation;
            this.values = values;
        }
    }

    /** Serves serve when all of its clauses match. */
    public static final class Rule {
        public final String description;
        public final List<Clause> clauses;
        public final Serve serve;

        Rule(String description, List<Clause> clauses, Serve serve) {
            this.description = description;
            this.clauses = clauses;
            this.serve = serve;
        }
    }

    /** Either a fixed variation or a rollout: exactly one is not null. */
    public static final class Serve {
        public final String variation;
        public final Rollout rollout;

        Serve(String variation, Rollout rollout) {
            this.variation = variation;
            this.rollout = rollout;
        }
    }

    /** Splits contexts by the bucket of attribute bucketBy. */
    public static final class Rollout {
        public final String bucketBy;
        public final List<Bucketing.Weight> weights;

        Rollout(String bucketBy, List<Bucketing.Weight> weights) {
            this.bucketBy = bucketBy;
            this.weights = weights;
        }
    }

    /**
     * Matches a context that is included, or that is not excluded and matches any rule (each rule
     * is a list of clauses that must all match).
     */
    public static final class Segment {
        public final Set<String> included;
        public final Set<String> excluded;
        public final List<List<Clause>> rules;

        Segment(Set<String> included, Set<String> excluded, List<List<Clause>> rules) {
            this.included = included;
            this.excluded = excluded;
            this.rules = rules;
        }
    }

    /** The clause operators, by their name in the ruleset. */
    enum Operator {
        IN("in"),
        NOT_IN("not_in"),
        CONTAINS("contains"),
        STARTS_WITH("starts_with"),
        ENDS_WITH("ends_with"),
        MATCHES("matches"),
        LT("lt"),
        LTE("lte"),
        GT("gt"),
        GTE("gte"),
        SEMVER_EQ("semver_eq"),
        SEMVER_LT("semver_lt"),
        SEMVER_GT("semver_gt"),
        BEFORE("before"),
        AFTER("after"),
        IN_SEGMENT("in_segment");

        final String wireName;

        Operator(String wireName) {
            this.wireName = wireName;
        }

        /** byName returns the operator called name, or null. */
        static Operator byName(String name) {
            for (Operator op : values()) {
                if (op.wireName.equals(name)) {
                    return op;
                }
            }
            return null;
        }
    }

    /** Tests one context attribute with an operator. */
    public static final class Clause {
        public final String attribute;
        public final String operator;
        public final boolean negate;
        /** The scalars of the ruleset: String, Boolean, Long or Double. */
        public final List<Object> values;
        /**
         * The values the operator can use, the others left out (evaluation.md §11): every value for
         * in and not_in; String for contains, starts_with, ends_with and in_segment; PortableRegex
         * for matches; Long or Double for lt, lte, gt and gte; Semver for semver_*; Long epoch
         * milliseconds for before and after.
         */
        public final List<?> prepared;

        final Operator op;

        Clause(String attribute, Operator op, boolean negate, List<Object> values, List<?> prepared) {
            this.attribute = attribute;
            this.operator = op.wireName;
            this.op = op;
            this.negate = negate;
            this.values = values;
            this.prepared = prepared;
        }
    }
}
