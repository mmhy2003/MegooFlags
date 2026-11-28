// Runs MegooFlagsProvider through OpenFeatureAPI against a running MegooFlags stack: evaluation, toggles over the
// stream, a context change, exposures counted by the server and the cache serving values with the API unreachable.
// Set MEGOOFLAGS_E2E_URL (for example http://localhost:8080) and MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh
// prints one) and run ./gradlew testDebugUnitTest -Pe2e.
package io.github.mmhy2003.megooflags.e2e

import dev.openfeature.kotlin.sdk.ImmutableContext
import dev.openfeature.kotlin.sdk.OpenFeatureAPI
import dev.openfeature.kotlin.sdk.events.OpenFeatureProviderEvents
import io.github.mmhy2003.megooflags.MegooFlagsConfig
import io.github.mmhy2003.megooflags.MegooFlagsLogger
import io.github.mmhy2003.megooflags.MegooFlagsProvider
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.net.HttpURLConnection
import java.net.ServerSocket
import java.net.URL
import java.nio.file.Files
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger
import kotlin.time.Duration.Companion.seconds

private val base = (System.getenv("MEGOOFLAGS_E2E_URL") ?: "").trimEnd('/')
private val token = System.getenv("MEGOOFLAGS_API_TOKEN") ?: ""

/** Keeps every log line as "<level> <message>". */
private class CapturingLogger : MegooFlagsLogger {
    val lines: MutableList<String> = CopyOnWriteArrayList()

    fun count(text: String): Int = lines.count { it.contains(text) }

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

/** api calls the management API with the API token and returns the status and the JSON object of the body. */
private fun api(
    method: String,
    path: String,
    body: JSONObject? = null,
): Pair<Int, JSONObject> {
    val conn = URL(base + path).openConnection() as HttpURLConnection
    try {
        conn.requestMethod = method
        conn.connectTimeout = 10_000
        conn.readTimeout = 10_000
        conn.setRequestProperty("Authorization", "Bearer $token")
        conn.setRequestProperty("Accept", "application/json")
        if (body != null) {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        val status = conn.responseCode
        val text = (if (status < 400) conn.inputStream else conn.errorStream)?.use { it.readBytes().decodeToString() } ?: ""
        return status to (if (text.isBlank()) JSONObject() else JSONObject(text))
    } finally {
        conn.disconnect()
    }
}

/** mustApi calls api and fails unless the status is [want]. */
private fun mustApi(
    want: Int,
    method: String,
    path: String,
    body: JSONObject? = null,
): JSONObject {
    val (status, json) = api(method, path, body)
    check(status == want) { "$method $path: status $status, want $want: $json" }
    return json
}

/** until polls [check] every [intervalMs] until it holds or [timeoutMs] passes. */
private fun until(
    what: String,
    timeoutMs: Long,
    intervalMs: Long = 50,
    check: () -> Boolean,
) {
    val deadline = System.nanoTime() + timeoutMs * 1_000_000
    while (!check()) {
        check(System.nanoTime() < deadline) { "$what: not within $timeoutMs ms" }
        Thread.sleep(intervalMs)
    }
}

/** closedPort returns a local port nothing listens on: the one the system chose for a socket that is now closed. */
private fun closedPort(): Int = ServerSocket(0).use { it.localPort }

private fun user(targetingKey: String) = ImmutableContext(targetingKey)

class E2eTest {
    private var project: String? = null
    private val cacheDir: File = Files.createTempDirectory("megooflags-e2e").toFile()
    private val collectors = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @Before
    fun skipWithoutStack() {
        assumeTrue("set MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN", base.isNotEmpty() && token.isNotEmpty())
    }

    @After
    fun tearDown() {
        runBlocking { OpenFeatureAPI.shutdown() }
        collectors.cancel()
        project?.let { runCatching { api("DELETE", "/api/projects/$it") } }
        cacheDir.deleteRecursively()
    }

    /**
     * setUpProject creates project sdkandroid-<8 hex> with environment e2e, the boolean flag e2e-flag turned on
     * (false for vip-user, true for everyone else) and a client key; it returns the flag's environment path and the
     * key.
     */
    private fun setUpProject(): Pair<String, String> {
        val key = "sdkandroid-${UUID.randomUUID().toString().take(8)}"
        val flagPath = "/api/projects/$key/flags/e2e-flag/environments/e2e"
        mustApi(
            201,
            "POST",
            "/api/projects",
            JSONObject()
                .put("key", key)
                .put("name", key)
                .put("environments", JSONArray().put(JSONObject().put("key", "e2e").put("name", "E2E"))),
        )
        project = key
        mustApi(
            201,
            "POST",
            "/api/projects/$key/flags",
            JSONObject().put("key", "e2e-flag").put("name", "E2E").put("type", "boolean"),
        )
        mustApi(200, "POST", "$flagPath/toggle", JSONObject().put("enabled", true))
        val config = mustApi(200, "GET", flagPath)
        mustApi(
            200,
            "PUT",
            flagPath,
            JSONObject()
                .put("version", config.get("version"))
                .put("enabled", true)
                .put(
                    "targets",
                    JSONArray().put(JSONObject().put("variation", "false").put("values", JSONArray().put("vip-user"))),
                ).put("rules", JSONArray())
                .put("fallthrough", JSONObject().put("variation", "true"))
                .put("prerequisites", JSONArray()),
        )
        val created =
            mustApi(
                201,
                "POST",
                "/api/projects/$key/environments/e2e/sdk-keys",
                JSONObject().put("name", "android-e2e").put("kind", "client"),
            )
        val sdkKey = created.optString("key")
        check(sdkKey.startsWith("mf_client_")) { "POST sdk-keys returned no client key" }
        return flagPath to sdkKey
    }

    private fun register(
        provider: MegooFlagsProvider,
        context: ImmutableContext,
    ) = runBlocking { OpenFeatureAPI.setProviderAndWait(provider, context, Dispatchers.Default) }

    @Test
    fun evaluatesFollowsTogglesAndContextSendsExposuresAndServesTheCache() {
        val (flagPath, sdkKey) = setUpProject()
        val logger = CapturingLogger()
        val provider =
            MegooFlagsProvider(
                MegooFlagsConfig
                    .Builder(sdkKey, cacheDir = cacheDir, lifecycle = null)
                    .baseUrl(base)
                    .flushInterval(10.seconds)
                    .logger(logger)
                    .build(),
            )
        val changes = AtomicInteger()
        // Undispatched: the shared flow is subscribed before launch returns, so no event is missed.
        collectors.launch(start = CoroutineStart.UNDISPATCHED) {
            provider.observe().collect {
                if (it is OpenFeatureProviderEvents.ProviderConfigurationChanged) changes.incrementAndGet()
            }
        }
        register(provider, user("user-1"))
        val client = OpenFeatureAPI.getClient()

        // Check 1: the server evaluates the flag for the context.
        assertEquals(true, client.getBooleanValue("e2e-flag", false))

        // Check 2: toggles arrive over the stream within 5 s.
        until("the stream connected", 5_000) { logger.count("megooflags: stream connected") > 0 }
        mustApi(200, "POST", "$flagPath/toggle", JSONObject().put("enabled", false))
        until("e2e-flag false after the toggle", 5_000) { !client.getBooleanValue("e2e-flag", true) }
        until("a configuration-changed event", 5_000) { changes.get() > 0 }
        mustApi(200, "POST", "$flagPath/toggle", JSONObject().put("enabled", true))
        until("e2e-flag true after the second toggle", 5_000) { client.getBooleanValue("e2e-flag", false) }

        // Check 3: a new targeting key is evaluated by the server.
        runBlocking { OpenFeatureAPI.setEvaluationContextAndWait(user("vip-user")) }
        val vip = client.getBooleanDetails("e2e-flag", true)
        assertEquals(false, vip.value)
        assertEquals("TARGETING_MATCH", vip.reason)

        // Check 4: exposures are sent at shutdown and counted.
        repeat(5) { client.getBooleanValue("e2e-flag", true) }
        provider.shutdown()
        assertTrue(logger.lines.toString(), logger.count("megooflags: sent ") > 0)
        until("evaluations total >= 1", 10_000, 500) {
            mustApi(200, "GET", "$flagPath/evaluations?range=24h").optInt("total") >= 1
        }

        // Check 5: with the API unreachable, a new provider serves the values the first one left in the same cache
        // directory.
        val offline =
            MegooFlagsProvider(
                MegooFlagsConfig
                    .Builder(sdkKey, cacheDir = cacheDir, lifecycle = null)
                    .baseUrl("http://127.0.0.1:${closedPort()}")
                    .initTimeout(1.seconds)
                    .logger(CapturingLogger())
                    .build(),
            )
        register(offline, user("vip-user"))
        val cached = OpenFeatureAPI.getClient().getBooleanDetails("e2e-flag", true)
        assertEquals(false, cached.value)
        assertEquals("CACHED", cached.reason)
    }
}
