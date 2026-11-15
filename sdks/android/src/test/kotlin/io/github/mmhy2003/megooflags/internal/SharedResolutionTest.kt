// The language-neutral resolution cases every client SDK must pass
// (spec/client/README.md). integer cases run through both the Int and the Long
// read, number cases through the Double read.
package io.github.mmhy2003.megooflags.internal

import dev.openfeature.kotlin.sdk.ProviderEvaluation
import dev.openfeature.kotlin.sdk.Value
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import kotlin.time.ExperimentalTime

/** toJson turns a resolved OpenFeature value back into JSON for comparison. */
@OptIn(ExperimentalTime::class)
private fun toJson(v: Value): Any? =
    when (v) {
        is Value.Boolean -> v.boolean
        is Value.String -> v.string
        is Value.Integer -> v.integer.toLong()
        is Value.Long -> v.long
        is Value.Double -> v.double
        is Value.Instant -> v.instant.toString()
        is Value.List -> v.list.map(::toJson)
        is Value.Structure -> v.structure.mapValues { toJson(it.value) }
        is Value.Null -> null
    }

/** The resolution of a case, as JSON. */
private data class Outcome(
    val value: Any?,
    val variant: String?,
    val reason: String?,
    val errorCode: String?,
)

private fun <T> outcome(
    e: ProviderEvaluation<T>,
    json: (T) -> Any? = { it },
): Outcome = Outcome(json(e.value), e.variant, e.reason, e.errorCode?.name)

/** resolveCase resolves one case's flag with its type and default; integer cases give one outcome per read. */
private fun resolveCase(c: Map<*, *>): List<Outcome> {
    val flag = c["flag"] as String
    val source = Source.valueOf((c["source"] as String).uppercase())
    val results = c["results"]
    val store =
        results?.let {
            val parsed = parseResults("""{"results":${writeCanonical(it)}}""", "megooflags: invalid results")
            StoredResults(parsed, etag = null, source = source, contextHash = "", targetingKey = null)
        }
    val def = c["default"]
    return when (c["type"]) {
        "boolean" -> {
            listOf(outcome(resolveBoolean(store, flag, def as Boolean)))
        }

        "string" -> {
            listOf(outcome(resolveString(store, flag, def as String)))
        }

        "integer" -> {
            val d = def as Long
            listOf(
                outcome(resolveInt(store, flag, d.toInt())) { it.toLong() },
                outcome(resolveLong(store, flag, d)),
            )
        }

        "number" -> {
            listOf(outcome(resolveDouble(store, flag, (def as Number).toDouble())))
        }

        "object" -> {
            listOf(outcome(resolveObject(store, flag, toValue(def)), ::toJson))
        }

        else -> {
            error("unsupported type ${c["type"]}")
        }
    }
}

class SharedResolutionTest {
    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun sharedResolutionCases() {
        val tests = loadCaseFile(File(clientDir, "resolution.json"))["tests"] as List<*>
        assertTrue(tests.isNotEmpty())
        val types = HashSet<String>()
        for (test in tests) {
            val c = test as Map<*, *>
            val name = "resolution::${c["name"]}"
            types.add(c["type"] as String)
            val expected = c["expected"] as Map<*, *>
            for (got in resolveCase(c)) {
                assertTrue(
                    "$name: value ${writeCanonical(got.value)} != ${writeCanonical(expected["value"])}",
                    sameJson(got.value, expected["value"]),
                )
                assertEquals("$name: variant", expected["variant"], got.variant)
                assertEquals("$name: reason", expected["reason"], got.reason)
                assertEquals("$name: error_code", expected["error_code"], got.errorCode)
            }
        }
        assertEquals(setOf("boolean", "string", "integer", "number", "object"), types)
    }

    @Test
    fun refusesSchemaVersion2() {
        val file = tmp.newFile("resolution.json")
        file.writeText("""{"schema_version": 2, "kind": "resolution", "tests": []}""")
        val err = assertThrows(CaseFileException::class.java) { loadCaseFile(file) }
        assertEquals("resolution.json: unsupported schema_version 2", err.message)
    }
}
