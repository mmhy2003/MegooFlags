// Stored evaluate results mapped to OpenFeature resolution details (spec/client/README.md, resolution cases).
package io.github.mmhy2003.megooflags.internal

import dev.openfeature.kotlin.sdk.ProviderEvaluation
import dev.openfeature.kotlin.sdk.Reason
import dev.openfeature.kotlin.sdk.Value
import dev.openfeature.kotlin.sdk.exceptions.ErrorCode

/** 2^63: the first double above Long.MAX_VALUE; -2^63 is exact. */
private const val LONG_LIMIT = 9.223372036854775808e18

/** errorCodeOf maps a server error code to OpenFeature's; unknown codes are GENERAL. */
private fun errorCodeOf(code: String): ErrorCode =
    when (code) {
        "FLAG_NOT_FOUND" -> ErrorCode.FLAG_NOT_FOUND
        "PARSE_ERROR" -> ErrorCode.PARSE_ERROR
        "TYPE_MISMATCH" -> ErrorCode.TYPE_MISMATCH
        "TARGETING_KEY_MISSING" -> ErrorCode.TARGETING_KEY_MISSING
        "INVALID_CONTEXT" -> ErrorCode.INVALID_CONTEXT
        else -> ErrorCode.GENERAL
    }

private fun <T> failed(
    default: T,
    code: ErrorCode,
    message: String? = null,
): ProviderEvaluation<T> = ProviderEvaluation(default, null, Reason.ERROR.name, code, message)

/**
 * resolve reads [flag] from [stored] with [read], which returns null for a value of another type. Errors return
 * the default with reason ERROR; a null variant returns the default with the stored reason; values from the cache
 * have reason CACHED. No flag metadata.
 */
private inline fun <T : Any> resolve(
    stored: StoredResults?,
    flag: String,
    default: T,
    read: (Any?) -> T?,
): ProviderEvaluation<T> {
    if (stored == null) return failed(default, ErrorCode.PROVIDER_NOT_READY)
    val r = stored.results[flag] ?: return failed(default, ErrorCode.FLAG_NOT_FOUND)
    if (r.errorCode != null) return failed(default, errorCodeOf(r.errorCode), r.errorMessage)
    val variant = r.variant ?: return ProviderEvaluation(default, null, r.reason)
    val value = read(r.value) ?: return failed(default, ErrorCode.TYPE_MISMATCH)
    return ProviderEvaluation(value, variant, if (stored.source == Source.CACHE) Reason.CACHED.name else r.reason)
}

/** integral is [v] as a Long when it is a whole number within Long's range, else null. */
private fun integral(v: Any?): Long? =
    when (v) {
        is Long -> v
        is Double -> if (v == Math.floor(v) && v >= -LONG_LIMIT && v < LONG_LIMIT) v.toLong() else null
        else -> null
    }

internal fun resolveBoolean(
    stored: StoredResults?,
    flag: String,
    default: Boolean,
): ProviderEvaluation<Boolean> = resolve(stored, flag, default) { it as? Boolean }

internal fun resolveString(
    stored: StoredResults?,
    flag: String,
    default: String,
): ProviderEvaluation<String> = resolve(stored, flag, default) { it as? String }

/** Integral values within Int's range. */
internal fun resolveInt(
    stored: StoredResults?,
    flag: String,
    default: Int,
): ProviderEvaluation<Int> = resolve(stored, flag, default) { v -> integral(v)?.takeIf { it in Int.MIN_VALUE..Int.MAX_VALUE }?.toInt() }

/** Integral values within Long's range. */
internal fun resolveLong(
    stored: StoredResults?,
    flag: String,
    default: Long,
): ProviderEvaluation<Long> = resolve(stored, flag, default, ::integral)

/** Any number. */
internal fun resolveDouble(
    stored: StoredResults?,
    flag: String,
    default: Double,
): ProviderEvaluation<Double> = resolve(stored, flag, default) { (it as? Number)?.toDouble() }

/** Objects as Value.Structure, arrays as Value.List. */
internal fun resolveObject(
    stored: StoredResults?,
    flag: String,
    default: Value,
): ProviderEvaluation<Value> = resolve(stored, flag, default) { if (it is Map<*, *> || it is List<*>) toValue(it) else null }

/** toValue builds an OpenFeature value from a parsed JSON value; Longs within Int's range are Value.Integer. */
private fun toValue(v: Any?): Value =
    when (v) {
        null -> Value.Null
        is Boolean -> Value.Boolean(v)
        is Long -> if (v in Int.MIN_VALUE..Int.MAX_VALUE) Value.Integer(v.toInt()) else Value.Long(v)
        is Double -> Value.Double(v)
        is String -> Value.String(v)
        is List<*> -> Value.List(v.map(::toValue))
        is Map<*, *> -> Value.Structure(v.entries.associate { (k, x) -> k as String to toValue(x) })
        else -> throw IllegalArgumentException("not JSON: ${v::class.java.simpleName}")
    }
