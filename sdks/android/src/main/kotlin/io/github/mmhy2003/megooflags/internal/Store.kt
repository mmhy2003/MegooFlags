// The evaluated results the provider reads from, their parsing, and what changed between two of them. The store
// holds one StoredResults or none and swaps it atomically.
package io.github.mmhy2003.megooflags.internal

import java.util.concurrent.atomic.AtomicReference

/** One evaluated flag, as POST /sdk/v1/evaluate returns it; [value] is a parsed JSON value (see parseJson). */
internal data class EvaluateResult(
    val key: String,
    val value: Any?,
    val variant: String?,
    val reason: String,
    val errorCode: String?,
    val errorMessage: String?,
)

/**
 * parseResults reads an evaluate response body ({"results": [...]}) and returns its results. It throws
 * IllegalArgumentException("<prefix>: <path>: <why>"); extra members are dropped, a missing error_message is null
 * and duplicate keys are refused.
 */
internal fun parseResults(
    text: String,
    prefix: String,
): List<EvaluateResult> {
    fun fail(why: String): Nothing = throw IllegalArgumentException("$prefix: $why")
    val body =
        try {
            parseJson(text)
        } catch (_: IllegalArgumentException) {
            fail("not valid JSON")
        }
    if (body !is Map<*, *>) fail("not an object")
    if (!body.containsKey("results")) fail("results: missing")
    val items = body["results"] as? List<*> ?: fail("results: not an array")
    val seen = HashSet<String>()
    return items.mapIndexed { i, item ->
        val path = "results[$i]"
        if (item !is Map<*, *>) fail("$path: not an object")

        fun member(name: String): Any? {
            if (!item.containsKey(name)) fail("$path.$name: missing")
            return item[name]
        }

        fun nullableString(
            name: String,
            v: Any?,
        ): String? = if (v == null || v is String) v as String? else fail("$path.$name: not a string or null")

        val key = member("key") as? String ?: fail("$path.key: not a string")
        val value = member("value")
        val variant = nullableString("variant", member("variant"))
        val reason = member("reason") as? String ?: fail("$path.reason: not a string")
        val errorCode = nullableString("error_code", member("error_code"))
        val errorMessage = nullableString("error_message", item["error_message"])
        if (!seen.add(key)) fail("$path.key: duplicate key")
        EvaluateResult(key, value, variant, reason, errorCode, errorMessage)
    }
}

/** Where the stored results came from. */
internal enum class Source {
    BOOTSTRAP,
    CACHE,
    NETWORK,
}

internal data class StoredResults(
    val results: Map<String, EvaluateResult>,
    val etag: String?,
    val source: Source,
    val contextHash: String,
    val targetingKey: String?,
) {
    /** Keys [results] by flag key; keys are unique (parseResults refuses duplicates), a later one would win. */
    constructor(
        results: List<EvaluateResult>,
        etag: String?,
        source: Source,
        contextHash: String,
        targetingKey: String?,
    ) : this(results.associateBy { it.key }, etag, source, contextHash, targetingKey)
}

internal class Store {
    private val current = AtomicReference<StoredResults?>(null)

    fun get(): StoredResults? = current.get()

    fun set(s: StoredResults?) {
        current.set(s)
    }
}

/** changedFlags lists, sorted, the keys whose value (JSON equality) or variant differs, plus the one-sided keys. */
internal fun changedFlags(
    old: StoredResults,
    new: StoredResults,
): List<String> {
    val changed =
        old.results
            .filter { (key, a) ->
                val b = new.results[key]
                b == null || a.variant != b.variant || !sameJson(a.value, b.value)
            }.keys + new.results.keys.filter { it !in old.results }
    return changed.sorted()
}
