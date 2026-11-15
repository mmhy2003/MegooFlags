// A text/event-stream parser over a byte stream (the stream response body).
package io.github.mmhy2003.megooflags.internal

import java.io.ByteArrayOutputStream

/** One dispatched server-sent event. name is "message" by default. */
internal data class SseEvent(
    val name: String,
    val data: String,
    val id: String,
)

private const val LF: Byte = 0x0a
private const val CR: Byte = 0x0d

/**
 * SseParser parses the chunks fed to it. Lines are split on the LF byte before decoding, so a character split
 * across chunks decodes whole, and a line of any length is accepted; a trailing CR is removed and a BOM is kept as
 * a character. An exception thrown by onEvent is passed through feed and stops reading. finish discards an
 * unterminated last line or event.
 */
internal class SseParser(
    /** Runs for every line, comments included (the dead timer). */
    private val onLine: () -> Unit,
    /** Receives a valid retry field, in milliseconds. */
    private val onRetry: (Int) -> Unit,
    /** Receives each event. */
    private val onEvent: (SseEvent) -> Unit,
) {
    private val pending = ByteArrayOutputStream()
    private var name = ""
    private var id = ""
    private val data = mutableListOf<String>()
    private var seen = false

    /** feed parses [length] bytes of [chunk] from [offset]. */
    fun feed(
        chunk: ByteArray,
        offset: Int = 0,
        length: Int = chunk.size - offset,
    ) {
        val stop = offset + length
        var start = offset
        while (true) {
            var end = start
            while (end < stop && chunk[end] != LF) {
                end++
            }
            if (end == stop) {
                pending.write(chunk, start, stop - start)
                return
            }
            val bytes: ByteArray
            val from: Int
            var to: Int
            if (pending.size() > 0) {
                pending.write(chunk, start, end - start)
                bytes = pending.toByteArray()
                pending.reset()
                from = 0
                to = bytes.size
            } else {
                bytes = chunk
                from = start
                to = end
            }
            if (to > from && bytes[to - 1] == CR) {
                to--
            }
            start = end + 1
            line(String(bytes, from, to - from, Charsets.UTF_8))
        }
    }

    /** finish ends the stream: an unterminated line or event is dropped. */
    fun finish() {
        pending.reset()
        reset()
    }

    private fun line(line: String) {
        onLine()
        if (line.isEmpty()) {
            dispatch()
            return
        }
        if (line.startsWith(":")) {
            return
        }
        var field = line
        var value = ""
        val colon = line.indexOf(':')
        if (colon >= 0) {
            field = line.substring(0, colon)
            value = line.substring(colon + 1).removePrefix(" ")
        }
        when (field) {
            "event" -> {
                name = value
            }

            "data" -> {
                data.add(value)
                seen = true
            }

            "id" -> {
                if (!value.contains('\u0000')) id = value
            }

            "retry" -> {
                if (value.isNotEmpty() && value.all { it in '0'..'9' }) {
                    value.toIntOrNull()?.let(onRetry)
                }
            }
        }
    }

    private fun dispatch() {
        val event = if (seen) SseEvent(name.ifEmpty { "message" }, data.joinToString("\n"), id) else null
        reset()
        if (event != null) {
            onEvent(event)
        }
    }

    private fun reset() {
        name = ""
        id = ""
        data.clear()
        seen = false
    }
}
