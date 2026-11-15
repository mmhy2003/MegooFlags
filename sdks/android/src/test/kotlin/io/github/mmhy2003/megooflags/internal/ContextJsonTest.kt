package io.github.mmhy2003.megooflags.internal

import dev.openfeature.kotlin.sdk.ImmutableContext
import dev.openfeature.kotlin.sdk.Value
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import kotlin.time.ExperimentalTime
import kotlin.time.Instant

private fun context(
    targetingKey: String = "",
    attributes: Map<String, Value> = emptyMap(),
) = ImmutableContext(targetingKey, attributes)

@OptIn(ExperimentalTime::class)
private fun instant(epochMillis: Long) = Value.Instant(Instant.fromEpochMilliseconds(epochMillis))

class ContextJsonTest {
    // toRequestContext

    @Test
    fun writesANonEmptyTargetingKeyAsKey() {
        assertEquals(
            mapOf("key" to "u1", "plan" to "pro"),
            toRequestContext(context("u1", mapOf("plan" to Value.String("pro")))),
        )
    }

    @Test
    fun replacesAKeyAttributeWithTheTargetingKey() {
        assertEquals(mapOf("key" to "u1"), toRequestContext(context("u1", mapOf("key" to Value.String("other")))))
    }

    @Test
    fun keepsAKeyAttributeWithoutATargetingKey() {
        assertEquals(mapOf("key" to "u2"), toRequestContext(context("", mapOf("key" to Value.String("u2")))))
    }

    @Test
    fun dropsAnEmptyTargetingKey() {
        assertEquals(mapOf("plan" to "free"), toRequestContext(context("", mapOf("plan" to Value.String("free")))))
    }

    @Test
    fun noContextIsAnEmptyBody() {
        assertEquals(emptyMap<String, Any?>(), toRequestContext(null))
    }

    @Test
    fun mapsEveryValueKind() {
        val body =
            toRequestContext(
                context(
                    "u1",
                    mapOf(
                        "admin" to Value.Boolean(true),
                        "age" to Value.Integer(42),
                        "big" to Value.Long(3_000_000_000L),
                        "ratio" to Value.Double(1.5),
                        "whole" to Value.Double(3.0),
                        "team" to Value.Null,
                        "huge" to Value.Double(1e20),
                        "device" to
                            Value.Structure(
                                mapOf(
                                    "tags" to Value.List(listOf(Value.String("beta"), Value.Structure(mapOf("level" to Value.Integer(2))))),
                                ),
                            ),
                    ),
                ),
            )
        assertEquals(
            mapOf(
                "key" to "u1",
                "admin" to true,
                "age" to 42L,
                "big" to 3_000_000_000L,
                "ratio" to 1.5,
                "whole" to 3L,
                "team" to null,
                "huge" to 1e20,
                "device" to mapOf("tags" to listOf("beta", mapOf("level" to 2L))),
            ),
            body,
        )
    }

    @Test
    fun writesDatesAsUtcWithMilliseconds() {
        val body =
            toRequestContext(
                context(
                    "",
                    mapOf(
                        "at" to instant(1_794_302_102_000),
                        "nested" to Value.Structure(mapOf("list" to Value.List(listOf(instant(1_794_302_102_500))))),
                    ),
                ),
            )
        assertEquals(
            mapOf(
                "at" to "2026-11-10T09:15:02.000Z",
                "nested" to mapOf("list" to listOf("2026-11-10T09:15:02.500Z")),
            ),
            body,
        )
    }

    @Test
    fun formatsEpochMillisAsRfc3339() {
        val cases =
            listOf(
                0L to "1970-01-01T00:00:00.000Z",
                -1L to "1969-12-31T23:59:59.999Z",
                951_782_400_007L to "2000-02-29T00:00:00.007Z",
                1_794_302_102_123L to "2026-11-10T09:15:02.123Z",
                253_402_300_799_999L to "9999-12-31T23:59:59.999Z",
                -62_135_596_800_000L to "0001-01-01T00:00:00.000Z",
            )
        for ((millis, want) in cases) {
            assertEquals("$millis", want, formatRfc3339Millis(millis))
        }
    }

    // contextHash and targetingKeyOf

    @Test
    fun contextHashIsTheSha256OfTheCanonicalJson() {
        assertEquals(
            "e38a15b496693e013a31fba1d1c8804c3c06f1d0686e0152549c8c05609babbe",
            contextHash(mapOf("plan" to "pro", "key" to "user-1")),
        )
    }

    @Test
    fun contextHashOfAnEmptyBodyIsTheHashOfBraces() {
        assertEquals("44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a", contextHash(emptyMap()))
    }

    @Test
    fun targetingKeyIsKeyWhenItIsAString() {
        assertEquals("u1", targetingKeyOf(mapOf("key" to "u1")))
        assertNull(targetingKeyOf(emptyMap()))
        assertNull(targetingKeyOf(mapOf("key" to 1L)))
        assertNull(targetingKeyOf(mapOf("key" to null)))
    }
}
