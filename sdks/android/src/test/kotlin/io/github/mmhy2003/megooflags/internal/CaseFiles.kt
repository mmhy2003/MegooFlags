// Loading the shared client cases (spec/client/README.md) and building
// OpenFeature values from them.
package io.github.mmhy2003.megooflags.internal

import dev.openfeature.kotlin.sdk.Value
import java.io.File
import kotlin.time.ExperimentalTime
import kotlin.time.Instant

/** The repository's spec/client directory, passed by the build as spec.dir. */
val clientDir: File = File(System.getProperty("spec.dir") ?: error("spec.dir is not set"))

class CaseFileException(
    message: String,
) : Exception(message)

/** loadCaseFile reads a case file and refuses any schema_version other than 1. */
fun loadCaseFile(file: File): Map<String, Any?> {
    val value = parseJson(file.readText())
    if (value !is Map<*, *>) {
        throw CaseFileException("${file.name}: not an object")
    }
    val version = value["schema_version"]
    if (version != 1L) {
        throw CaseFileException("${file.name}: unsupported schema_version ${version?.let(::writeCanonical) ?: "missing"}")
    }
    @Suppress("UNCHECKED_CAST")
    return value as Map<String, Any?>
}

/** toValue builds an OpenFeature value; {"$date": s} becomes an instant. */
@OptIn(ExperimentalTime::class)
fun toValue(v: Any?): Value =
    when (v) {
        null -> {
            Value.Null
        }

        is Boolean -> {
            Value.Boolean(v)
        }

        is Long -> {
            if (v in Int.MIN_VALUE..Int.MAX_VALUE) Value.Integer(v.toInt()) else Value.Long(v)
        }

        is Double -> {
            Value.Double(v)
        }

        is String -> {
            Value.String(v)
        }

        is List<*> -> {
            Value.List(v.map(::toValue))
        }

        is Map<*, *> -> {
            val date = v["\$date"]
            if (v.size == 1 && date is String) {
                Value.Instant(Instant.parse(date))
            } else {
                Value.Structure(v.entries.associate { (k, x) -> k as String to toValue(x) })
            }
        }

        else -> {
            error("not a JSON value: $v")
        }
    }

/** sameJson is JSON equality: numbers compare numerically (3 == 3.0). */
fun sameJson(
    a: Any?,
    b: Any?,
): Boolean =
    when {
        a is Number && b is Number -> a.toDouble() == b.toDouble()
        a is List<*> && b is List<*> -> a.size == b.size && a.indices.all { sameJson(a[it], b[it]) }
        a is Map<*, *> && b is Map<*, *> -> a.keys == b.keys && a.keys.all { sameJson(a[it], b[it]) }
        else -> a == b
    }
