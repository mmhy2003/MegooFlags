// Remote evaluation: posts the request context to /sdk/v1/evaluate and returns the evaluated results, or
// NotModified when the ETag still matches.
package io.github.mmhy2003.megooflags.internal

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

internal sealed class EvaluateOutcome {
    data class Ok(
        val results: List<EvaluateResult>,
        val etag: String?,
    ) : EvaluateOutcome()

    data class NotModified(
        val etag: String?,
    ) : EvaluateOutcome()
}

/** Evaluator runs the blocking request of [client] on [io]. */
internal class Evaluator(
    baseUrl: String,
    private val sdkKey: String,
    private val client: HttpClient,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) {
    private val url = sdkUrl(baseUrl, "evaluate").toString()

    /**
     * evaluate sends [body] as the context, with If-None-Match when [etag] is set. It throws the statusError of an
     * unexpected status, SdkException.InvalidResponse for an unreadable 200 body, and the client's IOException on a
     * network failure.
     */
    suspend fun evaluate(
        body: Map<String, Any?>,
        etag: String?,
    ): EvaluateOutcome {
        val headers = sdkHeaders(sdkKey, "application/json", etag = etag, json = true)
        val r = withContext(io) { client.request("POST", url, headers, writeCanonical(mapOf("context" to body))) }
        statusError(r.status, r.headers)?.let { throw it }
        if (r.status == 304) {
            return EvaluateOutcome.NotModified(r.headers("ETag"))
        }
        return try {
            EvaluateOutcome.Ok(parseResults(r.body, "evaluate"), r.headers("ETag"))
        } catch (e: IllegalArgumentException) {
            throw SdkException.InvalidResponse(e.message!!)
        }
    }
}
