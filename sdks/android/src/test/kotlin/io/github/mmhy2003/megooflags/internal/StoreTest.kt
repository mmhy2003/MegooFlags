package io.github.mmhy2003.megooflags.internal

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test

private fun result(
    key: String,
    value: Any?,
    variant: String? = "v",
): EvaluateResult = EvaluateResult(key, value, variant, "STATIC", null, null)

private fun stored(vararg results: EvaluateResult): StoredResults =
    StoredResults(results.toList(), etag = "e", source = Source.NETWORK, contextHash = "h", targetingKey = "u")

class StoreTest {
    @Test
    fun startsEmptyAndSwapsTheStoredResults() {
        val store = Store()
        assertNull(store.get())
        val s = stored(result("a", 1L))
        store.set(s)
        assertSame(s, store.get())
        store.set(null)
        assertNull(store.get())
    }

    @Test
    fun keysResultsByFlagKey() {
        val a = result("a", 1L)
        val b = result("b", 2L)
        assertEquals(mapOf("a" to a, "b" to b), stored(a, b).results)
    }

    @Test
    fun changedFlagsListsValueAndVariantChangesAndOneSidedKeys() {
        val old =
            stored(
                result("same", mapOf("x" to listOf(1L))),
                result("number", 1L),
                result("value", "a"),
                result("variant", true, "on"),
                result("gone", 1L),
            )
        val new =
            stored(
                result("same", mapOf("x" to listOf(1.0))),
                result("number", 1.0),
                result("value", "b"),
                result("variant", true, "yes"),
                result("added", 1L),
            )
        assertEquals(listOf("added", "gone", "value", "variant"), changedFlags(old, new))
        assertEquals(listOf<String>(), changedFlags(old, old))
    }

    @Test
    fun sameJsonComparesNumbersByValueOnly() {
        assertEquals(true, sameJson(1L, 1.0))
        assertEquals(false, sameJson(true, 1L))
        assertEquals(false, sameJson("1", 1L))
        assertEquals(false, sameJson(mapOf<String, Any?>(), listOf<Any?>()))
        assertEquals(false, sameJson(listOf(1L), listOf(1L, 2L)))
        assertEquals(false, sameJson(mapOf("a" to null), mapOf("b" to null)))
        assertEquals(true, sameJson(null, null))
    }
}
