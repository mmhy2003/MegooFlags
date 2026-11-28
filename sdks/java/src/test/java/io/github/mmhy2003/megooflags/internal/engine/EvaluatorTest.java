package io.github.mmhy2003.megooflags.internal.engine;

import static io.github.mmhy2003.megooflags.RulesetGen.cl;
import static io.github.mmhy2003.megooflags.RulesetGen.flagJson;
import static io.github.mmhy2003.megooflags.RulesetGen.list;
import static io.github.mmhy2003.megooflags.RulesetGen.obj;
import static io.github.mmhy2003.megooflags.RulesetGen.ruleFlag;
import static io.github.mmhy2003.megooflags.RulesetGen.rulesetJson;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.DynamicTest.dynamicTest;

import io.github.mmhy2003.megooflags.internal.json.Json;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

class EvaluatorTest {
    @SafeVarargs
    private static Ruleset build(Map<String, Object>... flags) {
        return buildWithSegments(obj(), flags);
    }

    @SafeVarargs
    private static Ruleset buildWithSegments(Map<String, Object> segments, Map<String, Object>... flags) {
        List<Map<String, Object>> list = new ArrayList<>();
        for (Map<String, Object> f : flags) {
            list.add(f);
        }
        return buildList(segments, list);
    }

    private static Ruleset buildList(Map<String, Object> segments, List<Map<String, Object>> flags) {
        try {
            return RulesetParser.parse(Json.write(rulesetJson(flags, segments)));
        } catch (ParseException e) {
            throw new AssertionError(e);
        }
    }

    private static Result eval(Ruleset rs, String flag, Object ctx, Object def) {
        return Evaluator.evaluate(rs, flag, ctx, def, null);
    }

    private static void assertError(Result r, Object def, String code) {
        assertEquals(def, r.value);
        assertNull(r.variant);
        assertEquals(Result.ERROR, r.reason);
        assertEquals(code, r.errorCode);
        assertNull(r.ruleIndex);
        assertNull(r.prerequisite);
        assertFalse(r.errorMessage.isEmpty());
    }

    private static void assertServed(Result r, String variant, String reason) {
        assertEquals(variant, r.variant);
        assertEquals(reason, r.reason);
        assertNull(r.errorCode);
        assertEquals("", r.errorMessage);
    }

    // Errors

    @TestFactory
    List<DynamicTest> invalidContext() {
        Ruleset rs = build(flagJson("f"));
        List<DynamicTest> tests = new ArrayList<>();
        for (Object ctx : Arrays.asList(list(), "user", null, 1L)) {
            tests.add(dynamicTest(
                    "context " + ctx + " is INVALID_CONTEXT",
                    () -> assertError(eval(rs, "f", ctx, "d"), "d", Result.INVALID_CONTEXT)));
        }
        return tests;
    }

    @Test
    void aMissingFlagIsFlagNotFound() {
        assertError(eval(build(flagJson("f")), "nope", obj(), "d"), "d", Result.FLAG_NOT_FOUND);
    }

    // Reasons

    @Test
    void servesStaticForAFlagWithNothingButAFallthrough() {
        Result r = eval(build(flagJson("f")), "f", obj(), "d");
        assertEquals("on", r.value);
        assertServed(r, "on", Result.STATIC);
        assertNull(r.ruleIndex);
        assertNull(r.prerequisite);
        assertEquals(Map.of(), r.metadata());
    }

    @Test
    void servesDefaultWhenAFlagHasTargetsOrRulesThatDoNotMatch() {
        Ruleset rs = build(
                flagJson("t", "targets", list(obj("variation", "off", "values", list("x")))),
                ruleFlag("r", cl("a", "in", false, "x")));
        assertServed(eval(rs, "t", obj("key", "u"), "d"), "on", Result.DEFAULT);
        assertServed(eval(rs, "r", obj(), "d"), "off", Result.DEFAULT);
    }

    @Test
    void servesTheOffVariationWhenDisabled() {
        Result r = eval(build(flagJson("f", "enabled", false)), "f", obj(), "d");
        assertEquals("off", r.value);
        assertServed(r, "off", Result.DISABLED);
    }

    @Test
    void servesTheDefaultWithoutAVariantWhenDisabledWithoutAnOffVariation() {
        Result r = eval(build(flagJson("f", "enabled", false, "off_variation", null)), "f", obj(), "d");
        assertEquals("d", r.value);
        assertNull(r.variant);
        assertEquals(Result.DISABLED, r.reason);
        assertNull(r.errorCode);
        assertEquals("", r.errorMessage);
        assertNull(r.ruleIndex);
        assertNull(r.prerequisite);
    }

    // Type check

    private static Ruleset typed() {
        return build(
                flagJson("s"),
                flagJson(
                        "b",
                        "type",
                        "boolean",
                        "variations",
                        list(obj("key", "on", "value", true), obj("key", "off", "value", false))),
                flagJson(
                        "n",
                        "type",
                        "number",
                        "variations",
                        list(obj("key", "on", "value", 1.5), obj("key", "off", "value", 0L))),
                flagJson(
                        "i",
                        "type",
                        "number",
                        "variations",
                        list(obj("key", "on", "value", 2L), obj("key", "off", "value", 0L))),
                flagJson(
                        "j",
                        "type",
                        "json",
                        "variations",
                        list(obj("key", "on", "value", null), obj("key", "off", "value", obj()))),
                flagJson("x", "enabled", false, "off_variation", null));
    }

    @TestFactory
    List<DynamicTest> typeCheck() {
        Ruleset rs = typed();
        List<DynamicTest> tests = new ArrayList<>();
        String[][] pass = {
            {"s", "string"},
            {"b", "boolean"},
            {"n", "number"},
            {"i", "number"},
            {"j", "json"},
            {"s", "json"},
            {"s", null}
        };
        for (String[] c : pass) {
            tests.add(dynamicTest(
                    c[0] + " as " + c[1] + " passes",
                    () -> assertNull(Evaluator.evaluate(rs, c[0], obj(), "d", c[1]).errorCode)));
        }
        String[][] fail = {
            {"s", "boolean"}, {"s", "number"}, {"b", "number"}, {"b", "string"}, {"n", "string"}, {"j", "string"}
        };
        for (String[] c : fail) {
            tests.add(dynamicTest(
                    c[0] + " as " + c[1] + " is TYPE_MISMATCH",
                    () -> assertError(Evaluator.evaluate(rs, c[0], obj(), "d", c[1]), "d", Result.TYPE_MISMATCH)));
        }
        return tests;
    }

    @Test
    void aNullVariationValueServedForJson() {
        Result r = Evaluator.evaluate(typed(), "j", obj(), "d", "json");
        assertNull(r.value);
        assertServed(r, "on", Result.STATIC);
    }

    @Test
    void theTypeIsNotCheckedWhenNoVariationWasServed() {
        Result r = Evaluator.evaluate(typed(), "x", obj(), 7L, "boolean");
        assertEquals(7L, r.value);
        assertEquals(Result.DISABLED, r.reason);
        assertNull(r.errorCode);
    }

    // Prerequisites

    @Test
    void failsWithTheFirstUnmetPrerequisite() {
        Ruleset rs = build(
                flagJson("ok"),
                flagJson("off", "enabled", false),
                flagJson(
                        "f",
                        "prerequisites",
                        list(
                                obj("flag", "ok", "variation", "on"),
                                obj("flag", "off", "variation", "on"),
                                obj("flag", "missing", "variation", "on"))));
        Result r = eval(rs, "f", obj(), "d");
        assertEquals("off", r.value);
        assertServed(r, "off", Result.PREREQUISITE_FAILED);
        assertEquals("off", r.prerequisite);
        assertEquals(Map.of("prerequisite", "off"), r.metadata());
    }

    @Test
    void failsOnAMissingPrerequisiteFlagOrADifferentVariant() {
        Ruleset rs = build(
                flagJson("p"),
                flagJson("a", "prerequisites", list(obj("flag", "missing", "variation", "on"))),
                flagJson("b", "prerequisites", list(obj("flag", "p", "variation", "off"))));
        assertEquals("missing", eval(rs, "a", obj(), "d").prerequisite);
        assertEquals(Result.PREREQUISITE_FAILED, eval(rs, "b", obj(), "d").reason);
    }

    @Test
    void passesWhenThePrerequisiteServesItsOffVariationAndThatIsTheOneRequired() {
        Ruleset rs = build(
                flagJson("p", "prerequisites", list(obj("flag", "missing", "variation", "on"))),
                flagJson("f", "prerequisites", list(obj("flag", "p", "variation", "off"))));
        assertServed(eval(rs, "f", obj(), "d"), "on", Result.DEFAULT);
    }

    @Test
    void failsWhenThePrerequisiteServesNoVariation() {
        Ruleset rs = build(
                flagJson("p", "off_variation", null, "prerequisites", list(obj("flag", "missing", "variation", "on"))),
                flagJson("f", "prerequisites", list(obj("flag", "p", "variation", "off"))));
        assertEquals(Result.PREREQUISITE_FAILED, eval(rs, "f", obj(), "d").reason);
    }

    /** chain is flag-0 requiring flag-1, ..., flag-(n-1) requiring flag-n. */
    private static Ruleset chain(int n) {
        List<Map<String, Object>> flags = new ArrayList<>();
        for (int i = 0; i <= n; i++) {
            flags.add(flagJson(
                    "flag-" + i,
                    "prerequisites",
                    i < n ? list(obj("flag", "flag-" + (i + 1), "variation", "on")) : list()));
        }
        return buildList(obj(), flags);
    }

    @Test
    void followsAChainOf32Prerequisites() {
        assertServed(eval(chain(32), "flag-0", obj(), "d"), "on", Result.DEFAULT);
    }

    @Test
    void refusesAChainOf33Prerequisites() {
        assertError(eval(chain(33), "flag-0", obj(), "d"), "d", Result.GENERAL);
    }

    @Test
    void refusesACycle() {
        Ruleset rs = build(
                flagJson("a", "prerequisites", list(obj("flag", "b", "variation", "on"))),
                flagJson("b", "prerequisites", list(obj("flag", "a", "variation", "on"))));
        assertError(eval(rs, "a", obj(), "d"), "d", Result.GENERAL);
    }

    // Targets and rules

    private static Ruleset targeted() {
        return build(flagJson(
                "f",
                "targets",
                list(
                        obj("variation", "off", "values", list("u1", "42")),
                        obj("variation", "on", "values", list("u1", "u2"))),
                "rules",
                list(
                        obj("clauses", list(cl("a", "in", false, 1L)), "serve", obj("variation", "off")),
                        obj("clauses", list(cl("a", "in", false, 2L)), "serve", obj("variation", "on"))),
                "fallthrough",
                obj("variation", "off")));
    }

    @Test
    void servesTheFirstTargetContainingTheKey() {
        Ruleset rs = targeted();
        assertServed(eval(rs, "f", obj("key", "u1"), "d"), "off", Result.TARGETING_MATCH);
        Result r = eval(rs, "f", obj("key", "u2"), "d");
        assertServed(r, "on", Result.TARGETING_MATCH);
        assertNull(r.ruleIndex);
    }

    @Test
    void matchesTargetsOnlyWithAStringKey() {
        assertServed(eval(targeted(), "f", obj("key", 42L), "d"), "off", Result.DEFAULT);
    }

    @Test
    void reportsTheIndexOfTheMatchingRule() {
        Ruleset rs = targeted();
        Result r = eval(rs, "f", obj("a", 2L), "d");
        assertServed(r, "on", Result.TARGETING_MATCH);
        assertEquals(1, r.ruleIndex);
        assertEquals(Map.of("rule_index", 1), r.metadata());
        assertEquals(0, eval(rs, "f", obj("a", 1L), "d").ruleIndex);
    }

    @Test
    void needsEveryClauseOfARule() {
        Ruleset rs = build(ruleFlag("f", cl("a", "in", false, 1L), cl("b", "in", false, 2L)));
        assertEquals("on", eval(rs, "f", obj("a", 1L, "b", 2L), "d").variant);
        assertEquals("off", eval(rs, "f", obj("a", 1L, "b", 3L), "d").variant);
    }

    // Clauses

    private static boolean matches(Map<String, Object> clause, Map<String, Object> ctx) {
        return "on".equals(eval(build(ruleFlag("f", clause)), "f", ctx, "d").variant);
    }

    @TestFactory
    List<DynamicTest> clauseShapes() {
        Object[][] cases = {
            {"missing attribute", false, obj(), false},
            {"missing attribute, negated", true, obj(), false},
            {"null attribute, negated", true, obj("a", null), false},
            {"wrong type", false, obj("a", 1L), false},
            {"wrong type, negated", true, obj("a", 1L), true},
            {"empty array", false, obj("a", list()), false},
            {"empty array, negated", true, obj("a", list()), true},
            {"array with a match", false, obj("a", list(1L, "x")), true},
            {"array with a match, negated", true, obj("a", list(1L, "x")), false},
            {"object", false, obj("a", obj("x", "x")), false},
            {"nested array", false, obj("a", list(list("x"))), false},
        };
        List<DynamicTest> tests = new ArrayList<>();
        for (Object[] c : cases) {
            @SuppressWarnings("unchecked")
            Map<String, Object> ctx = (Map<String, Object>) c[2];
            tests.add(dynamicTest(
                    (String) c[0], () -> assertEquals(c[3], matches(cl("a", "contains", (Boolean) c[1], "x"), ctx))));
        }
        return tests;
    }

    @TestFactory
    List<DynamicTest> operators() {
        Object[][] cases = {
            {"in", list("a", 1L), "a", true},
            {"in", list("a", 1L), 1.0, true},
            {"in", list(1L), true, false},
            {"in", list(true), true, true},
            {"in", list("1"), 1L, false},
            {"in", list(9007199254740993L), 9007199254740992L, false},
            {"not_in", list("a"), "b", true},
            {"not_in", list("a"), "a", false},
            {"not_in", list("a"), list(obj()), false},
            {"contains", list("ell"), "hello", true},
            {"contains", list("x"), "hello", false},
            {"contains", list("\uDE00"), "😀", false},
            {"contains", list("\uDE00"), "a\uDE00", true},
            {"starts_with", list("he"), "hello", true},
            {"starts_with", list("lo"), "hello", false},
            {"starts_with", list("\uD83D"), "😀", false},
            {"ends_with", list("lo"), "hello", true},
            {"ends_with", list("he"), "hello", false},
            {"ends_with", list("\uDE00"), "😀", false},
            {"matches", list("^h.l"), "hello", true},
            {"matches", list("^\\d+$"), "12a", false},
            {"matches", list("^\\d+$"), 12L, false},
            {"lt", list(5L), 4L, true},
            {"lt", list(5L), 5L, false},
            {"lte", list(5L), 5L, true},
            {"gt", list(5L), 6L, true},
            {"gt", list(5L), "6", false},
            {"gte", list(5L), 5L, true},
            {"gte", list(5L), 4.9, false},
            {"lt", list(5L), true, false},
            {"gt", list(9007199254740992L), 9007199254740993L, true},
            // NaN equals nothing and is unordered, so every comparison with it is false.
            {"in", list(5L), Double.NaN, false},
            {"in", list(5L, 5.5), Double.NaN, false},
            {"not_in", list(5L), Double.NaN, true},
            {"lt", list(5L), Double.NaN, false},
            {"lte", list(5L), Double.NaN, false},
            {"gt", list(5L), Double.NaN, false},
            {"gte", list(5L), Double.NaN, false},
            {"gte", list(5L), Double.POSITIVE_INFINITY, true},
            {"lt", list(5L), Double.NEGATIVE_INFINITY, true},
            {"semver_eq", list("1.0.0"), "1.0.0+build", true},
            {"semver_lt", list("1.0.0"), "1.0.0-rc.1", true},
            {"semver_gt", list("1.0.0"), "1.0.1", true},
            {"semver_gt", list("1.0.0"), "v1.0.1", false},
            {"before", list("2026-01-01T00:00:00Z"), "2025-12-31T23:59:59Z", true},
            {"before", list("2026-01-01T00:00:00Z"), 1767225599999L, true},
            {"before", list("2026-01-01T00:00:00Z"), 1767225600000L, false},
            {"after", list("2026-01-01T00:00:00Z"), "2026-01-01T00:00:00.001Z", true},
            {"after", list("2026-01-01T00:00:00Z"), "not a date", false},
            {"after", list(0L), 1L, false},
        };
        List<DynamicTest> tests = new ArrayList<>();
        for (Object[] c : cases) {
            String op = (String) c[0];
            List<?> values = (List<?>) c[1];
            tests.add(dynamicTest(
                    op + " " + values + " against " + c[2] + " is " + c[3],
                    () -> assertEquals(c[3], matches(cl("a", op, false, values.toArray()), obj("a", c[2])))));
        }
        return tests;
    }

    // Segments

    @TestFactory
    List<DynamicTest> segments() {
        Ruleset rs = buildWithSegments(
                obj(
                        "s",
                        obj(
                                "included",
                                list("in", "both"),
                                "excluded",
                                list("out", "both"),
                                "rules",
                                list(obj("clauses", list(cl("org", "in", false, "acme")))))),
                ruleFlag("f", cl("ignored", "in_segment", false, "s", "unknown")),
                ruleFlag("n", cl("ignored", "in_segment", true, "s")));
        Object[][] cases = {
            {"included", obj("key", "in"), true},
            {"excluded before included", obj("key", "both"), false},
            {"excluded even when a rule matches", obj("key", "out", "org", "acme"), false},
            {"by rule", obj("key", "other", "org", "acme"), true},
            {"by rule without a key", obj("org", "acme"), true},
            {"a number key is not looked up", obj("key", 1L, "org", "x"), false},
            {"no match", obj("key", "other"), false},
        };
        List<DynamicTest> tests = new ArrayList<>();
        for (Object[] c : cases) {
            boolean want = (Boolean) c[2];
            tests.add(dynamicTest((String) c[0], () -> {
                assertEquals(want ? "on" : "off", eval(rs, "f", c[1], "d").variant);
                assertEquals(want ? "off" : "on", eval(rs, "n", c[1], "d").variant);
            }));
        }
        return tests;
    }

    @Test
    void anUnknownSegmentNeverMatches() {
        Ruleset rs = build(ruleFlag("u", cl("ignored", "in_segment", false, "unknown")));
        assertEquals("off", eval(rs, "u", obj("key", "in"), "d").variant);
    }

    // Rollouts

    private static Ruleset rollouts() {
        Object weights = list(obj("variation", "on", "weight", 30000L), obj("variation", "off", "weight", 70000L));
        return build(
                flagJson("f", "fallthrough", obj("rollout", obj("bucket_by", "key", "weights", weights))),
                flagJson("g", "fallthrough", obj("rollout", obj("bucket_by", "org", "weights", weights))));
    }

    @Test
    void splitsByTheBucketOfTheAttribute() {
        // bucket("f", "u") is 68258: past "on" (30000), so "off".
        assertServed(eval(rollouts(), "f", obj("key", "u"), "d"), "off", Result.SPLIT);
    }

    @Test
    void bucketsByAnotherAttribute() {
        Ruleset rs = rollouts();
        Set<String> byOrg = new HashSet<>();
        for (int i = 0; i < 50; i++) {
            byOrg.add(eval(rs, "g", obj("key", "same", "org", "o" + i), "d").variant);
        }
        assertEquals(Set.of("on", "off"), byOrg);
    }

    @Test
    void usesBucket0WhenTheValueIsMissingOrCannotBeBucketed() {
        Ruleset rs = rollouts();
        for (Map<String, Object> ctx : List.of(obj(), obj("org", null), obj("org", 1.5), obj("org", true))) {
            assertServed(eval(rs, "g", ctx, "d"), "on", Result.SPLIT);
        }
    }
}
