package io.github.mmhy2003.megooflags.internal

import dev.openfeature.kotlin.sdk.ProviderEvaluation
import dev.openfeature.kotlin.sdk.Value
import dev.openfeature.kotlin.sdk.exceptions.ErrorCode
import org.junit.Assert.assertEquals
import org.junit.Test

private fun stored(
    vararg results: EvaluateResult,
    source: Source = Source.NETWORK,
): StoredResults = StoredResults(results.toList(), etag = null, source = source, contextHash = "", targetingKey = null)

private fun served(
    value: Any?,
    variant: String? = "v",
    reason: String = "TARGETING_MATCH",
): EvaluateResult = EvaluateResult("f", value, variant, reason, null, null)

private fun <T> failed(
    value: T,
    code: ErrorCode,
    message: String? = null,
): ProviderEvaluation<T> = ProviderEvaluation(value, null, "ERROR", code, message)

class ResolveTest {
    @Test
    fun noStoredResultsIsProviderNotReady() {
        assertEquals(failed(false, ErrorCode.PROVIDER_NOT_READY), resolveBoolean(null, "f", false))
    }

    @Test
    fun aMissingFlagIsFlagNotFound() {
        assertEquals(failed("d", ErrorCode.FLAG_NOT_FOUND), resolveString(stored(served("x")), "g", "d"))
    }

    @Test
    fun aStoredErrorCodeIsMappedWithItsMessage() {
        val codes =
            mapOf(
                "FLAG_NOT_FOUND" to ErrorCode.FLAG_NOT_FOUND,
                "PARSE_ERROR" to ErrorCode.PARSE_ERROR,
                "TYPE_MISMATCH" to ErrorCode.TYPE_MISMATCH,
                "TARGETING_KEY_MISSING" to ErrorCode.TARGETING_KEY_MISSING,
                "INVALID_CONTEXT" to ErrorCode.INVALID_CONTEXT,
                "GENERAL" to ErrorCode.GENERAL,
                "SOMETHING_NEW" to ErrorCode.GENERAL,
                "PROVIDER_FATAL" to ErrorCode.GENERAL,
            )
        for ((code, mapped) in codes) {
            val r = EvaluateResult("f", null, null, "ERROR", code, "why")
            for (source in Source.entries) {
                assertEquals(code, failed(7, mapped, "why"), resolveInt(stored(r, source = source), "f", 7))
            }
        }
    }

    @Test
    fun aStoredErrorWithoutMessageHasNoMessage() {
        val r = EvaluateResult("f", null, null, "ERROR", "GENERAL", null)
        assertEquals(failed(1.5, ErrorCode.GENERAL), resolveDouble(stored(r), "f", 1.5))
    }

    @Test
    fun aNullVariantReturnsTheDefaultWithTheStoredReason() {
        val r = served(true, variant = null, reason = "DISABLED")
        assertEquals(ProviderEvaluation(false, null, "DISABLED"), resolveBoolean(stored(r), "f", false))
        assertEquals(
            ProviderEvaluation(false, null, "DISABLED"),
            resolveBoolean(stored(r, source = Source.CACHE), "f", false),
        )
    }

    @Test
    fun servesEachTypeWithTheStoredReason() {
        assertEquals(ProviderEvaluation(true, "v", "TARGETING_MATCH"), resolveBoolean(stored(served(true)), "f", false))
        assertEquals(ProviderEvaluation("x", "v", "TARGETING_MATCH"), resolveString(stored(served("x")), "f", "d"))
        assertEquals(ProviderEvaluation(3, "v", "TARGETING_MATCH"), resolveInt(stored(served(3L)), "f", 0))
        assertEquals(ProviderEvaluation(3L, "v", "TARGETING_MATCH"), resolveLong(stored(served(3L)), "f", 0L))
        assertEquals(ProviderEvaluation(1.5, "v", "TARGETING_MATCH"), resolveDouble(stored(served(1.5)), "f", 0.0))
        assertEquals(
            ProviderEvaluation<Value>(Value.Structure(mapOf("a" to Value.Integer(1))), "v", "TARGETING_MATCH"),
            resolveObject(stored(served(mapOf("a" to 1L))), "f", Value.Null),
        )
    }

    @Test
    fun valuesFromTheCacheHaveReasonCached() {
        assertEquals(
            ProviderEvaluation("x", "v", "CACHED"),
            resolveString(stored(served("x"), source = Source.CACHE), "f", "d"),
        )
        assertEquals(
            ProviderEvaluation("x", "v", "TARGETING_MATCH"),
            resolveString(stored(served("x"), source = Source.BOOTSTRAP), "f", "d"),
        )
    }

    @Test
    fun aValueOfTheWrongTypeIsTypeMismatch() {
        assertEquals(failed(false, ErrorCode.TYPE_MISMATCH), resolveBoolean(stored(served("true")), "f", false))
        assertEquals(failed("d", ErrorCode.TYPE_MISMATCH), resolveString(stored(served(1L)), "f", "d"))
        assertEquals(failed(0.0, ErrorCode.TYPE_MISMATCH), resolveDouble(stored(served(true)), "f", 0.0))
        assertEquals(failed(0, ErrorCode.TYPE_MISMATCH), resolveInt(stored(served("1")), "f", 0))
        assertEquals(failed(0L, ErrorCode.TYPE_MISMATCH), resolveLong(stored(served(null)), "f", 0L))
        assertEquals(failed<Value>(Value.Null, ErrorCode.TYPE_MISMATCH), resolveObject(stored(served("{}")), "f", Value.Null))
        assertEquals(failed<Value>(Value.Null, ErrorCode.TYPE_MISMATCH), resolveObject(stored(served(1L)), "f", Value.Null))
    }

    @Test
    fun intReadsNeedIntegralValuesWithinIntRange() {
        val read = { v: Any? -> resolveInt(stored(served(v)), "f", -1) }
        assertEquals(ProviderEvaluation(3, "v", "TARGETING_MATCH"), read(3.0))
        assertEquals(ProviderEvaluation(Int.MAX_VALUE, "v", "TARGETING_MATCH"), read(Int.MAX_VALUE.toLong()))
        assertEquals(ProviderEvaluation(Int.MIN_VALUE, "v", "TARGETING_MATCH"), read(Int.MIN_VALUE.toDouble()))
        for (bad in listOf(1.5, 3_000_000_000L, -3_000_000_000L, 3e9, 1e20, Double.NaN)) {
            assertEquals("$bad", failed(-1, ErrorCode.TYPE_MISMATCH), read(bad))
        }
    }

    @Test
    fun longReadsNeedIntegralValuesWithinLongRange() {
        val read = { v: Any? -> resolveLong(stored(served(v)), "f", -1L) }
        assertEquals(ProviderEvaluation(3_000_000_000L, "v", "TARGETING_MATCH"), read(3_000_000_000L))
        assertEquals(ProviderEvaluation(3L, "v", "TARGETING_MATCH"), read(3.0))
        assertEquals(ProviderEvaluation(Long.MAX_VALUE, "v", "TARGETING_MATCH"), read(Long.MAX_VALUE))
        // -2^63 is exact as a double; 2^63 is the first double above Long.MAX_VALUE.
        assertEquals(ProviderEvaluation(Long.MIN_VALUE, "v", "TARGETING_MATCH"), read(-9.223372036854775808e18))
        for (bad in listOf(1.5, 9.223372036854775808e18, 1e20, Double.POSITIVE_INFINITY, Double.NaN)) {
            assertEquals("$bad", failed(-1L, ErrorCode.TYPE_MISMATCH), read(bad))
        }
    }

    @Test
    fun doubleReadsAcceptAnyNumber() {
        assertEquals(ProviderEvaluation(3.0, "v", "TARGETING_MATCH"), resolveDouble(stored(served(3L)), "f", 0.0))
    }

    @Test
    fun objectReadsBuildStructuresAndLists() {
        val value =
            mapOf(
                "s" to "x",
                "b" to true,
                "i" to 1L,
                "l" to 3_000_000_000L,
                "d" to 1.5,
                "n" to null,
                "a" to listOf(1L, mapOf("k" to listOf<Any?>())),
            )
        val expected =
            Value.Structure(
                mapOf(
                    "s" to Value.String("x"),
                    "b" to Value.Boolean(true),
                    "i" to Value.Integer(1),
                    "l" to Value.Long(3_000_000_000L),
                    "d" to Value.Double(1.5),
                    "n" to Value.Null,
                    "a" to Value.List(listOf(Value.Integer(1), Value.Structure(mapOf("k" to Value.List(listOf()))))),
                ),
            )
        assertEquals(ProviderEvaluation<Value>(expected, "v", "TARGETING_MATCH"), resolveObject(stored(served(value)), "f", Value.Null))
        assertEquals(
            ProviderEvaluation<Value>(Value.List(listOf(Value.String("a"))), "v", "TARGETING_MATCH"),
            resolveObject(stored(served(listOf("a"))), "f", Value.Null),
        )
    }
}
