package io.github.mmhy2003.megooflags.internal

import io.github.mmhy2003.megooflags.VERSION
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException
import java.net.ServerSocket

private const val KEY = "mf_client_secret123"
private const val RESULT_JSON =
    """{"key":"banner","value":"blue","variant":"blue","reason":"TARGETING_MATCH","error_code":null}"""
private val result = EvaluateResult("banner", "blue", "blue", "TARGETING_MATCH", null, null)

class EvaluatorTest {
    private val server = FakeServer()
    private val evaluator = Evaluator(server.baseUrl, KEY, UrlConnectionClient())

    @After
    fun tearDown() {
        server.close()
    }

    /** failure returns what evaluate throws. */
    private suspend fun failure(e: Evaluator = evaluator): Throwable {
        try {
            val outcome = e.evaluate(emptyMap(), null)
            fail("no error, got $outcome")
        } catch (t: Throwable) {
            if (t is AssertionError) throw t
            return t
        }
        error("unreachable")
    }

    @Test
    fun returnsTheResultsAndTheEtagOfA200() =
        runTest {
            server.replyEvaluate(200, """{"results":[$RESULT_JSON]}""", mapOf("ETag" to "\"e1\""))
            assertEquals(EvaluateOutcome.Ok(listOf(result), "\"e1\""), evaluator.evaluate(emptyMap(), null))
        }

    @Test
    fun returnsANullEtagWhenTheResponseHasNone() =
        runTest {
            server.replyEvaluate(200, """{"results":[]}""")
            assertEquals(EvaluateOutcome.Ok(emptyList(), null), evaluator.evaluate(emptyMap(), null))
        }

    @Test
    fun postsTheContextWithoutFlagsWithTheKeyAsABearerToken() =
        runTest {
            evaluator.evaluate(mapOf("key" to "user-1", "plan" to "pro"), null)
            val r = server.requests.single()
            assertEquals("POST", r.method)
            assertEquals("/sdk/v1/evaluate", r.path)
            assertEquals("Bearer $KEY", r.header("Authorization"))
            assertEquals("application/json", r.header("Content-Type"))
            assertEquals("application/json", r.header("Accept"))
            assertEquals("megooflags-android/$VERSION", r.header("User-Agent"))
            assertNull(r.header("If-None-Match"))
            assertEquals(mapOf("context" to mapOf("key" to "user-1", "plan" to "pro")), parseJson(r.body))
        }

    @Test
    fun sendsIfNoneMatchWhenAnEtagIsGiven() =
        runTest {
            evaluator.evaluate(emptyMap(), "\"e1\"")
            assertEquals("\"e1\"", server.requests.single().header("If-None-Match"))
        }

    @Test
    fun reportsA304AsNotModified() =
        runTest {
            server.replyEvaluate(304, headers = mapOf("ETag" to "\"e1\""))
            assertEquals(EvaluateOutcome.NotModified("\"e1\""), evaluator.evaluate(emptyMap(), "\"e1\""))
        }

    @Test
    fun throwsRetryAfterWithTheRetryAfterSecondsOn429() =
        runTest {
            server.replyEvaluate(429, headers = mapOf("Retry-After" to "7"))
            assertEquals(SdkException.RetryAfter(7), failure())
        }

    @Test
    fun throwsFatalOn401And403() =
        runTest {
            for (status in listOf(401, 403)) {
                server.replyEvaluate(status, """{"error":"refused"}""")
                assertEquals(SdkException.Fatal(status), failure())
            }
        }

    @Test
    fun throwsHttpOn503() =
        runTest {
            server.replyEvaluate(503, "unavailable")
            assertEquals(SdkException.Http(503), failure())
        }

    @Test
    fun throwsInvalidResponseOnAnUnreadableBody() =
        runTest {
            for (body in listOf("<html>", "{}", """{"results":[{"key":1}]}""")) {
                server.replyEvaluate(200, body)
                val error = failure()
                assertTrue("$body: $error", error is SdkException.InvalidResponse)
                val message = error.message!!
                assertTrue(message, message.startsWith("megooflags: invalid response: evaluate: "))
                assertFalse(message, message.contains("secret123"))
            }
        }

    @Test
    fun passesANetworkErrorThrough() =
        runTest {
            val port = ServerSocket(0).use { it.localPort }
            val closed = Evaluator("http://127.0.0.1:$port", KEY, UrlConnectionClient())
            assertTrue(failure(closed) is IOException)
        }
}
