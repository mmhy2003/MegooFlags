package io.github.mmhy2003.megooflags.internal

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Test

private data class Seen(
    var lines: Int = 0,
    val retries: MutableList<Int> = mutableListOf(),
    val events: MutableList<SseEvent> = mutableListOf(),
)

private fun recorder(seen: Seen): SseParser =
    SseParser(
        onLine = { seen.lines++ },
        onRetry = { seen.retries.add(it) },
        onEvent = { seen.events.add(it) },
    )

private fun readBytes(parts: List<ByteArray>): Seen {
    val seen = Seen()
    val parser = recorder(seen)
    parts.forEach { parser.feed(it) }
    parser.finish()
    return seen
}

private fun read(vararg parts: String): Seen = readBytes(parts.map { it.toByteArray() })

private fun event(
    name: String,
    data: String,
    id: String = "",
) = SseEvent(name, data, id)

class SseParserTest {
    @Test
    fun cases() {
        val cases =
            listOf(
                Triple(
                    "named event with id",
                    "event: put\nid: 42\ndata: {\"flags\":{}}\n\n",
                    4 to listOf(event("put", "{\"flags\":{}}", "42")),
                ),
                Triple("default name", "data: x\n\n", 2 to listOf(event("message", "x"))),
                Triple("multi-line data", "data: a\ndata: b\ndata:c\n\n", 4 to listOf(event("message", "a\nb\nc"))),
                Triple("CRLF endings", "event: put\r\nid: 7\r\ndata: x\r\n\r\n", 4 to listOf(event("put", "x", "7"))),
                Triple("unknown event name passed through", "event: mystery\ndata: 1\n\n", 3 to listOf(event("mystery", "1"))),
                Triple("unterminated last event discarded", "data: a\n\nevent: put\ndata: b\n", 4 to listOf(event("message", "a"))),
                Triple("unterminated last line discarded", "data: a\n\ndata: b", 2 to listOf(event("message", "a"))),
                Triple(
                    "state resets after dispatch",
                    "event: put\nid: 1\ndata: a\n\ndata: b\n\n",
                    6 to listOf(event("put", "a", "1"), event("message", "b")),
                ),
                Triple("empty line without data dispatches nothing", "event: put\n\ndata: a\n\n", 4 to listOf(event("message", "a"))),
                Triple("id with NUL ignored", "id: a\u0000b\ndata: x\n\n", 3 to listOf(event("message", "x"))),
                Triple("field without colon has an empty value", "data\ndata\n\n", 3 to listOf(event("message", "\n"))),
                Triple(
                    "only one leading space removed, unknown field ignored",
                    "foo: bar\ndata:  two\n\n",
                    3 to listOf(event("message", " two")),
                ),
                Triple("colon inside the value kept", "data: a: b\n\n", 2 to listOf(event("message", "a: b"))),
            )
        for ((name, input, want) in cases) {
            val seen = read(input)
            assertEquals(name, want.second, seen.events)
            assertEquals(name, want.first, seen.lines)
            assertEquals(name, emptyList<Int>(), seen.retries)
        }
    }

    @Test
    fun commentsCallOnLineOnly() {
        assertEquals(Seen(lines = 3), read(": ping\n:\n\n"))
    }

    @Test
    fun aValidRetryCallsOnRetryInMilliseconds() {
        assertEquals(Seen(lines = 2, retries = mutableListOf(3000)), read("retry: 3000\n\n"))
    }

    @Test
    fun anInvalidRetryIsIgnored() {
        for (input in listOf("retry: x\n", "retry: -1\n", "retry: 1.5\n", "retry:\n", "retry: +5\n", "retry: \u0663\n")) {
            assertEquals(input, emptyList<Int>(), read(input).retries)
        }
    }

    @Test
    fun anOverlongRetryIsIgnored() {
        assertEquals(emptyList<Int>(), read("retry: 99999999999999999999999\n").retries)
    }

    @Test
    fun readsA1MiBDataLine() {
        val big = "x".repeat(1 shl 20)
        val parts = mutableListOf("event: put\ndata: ")
        for (i in big.indices step 16_384) {
            parts.add(big.substring(i, minOf(i + 16_384, big.length)))
        }
        parts.add("\n\n")
        val seen = read(*parts.toTypedArray())
        assertEquals(1, seen.events.size)
        assertEquals(big, seen.events[0].data)
    }

    @Test
    fun decodesAMultiByteCharacterSplitAcrossTwoChunks() {
        val bytes = "data: é€😀\n\n".toByteArray()
        val at = bytes.indexOf(0xe2.toByte()) + 1
        val seen = readBytes(listOf(bytes.copyOfRange(0, at), bytes.copyOfRange(at, bytes.size)))
        assertEquals(listOf(event("message", "é€😀")), seen.events)
    }

    @Test
    fun splitsLinesThatArriveInOneChunkOrByteByByte() {
        val text = "event: put\r\ndata: a\r\n\r\nevent: put\ndata: b\n\n"
        val one = read(text)
        val split = readBytes(text.toByteArray().map { byteArrayOf(it) })
        assertEquals(one, split)
        assertEquals(listOf("a", "b"), one.events.map { it.data })
    }

    @Test
    fun readsOnlyTheGivenRangeOfABuffer() {
        val seen = Seen()
        val parser = recorder(seen)
        val buffer = "xxdata: a\n\nyy".toByteArray()
        parser.feed(buffer, 2, buffer.size - 4)
        assertEquals(listOf(event("message", "a")), seen.events)
    }

    @Test
    fun anErrorThrownByOnEventStopsReading() {
        val names = mutableListOf<String>()
        val stop = IllegalStateException("stop")
        val parser =
            SseParser(onLine = {}, onRetry = {}, onEvent = {
                names.add(it.name)
                throw stop
            })
        val thrown =
            assertThrows(IllegalStateException::class.java) {
                parser.feed("event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n".toByteArray())
            }
        assertSame(stop, thrown)
        assertEquals(listOf("revoked"), names)
    }

    @Test
    fun finishDiscardsAPartialLineAndEvent() {
        val seen = Seen()
        val parser = recorder(seen)
        parser.feed("data: a\ndata: b".toByteArray())
        parser.finish()
        parser.feed("data: c\n\n".toByteArray())
        assertEquals(listOf(event("message", "c")), seen.events)
    }
}
