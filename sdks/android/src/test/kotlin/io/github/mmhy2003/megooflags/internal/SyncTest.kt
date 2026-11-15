package io.github.mmhy2003.megooflags.internal

import dev.openfeature.kotlin.sdk.EvaluationContext
import dev.openfeature.kotlin.sdk.ImmutableContext
import dev.openfeature.kotlin.sdk.Value
import dev.openfeature.kotlin.sdk.exceptions.ErrorCode
import io.github.mmhy2003.megooflags.MegooFlagsConfig
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.time.Duration.Companion.seconds

private const val KEY = "mf_client_secret123"
private val USER1 = ImmutableContext("user-1")

private fun result(
    value: Any? = true,
    variant: String? = "on",
    key: String = "f",
    reason: String = "TARGETING_MATCH",
) = EvaluateResult(key, value, variant, reason, null, null)

private fun ok(
    vararg results: EvaluateResult,
    etag: String? = "\"e1\"",
) = EvaluateOutcome.Ok(results.toList(), etag)

/** The body of an evaluate response holding [results], for bootstrap. */
private fun bootstrapJson(vararg results: EvaluateResult): String =
    writeCanonical(
        mapOf(
            "results" to
                results.map {
                    mapOf(
                        "key" to it.key,
                        "value" to it.value,
                        "variant" to it.variant,
                        "reason" to it.reason,
                        "error_code" to null,
                    )
                },
        ),
    )

/** An evaluator whose answers the test gives; a cancelled call stays in [calls]. */
private class FakeEvaluator : Evaluates {
    class Call(
        val body: Map<String, Any?>,
        val etag: String?,
        private val outcome: CompletableDeferred<EvaluateOutcome>,
    ) {
        fun answer(o: EvaluateOutcome) {
            outcome.complete(o)
        }

        fun fail(e: Throwable) {
            outcome.completeExceptionally(e)
        }
    }

    val calls: MutableList<Call> = CopyOnWriteArrayList()

    val last: Call get() = calls.last()

    override suspend fun evaluate(
        body: Map<String, Any?>,
        etag: String?,
    ): EvaluateOutcome {
        val outcome = CompletableDeferred<EvaluateOutcome>()
        calls.add(Call(body, etag, outcome))
        return outcome.await()
    }
}

private class FakeStreamer : StreamControl {
    var starts = 0
    var pauses = 0
    var resumes = 0
    var stops = 0

    override fun start() {
        starts++
    }

    override fun pause() {
        pauses++
    }

    override fun resume() {
        resumes++
    }

    override fun stop() {
        stops++
    }
}

/** What one test works with. [events] are "ready", "stale", "changed [f]", "fatal <message>", "error <message>". */
@OptIn(ExperimentalCoroutinesApi::class)
private class SyncHarness(
    val test: TestScope,
    configure: MegooFlagsConfig.Builder.() -> Unit,
    val dir: File,
) {
    val logger = LineLogger()
    val config =
        MegooFlagsConfig
            .Builder(KEY, dir, null)
            .baseUrl("https://flags.example.com")
            .logger(logger)
            .apply(configure)
            .build()
    val store = Store()
    val cache = Cache(KEY, dir, logger)
    val evaluator = FakeEvaluator()
    val streamer = FakeStreamer()
    val events: MutableList<String> = CopyOnWriteArrayList()
    var fatalStops = 0

    /** Sync's scope: the test scheduler, not a child of the test, so syncTest cancels it. */
    val scope = CoroutineScope(test.coroutineContext + SupervisorJob())

    val sync =
        Sync(
            scope = scope,
            config = config,
            store = store,
            cache = cache,
            evaluator = evaluator,
            streamer = { streamer },
            events =
                object : SyncEvents {
                    override fun ready() {
                        events.add("ready")
                    }

                    override fun stale() {
                        events.add("stale")
                    }

                    override fun changed(flags: List<String>) {
                        events.add("changed $flags")
                    }

                    override fun fatal(error: SdkException) {
                        events.add("fatal ${error.message}")
                    }

                    override fun error(error: Throwable) {
                        events.add("error ${error.message}")
                    }

                    override fun fatalStop() {
                        fatalStops++
                    }
                },
            random = FixedRandom(0.0),
        )

    val hooks get() = sync.streamerHooks

    fun read(flag: String = "f") = resolveBoolean(store.get(), flag, false)

    /** begin runs [block] in the test and keeps how it ended. */
    fun <T> begin(block: suspend () -> T): Deferred<Result<T>> {
        val outcome = CompletableDeferred<Result<T>>()
        test.launch { outcome.complete(runCatching { block() }) }
        test.runCurrent()
        return outcome
    }

    fun advance(ms: Long) {
        test.advanceTimeBy(ms)
        test.runCurrent()
    }

    /** started has the first refresh, with bootstrap f=true, answer f=true. */
    fun started(): SyncHarness {
        begin { sync.start(USER1) }
        evaluator.last.answer(ok(result()))
        test.runCurrent()
        return this
    }

    fun saveCacheFor(
        ctx: EvaluationContext,
        etag: String?,
        vararg results: EvaluateResult,
    ) {
        val hash = contextHash(toRequestContext(ctx))
        Cache(KEY, dir, logger).save(StoredResults(results.toList(), etag, Source.NETWORK, hash, null))
    }
}

private fun syncTest(
    configure: MegooFlagsConfig.Builder.() -> Unit = {},
    beforeSync: (File) -> Unit = {},
    body: SyncHarness.() -> Unit,
) = runTest {
    val dir = Files.createTempDirectory("megooflags-sync-").toFile()
    beforeSync(dir)
    val h = SyncHarness(this, configure, dir)
    try {
        h.body()
    } finally {
        h.sync.stop()
        h.scope.cancel()
        dir.deleteRecursively()
    }
}

private fun withBootstrap(vararg results: EvaluateResult): MegooFlagsConfig.Builder.() -> Unit =
    {
        bootstrap(bootstrapJson(*results))
    }

@OptIn(ExperimentalCoroutinesApi::class)
private fun <T> Deferred<Result<T>>.error(): Throwable? = getCompleted().exceptionOrNull()

@OptIn(ExperimentalCoroutinesApi::class)
class SyncTest {
    // start

    @Test
    fun returnsAtOnceWithBootstrapValuesThenARefreshReplacesThem() =
        syncTest(withBootstrap(result(false, "off", reason = "STATIC"))) {
            val start = begin { sync.start(USER1) }

            assertTrue(start.isCompleted)
            assertNull(start.error())
            assertEquals(false, read().value)
            assertEquals("STATIC", read().reason)
            assertEquals(Source.BOOTSTRAP, store.get()?.source)
            assertEquals(1, evaluator.calls.size)
            assertEquals(mapOf("key" to "user-1"), evaluator.last.body)
            assertEquals(0, streamer.starts)

            evaluator.last.answer(ok(result()))
            test.runCurrent()

            assertEquals(true, read().value)
            assertEquals("TARGETING_MATCH", read().reason)
            assertEquals(Source.NETWORK, store.get()?.source)
            assertEquals(listOf("changed [f]"), events)
            assertTrue(cache.file!!.readText().contains("\"etag\":\"\\\"e1\\\"\""))
            assertEquals(1, streamer.starts)
        }

    @Test
    fun servesAMatchingCacheEntryOverBootstrapSendsItsEtagAndA304MakesItFresh() =
        syncTest(withBootstrap(result(reason = "STATIC"))) {
            saveCacheFor(USER1, "\"c1\"", result(false, "off"))

            begin { sync.start(USER1) }

            assertEquals(false, read().value)
            assertEquals("CACHED", read().reason)
            assertEquals("user-1", store.get()?.targetingKey)
            assertEquals("\"c1\"", evaluator.last.etag)

            evaluator.last.answer(EvaluateOutcome.NotModified("\"c1\""))
            test.runCurrent()

            assertEquals(false, read().value)
            assertEquals("TARGETING_MATCH", read().reason)
            assertEquals(Source.NETWORK, store.get()?.source)
            assertEquals(emptyList<String>(), events)
        }

    @Test
    fun ignoresACacheEntryForAnotherContext() =
        syncTest {
            saveCacheFor(ImmutableContext("user-2"), "\"c1\"", result())

            val start = begin { sync.start(USER1) }

            assertFalse(start.isCompleted)
            assertNull(store.get())
            assertNull(evaluator.last.etag)
        }

    @Test
    fun waitsForTheFirstEvaluateWhenThereAreNoValues() =
        syncTest {
            val start = begin { sync.start(USER1) }
            assertFalse(start.isCompleted)
            assertEquals(ErrorCode.PROVIDER_NOT_READY, read().errorCode)

            evaluator.last.answer(ok(result()))
            test.runCurrent()

            assertTrue(start.isCompleted)
            assertNull(start.error())
            assertEquals(true, read().value)
            assertEquals(emptyList<String>(), events)
            assertEquals(1, streamer.starts)
        }

    @Test
    fun failsAfterInitTimeoutAndEmitsReadyWhenValuesArriveLater() =
        syncTest {
            val start = begin { sync.start(USER1) }
            advance(4_999)
            assertFalse(start.isCompleted)
            advance(1)

            val e = start.error()
            assertTrue(e is SyncException)
            assertEquals("megooflags: no flag values within 5s", e!!.message)

            evaluator.last.answer(ok(result()))
            test.runCurrent()

            assertEquals(true, read().value)
            assertEquals(listOf("ready"), events)
            assertEquals(1, streamer.starts)
        }

    @Test
    fun failsWithTheFatalErrorOfA401AndRetriesNothing() =
        syncTest {
            val start = begin { sync.start(USER1) }
            evaluator.last.fail(SdkException.Fatal(401))
            test.runCurrent()

            assertEquals(SdkException.Fatal(401), start.error())
            advance(300_000)
            assertEquals(1, evaluator.calls.size)
            assertEquals(0, streamer.starts)
            assertEquals(1, fatalStops)
            assertEquals(emptyList<String>(), events)
        }

    @Test
    fun failsOfflineWithoutBootstrapOrCache() =
        syncTest({ offline() }) {
            val start = begin { sync.start(USER1) }

            assertEquals("megooflags: offline mode needs bootstrap values or a cache entry", start.error()?.message)
        }

    @Test
    fun startsOfflineFromBootstrapWithoutAnyNetwork() =
        syncTest({
            offline()
            bootstrap(bootstrapJson(result()))
        }) {
            val start = begin { sync.start(USER1) }
            val refresh = sync.refresh()
            advance(120_000)

            assertNull(start.error())
            assertTrue(refresh.isCompleted)
            assertEquals(true, read().value)
            assertEquals(0, evaluator.calls.size)
            assertEquals(0, streamer.starts)
        }

    // refresh

    @Test
    fun runsAtMostOneEvaluateInFlightAndOneQueued() =
        syncTest(withBootstrap(result())) {
            started()
            assertEquals(1, evaluator.calls.size)

            hooks.onChanged("1")
            hooks.onChanged("2")
            hooks.onChanged("3")
            test.runCurrent()
            assertEquals(2, evaluator.calls.size)

            evaluator.last.answer(ok(result()))
            test.runCurrent()
            assertEquals(3, evaluator.calls.size)
            evaluator.last.answer(ok(result()))
            advance(120_000)

            assertEquals(3, evaluator.calls.size)
        }

    @Test
    fun refreshCompletesWhenALaterRunStoresValues() =
        syncTest(withBootstrap(result())) {
            started()
            val first = sync.refresh()
            test.runCurrent()
            val second = sync.refresh()
            val third = sync.refresh()

            evaluator.last.answer(ok(result()))
            test.runCurrent()
            assertTrue(first.isCompleted)
            assertFalse(second.isCompleted)
            assertFalse(third.isCompleted)

            evaluator.last.answer(ok(result()))
            test.runCurrent()
            assertTrue(second.isCompleted && third.isCompleted)
        }

    @Test
    fun sendsTheStoredEtagAndEmitsNoChangeWhenNothingChanged() =
        syncTest(withBootstrap(result())) {
            started()

            hooks.onChanged("1")
            test.runCurrent()
            assertEquals("\"e1\"", evaluator.last.etag)
            evaluator.last.answer(ok(result(), etag = "\"e2\""))
            test.runCurrent()

            assertEquals("\"e2\"", store.get()?.etag)
            assertEquals(emptyList<String>(), events)
        }

    @Test
    fun retriesAFailureWithBackoffFrom1s() =
        syncTest(withBootstrap(result())) {
            started()

            val refresh = sync.refresh()
            test.runCurrent()
            evaluator.last.fail(SdkException.Http(500))
            test.runCurrent()
            assertEquals(SdkException.Http(500), refresh.getCompletionExceptionOrNull())
            assertEquals(listOf("error megooflags: HTTP 500"), events)
            assertTrue(logger.lines.contains("debug megooflags: evaluate failed: HTTP 500"))

            advance(999)
            assertEquals(2, evaluator.calls.size)
            advance(1)
            assertEquals(3, evaluator.calls.size)

            evaluator.last.fail(java.io.IOException("connection refused"))
            advance(1_599)
            assertEquals(3, evaluator.calls.size)
            advance(1)
            assertEquals(4, evaluator.calls.size)

            evaluator.last.answer(ok(result()))
            advance(300_000)
            assertEquals(4, evaluator.calls.size)
        }

    @Test
    fun waitsRetryAfterAfterA429() =
        syncTest(withBootstrap(result())) {
            started()

            hooks.onChanged("1")
            test.runCurrent()
            evaluator.last.fail(SdkException.RetryAfter(7))
            advance(6_999)
            assertEquals(2, evaluator.calls.size)
            advance(1)

            assertEquals(3, evaluator.calls.size)
        }

    @Test
    fun treatsA304WithoutValuesForTheContextAsAFailure() =
        syncTest {
            val start = begin { sync.start(USER1) }
            evaluator.last.answer(EvaluateOutcome.NotModified(null))
            test.runCurrent()

            assertTrue(start.error() is SdkException.InvalidResponse)
            assertNull(store.get())
        }

    @Test
    fun stopsEverythingOnAFatalAnswerAfterStart() =
        syncTest(withBootstrap(result())) {
            started()

            hooks.onChanged("1")
            test.runCurrent()
            evaluator.last.fail(SdkException.Fatal(403))
            advance(300_000)

            assertEquals(listOf("fatal megooflags: SDK key refused (403)"), events)
            assertEquals(1, streamer.stops)
            assertEquals(1, fatalStops)
            assertEquals(2, evaluator.calls.size)
            assertEquals(true, read().value)
            assertTrue(sync.refresh().getCompletionExceptionOrNull() is SdkException.Fatal)
        }

    @Test
    fun stopsEverythingWhenTheStreamReportsTheKeyRevoked() =
        syncTest(withBootstrap(result())) {
            started()

            hooks.onFatal(SdkException.Revoked())
            hooks.onChanged("1")
            advance(300_000)

            assertEquals(listOf("fatal megooflags: SDK key refused (revoked)"), events)
            assertEquals(1, fatalStops)
            assertEquals(1, evaluator.calls.size)
        }

    // setContext

    @Test
    fun clearsTheStoreAndTheCacheEntryAtOnceWhenTheTargetingKeyChanges() =
        syncTest(withBootstrap(result())) {
            started()
            assertTrue(cache.file!!.exists())

            val change = begin { sync.setContext(ImmutableContext("user-2")) }

            assertEquals(ErrorCode.PROVIDER_NOT_READY, read().errorCode)
            assertFalse(cache.file!!.exists())
            assertEquals(mapOf("key" to "user-2"), evaluator.last.body)
            assertNull(evaluator.last.etag)
            assertFalse(change.isCompleted)

            evaluator.last.answer(ok(result(false, "off")))
            test.runCurrent()

            assertTrue(change.isCompleted)
            assertNull(change.error())
            assertEquals(false, read().value)
            assertEquals(mapOf("key" to "user-2"), sync.body)
            assertEquals(emptyList<String>(), events)
        }

    @Test
    fun keepsServingTheOldValuesWhenOnlyAttributesChange() =
        syncTest(withBootstrap(result())) {
            started()

            val change = begin { sync.setContext(ImmutableContext("user-1", mapOf("plan" to Value.String("pro")))) }

            assertEquals(true, read().value)
            assertEquals(mapOf("key" to "user-1", "plan" to "pro"), evaluator.last.body)
            assertNull(evaluator.last.etag)
            evaluator.last.answer(ok(result(false, "off")))
            test.runCurrent()

            assertNull(change.error())
            assertEquals(false, read().value)
            assertEquals(listOf("changed [f]"), events)
        }

    @Test
    fun ignoresALateAnswerForTheOldContext() =
        syncTest(withBootstrap(result())) {
            started()
            hooks.onChanged("1")
            test.runCurrent()
            val old = evaluator.last

            val change = begin { sync.setContext(ImmutableContext("user-1", mapOf("plan" to Value.String("pro")))) }
            old.answer(ok(result(key = "late"), etag = "\"old\""))
            test.runCurrent()

            assertFalse(store.get()!!.results.containsKey("late"))
            assertEquals(3, evaluator.calls.size)
            assertNull(evaluator.last.etag)
            evaluator.last.answer(ok(result(false, "off")))
            test.runCurrent()

            assertNull(change.error())
            assertEquals(false, read().value)
            assertFalse(store.get()!!.results.containsKey("late"))
        }

    @Test
    fun failsWhenTheEvaluateForTheNewContextFailsAndKeepsRetrying() =
        syncTest(withBootstrap(result())) {
            started()

            val change = begin { sync.setContext(ImmutableContext("user-2")) }
            evaluator.last.fail(SdkException.Http(503))
            test.runCurrent()
            assertEquals(SdkException.Http(503), change.error())

            advance(1_000)
            evaluator.last.answer(ok(result()))
            test.runCurrent()

            assertEquals(3, evaluator.calls.size)
            assertEquals(true, read().value)
        }

    // Stream fallback, pause, resume and stop

    @Test
    fun pollsAfterTwoStreamFailuresStopsOnReconnectAndEmitsStaleThenReady() =
        syncTest(withBootstrap(result())) {
            started()
            hooks.onConnected()

            hooks.onFailure(1, java.io.IOException("x"))
            advance(60_000)
            assertEquals(1, evaluator.calls.size)
            assertEquals(emptyList<String>(), events)

            hooks.onFailure(2, java.io.IOException("x"))
            assertEquals(listOf("stale"), events)
            advance(30_000)
            assertEquals(2, evaluator.calls.size)
            evaluator.last.answer(ok(result()))
            hooks.onFailure(3, java.io.IOException("x"))
            advance(30_000)
            assertEquals(3, evaluator.calls.size)
            evaluator.last.answer(ok(result()))

            hooks.onConnected()
            advance(120_000)

            assertEquals(3, evaluator.calls.size)
            assertEquals(listOf("stale", "ready"), events)
        }

    @Test
    fun emitsNoStaleForFailuresBeforeTheStreamEverConnected() =
        syncTest(withBootstrap(result())) {
            started()

            hooks.onFailure(2, java.io.IOException("x"))
            hooks.onConnected()

            assertEquals(emptyList<String>(), events)
        }

    @Test
    fun pollsFromTheStartWithDisableStream() =
        syncTest({
            bootstrap(bootstrapJson(result()))
            disableStream()
        }) {
            started()
            assertEquals(0, streamer.starts)

            advance(30_000)
            assertEquals(2, evaluator.calls.size)
            evaluator.last.answer(ok(result()))
            advance(30_000)

            assertEquals(3, evaluator.calls.size)
        }

    @Test
    fun pauseStopsTheStreamAndThePollerResumeRestartsWhatRan() =
        syncTest(withBootstrap(result())) {
            started()
            hooks.onFailure(2, java.io.IOException("x"))

            sync.pause()
            advance(60_000)
            assertEquals(1, streamer.pauses)
            assertEquals(1, evaluator.calls.size)

            sync.resume()
            advance(30_000)

            assertEquals(1, streamer.resumes)
            assertEquals(2, evaluator.calls.size)
        }

    @Test
    fun resumeDoesNotStartThePollerWhenItWasNotRunning() =
        syncTest(withBootstrap(result())) {
            started()

            sync.pause()
            sync.resume()
            advance(120_000)

            assertEquals(1, streamer.resumes)
            assertEquals(1, evaluator.calls.size)
        }

    @Test
    fun stopEndsTheStreamThePollerAndTheRetries() =
        syncTest(withBootstrap(result())) {
            started()
            hooks.onFailure(2, java.io.IOException("x"))
            sync.refresh()
            test.runCurrent()
            evaluator.last.fail(SdkException.Http(500))
            test.runCurrent()

            sync.stop()
            advance(300_000)

            assertEquals(1, streamer.stops)
            assertEquals(2, evaluator.calls.size)
            assertEquals(0, fatalStops)
            assertEquals("megooflags: provider shut down", sync.refresh().getCompletionExceptionOrNull()?.message)
        }
}

@OptIn(ExperimentalCoroutinesApi::class)
class PollerTest {
    private fun pollerTest(body: suspend TestScope.(CoroutineScope) -> Unit) =
        runTest {
            val scope = CoroutineScope(coroutineContext + SupervisorJob())
            try {
                body(scope)
            } finally {
                scope.cancel()
            }
        }

    @Test
    fun callsRefreshEveryIntervalWhileRunning() =
        pollerTest { scope ->
            var ticks = 0
            val p = Poller(scope, 30.seconds) { ticks++ }

            p.start()
            p.start()
            assertTrue(p.running)
            advanceTimeBy(60_001)
            assertEquals(2, ticks)

            p.stop()
            assertFalse(p.running)
            advanceTimeBy(60_000)
            assertEquals(2, ticks)
        }

    @Test
    fun neverTicksMoreOftenThanEvery15s() =
        pollerTest { scope ->
            var ticks = 0
            val p = Poller(scope, 1.seconds) { ticks++ }

            p.start()
            advanceTimeBy(14_999)
            runCurrent()
            assertEquals(0, ticks)
            advanceTimeBy(2)
            assertEquals(1, ticks)
            p.stop()
        }

    @Test
    fun survivesARefreshThatThrows() =
        pollerTest { scope ->
            var ticks = 0
            val p =
                Poller(scope, 15.seconds) {
                    ticks++
                    throw IllegalStateException("boom")
                }

            p.start()
            advanceTimeBy(30_001)

            assertEquals(2, ticks)
            p.stop()
        }
}
