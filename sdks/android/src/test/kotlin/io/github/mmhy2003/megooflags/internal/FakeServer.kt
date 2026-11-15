// A local HTTP/1.1 server for the evaluator, streamer and provider tests: scripted evaluate replies, controllable
// streams and accepted events.
// It is written on ServerSocket because com.sun.net.httpserver is not on the compile classpath of Android unit
// tests (they compile against android.jar). One request per connection, answered with Connection: close.
package io.github.mmhy2003.megooflags.internal

import io.github.mmhy2003.megooflags.MegooFlagsLogger
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** One request the server received; header names are lowercase. */
internal class Request(
    val method: String,
    val path: String,
    val headers: Map<String, String>,
    val body: String,
) {
    fun header(name: String): String? = headers[name.lowercase()]
}

/** A scripted reply: status, headers and body. */
internal class Reply(
    val status: Int,
    val body: String = "",
    val headers: Map<String, String> = emptyMap(),
)

/**
 * One stream connection. A connection replied with a non-200 [Reply] is closed at once; the default reply is an
 * open, chunked 200 stream the test writes to with [send] and closes with [end].
 */
internal class StreamConn(
    val request: Request,
) {
    private val commands = LinkedBlockingQueue<(OutputStream) -> Boolean>()
    private val answered = CountDownLatch(1)

    @Volatile
    var clientGone: Boolean = false
        private set

    fun header(name: String): String? = request.header(name)

    /** respond sends the 200 status of a held connection. */
    fun respond() {
        answered.countDown()
    }

    /** send writes [text] as one chunk. */
    fun send(text: String) {
        commands.put { out ->
            val bytes = text.toByteArray()
            out.write("${Integer.toHexString(bytes.size)}\r\n".toByteArray())
            out.write(bytes)
            out.write("\r\n".toByteArray())
            out.flush()
            true
        }
    }

    /** end closes the response with the last chunk. */
    fun end() {
        commands.put { out ->
            out.write("0\r\n\r\n".toByteArray())
            out.flush()
            false
        }
    }

    /** awaitClientClose keeps writing comments until a write fails: the client closed the connection. */
    fun awaitClientClose(timeoutMs: Long = 5_000): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (!clientGone && System.currentTimeMillis() < deadline) {
            send(":\n")
            Thread.sleep(10)
        }
        return clientGone
    }

    fun serve(
        out: OutputStream,
        hold: Boolean,
    ) {
        try {
            if (hold) answered.await(10, TimeUnit.SECONDS)
            out.write(
                (
                    "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n" +
                        "Connection: close\r\n\r\n"
                ).toByteArray(),
            )
            out.flush()
            while (true) {
                val command = commands.poll(10, TimeUnit.SECONDS) ?: break
                if (!command(out)) break
            }
        } catch (_: IOException) {
            clientGone = true
        }
    }

    fun release() {
        answered.countDown()
        commands.put { false }
    }
}

internal class FakeServer : AutoCloseable {
    private val server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    private val sockets = CopyOnWriteArrayList<Socket>()
    private val evaluateReplies = ConcurrentLinkedQueue<Reply>()
    private val streamReplies = ConcurrentLinkedQueue<Reply>()

    /** Every evaluate request, in order. */
    val requests: MutableList<Request> = CopyOnWriteArrayList()

    /** Every events request, in order; each is answered 202. */
    val events: MutableList<Request> = CopyOnWriteArrayList()

    /** When set, evaluate requests are answered only once it is counted down (or after 10 s). */
    @Volatile
    var evaluateGate: CountDownLatch? = null

    /** Every stream connection, in order. */
    val connections: MutableList<StreamConn> = CopyOnWriteArrayList()

    /** When set, a 200 stream sends its status only after [StreamConn.respond]. */
    @Volatile
    var holdStreams: Boolean = false

    val baseUrl: String = "http://127.0.0.1:${server.localPort}"

    val last: StreamConn get() = connections.last()

    init {
        thread(isDaemon = true, name = "fake-server") {
            while (true) {
                val socket =
                    try {
                        server.accept()
                    } catch (_: IOException) {
                        break
                    }
                sockets.add(socket)
                thread(isDaemon = true) { handle(socket) }
            }
        }
    }

    /** replyEvaluate queues the reply of the next evaluate request (default: 200 with no results). */
    fun replyEvaluate(
        status: Int,
        body: String = "",
        headers: Map<String, String> = emptyMap(),
    ) {
        evaluateReplies.add(Reply(status, body, headers))
    }

    /** replyStream queues the status of the next stream connection (default: an open 200 stream). */
    fun replyStream(
        status: Int,
        headers: Map<String, String> = emptyMap(),
    ) {
        streamReplies.add(Reply(status, headers = headers))
    }

    override fun close() {
        connections.forEach { it.release() }
        server.close()
        sockets.forEach { it.close() }
    }

    private fun handle(socket: Socket) {
        try {
            socket.use {
                val input = BufferedInputStream(it.getInputStream())
                val out = it.getOutputStream()
                val request = read(input) ?: return
                when (request.path) {
                    "/sdk/v1/evaluate" -> {
                        requests.add(request)
                        evaluateGate?.await(10, TimeUnit.SECONDS)
                        write(out, evaluateReplies.poll() ?: Reply(200, """{"results":[]}"""))
                    }

                    "/sdk/v1/stream" -> {
                        val conn = StreamConn(request)
                        connections.add(conn)
                        val reply = streamReplies.poll()
                        if (reply != null && reply.status != 200) write(out, reply) else conn.serve(out, holdStreams)
                    }

                    "/sdk/v1/events" -> {
                        events.add(request)
                        write(out, Reply(202))
                    }

                    else -> {
                        write(out, Reply(404))
                    }
                }
            }
        } catch (_: IOException) {
            // The client went away.
        }
    }

    private fun read(input: InputStream): Request? {
        val start = line(input) ?: return null
        val (method, path) = start.split(" ")
        val headers = mutableMapOf<String, String>()
        while (true) {
            val l = line(input) ?: return null
            if (l.isEmpty()) break
            val colon = l.indexOf(':')
            headers[l.substring(0, colon).trim().lowercase()] = l.substring(colon + 1).trim()
        }
        val length = headers["content-length"]?.toInt() ?: 0
        val body = ByteArray(length)
        var got = 0
        while (got < length) {
            val n = input.read(body, got, length - got)
            if (n < 0) return null
            got += n
        }
        return Request(method, path, headers, body.toString(Charsets.UTF_8))
    }

    private fun line(input: InputStream): String? {
        val buf = ByteArrayOutputStream()
        while (true) {
            val b = input.read()
            if (b < 0) return null
            if (b == '\n'.code) break
            buf.write(b)
        }
        return buf.toString("ISO-8859-1").removeSuffix("\r")
    }

    private fun write(
        out: OutputStream,
        reply: Reply,
    ) {
        val bytes = reply.body.toByteArray()
        val head =
            buildString {
                append("HTTP/1.1 ${reply.status} X\r\n")
                reply.headers.forEach { (k, v) -> append("$k: $v\r\n") }
                append("Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n")
            }
        out.write(head.toByteArray())
        out.write(bytes)
        out.flush()
    }
}

/** Keeps each log line as "<level> <message>". */
internal class LineLogger : MegooFlagsLogger {
    val lines: MutableList<String> = CopyOnWriteArrayList()

    override fun debug(message: String) {
        lines.add("debug $message")
    }

    override fun info(message: String) {
        lines.add("info $message")
    }

    override fun warn(message: String) {
        lines.add("warn $message")
    }

    override fun error(message: String) {
        lines.add("error $message")
    }
}
