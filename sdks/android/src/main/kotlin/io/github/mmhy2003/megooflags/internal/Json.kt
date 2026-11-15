// JSON as plain Kotlin values: null, Boolean, Long, Double, String, List<Any?> and Map<String, Any?>. Parsing
// uses org.json (the platform's on Android); writing is the canonical form of spec/client/README.md.
package io.github.mmhy2003.megooflags.internal

import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import org.json.JSONTokener
import java.math.BigInteger

/** 2^53: integral doubles below it in magnitude are written as integers. */
private const val EXACT_LIMIT = 9_007_199_254_740_992.0

/**
 * parseJson reads one JSON value: objects become maps, arrays lists, whole numbers that fit a Long Longs, other
 * numbers Doubles. Invalid text, or text after the value, throws IllegalArgumentException.
 */
internal fun parseJson(text: String): Any? {
    try {
        val tokener = JSONTokener(text)
        val value = tokener.nextValue()
        if (tokener.nextClean() != 0.toChar()) {
            throw IllegalArgumentException("invalid JSON: text after the value")
        }
        return fromOrgJson(value)
    } catch (e: JSONException) {
        throw IllegalArgumentException("invalid JSON: ${e.message}", e)
    }
}

private fun fromOrgJson(v: Any?): Any? =
    when {
        v == null || v === JSONObject.NULL -> {
            null
        }

        v is JSONObject -> {
            val out = LinkedHashMap<String, Any?>()
            for (k in v.keys()) {
                out[k] = fromOrgJson(v.get(k))
            }
            out
        }

        v is JSONArray -> {
            List(v.length()) { fromOrgJson(v.get(it)) }
        }

        v is Int -> {
            v.toLong()
        }

        v is Long -> {
            v
        }

        v is BigInteger -> {
            if (v.bitLength() < 64) v.toLong() else v.toDouble()
        }

        v is Number -> {
            v.toDouble()
        }

        v is Boolean || v is String -> {
            v
        }

        else -> {
            throw IllegalArgumentException("invalid JSON: unexpected ${v::class.java.simpleName}")
        }
    }

/**
 * sameJson is JSON equality between parsed values: numbers compare numerically (1 == 1.0), but true != 1 and
 * "1" != 1; lists element by element, maps key by key, {} is not [].
 */
internal fun sameJson(
    a: Any?,
    b: Any?,
): Boolean =
    when {
        a is Number && b is Number -> a.toDouble() == b.toDouble()
        a is List<*> && b is List<*> -> a.size == b.size && a.indices.all { sameJson(a[it], b[it]) }
        a is Map<*, *> && b is Map<*, *> -> a.keys == b.keys && a.keys.all { sameJson(a[it], b[it]) }
        else -> a == b
    }

/**
 * writeCanonical writes [value] without whitespace, object keys sorted by Unicode code point, strings escaping
 * only `"`, `\` and control characters, and integral numbers (|x| < 2^53) as integers. Other doubles use
 * Double.toString; NaN and infinities are null. A value that is not JSON throws IllegalArgumentException.
 */
internal fun writeCanonical(value: Any?): String = StringBuilder().also { write(value, it) }.toString()

private fun write(
    value: Any?,
    out: StringBuilder,
) {
    when (value) {
        null -> {
            out.append("null")
        }

        is Boolean -> {
            out.append(value)
        }

        is Long, is Int, is Short, is Byte -> {
            out.append(value)
        }

        is Double -> {
            writeDouble(value, out)
        }

        is Float -> {
            writeDouble(value.toDouble(), out)
        }

        is String -> {
            writeString(value, out)
        }

        is List<*> -> {
            out.append('[')
            value.forEachIndexed { i, v ->
                if (i > 0) out.append(',')
                write(v, out)
            }
            out.append(']')
        }

        is Map<*, *> -> {
            val keys = value.keys.map { it as? String ?: throw IllegalArgumentException("not JSON: a non-string key") }
            out.append('{')
            keys.sortedWith(::compareCodePoints).forEachIndexed { i, k ->
                if (i > 0) out.append(',')
                writeString(k, out)
                out.append(':')
                write(value[k], out)
            }
            out.append('}')
        }

        else -> {
            throw IllegalArgumentException("not JSON: ${value::class.java.simpleName}")
        }
    }
}

private fun writeDouble(
    d: Double,
    out: StringBuilder,
) {
    when {
        d.isNaN() || d.isInfinite() -> out.append("null")
        exactInteger(d) != null -> out.append(exactInteger(d))
        else -> out.append(d.toString())
    }
}

/** exactInteger is d as a Long when it is integral and |d| < 2^53, else null. */
internal fun exactInteger(d: Double): Long? = if (d == Math.floor(d) && Math.abs(d) < EXACT_LIMIT) d.toLong() else null

/** compareCodePoints orders strings by Unicode code point; String.compareTo compares UTF-16 units. */
private fun compareCodePoints(
    a: String,
    b: String,
): Int {
    var i = 0
    var j = 0
    while (i < a.length && j < b.length) {
        val x = a.codePointAt(i)
        val y = b.codePointAt(j)
        if (x != y) {
            return x.compareTo(y)
        }
        i += Character.charCount(x)
        j += Character.charCount(y)
    }
    return (a.length - i).compareTo(b.length - j)
}

private fun writeString(
    s: String,
    out: StringBuilder,
) {
    out.append('"')
    for (c in s) {
        when {
            c == '"' -> out.append("\\\"")
            c == '\\' -> out.append("\\\\")
            c == '\b' -> out.append("\\b")
            c == '\u000c' -> out.append("\\f")
            c == '\n' -> out.append("\\n")
            c == '\r' -> out.append("\\r")
            c == '\t' -> out.append("\\t")
            c < ' ' -> out.append("\\u").append(Integer.toHexString(c.code).padStart(4, '0'))
            else -> out.append(c)
        }
    }
    out.append('"')
}
