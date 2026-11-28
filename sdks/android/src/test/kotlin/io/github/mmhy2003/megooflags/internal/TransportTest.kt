package io.github.mmhy2003.megooflags.internal

import io.github.mmhy2003.megooflags.VERSION
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import kotlin.concurrent.thread

private const val KEY = "mf_client_x"

// 2026-11-14T12:00:00Z, a Saturday.
private const val NOW = 1_794_657_600_000L

private fun headers(h: Map<String, String> = emptyMap()): (String) -> String? =
    { name -> h.entries.firstOrNull { it.key.equals(name, ignoreCase = true) }?.value }

/** What a one-shot local server received. */
private class Received(
    val method: String,
    val path: String,
    val headers: Map<String, String?>,
)

/** Sends one request through [openConnection] to a one-shot local server and returns what it received. */
private fun roundTrip(
    method: String,
    requestHeaders: Map<String, String>,
): Received {
    ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { server ->
        var received: Received? = null
        val serving =
            thread {
                server.accept().use { socket ->
                    val reader = BufferedReader(InputStreamReader(socket.getInputStream(), Charsets.ISO_8859_1))
                    val (requestMethod, path) = reader.readLine().split(" ")
                    val got = mutableMapOf<String, String>()
                    while (true) {
                        val line = reader.readLine()
                        if (line.isNullOrEmpty()) break
                        val colon = line.indexOf(':')
                        got[line.substring(0, colon).lowercase()] = line.substring(colon + 1).trim()
                    }
                    repeat(got["content-length"]?.toInt() ?: 0) { reader.read() }
                    received =
                        Received(
                            requestMethod,
                            path,
                            listOf("Authorization", "Accept", "Content-Type", "User-Agent", "If-None-Match", "Last-Event-ID")
                                .associateWith { got[it.lowercase()] },
                        )
                    socket.getOutputStream().write("HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n".toByteArray())
                }
            }
        val conn = openConnection(URL("http://127.0.0.1:${server.localPort}/sdk/v1/evaluate"), method, requestHeaders)
        if (method == "POST") {
            conn.doOutput = true
            conn.outputStream.use { it.write("{}".toByteArray()) }
        }
        assertEquals(204, conn.responseCode)
        conn.disconnect()
        serving.join(5_000)
        return received!!
    }
}

class TransportTest {
    // sdkHeaders, sdkUrl and openConnection

    @Test
    fun sdkHeadersCarryTheKeyAsABearerToken() {
        assertEquals(
            mapOf("Authorization" to "Bearer mf_client_x", "Accept" to "application/json"),
            sdkHeaders(KEY, "application/json"),
        )
    }

    @Test
    fun sdkHeadersAddContentTypeIfNoneMatchAndLastEventId() {
        assertEquals(
            mapOf(
                "Authorization" to "Bearer mf_client_x",
                "Accept" to "text/event-stream",
                "Content-Type" to "application/json",
                "If-None-Match" to "\"e1\"",
                "Last-Event-ID" to "42",
            ),
            sdkHeaders(KEY, "text/event-stream", etag = "\"e1\"", lastEventId = "42", json = true),
        )
    }

    @Test
    fun sdkUrlJoinsTheBaseUrlWithoutDoubleSlashes() {
        for (base in listOf("https://flags.example.com", "https://flags.example.com/", "https://flags.example.com/base//")) {
            assertEquals("${base.trimEnd('/')}/sdk/v1/stream", sdkUrl(base, "stream").toString())
        }
    }

    @Test
    fun openConnectionSendsTheHeadersAndTheUserAgent() {
        val got = roundTrip("GET", sdkHeaders(KEY, "text/event-stream", lastEventId = "7"))
        assertEquals("GET", got.method)
        assertEquals("/sdk/v1/evaluate", got.path)
        assertEquals("Bearer mf_client_x", got.headers["Authorization"])
        assertEquals("text/event-stream", got.headers["Accept"])
        assertEquals("megooflags-android/$VERSION", got.headers["User-Agent"])
        assertEquals("7", got.headers["Last-Event-ID"])
        assertNull(got.headers["If-None-Match"])
    }

    @Test
    fun openConnectionSendsAPostWithContentType() {
        val got = roundTrip("POST", sdkHeaders(KEY, "application/json", etag = "\"e1\"", json = true))
        assertEquals("POST", got.method)
        assertEquals("application/json", got.headers["Content-Type"])
        assertEquals("\"e1\"", got.headers["If-None-Match"])
    }

    @Test
    fun openConnectionSetsTimeoutsAndDisablesCaches() {
        val conn = openConnection(URL("http://127.0.0.1:1/sdk/v1/evaluate"), "GET", emptyMap())
        assertEquals(10_000, conn.connectTimeout)
        assertFalse(conn.useCaches)
        assertEquals("GET", conn.requestMethod)
    }

    // statusError

    @Test
    fun successAndNotModifiedAreNoError() {
        for (status in listOf(200, 202, 204, 304)) {
            assertNull("$status", statusError(status, headers(), NOW))
        }
    }

    @Test
    fun unauthorizedAndForbiddenAreFatal() {
        for (status in listOf(401, 403)) {
            val err = statusError(status, headers(), NOW)
            assertEquals(SdkException.Fatal(status), err)
            assertEquals("megooflags: SDK key refused ($status)", err?.message)
        }
    }

    @Test
    fun tooManyRequestsCarriesIntegerRetryAfter() {
        val err = statusError(429, headers(mapOf("Retry-After" to "7")), NOW)
        assertEquals(SdkException.RetryAfter(7), err)
        assertEquals("megooflags: rate limited, retry after 7s", err?.message)
    }

    @Test
    fun tooManyRequestsReadsAnHttpDate() {
        assertEquals(
            SdkException.RetryAfter(30),
            statusError(429, headers(mapOf("retry-after" to "Sat, 14 Nov 2026 12:00:30 GMT")), NOW),
        )
    }

    @Test
    fun tooManyRequestsWithoutRetryAfterWaits60Seconds() {
        assertEquals(SdkException.RetryAfter(60), statusError(429, headers(), NOW))
    }

    @Test
    fun usesTheCurrentTimeByDefault() {
        val f = SimpleDateFormat("EEE, dd MMM yyyy HH:mm:ss 'GMT'", Locale.US)
        f.timeZone = TimeZone.getTimeZone("GMT")
        val at = f.format(Date(System.currentTimeMillis() + 3_600_000))
        val err = statusError(429, headers(mapOf("Retry-After" to at)))
        assertTrue("$err", err is SdkException.RetryAfter && err.seconds in 3599..3600)
    }

    @Test
    fun otherStatusesAreHttpErrors() {
        for (status in listOf(404, 413, 422, 500, 503)) {
            val err = statusError(status, headers(), NOW)
            assertEquals(SdkException.Http(status), err)
            assertEquals("megooflags: HTTP $status", err?.message)
        }
    }

    @Test
    fun revokedFatalWithoutStatusAndInvalidResponseMessages() {
        assertEquals("megooflags: SDK key refused (revoked)", SdkException.Revoked().message)
        assertEquals("megooflags: SDK key refused", SdkException.Fatal(null).message)
        assertEquals("megooflags: invalid response: not JSON", SdkException.InvalidResponse("not JSON").message)
    }

    @Test
    fun neverPutsTheKeyInAMessage() {
        val errors =
            listOf(
                SdkException.Fatal(401),
                SdkException.Fatal(403),
                SdkException.Fatal(null),
                SdkException.Revoked(),
                SdkException.RetryAfter(60),
                SdkException.Http(500),
                SdkException.InvalidResponse("x"),
            )
        for (err in errors) {
            assertFalse(err.message!!, err.message!!.contains("mf_client_"))
            assertFalse(err.toString(), err.toString().contains("mf_client_"))
        }
    }

    // parseRetryAfter

    @Test
    fun parsesRetryAfter() {
        val cases =
            listOf(
                "5" to 5L,
                "0" to 0L,
                "-1" to 0L,
                " 7 " to 7L,
                "Sat, 14 Nov 2026 12:00:30 GMT" to 30L,
                "Sat, 14 Nov 2026 11:00:00 GMT" to 0L,
                "Saturday, 14-Nov-26 12:00:30 GMT" to 30L,
                "Sat Nov 14 12:00:30 2026" to 30L,
                "Sat Nov  4 12:00:30 2026" to 0L,
            )
        for ((value, want) in cases) {
            assertEquals(value, want, parseRetryAfter(value, NOW))
        }
    }

    @Test
    fun roundsAPartialSecondOfAnHttpDateUp() {
        assertEquals(31L, parseRetryAfter("Sat, 14 Nov 2026 12:00:30 GMT", NOW - 500))
    }

    @Test
    fun invalidRetryAfterIs60Seconds() {
        val values =
            listOf(
                "abc",
                "",
                "1.5",
                "5s",
                "99999999999999999999",
                "Sat, 14 Nov 2026 12:00:30",
                "Sat, 31 Feb 2026 12:00:30 GMT",
                "Sat, 14 Nov 2026 24:00:30 GMT",
                null,
            )
        for (value in values) {
            assertEquals("$value", 60L, parseRetryAfter(value, NOW))
        }
    }
}
