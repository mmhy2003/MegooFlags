// Deduplicated exposures waiting to be sent to POST /sdk/v1/events. Recording runs on the read path, so it never
// does I/O and only holds a lock briefly.
package io.github.mmhy2003.megooflags.internal

import io.github.mmhy2003.megooflags.MegooFlagsLogger

/** An exposure records that a context saw a variant; [context] is the request context, with sendContext only. */
internal data class Exposure(
    val flagKey: String,
    val variant: String,
    val reason: String,
    /** RFC 3339 UTC with milliseconds. */
    val timestamp: String,
    val context: Map<String, Any?>?,
) {
    /** toJson is the item of the events body; context only when set. */
    fun toJson(): Map<String, Any?> =
        buildMap {
            put("flag_key", flagKey)
            put("variant", variant)
            put("reason", reason)
            put("timestamp", timestamp)
            if (context != null) put("context", context)
        }
}

/** The buffered count that asks for an immediate flush, and the most items one events request carries. */
internal const val BATCH_SIZE: Int = 1000

/** Exposures is thread-safe; [now] is the clock of the timestamps, in epoch milliseconds. */
internal class Exposures(
    private val sendContext: Boolean,
    private val logger: MegooFlagsLogger,
    private val now: () -> Long = System::currentTimeMillis,
    maxKeys: Int = 10_000,
    private val maxBuffer: Int = 10_000,
) {
    private val maxKeys = maxOf(maxKeys, 1)
    private val lock = Any()

    /** The dedup keys, least recently seen first; a read moves a key to the end. */
    private val seen =
        object : LinkedHashMap<String, Unit>(16, 0.75f, true) {
            override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Unit>?): Boolean = size > this@Exposures.maxKeys
        }
    private val buffer = ArrayDeque<Exposure>()
    private var warned = false
    private var fullSignalled = false

    /** onFull is called, outside the lock, when the buffer reaches 1000 items; once until the next drain. */
    @Volatile
    var onFull: (() -> Unit)? = null

    val size: Int get() = synchronized(lock) { buffer.size }

    /**
     * record buffers an exposure unless the same targeting key saw this flag and variant in the current UTC hour.
     * A full buffer drops it, warning once until the next drain.
     */
    fun record(
        flag: String,
        variant: String,
        reason: String,
        body: Map<String, Any?>,
    ) {
        val timestamp = formatRfc3339Millis(now())
        val key = "${targetingKeyOf(body) ?: ""}|$flag|$variant|${timestamp.take(13)}"
        var warn = false
        var full = false
        synchronized(lock) {
            if (seen[key] != null) return
            if (buffer.size >= maxBuffer) {
                warn = !warned
                warned = true
            } else {
                seen[key] = Unit
                buffer.addLast(Exposure(flag, variant, reason, timestamp, if (sendContext) body else null))
                if (buffer.size >= BATCH_SIZE && !fullSignalled) {
                    fullSignalled = true
                    full = true
                }
            }
        }
        if (warn) warnFull()
        if (full) onFull?.invoke()
    }

    /** drain removes and returns up to [max] of the oldest exposures. */
    fun drain(max: Int): List<Exposure> =
        synchronized(lock) {
            warned = false
            fullSignalled = false
            List(minOf(max, buffer.size)) { buffer.removeFirst() }
        }

    /** restore puts items that could not be sent back in front of the buffer; the newest over the bound are dropped. */
    fun restore(items: List<Exposure>) {
        val warn =
            synchronized(lock) {
                for (i in items.indices.reversed()) buffer.addFirst(items[i])
                if (buffer.size <= maxBuffer) return
                while (buffer.size > maxBuffer) buffer.removeLast()
                !warned.also { warned = true }
            }
        if (warn) warnFull()
    }

    private fun warnFull() {
        logger.warn("megooflags: the exposure buffer is full ($maxBuffer); dropping new exposures")
    }
}
