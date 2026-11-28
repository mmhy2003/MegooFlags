package io.github.mmhy2003.megooflags

import io.github.mmhy2003.megooflags.internal.EvaluateResult
import io.github.mmhy2003.megooflags.internal.LifecycleSource
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import kotlin.time.Duration
import kotlin.time.Duration.Companion.milliseconds
import kotlin.time.Duration.Companion.seconds

private const val KEY = "mf_client_secret123"
private const val BASE = "https://flags.example.com"

private object SilentLogger : MegooFlagsLogger {
    override fun debug(message: String) {}

    override fun info(message: String) {}

    override fun warn(message: String) {}

    override fun error(message: String) {}
}

private fun builder(key: String = KEY): MegooFlagsConfig.Builder = MegooFlagsConfig.Builder(key, null, null)

/** assertConfigError checks that build() throws exactly [message], which never contains the key's secret part. */
private fun assertConfigError(
    b: MegooFlagsConfig.Builder,
    message: String,
) {
    val err = assertThrows(IllegalArgumentException::class.java) { b.build() }
    assertEquals(message, err.message)
    assertFalse(err.message!!.contains("secret123"))
}

class MegooFlagsConfigTest {
    // sdkKey

    @Test
    fun refusesAServerKey() {
        assertConfigError(
            builder("mf_server_secret123").baseUrl(BASE),
            "megooflags: a server key must never ship in an app; use a client key (mf_client_…)",
        )
    }

    @Test
    fun refusesAnyOtherKey() {
        for (bad in listOf("", "secret123", "MF_CLIENT_secret123", "mf_secret123")) {
            assertConfigError(builder(bad).baseUrl(BASE), "megooflags: sdkKey must be a client key (mf_client_…)")
        }
    }

    // baseUrl

    @Test
    fun requiresABaseUrlUnlessOffline() {
        assertConfigError(builder(), "megooflags: baseUrl is required unless offline")
        assertNull(builder().offline().build().baseUrl)
    }

    @Test
    fun refusesANonHttpBaseUrl() {
        for (bad in listOf("ftp://flags.example.com", "file:///tmp/flags", "flags.example.com", "https://", "http://a b")) {
            assertConfigError(builder().baseUrl(bad), "megooflags: baseUrl must be an http or https URL")
        }
    }

    @Test
    fun removesTrailingSlashesFromTheBaseUrl() {
        assertEquals("http://localhost:8000/flags", builder().baseUrl("http://localhost:8000/flags//").build().baseUrl)
        assertEquals("HTTPS://flags.example.com", builder().baseUrl("HTTPS://flags.example.com/").build().baseUrl)
    }

    // defaults and clamps

    @Test
    fun fillsTheDefaults() {
        val c = builder().baseUrl(BASE).build()
        assertEquals(KEY, c.sdkKey)
        assertEquals(BASE, c.baseUrl)
        assertNull(c.bootstrap)
        assertFalse(c.disableCache)
        assertFalse(c.offline)
        assertEquals(5.seconds, c.initTimeout)
        assertFalse(c.disableStream)
        assertEquals(30.seconds, c.pollInterval)
        assertEquals(30.seconds, c.flushInterval)
        assertFalse(c.sendContext)
        assertSame(LogcatLogger, c.logger)
        assertNull(c.cacheDir)
        assertNull(c.lifecycle)
    }

    @Test
    fun keepsTheGivenOptions() {
        val c =
            builder()
                .baseUrl(BASE)
                .disableCache()
                .offline()
                .initTimeout(2.seconds)
                .disableStream()
                .pollInterval(45.seconds)
                .flushInterval(20.seconds)
                .sendContext()
                .logger(SilentLogger)
                .build()
        assertTrue(c.disableCache)
        assertTrue(c.offline)
        assertEquals(2.seconds, c.initTimeout)
        assertTrue(c.disableStream)
        assertEquals(45.seconds, c.pollInterval)
        assertEquals(20.seconds, c.flushInterval)
        assertTrue(c.sendContext)
        assertSame(SilentLogger, c.logger)
    }

    @Test
    fun flagsCanBeTurnedOffAgain() {
        val c =
            builder()
                .baseUrl(BASE)
                .disableCache()
                .disableCache(false)
                .disableStream(false)
                .sendContext(false)
                .offline(false)
                .build()
        assertFalse(c.disableCache)
        assertFalse(c.disableStream)
        assertFalse(c.sendContext)
        assertFalse(c.offline)
    }

    @Test
    fun keepsTheCacheDirectoryAndLifecycleOfTheBuilder() {
        val dir = File("/tmp/megooflags")
        val lifecycle = LifecycleSource { {} }
        val c = MegooFlagsConfig.Builder(KEY, dir, lifecycle).baseUrl(BASE).build()
        assertEquals(dir, c.cacheDir)
        assertSame(lifecycle, c.lifecycle)
    }

    @Test
    fun requiresAPositiveInitTimeout() {
        for (bad in listOf(Duration.ZERO, (-1).seconds)) {
            assertConfigError(builder().baseUrl(BASE).initTimeout(bad), "megooflags: initTimeout must be greater than 0")
        }
        assertEquals(
            1.milliseconds,
            builder()
                .baseUrl(BASE)
                .initTimeout(1.milliseconds)
                .build()
                .initTimeout,
        )
    }

    @Test
    fun raisesThePollIntervalTo15Seconds() {
        assertEquals(
            15.seconds,
            builder()
                .baseUrl(BASE)
                .pollInterval(1.seconds)
                .build()
                .pollInterval,
        )
        assertEquals(
            15.seconds,
            builder()
                .baseUrl(BASE)
                .pollInterval(15.seconds)
                .build()
                .pollInterval,
        )
    }

    @Test
    fun clampsTheFlushIntervalTo10To60Seconds() {
        val flush = { d: Duration ->
            builder()
                .baseUrl(BASE)
                .flushInterval(d)
                .build()
                .flushInterval
        }
        assertEquals(10.seconds, flush(1.seconds))
        assertEquals(10.seconds, flush(10.seconds))
        assertEquals(60.seconds, flush(60.seconds))
        assertEquals(60.seconds, flush(600.seconds))
    }

    // bootstrap

    @Test
    fun parsesTheBootstrap() {
        val json =
            """
            {"results": [
              {"key": "a", "value": {"x": [1, 1.5]}, "variant": "on", "reason": "TARGETING_MATCH",
               "error_code": null, "error_message": null, "extra": 1},
              {"key": "b", "value": null, "variant": null, "reason": "ERROR", "error_code": "GENERAL"}
            ]}
            """.trimIndent()
        val c = builder().baseUrl(BASE).bootstrap(json).build()
        assertEquals(
            listOf(
                EvaluateResult("a", mapOf("x" to listOf(1L, 1.5)), "on", "TARGETING_MATCH", null, null),
                EvaluateResult("b", null, null, "ERROR", "GENERAL", null),
            ),
            c.bootstrap,
        )
    }

    @Test
    fun refusesAnInvalidBootstrap() {
        val item = """"key": "a", "value": 1, "variant": "v", "reason": "STATIC", "error_code": null"""
        val cases =
            listOf(
                """{"results": [""" to "not valid JSON",
                // org.json reads unquoted text as a string.
                "not json" to "not an object",
                "[]" to "not an object",
                """{"results": {}}""" to "results: not an array",
                "{}" to "results: missing",
                """{"results": [1]}""" to "results[0]: not an object",
                """{"results": [{"value": 1, "variant": "v", "reason": "STATIC", "error_code": null}]}""" to
                    "results[0].key: missing",
                """{"results": [{"key": 1, "value": 1, "variant": "v", "reason": "STATIC", "error_code": null}]}""" to
                    "results[0].key: not a string",
                """{"results": [{"key": "a", "variant": "v", "reason": "STATIC", "error_code": null}]}""" to
                    "results[0].value: missing",
                """{"results": [{"key": "a", "value": 1, "reason": "STATIC", "error_code": null}]}""" to
                    "results[0].variant: missing",
                """{"results": [{"key": "a", "value": 1, "variant": 2, "reason": "STATIC", "error_code": null}]}""" to
                    "results[0].variant: not a string or null",
                """{"results": [{"key": "a", "value": 1, "variant": "v", "reason": null, "error_code": null}]}""" to
                    "results[0].reason: not a string",
                """{"results": [{"key": "a", "value": 1, "variant": "v", "reason": "STATIC"}]}""" to
                    "results[0].error_code: missing",
                """{"results": [{"key": "a", "value": 1, "variant": "v", "reason": "STATIC", "error_code": 5}]}""" to
                    "results[0].error_code: not a string or null",
                """{"results": [{$item, "error_message": false}]}""" to "results[0].error_message: not a string or null",
                """{"results": [{$item}, {$item}]}""" to "results[1].key: duplicate key",
            )
        for ((json, why) in cases) {
            assertConfigError(builder().baseUrl(BASE).bootstrap(json), "megooflags: invalid bootstrap: $why")
        }
    }
}
