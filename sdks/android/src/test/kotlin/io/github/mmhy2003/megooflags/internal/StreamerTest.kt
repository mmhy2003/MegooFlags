package io.github.mmhy2003.megooflags.internal

import io.github.mmhy2003.megooflags.VERSION
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.SocketTimeoutException
import java.util.concurrent.CopyOnWriteArrayList

private const val KEY = "mf_client_secret123"

/** Keeps every hook call as a line: "connected", "changed <id>", "fatal <message>", "failure <n> <error>". */
private class HookLog {
    val lines: MutableList<String> = CopyOnWriteArrayList()
    val names: List<String> get() = lines.map { it.substringBefore(' ') }

    val hooks =
        StreamerHooks(
            onConnected = { lines.add("connected") },
            onChanged = { lines.add("changed $it") },
            onFatal = { lines.add("fatal ${it.message}") },
            onFailure = { n, e -> lines.add("failure $n ${e.message ?: e::class.java.simpleName}") },
        )
}

/** What one test works with: the server, the hook and log lines, the waits asked for, and the streamer. */
@OptIn(ExperimentalCoroutinesApi::class)
private class Harness(
    val scope: TestScope,
    val server: FakeServer,
) {
    val log = HookLog()
    val logger = LineLogger()

    /** The waits the streamer asked for, in milliseconds; each is a virtual-time delay. */
    val sleeps: MutableList<Long> = CopyOnWriteArrayList()

    /**
     * The streamer's scope: the test scheduler, but not backgroundScope, whose tasks advanceUntilIdle never runs.
     * It is not a child of the test, so streamTest cancels it.
     */
    val streamScope = CoroutineScope(scope.coroutineContext + SupervisorJob())

    var streamer: Streamer = make()

    fun make(
        random: Double = 1.0,
        hooks: StreamerHooks = log.hooks,
        client: HttpClient = UrlConnectionClient(),
        baseUrl: String = server.baseUrl,
    ): Streamer =
        Streamer(
            scope = streamScope,
            client = client,
            baseUrl = baseUrl,
            sdkKey = KEY,
            backoff = Backoff(3_000, 60_000, FixedRandom(random)),
            hooks = hooks,
            logger = logger,
            now = { scope.testScheduler.currentTime },
            delayFn = { ms ->
                sleeps.add(ms)
                delay(ms)
            },
        )

    /**
     * waitUntil runs the test dispatcher until [done] holds, for up to 5 s of real time. With [advance] it also
     * skips virtual-time waits (backoff); without it, only what is due now runs.
     */
    fun waitUntil(
        what: String,
        advance: Boolean = true,
        done: () -> Boolean,
    ) {
        val deadline = System.nanoTime() + 5_000_000_000L
        while (true) {
            if (advance) scope.advanceUntilIdle() else scope.runCurrent()
            if (done()) return
            check(System.nanoTime() < deadline) { "timed out waiting for $what; hooks ${log.lines}" }
            Thread.sleep(2)
        }
    }

    fun connections(
        n: Int,
        advance: Boolean = true,
    ) = waitUntil("$n connections", advance) { server.connections.size >= n }

    fun hooks(
        n: Int,
        advance: Boolean = true,
    ) = waitUntil("$n hook calls", advance) { log.lines.size >= n }

    /** settle lets a wrong reconnect or hook happen before the test checks there was none. */
    fun settle() {
        repeat(10) {
            scope.advanceUntilIdle()
            Thread.sleep(10)
        }
    }
}

private fun streamTest(body: Harness.() -> Unit) =
    runTest {
        FakeServer().use { server ->
            val h = Harness(this, server)
            try {
                h.body()
            } finally {
                h.streamer.stop()
                h.streamScope.cancel()
            }
        }
    }

@OptIn(ExperimentalCoroutinesApi::class)
class StreamerTest {
    @Test
    fun opensTheStreamWithTheSdkHeadersAndReportsTheConnection() =
        streamTest {
            streamer.start()
            hooks(1)
            val r = server.last.request
            assertEquals("GET", r.method)
            assertEquals("/sdk/v1/stream", r.path)
            assertEquals("Bearer $KEY", r.header("Authorization"))
            assertEquals("text/event-stream", r.header("Accept"))
            assertEquals("megooflags-android/$VERSION", r.header("User-Agent"))
            assertNull(r.header("Last-Event-ID"))
            assertEquals(listOf("connected"), log.lines)
            assertEquals(listOf("debug megooflags: stream connected"), logger.lines)
        }

    @Test
    fun aSecondStartOpensNothing() =
        streamTest {
            streamer.start()
            streamer.start()
            hooks(1)
            settle()
            assertEquals(1, server.connections.size)
        }

    @Test
    fun callsOnChangedWithTheIdAndResumesFromItOnTheNextConnection() =
        streamTest {
            streamer.start()
            hooks(1)
            server.last.send("event: changed\nid: 42\ndata: {}\n\n")
            hooks(2)
            assertEquals(listOf("connected", "changed 42"), log.lines)
            assertEquals("42", streamer.lastEventId)
            server.last.end()
            connections(2)
            assertEquals("42", server.last.header("Last-Event-ID"))
        }

    @Test
    fun aChangedEventSplitAcrossChunksIsDispatchedOnce() =
        streamTest {
            streamer.start()
            hooks(1)
            server.last.send("event: chan")
            server.last.send("ged\nid: 7\ndata: {}\n")
            server.last.send("\n")
            hooks(2)
            settle()
            assertEquals(listOf("connected", "changed 7"), log.lines)
        }

    @Test
    fun aChangedEventWithoutIdKeepsTheLastEventId() =
        streamTest {
            streamer.start()
            hooks(1)
            server.last.send("event: changed\nid: 5\ndata: {}\n\nevent: changed\ndata: {}\n\n")
            hooks(3)
            assertEquals(listOf("connected", "changed 5", "changed "), log.lines)
            assertEquals("5", streamer.lastEventId)
        }

    @Test
    fun ignoresUnknownEventsAndComments() =
        streamTest {
            streamer.start()
            hooks(1)
            server.last.send(": keep-alive\n\nevent: hello\ndata: {}\n\ndata: plain\n\n")
            server.last.send("event: changed\nid: 1\ndata: {}\n\n")
            hooks(2)
            settle()
            assertEquals(listOf("connected", "changed 1"), log.lines)
        }

    @Test
    fun theRetryFieldSetsTheNextWait() =
        streamTest {
            streamer.start()
            hooks(1)
            server.last.send("retry: 5000\n\n")
            server.last.end()
            connections(2)
            assertEquals(listOf(5_000L), sleeps)
        }

    @Test
    fun theRetryFieldNeverSetsAWaitUnder1Second() =
        streamTest {
            streamer.start()
            hooks(1)
            server.last.send("retry: 0\n\n")
            server.last.end()
            connections(2)
            assertEquals(listOf(1_000L), sleeps)
        }

    @Test
    fun theServerEndingTheStreamIsAFailureAndReconnects() =
        streamTest {
            streamer.start()
            hooks(1)
            server.last.end()
            connections(2)
            assertEquals(listOf("connected", "failure 1 megooflags: stream ended"), log.lines.take(2))
            assertEquals(listOf(3_000L), sleeps)
            assertTrue(logger.lines.toString(), logger.lines.contains("debug megooflags: stream failed: stream ended"))
        }

    @Test
    fun theStreamReadTimeoutIs45Seconds() {
        assertEquals(45_000, STREAM_READ_TIMEOUT_MS)
        assertEquals(STREAM_READ_TIMEOUT_MS, UrlConnectionClient().streamReadTimeoutMs)
    }

    @Test
    fun aSilentConnectionTimesOutAndReconnects() =
        streamTest {
            // The read timeout is the dead timer; 300 ms stands for 45 s.
            streamer = make(client = UrlConnectionClient(streamReadTimeoutMs = 300))
            streamer.start()
            hooks(1)
            hooks(2)
            assertEquals("failure", log.names[1])
            assertTrue(log.lines[1], log.lines[1].startsWith("failure 1 "))
            connections(2)
            assertTrue(
                logger.lines.toString(),
                logger.lines.contains("debug megooflags: stream failed: ${SocketTimeoutException::class.java.simpleName}"),
            )
        }

    @Test
    fun aLineBeforeTheReadTimeoutKeepsTheConnectionOpen() =
        streamTest {
            streamer = make(client = UrlConnectionClient(streamReadTimeoutMs = 300))
            streamer.start()
            hooks(1)
            repeat(10) {
                server.last.send(": ping\n")
                Thread.sleep(100)
                scope.runCurrent()
            }
            assertEquals(listOf("connected"), log.lines)
            assertEquals(1, server.connections.size)
        }

    @Test
    fun backsOff3612UpTo60Seconds() =
        streamTest {
            repeat(7) { server.replyStream(503) }
            streamer.start()
            connections(8)
            assertEquals(listOf(3_000L, 6_000L, 12_000L, 24_000L, 48_000L, 60_000L, 60_000L), sleeps)
        }

    @Test
    fun jittersLaterWaitsBetween80And100Percent() =
        streamTest {
            streamer = make(random = 0.0)
            repeat(3) { server.replyStream(503) }
            streamer.start()
            connections(4)
            assertEquals(listOf(3_000L, 4_800L, 9_600L), sleeps)
        }

    @Test
    fun resetsTheBackoffAfterAConnectionThatLasted60Seconds() =
        streamTest {
            server.replyStream(503)
            server.replyStream(503)
            streamer.start()
            connections(3)
            hooks(3)
            scope.advanceTimeBy(60_000)
            server.last.end()
            connections(4)
            assertEquals(listOf(3_000L, 6_000L, 3_000L), sleeps)
        }

    @Test
    fun keepsTheBackoffAfterAShortConnection() =
        streamTest {
            server.replyStream(503)
            server.replyStream(503)
            streamer.start()
            connections(3)
            hooks(3)
            scope.advanceTimeBy(59_000)
            server.last.end()
            connections(4)
            assertEquals(listOf(3_000L, 6_000L, 12_000L), sleeps)
        }

    @Test
    fun onFailureCountsConsecutiveFailuresAndTheCountResetsOnConnect() =
        streamTest {
            server.replyStream(503)
            server.replyStream(500)
            streamer.start()
            hooks(3)
            server.last.end()
            hooks(4)
            assertEquals(
                listOf("failure 1 megooflags: HTTP 503", "failure 2 megooflags: HTTP 500", "connected"),
                log.lines.take(3),
            )
            assertTrue(log.lines[3], log.lines[3].startsWith("failure 1 "))
        }

    @Test
    fun aConnectionErrorIsAFailure() =
        streamTest {
            val port = java.net.ServerSocket(0).use { it.localPort }
            streamer = make(baseUrl = "http://127.0.0.1:$port")
            streamer.start()
            hooks(2)
            assertEquals(listOf("failure", "failure"), log.names.take(2))
            assertEquals(listOf(3_000L), sleeps.take(1))
        }

    @Test
    fun fatalStatusesCallOnFatalOnceAndNeverReconnect() {
        for (status in listOf(401, 403)) {
            streamTest {
                server.replyStream(status)
                streamer.start()
                hooks(1)
                settle()
                assertEquals(listOf("fatal megooflags: SDK key refused ($status)"), log.lines)
                assertEquals(1, server.connections.size)
                streamer.resume()
                streamer.start()
                settle()
                assertEquals(1, server.connections.size)
            }
        }
    }

    @Test
    fun revokedCallsOnFatalOnceAndNeverReconnects() =
        streamTest {
            streamer.start()
            hooks(1)
            server.last.send("event: revoked\ndata: {}\n\n")
            hooks(2)
            settle()
            assertEquals(listOf("connected", "fatal megooflags: SDK key refused (revoked)"), log.lines)
            assertEquals(1, server.connections.size)
            assertTrue(server.last.awaitClientClose())
        }

    @Test
    fun a429WaitsRetryAfterSeconds() =
        streamTest {
            server.replyStream(429, mapOf("Retry-After" to "120"))
            streamer.start()
            connections(2)
            assertEquals(listOf(120_000L), sleeps)
            assertEquals("failure 1 megooflags: rate limited, retry after 120s", log.lines.first())
        }

    @Test
    fun pauseClosesTheConnectionAndDoesNotReconnect() =
        streamTest {
            streamer.start()
            hooks(1)
            streamer.pause()
            assertTrue(server.last.awaitClientClose())
            settle()
            assertEquals(1, server.connections.size)
            assertEquals(listOf("connected"), log.lines)
        }

    @Test
    fun pauseDuringABackoffWaitCancelsTheReconnect() =
        streamTest {
            server.replyStream(503)
            streamer.start()
            waitUntil("a backoff wait", advance = false) { sleeps.size == 1 }
            streamer.pause()
            settle()
            assertEquals(1, server.connections.size)
        }

    @Test
    fun resumeOpensAtOnceWithLastEventId() =
        streamTest {
            streamer.start()
            hooks(1)
            server.last.send("event: changed\nid: 9\ndata: {}\n\n")
            hooks(2)
            streamer.pause()
            streamer.resume()
            connections(2, advance = false)
            assertEquals("9", server.last.header("Last-Event-ID"))
            hooks(3, advance = false)
            assertEquals(emptyList<Long>(), sleeps)
            assertEquals(listOf("connected", "changed 9", "connected"), log.lines)
        }

    @Test
    fun resumeWithoutPauseOpensNothing() =
        streamTest {
            streamer.resume()
            settle()
            assertEquals(0, server.connections.size)
            streamer.start()
            hooks(1)
            streamer.resume()
            settle()
            assertEquals(1, server.connections.size)
        }

    @Test
    fun stopIsFinal() =
        streamTest {
            streamer.start()
            hooks(1)
            streamer.stop()
            assertTrue(server.last.awaitClientClose())
            streamer.start()
            streamer.resume()
            settle()
            assertEquals(1, server.connections.size)
            assertEquals(listOf("connected"), log.lines)
        }

    @Test
    fun stopBeforeTheOpenAnswersRunsNoHook() =
        streamTest {
            server.holdStreams = true
            streamer.start()
            connections(1)
            streamer.stop()
            server.last.respond()
            server.last.send("event: changed\nid: 1\ndata: {}\n\n")
            settle()
            assertEquals(emptyList<String>(), log.lines)
            assertEquals(1, server.connections.size)
        }

    @Test
    fun aHookThatThrowsIsLoggedAndTheStreamGoesOn() =
        streamTest {
            streamer =
                make(
                    hooks =
                        StreamerHooks(
                            onConnected = { log.lines.add("connected") },
                            onChanged = { error("boom") },
                            onFatal = {},
                            onFailure = { _, _ -> },
                        ),
                )
            streamer.start()
            hooks(1)
            server.last.send("event: changed\nid: 1\ndata: {}\n\n")
            waitUntil("the error line") { logger.lines.any { it.startsWith("error ") } }
            assertTrue(logger.lines.toString(), logger.lines.contains("error megooflags: stream callback failed: IllegalStateException"))
            server.last.send("event: changed\nid: 2\ndata: {}\n\n")
            waitUntil("the second event") { streamer.lastEventId == "2" }
            assertFalse(server.last.clientGone)
            assertEquals(1, server.connections.size)
        }
}
