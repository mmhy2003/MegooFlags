package io.github.mmhy2003.megooflags.internal.engine;

import static io.github.mmhy2003.megooflags.RulesetGen.cl;
import static io.github.mmhy2003.megooflags.RulesetGen.flagJson;
import static io.github.mmhy2003.megooflags.RulesetGen.list;
import static io.github.mmhy2003.megooflags.RulesetGen.obj;
import static io.github.mmhy2003.megooflags.RulesetGen.rulesetJson;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.DynamicTest.dynamicTest;

import io.github.mmhy2003.megooflags.internal.json.Json;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Consumer;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

class RulesetParserTest {
    /** base is a valid ruleset: flag f with one rule, rollout fallthrough, segment s. */
    private static Map<String, Object> base() {
        return rulesetJson(
                List.of(flagJson(
                        "f",
                        "rules",
                        list(obj(
                                "description",
                                "r",
                                "clauses",
                                list(cl("country", "in", false, "NL")),
                                "serve",
                                obj("variation", "on"))),
                        "fallthrough",
                        obj(
                                "rollout",
                                obj(
                                        "bucket_by",
                                        "key",
                                        "weights",
                                        list(
                                                obj("variation", "on", "weight", 50000L),
                                                obj("variation", "off", "weight", 50000L)))))),
                obj(
                        "s",
                        obj(
                                "included",
                                list("a"),
                                "excluded",
                                list("b"),
                                "rules",
                                list(obj("clauses", list(cl("org", "in", false, "acme")))))));
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> m(Object v) {
        return (Map<String, Object>) v;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> l(Object v) {
        return (List<Object>) v;
    }

    private static Map<String, Object> flag(Map<String, Object> r) {
        return m(m(r.get("flags")).get("f"));
    }

    private static Map<String, Object> rule(Map<String, Object> r) {
        return m(l(flag(r).get("rules")).get(0));
    }

    private static Map<String, Object> clause(Map<String, Object> r) {
        return m(l(rule(r).get("clauses")).get(0));
    }

    private static Map<String, Object> rollout(Map<String, Object> r) {
        return m(m(flag(r).get("fallthrough")).get("rollout"));
    }

    private static Map<String, Object> segment(Map<String, Object> r) {
        return m(m(r.get("segments")).get("s"));
    }

    private static Ruleset.Clause firstClause(Map<String, Object> root) throws ParseException {
        return RulesetParser.parse(root).flags.get("f").rules.get(0).clauses.get(0);
    }

    @Test
    void parsesAValidRulesetFromTextAndFromATree() throws ParseException {
        for (Ruleset rs : List.of(RulesetParser.parse(Json.write(base())), RulesetParser.parse(base()))) {
            assertEquals("p", rs.project);
            assertEquals("e", rs.environment);
            assertEquals(List.of(), rs.warnings);
            Ruleset.Flag f = rs.flags.get("f");
            assertEquals("f", f.key);
            assertEquals("string", f.type);
            assertTrue(f.enabled);
            assertEquals(1L, f.version);
            assertEquals(List.of("on", "off"), new ArrayList<>(f.variations.keySet()));
            assertEquals("on", f.variations.get("on").value);
            assertEquals("off", f.offVariation);
            assertEquals(List.of(), f.prerequisites);
            assertEquals(List.of(), f.targets);
            assertEquals(1, f.rules.size());
            assertEquals("r", f.rules.get(0).description);
            assertEquals("on", f.rules.get(0).serve.variation);
            assertNull(f.rules.get(0).serve.rollout);
            assertNull(f.fallthrough.variation);
            assertEquals("key", f.fallthrough.rollout.bucketBy);
            assertEquals(2, f.fallthrough.rollout.weights.size());
            assertEquals("off", f.fallthrough.rollout.weights.get(1).variation);
            assertEquals(50000, f.fallthrough.rollout.weights.get(1).weight);
            Ruleset.Segment s = rs.segments.get("s");
            assertEquals(Set.of("a"), s.included);
            assertEquals(Set.of("b"), s.excluded);
            assertEquals(1, s.rules.size());
            assertEquals("org", s.rules.get(0).get(0).attribute);
        }
    }

    @Test
    void parsesPrerequisitesTargetsAndANullOffVariation() throws ParseException {
        Map<String, Object> root = base();
        flag(root).put("off_variation", null);
        flag(root).put("prerequisites", list(obj("flag", "g", "variation", "on")));
        flag(root).put("targets", list(obj("variation", "off", "values", list("u1", "u2"))));
        Ruleset.Flag f = RulesetParser.parse(root).flags.get("f");
        assertNull(f.offVariation);
        assertEquals(1, f.prerequisites.size());
        assertEquals("g", f.prerequisites.get(0).flag);
        assertEquals("on", f.prerequisites.get(0).variation);
        assertEquals(1, f.targets.size());
        assertEquals("off", f.targets.get(0).variation);
        assertEquals(Set.of("u1", "u2"), f.targets.get(0).values);
    }

    @Test
    void keepsVariationValuesAndTheirIntegralForm() throws ParseException {
        Map<String, Object> root = base();
        flag(root)
                .put(
                        "variations",
                        list(
                                obj("key", "on", "value", null),
                                obj("key", "off", "value", obj("a", list(1L))),
                                obj("key", "i", "value", 9007199254740993L),
                                obj("key", "d", "value", 3.0),
                                obj("key", "h", "value", 2.5),
                                obj("key", "b", "value", true)));
        Ruleset.Flag f = RulesetParser.parse(root).flags.get("f");
        assertNull(f.variations.get("on").value);
        assertEquals(Map.of("a", List.of(1L)), f.variations.get("off").value);
        assertTrue(f.variations.get("i").isInt);
        assertEquals(9007199254740993L, f.variations.get("i").intValue);
        assertTrue(f.variations.get("d").isInt);
        assertEquals(3L, f.variations.get("d").intValue);
        assertFalse(f.variations.get("h").isInt);
        assertFalse(f.variations.get("b").isInt);
        assertFalse(f.variations.get("on").isInt);
    }

    @Test
    void defaultsTheRuleDescriptionToEmpty() throws ParseException {
        Map<String, Object> root = base();
        rule(root).remove("description");
        assertEquals("", RulesetParser.parse(root).flags.get("f").rules.get(0).description);
    }

    @Test
    void ignoresUnknownFields() throws ParseException {
        Map<String, Object> root = base();
        root.put("extra", obj("x", 1L));
        flag(root).put("extra", true);
        clause(root).put("extra", list());
        segment(root).put("extra", null);
        assertTrue(RulesetParser.parse(root).flags.containsKey("f"));
    }

    @Test
    void defaultsNegateToFalseAndReadsItWhenPresent() throws ParseException {
        Map<String, Object> root = base();
        clause(root).remove("negate");
        assertFalse(firstClause(root).negate);
        clause(root).put("negate", true);
        assertTrue(firstClause(root).negate);
    }

    @Test
    void returnsImmutableModelObjects() throws ParseException {
        Ruleset rs = RulesetParser.parse(base());
        assertThrows(UnsupportedOperationException.class, () -> rs.flags.remove("f"));
        assertThrows(UnsupportedOperationException.class, () -> rs.segments.clear());
        assertThrows(UnsupportedOperationException.class, () -> rs.warnings.add("x"));
        Ruleset.Flag f = rs.flags.get("f");
        assertThrows(UnsupportedOperationException.class, () -> f.variations.clear());
        assertThrows(UnsupportedOperationException.class, () -> f.rules.clear());
        assertThrows(
                UnsupportedOperationException.class,
                () -> f.rules.get(0).clauses.clear());
        assertThrows(
                UnsupportedOperationException.class,
                () -> rs.segments.get("s").included.clear());
    }

    private static DynamicTest errorCase(String name, String text, String path) {
        return dynamicTest(name + " fails at " + path, () -> assertParseError(RulesetParser::parse, text, path));
    }

    private static DynamicTest errorCase(String name, Consumer<Map<String, Object>> mutate, String path) {
        return dynamicTest(name + " fails at " + path, () -> {
            Map<String, Object> root = base();
            mutate.accept(root);
            assertParseError(RulesetParser::parse, Json.write(root), path);
            assertParseError(RulesetParser::parse, root, path);
        });
    }

    private interface Parser<T> {
        Ruleset parse(T input) throws ParseException;
    }

    private static <T> void assertParseError(Parser<T> parser, T input, String path) {
        ParseException e = assertThrows(ParseException.class, () -> parser.parse(input));
        assertEquals(path, e.path());
        assertFalse(e.reason().isEmpty());
        assertEquals(path + ": " + e.reason(), e.getMessage());
    }

    @TestFactory
    List<DynamicTest> errors() {
        return List.of(
                errorCase("invalid JSON", "{\"schema_version\": 1,", "ruleset"),
                errorCase("trailing data", "{} {}", "ruleset"),
                errorCase("non-object root", "[]", "ruleset"),
                errorCase("null root", "null", "ruleset"),
                errorCase(
                        "schema_version 1.0",
                        "{\"schema_version\": 1.0, \"project\": \"p\", \"environment\": \"e\","
                                + " \"flags\": {}, \"segments\": {}}",
                        "schema_version"),
                errorCase("schema_version 2", r -> r.put("schema_version", 2L), "schema_version"),
                errorCase("schema_version true", r -> r.put("schema_version", true), "schema_version"),
                errorCase("schema_version string", r -> r.put("schema_version", "1"), "schema_version"),
                errorCase("missing schema_version", r -> r.remove("schema_version"), "schema_version"),
                errorCase("missing project", r -> r.remove("project"), "project"),
                errorCase("environment not a string", r -> r.put("environment", 1L), "environment"),
                errorCase("flags not an object", r -> r.put("flags", list()), "flags"),
                errorCase("flags null", r -> r.put("flags", null), "flags"),
                errorCase("segments missing", r -> r.remove("segments"), "segments"),
                errorCase("flag not an object", r -> m(r.get("flags")).put("f", 1L), "flags.f"),
                errorCase("flag key mismatch", r -> flag(r).put("key", "g"), "flags.f.key"),
                errorCase("missing flag key", r -> flag(r).remove("key"), "flags.f.key"),
                errorCase("bad type", r -> flag(r).put("type", "int"), "flags.f.type"),
                errorCase("enabled yes", r -> flag(r).put("enabled", "yes"), "flags.f.enabled"),
                errorCase("version 1.5", r -> flag(r).put("version", 1.5), "flags.f.version"),
                errorCase("version 1.0", r -> flag(r).put("version", 1.0), "flags.f.version"),
                errorCase("version true", r -> flag(r).put("version", true), "flags.f.version"),
                errorCase("version out of int64", r -> flag(r).put("version", 9.3e18), "flags.f.version"),
                errorCase("empty variations", r -> flag(r).put("variations", list()), "flags.f.variations"),
                errorCase("variations not a list", r -> flag(r).put("variations", obj()), "flags.f.variations"),
                errorCase("variation not an object", r -> flag(r).put("variations", list(1L)), "flags.f.variations[0]"),
                errorCase(
                        "variation without value",
                        r -> flag(r).put("variations", list(obj("key", "on"))),
                        "flags.f.variations[0].value"),
                errorCase(
                        "variation key not a string",
                        r -> flag(r).put("variations", list(obj("key", 1L, "value", true))),
                        "flags.f.variations[0].key"),
                errorCase(
                        "duplicate variation",
                        r -> flag(r).put(
                                        "variations",
                                        list(obj("key", "on", "value", true), obj("key", "on", "value", false))),
                        "flags.f.variations[1].key"),
                errorCase("unknown off_variation", r -> flag(r).put("off_variation", "nope"), "flags.f.off_variation"),
                errorCase("missing off_variation", r -> flag(r).remove("off_variation"), "flags.f.off_variation"),
                errorCase("missing prerequisites", r -> flag(r).remove("prerequisites"), "flags.f.prerequisites"),
                errorCase(
                        "prerequisite not an object",
                        r -> flag(r).put("prerequisites", list("g")),
                        "flags.f.prerequisites[0]"),
                errorCase(
                        "prerequisite flag not a string",
                        r -> flag(r).put("prerequisites", list(obj("flag", 1L, "variation", "on"))),
                        "flags.f.prerequisites[0].flag"),
                errorCase(
                        "prerequisite variation missing",
                        r -> flag(r).put("prerequisites", list(obj("flag", "g"))),
                        "flags.f.prerequisites[0].variation"),
                errorCase("targets not a list", r -> flag(r).put("targets", obj()), "flags.f.targets"),
                errorCase(
                        "unknown target variation",
                        r -> flag(r).put("targets", list(obj("variation", "nope", "values", list("a")))),
                        "flags.f.targets[0].variation"),
                errorCase(
                        "target value not a string",
                        r -> flag(r).put("targets", list(obj("variation", "on", "values", list("a", 2L)))),
                        "flags.f.targets[0].values[1]"),
                errorCase("missing rules", r -> flag(r).remove("rules"), "flags.f.rules"),
                errorCase("rule not an object", r -> flag(r).put("rules", list(1L)), "flags.f.rules[0]"),
                errorCase("missing fallthrough", r -> flag(r).remove("fallthrough"), "flags.f.fallthrough"),
                errorCase(
                        "description not a string",
                        r -> rule(r).put("description", null),
                        "flags.f.rules[0].description"),
                errorCase(
                        "serve with both",
                        r -> flag(r).put("fallthrough", obj("variation", "on", "rollout", obj())),
                        "flags.f.fallthrough"),
                errorCase("serve with neither", r -> rule(r).put("serve", obj()), "flags.f.rules[0].serve"),
                errorCase("serve not an object", r -> rule(r).put("serve", "on"), "flags.f.rules[0].serve"),
                errorCase(
                        "unknown serve variation",
                        r -> rule(r).put("serve", obj("variation", "nope")),
                        "flags.f.rules[0].serve.variation"),
                errorCase(
                        "rollout not an object",
                        r -> flag(r).put("fallthrough", obj("rollout", list())),
                        "flags.f.fallthrough.rollout"),
                errorCase(
                        "bucket_by not a string",
                        r -> rollout(r).put("bucket_by", null),
                        "flags.f.fallthrough.rollout.bucket_by"),
                errorCase(
                        "empty weights", r -> rollout(r).put("weights", list()), "flags.f.fallthrough.rollout.weights"),
                errorCase(
                        "weights summing to 99999",
                        r -> rollout(r)
                                .put(
                                        "weights",
                                        list(
                                                obj("variation", "on", "weight", 50000L),
                                                obj("variation", "off", "weight", 49999L))),
                        "flags.f.fallthrough.rollout.weights"),
                errorCase(
                        "weight of 100001",
                        r -> rollout(r).put("weights", list(obj("variation", "on", "weight", 100001L))),
                        "flags.f.fallthrough.rollout.weights[0].weight"),
                errorCase(
                        "negative weight",
                        r -> rollout(r).put("weights", list(obj("variation", "on", "weight", -1L))),
                        "flags.f.fallthrough.rollout.weights[0].weight"),
                errorCase(
                        "fractional weight",
                        r -> rollout(r).put("weights", list(obj("variation", "on", "weight", 99999.5))),
                        "flags.f.fallthrough.rollout.weights[0].weight"),
                errorCase(
                        "weight 100000.0",
                        r -> rollout(r).put("weights", list(obj("variation", "on", "weight", 100000.0))),
                        "flags.f.fallthrough.rollout.weights[0].weight"),
                errorCase(
                        "boolean weight",
                        r -> rollout(r).put("weights", list(obj("variation", "on", "weight", true))),
                        "flags.f.fallthrough.rollout.weights[0].weight"),
                errorCase(
                        "unknown weight variation",
                        r -> rollout(r).put("weights", list(obj("variation", "nope", "weight", 100000L))),
                        "flags.f.fallthrough.rollout.weights[0].variation"),
                errorCase("empty clauses", r -> rule(r).put("clauses", list()), "flags.f.rules[0].clauses"),
                errorCase(
                        "attribute not a string",
                        r -> clause(r).put("attribute", 1L),
                        "flags.f.rules[0].clauses[0].attribute"),
                errorCase(
                        "unknown operator",
                        r -> clause(r).put("operator", "like"),
                        "flags.f.rules[0].clauses[0].operator"),
                errorCase(
                        "empty clause values",
                        r -> clause(r).put("values", list()),
                        "flags.f.rules[0].clauses[0].values"),
                errorCase(
                        "null clause value",
                        r -> clause(r).put("values", list("a", null)),
                        "flags.f.rules[0].clauses[0].values[1]"),
                errorCase(
                        "array clause value",
                        r -> clause(r).put("values", list(list())),
                        "flags.f.rules[0].clauses[0].values[0]"),
                errorCase(
                        "object clause value",
                        r -> clause(r).put("values", list(obj())),
                        "flags.f.rules[0].clauses[0].values[0]"),
                errorCase(
                        "negate not a boolean",
                        r -> clause(r).put("negate", "no"),
                        "flags.f.rules[0].clauses[0].negate"),
                errorCase(
                        "in_segment in a segment rule",
                        r -> segment(r).put("rules", list(obj("clauses", list(cl("key", "in_segment", false, "s"))))),
                        "segments.s.rules[0].clauses[0].operator"),
                errorCase("segment not an object", r -> m(r.get("segments")).put("s", list()), "segments.s"),
                errorCase("segment included [1]", r -> segment(r).put("included", list(1L)), "segments.s.included[0]"),
                errorCase("segment excluded missing", r -> segment(r).remove("excluded"), "segments.s.excluded"),
                errorCase("segment rules missing", r -> segment(r).remove("rules"), "segments.s.rules"),
                errorCase("segment rule not an object", r -> segment(r).put("rules", list(1L)), "segments.s.rules[0]"),
                errorCase(
                        "segment rule empty clauses",
                        r -> segment(r).put("rules", list(obj("clauses", list()))),
                        "segments.s.rules[0].clauses"));
    }

    @Test
    void namesTheUnknownOperator() {
        Map<String, Object> root = base();
        clause(root).put("operator", "like");
        ParseException e = assertThrows(ParseException.class, () -> RulesetParser.parse(root));
        assertEquals("flags.f.rules[0].clauses[0].operator: unknown operator \"like\"", e.getMessage());
    }

    private static Ruleset.Clause prepared(String operator, Object... values) throws ParseException {
        Map<String, Object> root = base();
        clause(root).put("operator", operator);
        clause(root).put("values", list(values));
        return firstClause(root);
    }

    @Test
    void inAndNotInKeepEveryValue() throws ParseException {
        for (String op : List.of("in", "not_in")) {
            assertEquals(List.of("a", 1.5, true, 2L), prepared(op, "a", 1.5, true, 2L).prepared);
        }
        assertEquals(List.of("a"), prepared("in", "a").values);
    }

    @Test
    void stringOperatorsKeepStrings() throws ParseException {
        for (String op : List.of("contains", "starts_with", "ends_with")) {
            assertEquals(List.of("a"), prepared(op, "a", 1L, true).prepared);
        }
    }

    @Test
    void matchesKeepsThePatternsThatCompileAndWarnsAboutTheOthers() throws ParseException {
        Map<String, Object> root = base();
        clause(root).put("operator", "matches");
        clause(root).put("values", list("(", "^a", 1L));
        Ruleset rs = RulesetParser.parse(root);
        List<?> regexes = rs.flags.get("f").rules.get(0).clauses.get(0).prepared;
        assertEquals(1, regexes.size());
        assertTrue(((PortableRegex) regexes.get(0)).test("abc"));
        assertEquals(1, rs.warnings.size());
        assertTrue(
                rs.warnings
                        .get(0)
                        .matches("flags\\.f\\.rules\\[0\\]\\.clauses\\[0\\]\\.values\\[0\\]: regex does not"
                                + " compile: .+"),
                rs.warnings.get(0));
    }

    @Test
    void warnsAboutABadPatternInASegmentRule() throws ParseException {
        Map<String, Object> root = base();
        segment(root).put("rules", list(obj("clauses", list(cl("email", "matches", false, "a{,2}")))));
        List<String> warnings = RulesetParser.parse(root).warnings;
        assertEquals(1, warnings.size());
        assertTrue(
                warnings.get(0).startsWith("segments.s.rules[0].clauses[0].values[0]: regex does not compile: "),
                warnings.get(0));
    }

    @Test
    void numericOperatorsKeepNumbers() throws ParseException {
        for (String op : List.of("lt", "lte", "gt", "gte")) {
            assertEquals(List.of(5L, 2.5), prepared(op, "5", 5L, 2.5, false).prepared);
        }
    }

    @Test
    void semverOperatorsKeepValidVersions() throws ParseException {
        for (String op : List.of("semver_eq", "semver_lt", "semver_gt")) {
            List<?> versions = prepared(op, "x", "1.0.0", 1L).prepared;
            assertEquals(1, versions.size());
            assertEquals(0, Semver.compare((Semver) versions.get(0), Semver.parse("1.0.0")));
        }
    }

    @Test
    void dateOperatorsKeepValidRfc3339StringsNeverNumbers() throws ParseException {
        assertEquals(List.of(1767225600000L), prepared("before", 123L, "2026-01-01T00:00:00Z").prepared);
        assertEquals(List.of(), prepared("after", "not a date").prepared);
    }

    @Test
    void inSegmentKeepsStrings() throws ParseException {
        assertEquals(List.of("s"), prepared("in_segment", "s", 1L).prepared);
    }
}
