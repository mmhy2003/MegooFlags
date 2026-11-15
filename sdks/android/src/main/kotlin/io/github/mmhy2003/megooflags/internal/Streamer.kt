// The SSE client for GET /sdk/v1/stream: follows `changed` events and reconnects with backoff until it is paused,
// stopped or the key is refused.
package io.github.mmhy2003.megooflags.internal

import io.github.mmhy2003.megooflags.MegooFlagsLogger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException

/** A connection that lasted this long, in milliseconds, resets the backoff. */
private const val STABLE_AFTER_MS = 60_000L

/** The size of one read of the stream body. */
private const val CHUNK_BYTES = 8 * 1024

/** What the streamer reports. The hooks run on the streamer's scope and must return quickly. */
internal class StreamerHooks(
    /** Runs when the server answers 200. */
    val onConnected: () -> Unit,
    /** Receives the id of each `changed` event. */
    val onChanged: (String) -> Unit,
    /** Runs once when the key is refused (Fatal or Revoked); the streamer is done. */
    val onFatal: (SdkException) -> Unit,
    /** Receives the failures since the last 200 (2 means: start polling) and the last error. */
    val onFailure: (Int, Throwable) -> Unit,
)

/** StreamEndedException is a stream the server closed. */
internal class StreamEndedException : IOException("megooflags: stream ended")

/**
 * Streamer runs its loop on [scope] and the blocking calls of [client] on [io]. [now] is the clock of the 60 s
 * backoff reset, in milliseconds; [delayFn] waits between connections. start, pause, resume and stop may be called
 * from any thread.
 */
internal class Streamer(
    private val scope: CoroutineScope,
    private val client: HttpClient,
    baseUrl: String,
    private val sdkKey: String,
    private val backoff: Backoff,
    private val hooks: StreamerHooks,
    private val logger: MegooFlagsLogger,
    private val now: () -> Long = System::currentTimeMillis,
    private val io: CoroutineDispatcher = Dispatchers.IO,
    private val delayFn: suspend (Long) -> Unit = ::delay,
) {
    private enum class State { IDLE, RUNNING, PAUSED, DONE }

    private val url = sdkUrl(baseUrl, "stream").toString()
    private val lock = Any()
    private var state = State.IDLE
    private var job: Job? = null
    private var connection: StreamConnection? = null

    // Only the loop touches these; one loop runs at a time.
    private var failures = 0

    /** lastEventId is the id of the last `changed` event, sent on reconnect. */
    @Volatile
    var lastEventId: String? = null
        private set

    /** start opens the stream; only the first call does anything. */
    fun start() =
        synchronized(lock) {
            if (state == State.IDLE) run()
        }

    /** pause closes the connection and stops reconnecting until resume. */
    fun pause() =
        synchronized(lock) {
            if (state == State.RUNNING) {
                state = State.PAUSED
                cancel()
            }
        }

    /** resume reconnects at once after pause, with Last-Event-ID. */
    fun resume() =
        synchronized(lock) {
            if (state == State.PAUSED) run()
        }

    /** stop closes the connection for good. */
    fun stop() =
        synchronized(lock) {
            state = State.DONE
            cancel()
        }

    private fun run() {
        state = State.RUNNING
        // Lazy, so job is set before the loop runs: register compares the two.
        val j = scope.launch(start = CoroutineStart.LAZY) { loop() }
        job = j
        j.start()
    }

    /** cancel stops the loop and closes its connection, which ends a blocked read. Called under the lock. */
    private fun cancel() {
        job?.cancel()
        job = null
        connection?.close()
        connection = null
    }

    private suspend fun loop() {
        val self = currentCoroutineContext()[Job]!!
        while (true) {
            val (connectedAt, error) = connect(self)
            if (error is SdkException.Fatal || error is SdkException.Revoked) {
                synchronized(lock) {
                    if (job !== self) return
                    state = State.DONE
                    cancel()
                }
                call { hooks.onFatal(error as SdkException) }
                return
            }
            if (connectedAt != null && now() - connectedAt >= STABLE_AFTER_MS) {
                backoff.reset()
            }
            failures++
            var wait = backoff.next()
            if (error is SdkException.RetryAfter) {
                wait = maxOf(error.seconds * 1_000, wait)
            }
            logger.debug("megooflags: stream failed: ${failureReason(error)}")
            val n = failures
            call { hooks.onFailure(n, error) }
            delayFn(wait)
        }
    }

    /**
     * connect opens one stream and reads it until it ends, and returns when the server answered 200 (null if it
     * did not) and why it ended. A cancellation (pause, stop) is thrown.
     */
    private suspend fun connect(self: Job): Pair<Long?, Throwable> {
        val headers = sdkHeaders(sdkKey, "text/event-stream", lastEventId = lastEventId)
        val conn =
            try {
                withContext(io) { client.stream(url, headers).also { register(self, it) } }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                return null to e
            }
        var connectedAt: Long? = null
        try {
            val bad =
                statusError(conn.status, conn::header, now())
                    ?: if (conn.status == 200) null else SdkException.Http(conn.status)
            if (bad != null) {
                return null to bad
            }
            connectedAt = now()
            failures = 0
            logger.debug("megooflags: stream connected")
            call { hooks.onConnected() }
            val parser =
                SseParser(
                    onLine = {},
                    onRetry = { backoff.base = it.toLong() },
                    onEvent = { event ->
                        when (event.name) {
                            "changed" -> {
                                if (event.id.isNotEmpty()) lastEventId = event.id
                                call { hooks.onChanged(event.id) }
                            }

                            "revoked" -> {
                                throw SdkException.Revoked()
                            }
                        }
                    },
                )
            val buf = ByteArray(CHUNK_BYTES)
            while (true) {
                val n = withContext(io) { conn.input.read(buf) }
                if (n < 0) {
                    parser.finish()
                    return connectedAt to StreamEndedException()
                }
                parser.feed(buf, 0, n)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return connectedAt to e
        } finally {
            synchronized(lock) {
                if (connection === conn) connection = null
            }
            conn.close()
        }
    }

    /** register makes [conn] the one cancel closes, or closes it when [self] was already cancelled. */
    private fun register(
        self: Job,
        conn: StreamConnection,
    ) {
        val current =
            synchronized(lock) {
                if (job === self) {
                    connection = conn
                    true
                } else {
                    false
                }
            }
        if (!current) {
            conn.close()
            throw CancellationException("megooflags: stream cancelled")
        }
    }

    /** call runs a hook; an exception it throws is logged, never propagated. */
    private fun call(hook: () -> Unit) {
        try {
            hook()
        } catch (e: Exception) {
            logger.error("megooflags: stream callback failed: ${e::class.java.simpleName}")
        }
    }
}

/**
 * failureReason describes a request failure for the debug log: an SDK error's message without the prefix, else the
 * exception's class (a message could hold a host name).
 */
internal fun failureReason(e: Throwable): String =
    if (e is SdkException || e is StreamEndedException) {
        e.message!!.removePrefix("megooflags: ")
    } else {
        e::class.java.simpleName
    }
