// The last-known results on the device, one file per SDK key. A broken file or directory never fails an
// evaluation: reads return null and write failures are logged.
package io.github.mmhy2003.megooflags.internal

import io.github.mmhy2003.megooflags.MegooFlagsLogger
import java.io.File
import java.io.IOException

private const val ENTRY_VERSION = 1L

/** A cache failure with a message safe to log (no path, no key). */
private class CacheException(
    why: String,
) : IOException(why)

/**
 * Cache keeps the entry of [sdkKey] in [dir], created on the first write; a null [dir] means no cache (no reads, no
 * writes). [now] is the time written as saved_at, in epoch milliseconds.
 */
internal class Cache(
    sdkKey: String,
    dir: File?,
    private val logger: MegooFlagsLogger,
    private val now: () -> Long = System::currentTimeMillis,
) {
    /** The entry file; its name never contains the SDK key itself. */
    val file: File? = dir?.let { File(it, "megooflags-${sha256Hex(sdkKey).take(16)}.json") }

    /**
     * load returns the entry for [contextHash] with source cache, or null when it is missing, unreadable, of another
     * version or another context. The entry does not record the targeting key: the caller knows it from the context
     * whose hash matched.
     */
    fun load(contextHash: String): StoredResults? {
        val f = file ?: return null
        if (!f.exists()) {
            return null
        }
        val text =
            try {
                f.readText()
            } catch (e: IOException) {
                logger.debug("megooflags: cache read failed: ${reason(e)}")
                return null
            }
        return try {
            entry(text, contextHash)
        } catch (e: IllegalArgumentException) {
            logger.debug("megooflags: cache entry ignored: ${e.message}")
            null
        }
    }

    private fun entry(
        text: String,
        contextHash: String,
    ): StoredResults {
        fun fail(why: String): Nothing = throw IllegalArgumentException(why)
        val o =
            try {
                parseJson(text)
            } catch (_: IllegalArgumentException) {
                fail("not valid JSON")
            } as? Map<*, *> ?: fail("not an object")
        if (o["v"] != ENTRY_VERSION) fail("version ${o["v"] ?: "missing"}")
        if (o["context_hash"] != contextHash) fail("another context")
        if (!o.containsKey("etag")) fail("etag: missing")
        val etag = o["etag"]
        if (etag != null && etag !is String) fail("etag: not a string or null")
        val results = parseResults(text, "invalid entry")
        return StoredResults(results, etag as String?, Source.CACHE, contextHash, null)
    }

    /** save writes [s] as the entry: to a temporary file first, then renamed over the entry. A failure is a warning. */
    fun save(s: StoredResults) {
        val f = file ?: return
        val entry =
            mapOf(
                "v" to ENTRY_VERSION,
                "context_hash" to s.contextHash,
                "etag" to s.etag,
                "results" to
                    s.results.values.sortedBy { it.key }.map {
                        mapOf(
                            "key" to it.key,
                            "value" to it.value,
                            "variant" to it.variant,
                            "reason" to it.reason,
                            "error_code" to it.errorCode,
                            "error_message" to it.errorMessage,
                        )
                    },
                "saved_at" to formatRfc3339Millis(now()),
            )
        var tmp: File? = null
        try {
            val dir = f.parentFile!!
            if (!dir.isDirectory && !dir.mkdirs()) throw CacheException("cannot create the directory")
            tmp = File.createTempFile("megooflags-", ".tmp", dir)
            tmp.writeText(writeCanonical(entry))
            if (!tmp.renameTo(f)) throw CacheException("cannot replace the entry")
            tmp = null
        } catch (e: IOException) {
            logger.warn("megooflags: cache write failed: ${reason(e)}")
        } catch (e: SecurityException) {
            logger.warn("megooflags: cache write failed: ${reason(e)}")
        } finally {
            tmp?.delete()
        }
    }

    /** remove deletes the entry; a failure other than a missing file is a warning. */
    fun remove() {
        val f = file ?: return
        if (!f.delete() && f.exists()) {
            logger.warn("megooflags: cache remove failed")
        }
    }
}

/** reason describes a failure for a log line: the message of a CacheException, else the class (never a path). */
private fun reason(e: Exception): String = if (e is CacheException) e.message!! else e::class.java.simpleName
