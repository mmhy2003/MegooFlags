// The language-neutral context cases every client SDK must pass
// (spec/client/README.md).
package io.github.mmhy2003.megooflags.internal

import dev.openfeature.kotlin.sdk.EvaluationContext
import dev.openfeature.kotlin.sdk.ImmutableContext
import dev.openfeature.kotlin.sdk.Value
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import kotlin.time.ExperimentalTime
import kotlin.time.Instant

/** toContext builds the case's context: targetingKey is the targeting key, every other member an attribute. */
private fun toContext(o: Map<*, *>): EvaluationContext {
    val attributes = o.filterKeys { it != "targetingKey" }.entries.associate { (k, v) -> k as String to toValue(v) }
    return ImmutableContext(o["targetingKey"] as? String ?: "", attributes)
}

class SharedContextTest {
    @get:Rule
    val tmp = TemporaryFolder()

    @Test
    fun sharedContextCases() {
        val tests = loadCaseFile(File(clientDir, "context.json"))["tests"] as List<*>
        assertTrue(tests.isNotEmpty())
        for (test in tests) {
            val c = test as Map<*, *>
            val name = "context::${c["name"]}"
            val body = toRequestContext(toContext(c["context"] as Map<*, *>))
            assertTrue("$name: body ${writeCanonical(body)} != ${writeCanonical(c["body"])}", sameJson(body, c["body"]))
            assertEquals(name, c["canonical"], writeCanonical(body))
            assertEquals(name, c["context_hash"], contextHash(body))
        }
    }

    @Test
    fun refusesSchemaVersion2() {
        val file = tmp.newFile("case.json")
        file.writeText("""{"schema_version": 2, "kind": "context", "tests": []}""")
        val err = assertThrows(CaseFileException::class.java) { loadCaseFile(file) }
        assertEquals("case.json: unsupported schema_version 2", err.message)
    }

    @OptIn(ExperimentalTime::class)
    @Test
    fun buildsDatesFromDateObjects() {
        val ctx = toContext(mapOf("a" to listOf(mapOf("\$date" to "2026-11-10T09:15:02Z"))))
        assertEquals(
            Value.List(listOf(Value.Instant(Instant.fromEpochMilliseconds(1_794_302_102_000)))),
            ctx.getValue("a"),
        )
        assertEquals("", ctx.getTargetingKey())
    }
}
