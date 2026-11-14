package io.github.mmhy2003.megooflags;

import static io.github.mmhy2003.megooflags.RulesetGen.list;
import static io.github.mmhy2003.megooflags.RulesetGen.obj;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.DynamicTest.dynamicTest;

import io.github.mmhy2003.megooflags.internal.engine.Bucketing;
import io.github.mmhy2003.megooflags.internal.engine.Evaluator;
import io.github.mmhy2003.megooflags.internal.engine.ParseException;
import io.github.mmhy2003.megooflags.internal.engine.Result;
import io.github.mmhy2003.megooflags.internal.engine.Ruleset;
import io.github.mmhy2003.megooflags.internal.engine.RulesetParser;
import io.github.mmhy2003.megooflags.internal.engine.Values;
import io.github.mmhy2003.megooflags.internal.json.Json;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.junit.jupiter.api.io.TempDir;

/** The language-neutral conformance suite every SDK must pass (spec/conformance/README.md). */
class ConformanceTest {
    private static final Path CONFORMANCE_DIR = Paths.get("..", "..", "spec", "conformance");

    /** loadCaseFile reads a case file and refuses any schema_version other than the integer 1. */
    @SuppressWarnings("unchecked")
    static Map<String, Object> loadCaseFile(Path path) throws IOException {
        Map<String, Object> f = (Map<String, Object>) Json.parse(Files.readString(path, StandardCharsets.UTF_8));
        Object version = f.get("schema_version");
        if (!Long.valueOf(1).equals(version)) {
            throw new IllegalArgumentException(
                    path.getFileName() + ": unsupported schema_version " + Json.write(version));
        }
        return f;
    }

    /**
     * sameJson is JSON equality: 1 == 1.0, but true != 1 and "1" != 1; arrays element by element,
     * objects key by key, {} is not [].
     */
    static boolean sameJson(Object a, Object b) {
        if (a == null || b == null) {
            return a == b;
        }
        if (a instanceof List || b instanceof List) {
            if (!(a instanceof List && b instanceof List)) {
                return false;
            }
            List<?> x = (List<?>) a;
            List<?> y = (List<?>) b;
            if (x.size() != y.size()) {
                return false;
            }
            for (int i = 0; i < x.size(); i++) {
                if (!sameJson(x.get(i), y.get(i))) {
                    return false;
                }
            }
            return true;
        }
        if (a instanceof Map || b instanceof Map) {
            if (!(a instanceof Map && b instanceof Map)) {
                return false;
            }
            Map<?, ?> x = (Map<?, ?>) a;
            Map<?, ?> y = (Map<?, ?>) b;
            if (x.size() != y.size()) {
                return false;
            }
            for (Map.Entry<?, ?> e : x.entrySet()) {
                if (!y.containsKey(e.getKey()) || !sameJson(e.getValue(), y.get(e.getKey()))) {
                    return false;
                }
            }
            return true;
        }
        return Values.jsonEqual(a, b);
    }

    private static List<Path> caseFiles() throws IOException {
        try (Stream<Path> files = Files.list(CONFORMANCE_DIR.resolve("cases"))) {
            return files.filter(p -> p.getFileName().toString().endsWith(".json"))
                    .sorted()
                    .collect(Collectors.toList());
        }
    }

    @Test
    void hasCaseFiles() throws IOException {
        assertFalse(caseFiles().isEmpty());
    }

    @TestFactory
    @SuppressWarnings("unchecked")
    List<DynamicTest> cases() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Path path : caseFiles()) {
            String name = path.getFileName().toString();
            String stem = name.substring(0, name.length() - ".json".length());
            Map<String, Object> f = loadCaseFile(path);
            Ruleset parsed = null;
            ParseException parseError = null;
            try {
                parsed = RulesetParser.parse(f.get("ruleset"));
            } catch (ParseException e) {
                parseError = e;
            }
            Ruleset rs = parsed;
            ParseException err = parseError;
            for (Object item : (List<Object>) f.getOrDefault("tests", List.of())) {
                Map<String, Object> c = (Map<String, Object>) item;
                tests.add(dynamicTest(stem + "::" + c.get("name"), () -> {
                    if (rs == null) {
                        throw new AssertionError("ruleset does not parse", err);
                    }
                    check(rs, c);
                }));
            }
        }
        return tests;
    }

    @SuppressWarnings("unchecked")
    private static void check(Ruleset rs, Map<String, Object> c) {
        Result r = Evaluator.evaluate(
                rs, (String) c.get("flag"), c.get("context"), c.get("default"), (String) c.get("expected_type"));
        Map<String, Object> want = (Map<String, Object>) c.get("expected");
        assertTrue(sameJson(r.value, want.get("value")), () -> "value: got " + r.value + ", want " + want.get("value"));
        assertEquals(want.get("variant"), r.variant, "variant");
        assertEquals(want.get("reason"), r.reason, "reason");
        assertEquals(want.get("error_code"), r.errorCode, "error_code");
        Map<String, Object> meta = r.metadata();
        for (Map.Entry<String, Object> e : ((Map<String, Object>) want.get("metadata")).entrySet()) {
            assertTrue(meta.containsKey(e.getKey()), () -> "metadata " + e.getKey() + " missing in " + meta);
            assertTrue(sameJson(meta.get(e.getKey()), e.getValue()), () -> "metadata " + e.getKey() + ": " + meta);
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Object> vectors() throws IOException {
        return (List<Object>)
                loadCaseFile(CONFORMANCE_DIR.resolve("bucketing.json")).get("vectors");
    }

    @Test
    void hasAtLeast20BucketingVectors() throws IOException {
        assertTrue(vectors().size() >= 20);
    }

    @TestFactory
    @SuppressWarnings("unchecked")
    List<DynamicTest> bucketing() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Object item : vectors()) {
            Map<String, Object> v = (Map<String, Object>) item;
            String input = (String) v.get("input");
            tests.add(dynamicTest("bucketing::" + input, () -> {
                int dot = input.indexOf('.');
                assertEquals(
                        ((Long) v.get("bucket")).intValue(),
                        Bucketing.bucket(input.substring(0, dot), input.substring(dot + 1)));
            }));
        }
        return tests;
    }

    @Test
    void refusesACaseFileWithSchemaVersion2Or1Point0(@TempDir Path dir) throws IOException {
        for (String version : List.of("2", "1.0")) {
            Path path = dir.resolve("case.json");
            Files.writeString(
                    path,
                    "{\"schema_version\": " + version + ", \"description\": \"\", \"ruleset\": {}, \"tests\": []}");
            IllegalArgumentException e = assertThrows(IllegalArgumentException.class, () -> loadCaseFile(path));
            assertEquals("case.json: unsupported schema_version " + version, e.getMessage());
        }
    }

    @TestFactory
    List<DynamicTest> sameJsonPairs() {
        Object[][] pairs = {
            {1L, 1.0, true},
            {true, 1L, false},
            {"1", 1L, false},
            {null, null, true},
            {null, false, false},
            {list(1L, obj("a", true)), list(1.0, obj("a", true)), true},
            {list(1L), list(1L, 2L), false},
            {obj("a", 1L), obj("a", 1L, "b", 2L), false},
            {obj("a", 0L), obj("a", false), false},
        };
        List<DynamicTest> tests = new ArrayList<>();
        for (Object[] p : pairs) {
            tests.add(dynamicTest(
                    "sameJson(" + Arrays.toString(Arrays.copyOf(p, 2)) + ") is " + p[2],
                    () -> assertEquals(p[2], sameJson(p[0], p[1]))));
        }
        return tests;
    }

    @Test
    void doesNotTakeAnEmptyObjectForAnEmptyArray() {
        assertFalse(sameJson(obj(), list()));
        assertFalse(sameJson(list(), obj()));
    }
}
