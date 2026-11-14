package io.github.mmhy2003.megooflags;

import io.github.mmhy2003.megooflags.internal.json.Json;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Ruleset builders for the engine tests and the p99 guard. Every map and list is mutable. */
public final class RulesetGen {
    private static final int BENCH_SEGMENTS = 50;

    private RulesetGen() {}

    /** obj is a mutable JSON object of alternating keys and values. */
    public static Map<String, Object> obj(Object... keysAndValues) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < keysAndValues.length; i += 2) {
            m.put((String) keysAndValues[i], keysAndValues[i + 1]);
        }
        return m;
    }

    /** list is a mutable JSON array. */
    public static List<Object> list(Object... items) {
        return new ArrayList<>(Arrays.asList(items));
    }

    /**
     * flagJson is a minimal enabled string flag with variations "on" and "off", off_variation
     * "off" and fallthrough "on". fields (alternating keys and values) override or add fields.
     */
    public static Map<String, Object> flagJson(String key, Object... fields) {
        Map<String, Object> f = obj(
                "key",
                key,
                "type",
                "string",
                "enabled",
                true,
                "version",
                1L,
                "variations",
                list(obj("key", "on", "value", "on"), obj("key", "off", "value", "off")),
                "off_variation",
                "off",
                "prerequisites",
                list(),
                "targets",
                list(),
                "rules",
                list(),
                "fallthrough",
                obj("variation", "on"));
        f.putAll(obj(fields));
        return f;
    }

    /** ruleFlag has one rule of clauses serving "on", and fallthrough "off". */
    public static Map<String, Object> ruleFlag(String key, Object... clauses) {
        return flagJson(
                key,
                "rules",
                list(obj("clauses", list(clauses), "serve", obj("variation", "on"))),
                "fallthrough",
                obj("variation", "off"));
    }

    /** cl is a clause. */
    public static Map<String, Object> cl(String attribute, String operator, boolean negate, Object... values) {
        return obj("attribute", attribute, "operator", operator, "negate", negate, "values", list(values));
    }

    /** rulesetJson wraps flags (keyed by their key) and segments in a ruleset. */
    public static Map<String, Object> rulesetJson(List<Map<String, Object>> flags, Map<String, Object> segments) {
        Map<String, Object> byKey = new LinkedHashMap<>();
        for (Map<String, Object> f : flags) {
            byKey.put((String) f.get("key"), f);
        }
        return obj("schema_version", 1L, "project", "p", "environment", "e", "flags", byKey, "segments", segments);
    }

    /** flagKeys is flag-0 to flag-(n-1). */
    public static List<String> flagKeys(int n) {
        List<String> keys = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            keys.add("flag-" + i);
        }
        return keys;
    }

    /**
     * genRuleset is a deterministic ruleset of n flags and 50 segments. Even flags are booleans
     * with three rules (in, matches, semver_gt), a 100-key target list and a three-way fallthrough
     * rollout; odd flags are strings with an in_segment rule and a before rule. Every 10th flag has
     * a prerequisite on the flag before it.
     */
    public static Map<String, Object> genRuleset(int n) {
        Object[] plans = new Object[20];
        for (int i = 0; i < plans.length; i++) {
            plans[i] = "plan-" + i;
        }
        List<Map<String, Object>> flags = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            String key = "flag-" + i;
            Map<String, Object> f;
            if (i % 2 == 0) {
                List<Object> targets = list();
                for (int k = 0; k < 100; k++) {
                    targets.add("user-" + (i + k * 37) % 1000);
                }
                f = flagJson(
                        key,
                        "type",
                        "boolean",
                        "variations",
                        list(
                                obj("key", "on", "value", true),
                                obj("key", "off", "value", false),
                                obj("key", "alt", "value", false)),
                        "targets",
                        list(obj("variation", "alt", "values", targets)),
                        "rules",
                        list(
                                benchRule("on", cl("plan", "in", false, plans)),
                                benchRule("off", cl("email", "matches", false, "^user-[0-9]+$")),
                                benchRule("alt", cl("app_version", "semver_gt", false, "1.5.0"))),
                        "fallthrough",
                        obj(
                                "rollout",
                                obj(
                                        "bucket_by",
                                        "key",
                                        "weights",
                                        list(
                                                obj("variation", "on", "weight", 33334L),
                                                obj("variation", "off", "weight", 33333L),
                                                obj("variation", "alt", "weight", 33333L)))));
            } else {
                f = flagJson(
                        key,
                        "variations",
                        list(
                                obj("key", "on", "value", "on"),
                                obj("key", "off", "value", "off"),
                                obj("key", "alt", "value", "alt")),
                        "rules",
                        list(
                                benchRule("alt", cl("key", "in_segment", false, "segment-" + i % BENCH_SEGMENTS)),
                                benchRule("off", cl("signed_up", "before", false, "2025-01-01T00:00:00Z"))));
            }
            if (i > 0 && i % 10 == 0) {
                f.put("prerequisites", list(obj("flag", "flag-" + (i - 1), "variation", "on")));
            }
            flags.add(f);
        }
        Map<String, Object> segments = new LinkedHashMap<>();
        for (int s = 0; s < BENCH_SEGMENTS; s++) {
            List<Object> included = list();
            for (int k = 0; k < 1000; k++) {
                included.add("user-" + (s * 1000 + k));
            }
            segments.put(
                    "segment-" + s,
                    obj(
                            "included",
                            included,
                            "excluded",
                            list(),
                            "rules",
                            list(obj("clauses", list(cl("org", "in", false, "org-" + s))))));
        }
        Map<String, Object> rs = rulesetJson(flags, segments);
        rs.put("project", "bench");
        rs.put("environment", "bench");
        return rs;
    }

    /** benchContexts are 100 contexts that hit every rule shape of genRuleset. */
    public static List<Map<String, Object>> benchContexts() {
        List<Map<String, Object>> ctxs = new ArrayList<>(100);
        for (int j = 0; j < 100; j++) {
            ctxs.add(obj(
                    "key", "user-" + j * 503,
                    "plan", "plan-" + j % 40,
                    "email", j % 3 == 0 ? "user-" + j : "user" + j + "@example.com",
                    "app_version", "1." + j % 10 + ".0",
                    "org", "org-" + j % 100));
        }
        return ctxs;
    }

    private static List<Object> boolVariations() {
        return list(obj("key", "on", "value", true), obj("key", "off", "value", false));
    }

    /**
     * providerFlags are the provider tests' flags: one of each type, a target, a rule and a failing
     * prerequisite (the Go SDK's testdata/ruleset.json), plus int-flag for the integer evaluation.
     */
    private static List<Map<String, Object>> providerFlags() {
        return new ArrayList<>(List.of(
                flagJson(
                        "bool-flag",
                        "type",
                        "boolean",
                        "variations",
                        boolVariations(),
                        "rules",
                        list(obj(
                                "description",
                                "pro plan",
                                "clauses",
                                list(cl("plan", "in", false, "pro")),
                                "serve",
                                obj("variation", "on"))),
                        "fallthrough",
                        obj("variation", "off")),
                flagJson(
                        "targeted",
                        "variations",
                        list(obj("key", "alice", "value", "hello alice"), obj("key", "other", "value", "hello")),
                        "off_variation",
                        "other",
                        "targets",
                        list(obj("variation", "alice", "values", list("alice"))),
                        "fallthrough",
                        obj("variation", "other")),
                flagJson(
                        "string-flag",
                        "variations",
                        list(obj("key", "blue", "value", "blue"), obj("key", "red", "value", "red")),
                        "off_variation",
                        "red",
                        "fallthrough",
                        obj("variation", "blue")),
                flagJson(
                        "number-flag",
                        "type",
                        "number",
                        "variations",
                        list(obj("key", "half", "value", 2.5), obj("key", "zero", "value", 0L)),
                        "off_variation",
                        "zero",
                        "fallthrough",
                        obj("variation", "half")),
                flagJson(
                        "int-flag",
                        "type",
                        "number",
                        "variations",
                        list(
                                obj("key", "three", "value", 3L),
                                obj("key", "two", "value", 2.0),
                                obj("key", "half", "value", 2.5),
                                obj("key", "big", "value", 3_000_000_000L)),
                        "off_variation",
                        "three",
                        "targets",
                        list(
                                obj("variation", "two", "values", list("two")),
                                obj("variation", "half", "values", list("half")),
                                obj("variation", "big", "values", list("big"))),
                        "fallthrough",
                        obj("variation", "three")),
                flagJson(
                        "json-flag",
                        "type",
                        "json",
                        "variations",
                        list(
                                obj(
                                        "key",
                                        "cfg",
                                        "value",
                                        obj("limit", 10L, "big", 3_000_000_000L, "ratio", 0.5, "tags", list("a", "b"))),
                                obj("key", "empty", "value", obj())),
                        "off_variation",
                        "empty",
                        "fallthrough",
                        obj("variation", "cfg")),
                flagJson("prereq-off", "type", "boolean", "enabled", false, "variations", boolVariations()),
                flagJson(
                        "needs-prereq",
                        "type",
                        "boolean",
                        "variations",
                        boolVariations(),
                        "prerequisites",
                        list(obj("flag", "prereq-off", "variation", "on")))));
    }

    /** providerRuleset is the provider tests' ruleset as JSON text. */
    public static String providerRuleset() {
        return Json.write(rulesetJson(providerFlags(), obj()));
    }

    /** changedProviderRuleset serves red for string-flag (version 2), removes targeted and adds new-flag. */
    public static String changedProviderRuleset() {
        List<Map<String, Object>> flags = new ArrayList<>();
        for (Map<String, Object> f : providerFlags()) {
            if (f.get("key").equals("targeted")) {
                continue;
            }
            if (f.get("key").equals("string-flag")) {
                f.put("version", 2L);
                f.put("fallthrough", obj("variation", "red"));
                Map<String, Object> added = new LinkedHashMap<>(f);
                added.put("key", "new-flag");
                flags.add(added);
            }
            flags.add(f);
        }
        return Json.write(rulesetJson(flags, obj()));
    }

    private static Map<String, Object> benchRule(String variation, Map<String, Object> clause) {
        return obj("description", "", "clauses", list(clause), "serve", obj("variation", variation));
    }
}
