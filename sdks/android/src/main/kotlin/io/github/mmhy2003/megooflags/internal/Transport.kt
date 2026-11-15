// HTTP helpers shared by the evaluate, stream and events requests: headers, connections, status mapping and
// Retry-After parsing. The SDK key travels in the Authorization header only, and no message ever contains it.
package io.github.mmhy2003.megooflags.internal

import io.github.mmhy2003.megooflags.VERSION
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.GregorianCalendar
import java.util.TimeZone
import kotlin.concurrent.thread

/** The ways an SDK request fails. */
internal sealed class SdkException(
    message: String,
) : Exception(message) {
    /** The server refused the SDK key (401 or 403); retrying cannot succeed. */
    data class Fatal(
        val status: Int?,
    ) : SdkException(if (status == null) "megooflags: SDK key refused" else "megooflags: SDK key refused ($status)")

    /** The stream's revoked event: the key was revoked. */
    class Revoked : SdkException("megooflags: SDK key refused (revoked)")

    /** A 429; the wait, in seconds, the server asked for. */
    data class RetryAfter(
        val seconds: Long,
    ) : SdkException("megooflags: rate limited, retry after ${seconds}s")

    /** Any other unexpected status. */
    data class Http(
        val status: Int,
    ) : SdkException("megooflags: HTTP $status")

    /** A response the SDK could not read. */
    data class InvalidResponse(
        val why: String,
    ) : SdkException("megooflags: invalid response: $why")
}

/** sdkUrl is `<baseUrl>/sdk/v1/<path>`, trailing slashes of baseUrl removed. */
internal fun sdkUrl(
    baseUrl: String,
    path: String,
): URL = URL("${baseUrl.trimEnd('/')}/sdk/v1/$path")

/**
 * sdkHeaders are the SDK request headers: the key as a bearer token, Accept, Content-Type when [json] (a JSON
 * body), If-None-Match and Last-Event-ID when given. openConnection adds the User-Agent.
 */
internal fun sdkHeaders(
    sdkKey: String,
    accept: String,
    etag: String? = null,
    lastEventId: String? = null,
    json: Boolean = false,
): Map<String, String> =
    buildMap {
        put("Authorization", "Bearer $sdkKey")
        put("Accept", accept)
        if (json) put("Content-Type", "application/json")
        if (etag != null) put("If-None-Match", etag)
        if (lastEventId != null) put("Last-Event-ID", lastEventId)
    }

/** The connect timeout of every request, in milliseconds. */
internal const val CONNECT_TIMEOUT_MS: Int = 10_000

/**
 * openConnection prepares, without connecting, a [method] request to [url] with [headers] and the SDK's
 * User-Agent, a 10 s connect timeout and no HTTP cache. Callers set the read timeout and the body.
 */
internal fun openConnection(
    url: URL,
    method: String,
    headers: Map<String, String>,
): HttpURLConnection {
    val conn = url.openConnection() as HttpURLConnection
    conn.requestMethod = method
    conn.connectTimeout = CONNECT_TIMEOUT_MS
    conn.useCaches = false
    for ((name, value) in headers) {
        conn.setRequestProperty(name, value)
    }
    conn.setRequestProperty("User-Agent", "megooflags-android/$VERSION")
    return conn
}

/**
 * statusError maps a response status to an error: null for 2xx and 304. [header] reads a response header;
 * [nowMillis] is the current time, for an HTTP-date Retry-After.
 */
internal fun statusError(
    status: Int,
    header: (String) -> String?,
    nowMillis: Long = System.currentTimeMillis(),
): SdkException? =
    when (status) {
        in 200..299, 304 -> null
        401, 403 -> SdkException.Fatal(status)
        429 -> SdkException.RetryAfter(parseRetryAfter(header("Retry-After"), nowMillis))
        else -> SdkException.Http(status)
    }

/** The wait, in seconds, when Retry-After is missing or invalid. */
private const val DEFAULT_RETRY_AFTER = 60L

private val DELTA_SECONDS = Regex("^[+-]?[0-9]+$")

/**
 * parseRetryAfter reads a Retry-After value as delta-seconds or an HTTP-date and returns whole seconds (a partial
 * second rounds up). A negative number or a date in the past is 0; a missing or invalid value is 60.
 */
internal fun parseRetryAfter(
    value: String?,
    nowMillis: Long,
): Long {
    val v = value?.trim() ?: ""
    if (DELTA_SECONDS.matches(v)) {
        return v.toLongOrNull()?.coerceAtLeast(0) ?: DEFAULT_RETRY_AFTER
    }
    val at = parseHttpDate(v) ?: return DEFAULT_RETRY_AFTER
    val wait = at - nowMillis
    return if (wait <= 0) 0 else (wait + 999) / 1_000
}

private val MONTHS = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

// The three HTTP-date forms of RFC 9110 §5.6.7.
private val IMF_FIXDATE =
    Regex("^[A-Z][a-z]{2}, ([0-9]{2}) ([A-Z][a-z]{2}) ([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$")
private val RFC_850 =
    Regex("^[A-Z][a-z]+, ([0-9]{2})-([A-Z][a-z]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT$")
private val ASCTIME =
    Regex("^[A-Z][a-z]{2} ([A-Z][a-z]{2}) ([ 0-9][0-9]) ([0-9]{2}):([0-9]{2}):([0-9]{2}) ([0-9]{4})$")

/** parseHttpDate returns the epoch milliseconds of an HTTP-date, or null. */
private fun parseHttpDate(v: String): Long? {
    IMF_FIXDATE.find(v)?.groupValues?.let { m ->
        return utc(year = m[3], month = m[2], day = m[1], hour = m[4], minute = m[5], second = m[6])
    }
    RFC_850.find(v)?.groupValues?.let { m ->
        // Two-digit years as Go's time.Parse reads them: 69 to 99 are 19xx.
        val yy = m[3].toInt()
        val year = if (yy >= 69) 1900 + yy else 2000 + yy
        return utc(year = year.toString(), month = m[2], day = m[1], hour = m[4], minute = m[5], second = m[6])
    }
    ASCTIME.find(v)?.groupValues?.let { m ->
        return utc(year = m[6], month = m[1], day = m[2], hour = m[3], minute = m[4], second = m[5])
    }
    return null
}

private fun utc(
    year: String,
    month: String,
    day: String,
    hour: String,
    minute: String,
    second: String,
): Long? {
    val mon = MONTHS.indexOf(month)
    if (mon < 0) {
        return null
    }
    val cal = GregorianCalendar(TimeZone.getTimeZone("UTC"))
    cal.isLenient = false
    cal.clear()
    cal.set(year.trim().toInt(), mon, day.trim().toInt(), hour.toInt(), minute.toInt(), second.toInt())
    // A non-lenient calendar refuses a day the month does not have, and hours, minutes or seconds out of range.
    return try {
        cal.timeInMillis
    } catch (e: IllegalArgumentException) {
        null
    }
}

/** The read timeout of the stream, in milliseconds: the dead timer of B7 (the server sends a line every 15 s). */
internal const val STREAM_READ_TIMEOUT_MS: Int = 45_000

/** The read timeout of the evaluate and events requests, in milliseconds. */
internal const val REQUEST_READ_TIMEOUT_MS: Int = 30_000

/** A whole response: [headers] reads a header by name (case-insensitive). */
internal class HttpResult(
    val status: Int,
    val headers: (String) -> String?,
    val body: String,
)

/**
 * An open stream response. [close] may be called from any thread and never blocks; it ends a read of [input] blocked
 * in another thread (on Android at once, on the JVM when the next data arrives).
 */
internal interface StreamConnection {
    val status: Int

    fun header(name: String): String?

    val input: InputStream

    fun close()
}

/** The HTTP seam of the evaluator, the streamer and the batcher. Its calls block: run them on an IO dispatcher. */
internal interface HttpClient {
    /** request sends [body] (when not null) and reads the whole response; a network failure throws IOException. */
    fun request(
        method: String,
        url: String,
        headers: Map<String, String>,
        body: String?,
    ): HttpResult

    /** stream opens a GET and returns once the status and headers arrived; the caller closes it. */
    fun stream(
        url: String,
        headers: Map<String, String>,
    ): StreamConnection
}

/** UrlConnectionClient is the HttpClient of the platform, on HttpURLConnection. */
internal class UrlConnectionClient(
    val streamReadTimeoutMs: Int = STREAM_READ_TIMEOUT_MS,
) : HttpClient {
    override fun request(
        method: String,
        url: String,
        headers: Map<String, String>,
        body: String?,
    ): HttpResult {
        val conn = openConnection(URL(url), method, headers)
        conn.readTimeout = REQUEST_READ_TIMEOUT_MS
        try {
            if (body != null) {
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val status = conn.responseCode
            val stream = if (status >= 400) conn.errorStream else conn.inputStream
            val text = stream?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
            return HttpResult(status, conn::getHeaderField, text)
        } catch (e: IOException) {
            conn.disconnect()
            throw e
        }
    }

    override fun stream(
        url: String,
        headers: Map<String, String>,
    ): StreamConnection {
        val conn = openConnection(URL(url), "GET", headers)
        conn.readTimeout = streamReadTimeoutMs
        try {
            val status = conn.responseCode
            val input = (if (status >= 400) conn.errorStream else conn.inputStream) ?: ByteArrayInputStream(ByteArray(0))
            return object : StreamConnection {
                override val status = status

                override fun header(name: String): String? = conn.getHeaderField(name)

                override val input = input

                // On Android, disconnect closes the socket, which makes a read blocked in another thread throw. On
                // the JVM it waits for that read to return (the chunked stream's read and close share a lock), so
                // it runs on its own thread: close never blocks its caller.
                override fun close() {
                    thread(isDaemon = true, name = "megooflags-stream-close") { conn.disconnect() }
                }
            }
        } catch (e: IOException) {
            conn.disconnect()
            throw e
        }
    }
}
