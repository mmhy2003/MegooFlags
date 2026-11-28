package io.github.mmhy2003.megooflags.internal

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.time.Duration.Companion.milliseconds
import kotlin.time.Duration.Companion.seconds

private const val KEY = "mf_client_secret123"
private const val BASE = "https://flags.example.com"
private val USER1 = mapOf("key" to "user-1")

// 2026-11-10T09:15:02Z.
private const val NOW = 1_794_302_102_000L

/** What one test works with. [io] is the test dispatcher, or a real one for requests that hang. */
@OptIn(ExperimentalCoroutinesApi::class)
private class BatcherHarness(
    val test: TestScope,
    val client: FakeHttpClient,
    io: CoroutineDispatcher?,
) {
    val logger = LineLogger()
    val exposures = Exposures(false, logger, { NOW })
    val fatal: MutableList<Int?> = CopyOnWriteArrayList()

    /** The batcher's scope: the test scheduler, not a child of the test, so batcherTest cancels it. */
    val scope = CoroutineScope(test.coroutineContext + SupervisorJob())

    val batcher =
        Batcher(
            scope = scope,
            client = client,
            baseUrl = BASE,
            sdkKey = KEY,
            exposures = exposures,
            flushInterval = 30.seconds,
            logger = logger,
            onFatal = { fatal.add((it as SdkException.Fatal).status) },
            io = io ?: StandardTestDispatcher(test.testScheduler),
            random = FixedRandom(0.0),
        )

    /** fill records [n] distinct exposures. */
    fun fill(
        n: Int,
        prefix: String = "f",
    ) {
        for (i in 0 until n) exposures.record("$prefix$i", "on", "STATIC", USER1)
    }

    fun advance(ms: Long) {
        test.advanceTimeBy(ms)
        test.runCurrent()
    }

    /** begin runs [block] in the test; the result completes when it returns. */
    fun begin(block: suspend () -> Unit): CompletableDeferred<Unit> {
        val done = CompletableDeferred<Unit>()
        test.launch {
            block()
            done.complete(Unit)
        }
        test.runCurrent()
        return done
    }

    /** waitForRequests runs the test dispatcher until [n] requests arrived, for up to 5 s of real time. */
    fun waitForRequests(n: Int) {
        val deadline = System.nanoTime() + 5_000_000_000L
        while (client.requests.size < n) {
            test.runCurrent()
            check(System.nanoTime() < deadline) { "timed out waiting for $n requests" }
            Thread.sleep(2)
        }
    }

    /** settle gives a wrong extra request time to happen before the test checks there was none. */
    fun settle() {
        repeat(10) {
            test.runCurrent()
            Thread.sleep(5)
        }
    }

    val warnings get() = logger.lines.filter { it.startsWith("warn ") }
}

private fun batcherTest(
    vararg answers: Any,
    realIo: Boolean = false,
    body: suspend BatcherHarness.() -> Unit,
) = runTest {
    val h = BatcherHarness(this, FakeHttpClient(*answers), if (realIo) Dispatchers.IO else null)
    try {
        h.body()
    } finally {
        h.client.release()
        h.batcher.stop()
        h.scope.cancel()
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class BatcherTest {
    @Test
    fun flushesAfterTheInterval() =
        batcherTest(202) {
            batcher.start()
            fill(3)

            advance(29_999)
            assertEquals(0, client.requests.size)
            advance(1)

            assertEquals(listOf(3), client.sizes)
            assertEquals(0, exposures.size)
        }

    @Test
    fun keepsFlushingEveryInterval() =
        batcherTest(202) {
            batcher.start()
            batcher.start()
            fill(1)
            advance(30_000)
            fill(1, "later")
            advance(30_000)

            assertEquals(listOf(1, 1), client.sizes)
        }

    @Test
    fun postsExposuresOnlyWithTheKey() =
        batcherTest(202) {
            fill(1)

            batcher.flush()

            val r = client.requests.single()
            assertEquals("POST", r.method)
            assertEquals("/sdk/v1/events", r.path)
            assertEquals("Bearer $KEY", r.header("Authorization"))
            assertEquals("application/json", r.header("Content-Type"))
            assertEquals("application/json", r.header("Accept"))
            val body = parseJson(r.body) as Map<*, *>
            assertEquals(setOf("exposures"), body.keys)
            assertEquals(
                mapOf(
                    "flag_key" to "f0",
                    "variant" to "on",
                    "reason" to "STATIC",
                    "timestamp" to "2026-11-10T09:15:02.000Z",
                ),
                (body["exposures"] as List<*>)[0],
            )
        }

    @Test
    fun logsTheCountItSentAfterA202() =
        batcherTest(202) {
            fill(2)

            batcher.flush()

            assertTrue(logger.lines.contains("debug megooflags: sent 2 exposures"))
        }

    @Test
    fun doesNotSendAnEmptyBatch() =
        batcherTest(202) {
            batcher.flush()

            assertEquals(0, client.requests.size)
        }

    @Test
    fun flushesAtOnceWhen1000ExposuresAreBuffered() =
        batcherTest(202) {
            batcher.start()

            fill(999)
            test.runCurrent()
            assertEquals(0, client.requests.size)
            fill(1, "last")
            test.runCurrent()

            assertEquals(listOf(1000), client.sizes)
        }

    @Test
    fun sendsAtMost1000ExposuresPerBatch() =
        batcherTest(202) {
            fill(2500)

            batcher.flush()

            assertEquals(listOf(1000, 1000, 500), client.sizes)
        }

    @Test
    fun keepsOneSendInFlight() =
        batcherTest(Answer.Hang, realIo = true) {
            fill(1)

            begin { batcher.flush() }
            begin { batcher.flush() }
            waitForRequests(1)
            settle()

            assertEquals(1, client.requests.size)
        }

    @Test
    fun splitsA413BatchOf1000Into500And500() =
        batcherTest(413, 202, 202) {
            fill(1000)

            batcher.flush()

            assertEquals(listOf(1000, 500, 500), client.sizes)
            assertEquals(0, exposures.size)
        }

    @Test
    fun splitsRecursivelyAndDropsASingleItemThatIsStill413() =
        batcherTest(413, 202, 413, 413, 202) {
            fill(3)

            batcher.flush()

            // [3] 413 → [1] 202, [2] 413 → [1] 413 dropped, [1] 202
            assertEquals(listOf(3, 1, 2, 1, 1), client.sizes)
            assertEquals(
                listOf("warn megooflags: the server refused an exposure as too large (HTTP 413); dropping it"),
                warnings,
            )
            assertEquals(0, exposures.size)
        }

    @Test
    fun dropsA422BatchWithAWarning() =
        batcherTest(422, 202) {
            fill(2)

            batcher.flush()
            batcher.flush()

            assertEquals(listOf(2), client.sizes)
            assertEquals(0, exposures.size)
            assertEquals(listOf("warn megooflags: the server refused 2 exposures (HTTP 422); dropping them"), warnings)
        }

    @Test
    fun retriesA429AfterRetryAfterKeepingTheItemsInFront() =
        batcherTest(Answer.Status(429, mapOf("Retry-After" to "7")), 202) {
            fill(2)

            batcher.flush()
            fill(1, "later")
            assertEquals(3, exposures.size)

            batcher.flush() // still waiting: nothing sent
            advance(6_999)
            assertEquals(1, client.requests.size)
            advance(1)

            assertEquals(2, client.requests.size)
            assertEquals(listOf("f0", "f1", "later0"), client.flagKeys(1))
        }

    @Test
    fun retriesA503WithABackoffFrom1s() =
        batcherTest(503, 503, 202) {
            fill(1)

            batcher.flush()
            assertTrue(logger.lines.contains("debug megooflags: sending exposures failed (HTTP 503); will retry"))
            advance(999)
            assertEquals(1, client.requests.size)
            advance(1)
            assertEquals(2, client.requests.size)
            // The second wait is 2 s with up to 20 % jitter off.
            advance(1_599)
            assertEquals(2, client.requests.size)
            advance(1)

            assertEquals(3, client.requests.size)
            assertEquals(0, exposures.size)
        }

    @Test
    fun retriesAfterANetworkFailure() =
        batcherTest(Answer.Network, 202) {
            fill(1)

            batcher.flush()
            assertEquals(1, exposures.size)
            advance(1_000)

            assertEquals(2, client.requests.size)
            assertEquals(0, exposures.size)
        }

    @Test
    fun callsOnFatalOn401AndStops() = fatalStops(401)

    @Test
    fun callsOnFatalOn403AndStops() = fatalStops(403)

    private fun fatalStops(status: Int) =
        batcherTest(status, 202) {
            batcher.start()
            fill(1)

            batcher.flush()
            fill(1, "later")
            batcher.flush()
            advance(120_000)

            assertEquals(listOf<Int?>(status), fatal)
            assertEquals(1, client.requests.size)
        }

    @Test
    fun closeReturnsWithin5sWhenTheServerNeverAnswers() =
        batcherTest(Answer.Hang, realIo = true) {
            batcher.start()
            fill(1)

            val closed = begin { batcher.close(5.seconds) }
            waitForRequests(1)
            advance(4_999)
            assertFalse(closed.isCompleted)
            advance(1)

            assertTrue(closed.isCompleted)
            assertEquals(1, client.requests.size)
        }

    @Test
    fun doesNotStartAnotherSendWhileClosing() =
        batcherTest(Answer.Hang, realIo = true) {
            fill(1)

            begin { batcher.close(5.seconds) }
            waitForRequests(1)
            fill(1, "late")
            begin { batcher.flush() }
            settle()

            assertEquals(1, client.requests.size)
        }

    @Test
    fun closeFlushesOnceASecondCloseReturnsAtOnce() =
        batcherTest(202) {
            batcher.start()
            fill(1)

            batcher.close(5.seconds)
            assertEquals(listOf(1), client.sizes)

            fill(1, "late")
            val again = begin { batcher.close(5.seconds) }

            assertTrue(again.isCompleted)
            advance(60_000)
            assertEquals(1, client.requests.size)
        }

    @Test
    fun closeIgnoresARetryWait() =
        batcherTest(503, 202) {
            fill(1)
            batcher.flush()

            batcher.close(5.seconds)

            assertEquals(2, client.requests.size)
            assertEquals(0, exposures.size)
        }

    @Test
    fun closeWithAZeroTimeoutStillReturns() =
        batcherTest(Answer.Hang, realIo = true) {
            fill(1)

            val closed = begin { batcher.close(0.milliseconds) }
            advance(0)

            assertTrue(closed.isCompleted)
        }

    @Test
    fun stopSendsNothingMoreEvenOnClose() =
        batcherTest(202) {
            batcher.start()
            fill(2)

            batcher.stop()
            advance(60_000)
            batcher.close(5.seconds)

            assertEquals(0, client.requests.size)
        }
}
