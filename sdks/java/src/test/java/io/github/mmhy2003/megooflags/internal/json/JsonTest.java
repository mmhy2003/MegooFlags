package io.github.mmhy2003.megooflags.internal.json;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class JsonTest {
    @Test
    void parsesScalars() {
        assertNull(Json.parse("null"));
        assertEquals(Boolean.TRUE, Json.parse("true"));
        assertEquals(Boolean.FALSE, Json.parse(" false "));
        assertEquals("a\"b\\c/\b\f\n\r\t", Json.parse("\"a\\\"b\\\\c\\/\\b\\f\\n\\r\\t\""));
        assertEquals("é€", Json.parse("\"\\u00e9\\u20AC\""));
        assertEquals("ü", Json.parse("\"ü\""));
    }

    @Test
    void integersThatFitAreLongs() {
        assertEquals(42L, Json.parse("42"));
        assertEquals(-7L, Json.parse("-7"));
        assertEquals(0L, Json.parse("0"));
        assertEquals(Long.MAX_VALUE, Json.parse("9223372036854775807"));
        assertEquals(Long.MIN_VALUE, Json.parse("-9223372036854775808"));
    }

    @Test
    void otherNumbersAreDoubles() {
        assertEquals(42.0, Json.parse("42.0"));
        assertEquals(1e3, Json.parse("1e3"));
        assertEquals(-2.5e-3, Json.parse("-2.5E-3"));
        assertEquals(-0.0, Json.parse("-0.0"));
        assertEquals(9223372036854775808.0, Json.parse("9223372036854775808"));
        assertEquals(Double.POSITIVE_INFINITY, Json.parse("1e400"));
    }

    @Test
    void parsesContainersInOrder() {
        Object v = Json.parse("{\"b\": [1, \"x\", null, {}], \"a\": {\"c\": []}}");
        assertTrue(v instanceof LinkedHashMap);
        Map<?, ?> m = (Map<?, ?>) v;
        assertEquals(List.of("b", "a"), new ArrayList<>(m.keySet()));
        assertTrue(m.get("b") instanceof ArrayList);
        assertEquals(Arrays.asList(1L, "x", null, Map.of()), m.get("b"));
        assertEquals(Map.of("c", List.of()), m.get("a"));
    }

    @Test
    void duplicateKeysLastWins() {
        assertEquals(Map.of("a", 2L), Json.parse("{\"a\": 1, \"a\": 2}"));
    }

    @Test
    void combinesSurrogateEscapes() {
        assertEquals("\uD83D\uDE00", Json.parse("\"\\ud83d\\ude00\""));
        assertEquals(1, ((String) Json.parse("\"\\ud83d\\ude00\"")).codePointCount(0, 2));
    }

    @Test
    void loneSurrogateEscapesBecomeReplacementCharacters() {
        // As Go's encoding/json does.
        assertEquals("\uFFFD", Json.parse("\"\\ud83d\""));
        assertEquals("\uFFFDx", Json.parse("\"\\ude00x\""));
        assertEquals("\uFFFDA", Json.parse("\"\\ud83d\\u0041\""));
        assertEquals("\uFFFD\uD83D\uDE00", Json.parse("\"\\ud83d\\ud83d\\ude00\""));
    }

    @ParameterizedTest
    @ValueSource(
            strings = {
                "",
                " ",
                "1 2",
                "{} x",
                "[1,]",
                "{\"a\":1,}",
                "[,1]",
                "01",
                "-01",
                "00",
                "NaN",
                "Infinity",
                "-Infinity",
                "-",
                "1.",
                ".5",
                "1e",
                "1e+",
                "+1",
                "\"a\nb\"",
                "\"a\u0001b\"",
                "\"\\x\"",
                "\"\\u12\"",
                "\"\\u12G4\"",
                "\"abc",
                "[1",
                "{\"a\"}",
                "{\"a\" 1}",
                "{1:2}",
                "tru",
                "nul",
                "'a'"
            })
    void refusesInvalidJson(String text) {
        assertThrows(JsonException.class, () -> Json.parse(text));
    }

    @Test
    void nestingLimitIs512() {
        assertEquals(1L, unwrap(Json.parse("[".repeat(511) + "1" + "]".repeat(511)), 511));
        Json.parse("[".repeat(512) + "]".repeat(512));
        assertThrows(JsonException.class, () -> Json.parse("[".repeat(513) + "]".repeat(513)));
        assertThrows(JsonException.class, () -> Json.parse("{\"a\":".repeat(513) + "1" + "}".repeat(513)));
    }

    private static Object unwrap(Object v, int depth) {
        for (int i = 0; i < depth; i++) {
            v = ((List<?>) v).get(0);
        }
        return v;
    }

    @Test
    void writesScalars() {
        assertEquals("null", Json.write(null));
        assertEquals("true", Json.write(true));
        assertEquals("42", Json.write(42L));
        assertEquals("7", Json.write(7));
        assertEquals("1.5", Json.write(1.5));
        assertEquals("\"q\\\"b\\\\n\\n\\u0001\\u001f\"", Json.write("q\"b\\n\n\u0001\u001f"));
        assertEquals("\"é😀\"", Json.write("é😀"));
    }

    @Test
    void writesContainers() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("b", Arrays.asList(1L, null, "x"));
        m.put("a", Map.of());
        assertEquals("{\"b\":[1,null,\"x\"],\"a\":{}}", Json.write(m));
    }

    @Test
    void refusesNonFiniteNumbersAndUnknownTypes() {
        assertThrows(JsonException.class, () -> Json.write(Double.NaN));
        assertThrows(JsonException.class, () -> Json.write(Double.POSITIVE_INFINITY));
        assertThrows(JsonException.class, () -> Json.write(List.of(Double.NEGATIVE_INFINITY)));
        assertThrows(JsonException.class, () -> Json.write(new Object()));
        assertThrows(JsonException.class, () -> Json.write(Map.of(1, 2)));
    }

    @Test
    void roundTrips() {
        String text = "{\"s\":\"a\\\"\\\\\\n\\u0000é😀\",\"i\":-12,\"big\":9223372036854775807,"
                + "\"d\":0.25,\"e\":1.0E-7,\"t\":true,\"f\":false,\"n\":null,"
                + "\"arr\":[[],{},[1,[2,{\"k\":\"v\"}]]]}";
        Object tree = Json.parse(text);
        assertEquals(tree, Json.parse(Json.write(tree)));
        assertEquals(text, Json.write(tree));
    }
}
