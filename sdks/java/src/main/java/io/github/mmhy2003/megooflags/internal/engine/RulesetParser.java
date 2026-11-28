package io.github.mmhy2003.megooflags.internal.engine;

import io.github.mmhy2003.megooflags.internal.json.Json;
import io.github.mmhy2003.megooflags.internal.json.JsonException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * Validates a ruleset as in evaluation.md §11 and prepares its clause values. Unknown fields are
 * ignored. Prerequisite flags and segment keys are not checked: a missing flag fails the
 * prerequisite and a missing segment is never matched.
 */
public final class RulesetParser {
    /** The only ruleset schema_version this engine accepts. */
    public static final long SCHEMA_VERSION = 1;

    private static final List<String> FLAG_TYPES = List.of("boolean", "json", "number", "string");

    private final List<String> warnings = new ArrayList<>();

    private RulesetParser() {}

    /** parse parses JSON text; invalid JSON is a ParseException with path "ruleset". */
    public static Ruleset parse(String json) throws ParseException {
        Object tree;
        try {
            tree = Json.parse(json);
        } catch (JsonException e) {
            throw new ParseException("ruleset", "invalid JSON: " + e.getMessage());
        }
        return parse(tree);
    }

    /**
     * parse validates a decoded tree: Map, List, String, Boolean, null and numbers (an integer is a
     * Long, Integer, Short or Byte; a Double such as 1.0 is not an integer).
     */
    public static Ruleset parse(Object tree) throws ParseException {
        return new RulesetParser().parseRuleset(tree);
    }

    private Ruleset parseRuleset(Object data) throws ParseException {
        Map<?, ?> root = asObject(data, "ruleset");
        long version = asInt(get(root, "schema_version", ""), "schema_version");
        if (version != SCHEMA_VERSION) {
            throw new ParseException("schema_version", "must be " + SCHEMA_VERSION);
        }
        String project = asString(get(root, "project", ""), "project");
        String environment = asString(get(root, "environment", ""), "environment");
        Map<String, Object> flags = sorted(asObject(get(root, "flags", ""), "flags"), "flags");
        Map<String, Object> segments = sorted(asObject(get(root, "segments", ""), "segments"), "segments");
        Map<String, Ruleset.Flag> parsedFlags = new HashMap<>(flags.size() * 2);
        for (Map.Entry<String, Object> e : flags.entrySet()) {
            parsedFlags.put(e.getKey(), parseFlag(e.getKey(), e.getValue(), "flags." + e.getKey()));
        }
        Map<String, Ruleset.Segment> parsedSegments = new HashMap<>(segments.size() * 2);
        for (Map.Entry<String, Object> e : segments.entrySet()) {
            parsedSegments.put(e.getKey(), parseSegment(e.getValue(), "segments." + e.getKey()));
        }
        return new Ruleset(
                project,
                environment,
                Collections.unmodifiableMap(parsedFlags),
                Collections.unmodifiableMap(parsedSegments),
                Collections.unmodifiableList(new ArrayList<>(warnings)));
    }

    private Ruleset.Flag parseFlag(String key, Object value, String path) throws ParseException {
        Map<?, ?> obj = asObject(value, path);
        if (!key.equals(get(obj, "key", path))) {
            throw new ParseException(path + ".key", "must equal '" + key + "'");
        }
        String type = asString(get(obj, "type", path), path + ".type");
        if (!FLAG_TYPES.contains(type)) {
            throw new ParseException(path + ".type", "must be one of " + String.join(", ", FLAG_TYPES));
        }
        Map<String, Ruleset.Variation> variations = parseVariations(get(obj, "variations", path), path + ".variations");
        Object off = get(obj, "off_variation", path);
        String offVariation = off == null ? null : known(off, variations, path + ".off_variation");
        List<?> prerequisites = asList(get(obj, "prerequisites", path), path + ".prerequisites", false);
        List<?> targets = asList(get(obj, "targets", path), path + ".targets", false);
        List<?> rules = asList(get(obj, "rules", path), path + ".rules", false);
        boolean enabled = asBool(get(obj, "enabled", path), path + ".enabled");
        long version = asInt(get(obj, "version", path), path + ".version");

        List<Ruleset.Prerequisite> parsedPrerequisites = new ArrayList<>(prerequisites.size());
        for (int i = 0; i < prerequisites.size(); i++) {
            String itemPath = path + ".prerequisites[" + i + "]";
            Map<?, ?> p = asObject(prerequisites.get(i), itemPath);
            parsedPrerequisites.add(new Ruleset.Prerequisite(
                    asString(get(p, "flag", itemPath), itemPath + ".flag"),
                    asString(get(p, "variation", itemPath), itemPath + ".variation")));
        }
        List<Ruleset.Target> parsedTargets = new ArrayList<>(targets.size());
        for (int i = 0; i < targets.size(); i++) {
            String itemPath = path + ".targets[" + i + "]";
            Map<?, ?> t = asObject(targets.get(i), itemPath);
            parsedTargets.add(new Ruleset.Target(
                    known(get(t, "variation", itemPath), variations, itemPath + ".variation"),
                    stringSet(get(t, "values", itemPath), itemPath + ".values")));
        }
        List<Ruleset.Rule> parsedRules = new ArrayList<>(rules.size());
        for (int i = 0; i < rules.size(); i++) {
            parsedRules.add(parseRule(rules.get(i), variations, path + ".rules[" + i + "]"));
        }
        Ruleset.Serve fallthrough = parseServe(get(obj, "fallthrough", path), variations, path + ".fallthrough");
        return new Ruleset.Flag(
                key,
                type,
                enabled,
                version,
                variations,
                offVariation,
                Collections.unmodifiableList(parsedPrerequisites),
                Collections.unmodifiableList(parsedTargets),
                Collections.unmodifiableList(parsedRules),
                fallthrough);
    }

    private static Map<String, Ruleset.Variation> parseVariations(Object value, String path) throws ParseException {
        List<?> items = asList(value, path, true);
        Map<String, Ruleset.Variation> variations = new LinkedHashMap<>(items.size() * 2);
        for (int i = 0; i < items.size(); i++) {
            String itemPath = path + "[" + i + "]";
            Map<?, ?> obj = asObject(items.get(i), itemPath);
            String key = asString(get(obj, "key", itemPath), itemPath + ".key");
            if (variations.containsKey(key)) {
                throw new ParseException(itemPath + ".key", "duplicate variation '" + key + "'");
            }
            Object raw = get(obj, "value", itemPath);
            Long integral = integral(raw);
            variations.put(
                    key, new Ruleset.Variation(key, freeze(raw), integral != null, integral == null ? 0 : integral));
        }
        return Collections.unmodifiableMap(variations);
    }

    /** freeze is a deep copy of a JSON value with unmodifiable maps and lists. */
    private static Object freeze(Object v) {
        if (v instanceof Map) {
            Map<Object, Object> out = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                out.put(e.getKey(), freeze(e.getValue()));
            }
            return Collections.unmodifiableMap(out);
        }
        if (v instanceof List) {
            List<Object> out = new ArrayList<>(((List<?>) v).size());
            for (Object item : (List<?>) v) {
                out.add(freeze(item));
            }
            return Collections.unmodifiableList(out);
        }
        return v;
    }

    /**
     * integral is the exact long of a number with an integral value: an integer, or a Double with
     * an integral value in the long range. Anything else is null.
     */
    private static Long integral(Object raw) {
        if (Values.isInteger(raw)) {
            return ((Number) raw).longValue();
        }
        if (raw instanceof Double || raw instanceof Float) {
            double d = ((Number) raw).doubleValue();
            // -2^63 and 2^63 are exact doubles; NaN and the infinities fail the first test.
            if (d == Math.rint(d) && d >= -0x1p63 && d < 0x1p63) {
                return (long) d;
            }
        }
        return null;
    }

    private Ruleset.Rule parseRule(Object value, Map<String, Ruleset.Variation> variations, String path)
            throws ParseException {
        Map<?, ?> obj = asObject(value, path);
        String description = "";
        if (obj.containsKey("description")) {
            description = asString(obj.get("description"), path + ".description");
        }
        List<Ruleset.Clause> clauses = parseClauses(get(obj, "clauses", path), path + ".clauses", false);
        return new Ruleset.Rule(description, clauses, parseServe(get(obj, "serve", path), variations, path + ".serve"));
    }

    private static Ruleset.Serve parseServe(Object value, Map<String, Ruleset.Variation> variations, String path)
            throws ParseException {
        Map<?, ?> obj = asObject(value, path);
        boolean hasVariation = obj.containsKey("variation");
        if (hasVariation == obj.containsKey("rollout")) {
            throw new ParseException(path, "must have exactly one of variation or rollout");
        }
        if (hasVariation) {
            return new Ruleset.Serve(known(obj.get("variation"), variations, path + ".variation"), null);
        }
        return new Ruleset.Serve(null, parseRollout(obj.get("rollout"), variations, path + ".rollout"));
    }

    private static Ruleset.Rollout parseRollout(Object value, Map<String, Ruleset.Variation> variations, String path)
            throws ParseException {
        Map<?, ?> obj = asObject(value, path);
        String bucketBy = asString(get(obj, "bucket_by", path), path + ".bucket_by");
        String weightsPath = path + ".weights";
        List<?> items = asList(get(obj, "weights", path), weightsPath, true);
        List<Bucketing.Weight> weights = new ArrayList<>(items.size());
        long sum = 0;
        for (int i = 0; i < items.size(); i++) {
            String itemPath = weightsPath + "[" + i + "]";
            Map<?, ?> w = asObject(items.get(i), itemPath);
            String variation = known(get(w, "variation", itemPath), variations, itemPath + ".variation");
            long weight = asInt(get(w, "weight", itemPath), itemPath + ".weight");
            if (weight < 0 || weight > Bucketing.BUCKETS) {
                throw new ParseException(itemPath + ".weight", "must be between 0 and " + Bucketing.BUCKETS);
            }
            weights.add(new Bucketing.Weight(variation, (int) weight));
            sum += weight;
        }
        if (sum != Bucketing.BUCKETS) {
            throw new ParseException(weightsPath, "must add up to " + Bucketing.BUCKETS);
        }
        return new Ruleset.Rollout(bucketBy, Collections.unmodifiableList(weights));
    }

    private List<Ruleset.Clause> parseClauses(Object value, String path, boolean inSegment) throws ParseException {
        List<?> items = asList(value, path, true);
        List<Ruleset.Clause> clauses = new ArrayList<>(items.size());
        for (int i = 0; i < items.size(); i++) {
            clauses.add(parseClause(items.get(i), path + "[" + i + "]", inSegment));
        }
        return Collections.unmodifiableList(clauses);
    }

    private Ruleset.Clause parseClause(Object value, String path, boolean inSegment) throws ParseException {
        Map<?, ?> obj = asObject(value, path);
        String attribute = asString(get(obj, "attribute", path), path + ".attribute");
        String operator = asString(get(obj, "operator", path), path + ".operator");
        Ruleset.Operator op = Ruleset.Operator.byName(operator);
        if (op == null) {
            throw new ParseException(path + ".operator", "unknown operator " + Json.write(operator));
        }
        if (inSegment && op == Ruleset.Operator.IN_SEGMENT) {
            throw new ParseException(path + ".operator", "in_segment is not allowed in a segment rule");
        }
        String valuesPath = path + ".values";
        List<?> raw = asList(get(obj, "values", path), valuesPath, true);
        for (int i = 0; i < raw.size(); i++) {
            if (!Values.isScalar(raw.get(i))) {
                throw new ParseException(valuesPath + "[" + i + "]", "must be a string, number or boolean");
            }
        }
        boolean negate = false;
        if (obj.containsKey("negate")) {
            negate = asBool(obj.get("negate"), path + ".negate");
        }
        List<Object> values = Collections.unmodifiableList(new ArrayList<>(raw));
        return new Ruleset.Clause(attribute, op, negate, values, prepare(op, values, valuesPath));
    }

    /** prepare keeps the values op can use, converted for it, and leaves out the others. */
    private List<?> prepare(Ruleset.Operator op, List<Object> values, String valuesPath) {
        List<Object> out = new ArrayList<>(values.size());
        for (int i = 0; i < values.size(); i++) {
            Object v = values.get(i);
            switch (op) {
                case IN:
                case NOT_IN:
                    out.add(v);
                    break;
                case CONTAINS:
                case STARTS_WITH:
                case ENDS_WITH:
                case IN_SEGMENT:
                    if (v instanceof String) {
                        out.add(v);
                    }
                    break;
                case MATCHES:
                    if (v instanceof String) {
                        try {
                            out.add(PortableRegex.compile((String) v));
                        } catch (RegexSyntaxException e) {
                            warnings.add(valuesPath + "[" + i + "]: regex does not compile: " + e.getMessage());
                        }
                    }
                    break;
                case LT:
                case LTE:
                case GT:
                case GTE:
                    if (Values.isNumber(v)) {
                        out.add(v);
                    }
                    break;
                case SEMVER_EQ:
                case SEMVER_LT:
                case SEMVER_GT:
                    if (v instanceof String) {
                        Semver sv = Semver.parse((String) v);
                        if (sv != null) {
                            out.add(sv);
                        }
                    }
                    break;
                case BEFORE:
                case AFTER:
                    // Only a string is a date here; a number value never matches.
                    if (v instanceof String) {
                        Long ms = Dates.parseRfc3339Millis((String) v);
                        if (ms != null) {
                            out.add(ms);
                        }
                    }
                    break;
                default:
                    throw new IllegalStateException("operator " + op);
            }
        }
        return Collections.unmodifiableList(out);
    }

    private Ruleset.Segment parseSegment(Object value, String path) throws ParseException {
        Map<?, ?> obj = asObject(value, path);
        String rulesPath = path + ".rules";
        List<?> rules = asList(get(obj, "rules", path), rulesPath, false);
        Set<String> included = stringSet(get(obj, "included", path), path + ".included");
        Set<String> excluded = stringSet(get(obj, "excluded", path), path + ".excluded");
        List<List<Ruleset.Clause>> parsedRules = new ArrayList<>(rules.size());
        for (int i = 0; i < rules.size(); i++) {
            String rulePath = rulesPath + "[" + i + "]";
            Map<?, ?> rule = asObject(rules.get(i), rulePath);
            parsedRules.add(parseClauses(get(rule, "clauses", rulePath), rulePath + ".clauses", true));
        }
        return new Ruleset.Segment(included, excluded, Collections.unmodifiableList(parsedRules));
    }

    /** get returns obj[name], failing with "<path>.<name>: is required" when absent. */
    private static Object get(Map<?, ?> obj, String name, String path) throws ParseException {
        if (!obj.containsKey(name)) {
            throw new ParseException(path.isEmpty() ? name : path + "." + name, "is required");
        }
        return obj.get(name);
    }

    private static Map<?, ?> asObject(Object v, String path) throws ParseException {
        if (!(v instanceof Map)) {
            throw new ParseException(path, "must be an object");
        }
        return (Map<?, ?>) v;
    }

    /** sorted is obj by key in sorted order, so the first problem reported is deterministic. */
    private static Map<String, Object> sorted(Map<?, ?> obj, String path) throws ParseException {
        Map<String, Object> out = new TreeMap<>();
        for (Map.Entry<?, ?> e : obj.entrySet()) {
            if (!(e.getKey() instanceof String)) {
                throw new ParseException(path, "must be an object with string keys");
            }
            out.put((String) e.getKey(), e.getValue());
        }
        return out;
    }

    private static List<?> asList(Object v, String path, boolean nonEmpty) throws ParseException {
        if (!(v instanceof List)) {
            throw new ParseException(path, "must be an array");
        }
        List<?> list = (List<?>) v;
        if (nonEmpty && list.isEmpty()) {
            throw new ParseException(path, "must not be empty");
        }
        return list;
    }

    private static String asString(Object v, String path) throws ParseException {
        if (!(v instanceof String)) {
            throw new ParseException(path, "must be a string");
        }
        return (String) v;
    }

    private static boolean asBool(Object v, String path) throws ParseException {
        if (!(v instanceof Boolean)) {
            throw new ParseException(path, "must be a boolean");
        }
        return (Boolean) v;
    }

    /** asInt requires an integer: a Double such as 1.0 is not one, nor is a Boolean. */
    private static long asInt(Object v, String path) throws ParseException {
        if (!Values.isInteger(v)) {
            throw new ParseException(path, "must be an integer");
        }
        return ((Number) v).longValue();
    }

    private static Set<String> stringSet(Object v, String path) throws ParseException {
        List<?> items = asList(v, path, false);
        Set<String> set = new HashSet<>(items.size() * 2);
        for (int i = 0; i < items.size(); i++) {
            set.add(asString(items.get(i), path + "[" + i + "]"));
        }
        return Collections.unmodifiableSet(set);
    }

    private static String known(Object v, Map<String, Ruleset.Variation> variations, String path)
            throws ParseException {
        String key = asString(v, path);
        if (!variations.containsKey(key)) {
            throw new ParseException(path, "unknown variation '" + key + "'");
        }
        return key;
    }
}
