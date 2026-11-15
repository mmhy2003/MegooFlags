package io.github.mmhy2003.megooflags

import dev.openfeature.kotlin.sdk.ImmutableContext
import dev.openfeature.kotlin.sdk.OpenFeatureAPI
import dev.openfeature.kotlin.sdk.OpenFeatureStatus
import dev.openfeature.kotlin.sdk.Value
import dev.openfeature.kotlin.sdk.events.OpenFeatureProviderEvents
import dev.openfeature.kotlin.sdk.exceptions.ErrorCode
import io.github.mmhy2003.megooflags.internal.FakeServer
import io.github.mmhy2003.megooflags.internal.LineLogger
import io.github.mmhy2003.megooflags.internal.parseJson
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import kotlin.time.Duration.Companion.milliseconds
import kotlin.time.Duration.Companion.seconds

private const val KEY = "mf_client_secret123"
private val USER1 = ImmutableContext("user-1")
private val USER2 = ImmutableContext("user-2")

/** An evaluate response body with string flag f = [value] (its variant has the same name). */
private fun results(value: String): String =
    """{"results":[{"key":"f","value":"$value","variant":"$value","reason":"TARGETING_MATCH","error_code":null}]}"""

/**
 * waitFor polls [done] every 5 ms for up to 5 s of real time. The provider runs on real dispatchers: the fake server
 * is a real socket, so the test scheduler's virtual time would fire initTimeout before an answer arrives.
 */
private fun waitFor(
    what: String,
    done: () -> Boolean,
) {
    val deadline = System.nanoTime() + 5_000_000_000L
    while (!done()) {
        check(System.nanoTime() < deadline) { "timed out waiting for $what" }
        Thread.sleep(5)
    }
}

class MegooFlagsProviderTest {
    private val server = FakeServer()
    private val logger = LineLogger()
    private val dir = Files.createTempDirectory("megooflags-provider").toFile()
    private val events: MutableList<OpenFeatureProviderEvents> = CopyOnWriteArrayList()
    private val collectors = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private fun config(configure: MegooFlagsConfig.Builder.() -> Unit = {}): MegooFlagsConfig =
        MegooFlagsConfig
            .Builder(KEY, dir, null)
            .baseUrl(server.baseUrl)
            .logger(logger)
            .initTimeout(2.seconds)
            .apply(configure)
            .build()

    /** provider builds a provider whose events the test collects in [events]. */
    private fun provider(config: MegooFlagsConfig = config()): MegooFlagsProvider =
        MegooFlagsProvider(config).also { p ->
            // Undispatched: the shared flow is subscribed before launch returns, so no event is missed.
            collectors.launch(start = CoroutineStart.UNDISPATCHED) { p.observe().collect { events.add(it) } }
        }

    private fun register(
        p: MegooFlagsProvider,
        context: ImmutableContext = USER1,
    ) = runBlocking { OpenFeatureAPI.setProviderAndWait(p, context, Dispatchers.Default) }

    private val client get() = OpenFeatureAPI.getClient()

    @After
    fun tearDown() {
        runBlocking { OpenFeatureAPI.shutdown() }
        collectors.cancel()
        server.evaluateGate?.countDown()
        server.close()
        dir.deleteRecursively()
    }

    @Test
    fun metadataAndHooks() {
        val p = MegooFlagsProvider(config())
        assertEquals("MegooFlags", p.metadata.name)
        assertTrue(p.hooks.isEmpty())
        p.shutdown()
    }

    @Test
    fun servesTheServerValue() {
        server.replyEvaluate(200, results("blue"))
        register(provider())
        assertEquals(OpenFeatureStatus.Ready, OpenFeatureAPI.getStatus())
        val details = client.getStringDetails("f", "default")
        assertEquals("blue", details.value)
        assertEquals("blue", details.variant)
        assertEquals("TARGETING_MATCH", details.reason)
        assertNull(details.errorCode)
        val sent = parseJson(server.requests.single().body) as Map<*, *>
        assertEquals(mapOf("key" to "user-1"), sent["context"])
    }

    @Test
    fun bootstrapIsReadyAtOnce() {
        // The server never answers in time: only bootstrap can make initialisation succeed.
        server.evaluateGate = CountDownLatch(1)
        val p = provider(config { bootstrap(results("green")).initTimeout(30.seconds) })
        val started = System.nanoTime()
        register(p)
        assertTrue(System.nanoTime() - started < 2_000_000_000L)
        assertEquals(OpenFeatureStatus.Ready, OpenFeatureAPI.getStatus())
        assertEquals("green", client.getStringValue("f", "default"))
    }

    @Test
    fun readsEveryType() {
        val bootstrap =
            """{"results":[
              {"key":"b","value":true,"variant":"on","reason":"STATIC","error_code":null},
              {"key":"i","value":42,"variant":"v","reason":"STATIC","error_code":null},
              {"key":"l","value":5000000000,"variant":"v","reason":"STATIC","error_code":null},
              {"key":"d","value":1.5,"variant":"v","reason":"STATIC","error_code":null},
              {"key":"o","value":{"a":[1,"x"]},"variant":"v","reason":"STATIC","error_code":null}
            ]}"""
        val p =
            MegooFlagsProvider(
                MegooFlagsConfig
                    .Builder(KEY, null, null)
                    .offline()
                    .bootstrap(bootstrap)
                    .build(),
            )
        register(p)
        assertEquals(true, client.getBooleanValue("b", false))
        assertEquals(42, client.getIntegerValue("i", 0))
        assertEquals(5_000_000_000L, p.getLongEvaluation("l", 0L, USER1).value)
        assertEquals(1.5, client.getDoubleValue("d", 0.0), 0.0)
        assertEquals(
            Value.Structure(mapOf("a" to Value.List(listOf(Value.Integer(1), Value.String("x"))))),
            client.getObjectValue("o", Value.Null),
        )
        assertEquals(ErrorCode.TYPE_MISMATCH, client.getIntegerDetails("b", 7).errorCode)
        assertEquals(ErrorCode.FLAG_NOT_FOUND, client.getBooleanDetails("missing", false).errorCode)
    }

    @Test
    fun aStreamChangeEmitsConfigurationChanged() {
        server.replyEvaluate(200, results("blue"))
        register(provider())
        waitFor("the stream") { server.connections.isNotEmpty() }
        server.replyEvaluate(200, results("red"))
        server.last.send("event: changed\nid: 1\ndata: {}\n\n")
        waitFor("configuration changed") { events.any { it is OpenFeatureProviderEvents.ProviderConfigurationChanged } }
        val changed = events.filterIsInstance<OpenFeatureProviderEvents.ProviderConfigurationChanged>().single()
        assertEquals(setOf("f"), changed.eventDetails?.flagsChanged)
        assertEquals("red", client.getStringValue("f", "default"))
    }

    @Test
    fun anotherTargetingKeyServesDefaultsUntilItsValuesArrive() {
        server.replyEvaluate(200, results("blue"))
        register(provider())
        assertEquals("blue", client.getStringValue("f", "default"))

        val gate = CountDownLatch(1)
        server.evaluateGate = gate
        server.replyEvaluate(200, results("red"))
        OpenFeatureAPI.setEvaluationContext(USER2, Dispatchers.Default)
        waitFor("the new context's evaluate") { server.requests.size == 2 }
        val pending = client.getStringDetails("f", "default")
        assertEquals("default", pending.value)
        assertEquals(ErrorCode.PROVIDER_NOT_READY, pending.errorCode)

        gate.countDown()
        waitFor("the new value") { client.getStringValue("f", "default") == "red" }
        waitFor("ready") { OpenFeatureAPI.getStatus() == OpenFeatureStatus.Ready }
        val sent = parseJson(server.requests[1].body) as Map<*, *>
        assertEquals(mapOf("key" to "user-2"), sent["context"])
    }

    @Test
    fun aRefusedKeyIsAFatalError() {
        server.replyEvaluate(401)
        register(provider())
        val status = OpenFeatureAPI.getStatus()
        assertTrue("status $status", status is OpenFeatureStatus.Error)
        assertEquals(ErrorCode.PROVIDER_FATAL, (status as OpenFeatureStatus.Error).error.errorCode())
        Thread.sleep(100)
        assertTrue(server.connections.isEmpty())
    }

    @Test
    fun noValuesWithinInitTimeoutIsAGeneralErrorThenReady() {
        server.evaluateGate = CountDownLatch(1)
        server.replyEvaluate(200, results("blue"))
        register(provider(config { initTimeout(200.milliseconds) }))
        val status = OpenFeatureAPI.getStatus()
        assertTrue("status $status", status is OpenFeatureStatus.Error)
        assertEquals(ErrorCode.GENERAL, (status as OpenFeatureStatus.Error).error.errorCode())

        server.evaluateGate?.countDown()
        waitFor("ready") { OpenFeatureAPI.getStatus() == OpenFeatureStatus.Ready }
        assertTrue(events.any { it is OpenFeatureProviderEvents.ProviderReady })
        assertEquals("blue", client.getStringValue("f", "default"))
    }

    @Test
    fun shutdownSendsTheExposures() {
        server.replyEvaluate(200, results("blue"))
        register(provider())
        repeat(5) { client.getStringValue("f", "default") }
        assertTrue(server.events.isEmpty())
        runBlocking { OpenFeatureAPI.shutdown() }
        val body = parseJson(server.events.single().body) as Map<*, *>
        val exposure = (body["exposures"] as List<*>).single() as Map<*, *>
        assertEquals("f", exposure["flag_key"])
        assertEquals("blue", exposure["variant"])
        assertEquals("TARGETING_MATCH", exposure["reason"])
        assertTrue(logger.lines.contains("debug megooflags: sent 1 exposures"))
    }

    @Test
    fun aSecondShutdownReturnsAtOnce() {
        server.replyEvaluate(200, results("blue"))
        val p = provider()
        register(p)
        client.getStringValue("f", "default")
        p.shutdown()
        val started = System.nanoTime()
        p.shutdown()
        assertTrue(System.nanoTime() - started < 100_000_000L)
        assertEquals(1, server.events.size)
    }
}
