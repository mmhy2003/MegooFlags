// The evaluate request context: the OpenFeature context mapped to JSON, its canonical form and the hash that keys
// the cache (spec/client/README.md).
package io.github.mmhy2003.megooflags.internal

import dev.openfeature.kotlin.sdk.EvaluationContext
import dev.openfeature.kotlin.sdk.Value
import java.security.MessageDigest
import kotlin.time.ExperimentalTime

/**
 * toRequestContext copies every attribute as JSON. A non-empty targeting key is written as key, replacing a key
 * attribute; instants become UTC RFC 3339 with milliseconds; integral doubles become integers.
 */
internal fun toRequestContext(ctx: EvaluationContext?): Map<String, Any?> {
    if (ctx == null) {
        return emptyMap()
    }
    val body = LinkedHashMap<String, Any?>()
    for ((k, v) in ctx.asMap()) {
        body[k] = toJson(v)
    }
    val targetingKey = ctx.getTargetingKey()
    if (targetingKey.isNotEmpty()) {
        body["key"] = targetingKey
    }
    return body
}

@OptIn(ExperimentalTime::class)
private fun toJson(v: Value): Any? =
    when (v) {
        is Value.Boolean -> v.boolean
        is Value.String -> v.string
        is Value.Integer -> v.integer.toLong()
        is Value.Long -> v.long
        is Value.Double -> exactInteger(v.double) ?: v.double
        is Value.Instant -> formatRfc3339Millis(v.instant.toEpochMilliseconds())
        is Value.List -> v.list.map(::toJson)
        is Value.Structure -> v.structure.mapValues { toJson(it.value) }
        is Value.Null -> null
    }

/** contextHash is the lowercase hex SHA-256 of the UTF-8 canonical JSON of [body]. */
internal fun contextHash(body: Map<String, Any?>): String =
    MessageDigest
        .getInstance("SHA-256")
        .digest(writeCanonical(body).toByteArray(Charsets.UTF_8))
        .joinToString("") { Integer.toHexString((it.toInt() and 0xff) or 0x100).substring(1) }

/** targetingKeyOf is body.key when it is a string, else null. */
internal fun targetingKeyOf(body: Map<String, Any?>): String? = body["key"] as? String

private const val MILLIS_PER_DAY = 86_400_000L

/**
 * formatRfc3339Millis writes [epochMillis] as yyyy-MM-dd'T'HH:mm:ss.SSS'Z' in UTC, by hand: java.time needs API
 * 26 and SimpleDateFormat is not thread-safe.
 */
internal fun formatRfc3339Millis(epochMillis: Long): String {
    val days = Math.floorDiv(epochMillis, MILLIS_PER_DAY)
    val ms = Math.floorMod(epochMillis, MILLIS_PER_DAY)
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm), with March-based years.
    val z = days + 719_468
    val era = Math.floorDiv(z, 146_097L)
    val doe = z - era * 146_097
    val yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365
    val doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
    val mp = (5 * doy + 2) / 153
    val day = doy - (153 * mp + 2) / 5 + 1
    val month = if (mp < 10) mp + 3 else mp - 9
    val year = yoe + era * 400 + if (month <= 2) 1 else 0
    return buildString {
        append(year.toString().padStart(4, '0')).append('-')
        append(month.toString().padStart(2, '0')).append('-')
        append(day.toString().padStart(2, '0')).append('T')
        append((ms / 3_600_000).toString().padStart(2, '0')).append(':')
        append((ms / 60_000 % 60).toString().padStart(2, '0')).append(':')
        append((ms / 1_000 % 60).toString().padStart(2, '0')).append('.')
        append((ms % 1_000).toString().padStart(3, '0')).append('Z')
    }
}
