package io.github.mmhy2003.megooflags.internal

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class JsonTest {
    // parseJson

    @Test
    fun parsesEachKindAsKotlinValues() {
        val v = parseJson("""[null, true, false, 3, -7, 3000000000, 1.5, 3.0, 1e20, "s", [1], {"a": {"b": null}}]""")
        assertEquals(
            listOf(
                null,
                true,
                false,
                3L,
                -7L,
                3_000_000_000L,
                1.5,
                3.0,
                1e20,
                "s",
                listOf(1L),
                mapOf("a" to mapOf("b" to null)),
            ),
            v,
        )
    }

    @Test
    fun parsesAnIntegerTooLargeForALongAsADouble() {
        assertEquals(listOf(1e20), parseJson("[100000000000000000000]"))
    }

    @Test
    fun parsesTopLevelScalars() {
        assertEquals("x", parseJson("\"x\""))
        assertEquals(null, parseJson("null"))
        assertEquals(42L, parseJson(" 42 "))
    }

    @Test
    fun refusesInvalidJson() {
        for (text in listOf("", "{", "[1,", "{\"a\":1} x", "[1] [2]")) {
            assertThrows(text, IllegalArgumentException::class.java) { parseJson(text) }
        }
    }

    // writeCanonical

    @Test
    fun sortsKeysByCodePointNotUtf16Unit() {
        val v = mapOf("😀" to 1L, "～" to 2L, "é" to 3L, "Z" to 4L, "a" to 5L)
        assertEquals("""{"Z":4,"a":5,"é":3,"～":2,"😀":1}""", writeCanonical(v))
    }

    @Test
    fun sortsAPrefixBeforeItsExtension() {
        assertEquals("""{"a":2,"ab":1}""", writeCanonical(mapOf("ab" to 1L, "a" to 2L)))
    }

    @Test
    fun sortsNestedKeysWithoutWhitespace() {
        val v = mapOf("b" to listOf(mapOf("y" to 1L, "x" to 2L), emptyList<Any?>()), "a" to emptyMap<String, Any?>())
        assertEquals("""{"a":{},"b":[{"x":2,"y":1},[]]}""", writeCanonical(v))
    }

    @Test
    fun escapesOnlyQuoteBackslashAndControlCharacters() {
        val s = "q \" b \\ n \n t \t \b\u000c\r \u0001 \u001f / \u007f café 😀"
        assertEquals(
            "\"q \\\" b \\\\ n \\n t \\t \\b\\f\\r \\u0001 \\u001f / \u007f café 😀\"",
            writeCanonical(s),
        )
    }

    @Test
    fun writesIntegralNumbersAsIntegers() {
        val v = listOf(3.0, -7L, 0.0, -0.0, 1.5, 4_503_599_627_370_496.0, Long.MAX_VALUE, 0.1, 5)
        assertEquals("[3,-7,0,0,1.5,4503599627370496,9223372036854775807,0.1,5]", writeCanonical(v))
    }

    @Test
    fun writesLargeAndNonFiniteDoubles() {
        assertEquals("1.0E21", writeCanonical(1e21))
        assertEquals("null", writeCanonical(Double.POSITIVE_INFINITY))
        assertEquals("null", writeCanonical(Double.NaN))
    }

    @Test
    fun writesBooleansAndNull() {
        assertEquals("[true,false,null]", writeCanonical(listOf(true, false, null)))
    }

    @Test
    fun refusesValuesThatAreNotJson() {
        assertThrows(IllegalArgumentException::class.java) { writeCanonical(listOf(Any())) }
        assertThrows(IllegalArgumentException::class.java) { writeCanonical(mapOf(1 to "x")) }
    }

    @Test
    fun parsesWhatItWrites() {
        val v = mapOf("a" to listOf(null, true, -3L, 1.5, "x\n\"y\""), "b" to emptyMap<String, Any?>())
        assertEquals(v, parseJson(writeCanonical(v)))
    }
}
