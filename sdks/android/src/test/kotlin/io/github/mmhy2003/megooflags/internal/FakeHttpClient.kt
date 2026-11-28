// An in-memory HttpClient for the batcher tests: it answers each request from a script, without a socket.
package io.github.mmhy2003.megooflags.internal

import java.io.IOException
import java.net.URL
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** One scripted answer of [FakeHttpClient]. */
internal sealed class Answer {
    data class Status(
        val status: Int,
        val headers: Map<String, String> = emptyMap(),
    ) : Answer()

    /** Throws an IOException, like a refused connection. */
    object Network : Answer()

    /** Blocks the calling thread until the client is released; use it with a real IO dispatcher. */
    object Hang : Answer()
}

/**
 * FakeHttpClient answers requests with [answers] in order; the last answer repeats. An Int is a status. Requests
 * keep their path, lowercase header names and body.
 */
internal class FakeHttpClient(
    vararg answers: Any,
) : HttpClient {
    private val script = answers.map { if (it is Int) Answer.Status(it) else it as Answer }.toMutableList()
    private val released = CountDownLatch(1)

    val requests: MutableList<Request> = CopyOnWriteArrayList()

    /** The number of exposures in each request body, in order. */
    val sizes: List<Int> get() = requests.map { exposuresOf(it).size }

    /** The flag keys of the exposures of request [i]. */
    fun flagKeys(i: Int): List<Any?> = exposuresOf(requests[i]).map { (it as Map<*, *>)["flag_key"] }

    /** release ends every hanging request with an IOException. */
    fun release() {
        released.countDown()
    }

    override fun request(
        method: String,
        url: String,
        headers: Map<String, String>,
        body: String?,
    ): HttpResult {
        requests.add(Request(method, URL(url).path, headers.mapKeys { it.key.lowercase() }, body ?: ""))
        val answer = synchronized(script) { if (script.size > 1) script.removeAt(0) else script.single() }
        return when (answer) {
            is Answer.Status -> {
                val lower = answer.headers.mapKeys { it.key.lowercase() }
                HttpResult(answer.status, { lower[it.lowercase()] }, "")
            }

            Answer.Network -> {
                throw IOException("connection refused")
            }

            Answer.Hang -> {
                released.await(30, TimeUnit.SECONDS)
                throw IOException("released")
            }
        }
    }

    override fun stream(
        url: String,
        headers: Map<String, String>,
    ): StreamConnection = throw UnsupportedOperationException("FakeHttpClient has no streams")

    private fun exposuresOf(r: Request): List<*> = (parseJson(r.body) as Map<*, *>)["exposures"] as List<*>
}
