// Sends the buffered exposures to POST /sdk/v1/events in the background: on an interval, at 1000 buffered items,
// on request (the app was hidden) and on close. No counts are ever sent: the server counts remote evaluations.
package io.github.mmhy2003.megooflags.internal

import io.github.mmhy2003.megooflags.MegooFlagsLogger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.random.Random
import kotlin.time.Duration
import kotlin.time.Duration.Companion.seconds

/**
 * Batcher runs its timers and sends on [scope] and the blocking calls of [client] on [io]. [onFatal] receives the
 * SdkException.Fatal of a refused key, after the batcher stopped. Every method may be called from any thread.
 */
internal class Batcher(
    private val scope: CoroutineScope,
    private val client: HttpClient,
    baseUrl: String,
    private val sdkKey: String,
    private val exposures: Exposures,
    private val flushInterval: Duration,
    private val logger: MegooFlagsLogger,
    private val onFatal: (SdkException) -> Unit,
    private val io: CoroutineDispatcher = Dispatchers.IO,
    random: Random = Random.Default,
) {
    private val url = sdkUrl(baseUrl, "events").toString()
    private val backoff = Backoff(1_000, 60_000, random)
    private val lock = Any()
    private var timer: Job? = null

    /** The retry wait; while it runs, a flush sends nothing. */
    private var retry: Job? = null
    private var flushing: Job? = null
    private var finalRun: Job? = null
    private var closing: Job? = null

    @Volatile
    private var stopped = false

    /** start runs the flush timer and flushes at 1000 buffered items; it is idempotent. */
    fun start() {
        synchronized(lock) {
            if (timer != null || stopped || closing != null) return
            exposures.onFull = { scope.launch { flush() } }
            timer =
                scope.launch {
                    while (true) {
                        delay(flushInterval)
                        flush()
                    }
                }
        }
    }

    /**
     * flush sends the buffered exposures in batches of at most 1000, joining the flush in flight if there is one.
     * It sends nothing while a retry wait runs and stops at the first batch that must be retried. While closing it
     * waits for the close.
     */
    suspend fun flush() {
        val job =
            synchronized(lock) {
                closing ?: flushing ?: scope
                    .launch(start = CoroutineStart.LAZY) {
                        try {
                            run(final = false)
                        } finally {
                            synchronized(lock) { flushing = null }
                        }
                    }.also {
                        flushing = it
                        it.start()
                    }
            }
        job.join()
    }

    /**
     * close stops the timers and flushes once, ignoring a retry wait, then stops for good; it returns within
     * [timeout], abandoning a request still in flight. A second call waits for the first.
     */
    suspend fun close(timeout: Duration = 5.seconds) {
        val job =
            synchronized(lock) {
                closing ?: run {
                    clearTimers()
                    val previous = flushing
                    val final =
                        scope.launch {
                            previous?.join()
                            run(final = true)
                        }
                    finalRun = final
                    scope
                        .launch {
                            withTimeoutOrNull(timeout) { final.join() }
                            stop()
                        }.also { closing = it }
                }
            }
        job.join()
    }

    /** stop ends the timers and abandons a request in flight, sending nothing more. */
    fun stop() {
        synchronized(lock) {
            stopped = true
            clearTimers()
            flushing?.cancel()
            finalRun?.cancel()
            exposures.onFull = null
        }
    }

    /** clearTimers cancels the flush timer and the retry wait; call it holding the lock. */
    private fun clearTimers() {
        timer?.cancel()
        retry?.cancel()
        timer = null
        retry = null
    }

    private suspend fun run(final: Boolean) {
        if (!final && synchronized(lock) { retry != null }) return
        while (!stopped && exposures.size > 0) {
            val unsent = send(exposures.drain(BATCH_SIZE)) ?: continue
            if (!stopped) exposures.restore(unsent)
            return
        }
    }

    /** send posts [batch], splitting it on 413, and returns what must be retried, after scheduling the retry. */
    private suspend fun send(batch: List<Exposure>): List<Exposure>? {
        if (stopped) return null
        val body = writeCanonical(mapOf("exposures" to batch.map { it.toJson() }))
        val headers = sdkHeaders(sdkKey, "application/json", json = true)
        val failure =
            try {
                val r = withContext(io) { client.request("POST", url, headers, body) }
                statusError(r.status, r.headers)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                e
            }
        if (stopped) return null
        when {
            failure == null -> {
                backoff.reset()
                logger.debug("megooflags: sent ${batch.size} exposures")
                return null
            }

            failure is SdkException.Fatal -> {
                stop()
                onFatal(failure)
                return null
            }

            failure is SdkException.Http && failure.status == 413 -> {
                return split(batch)
            }

            failure is SdkException.Http && failure.status < 500 -> {
                logger.warn(
                    "megooflags: the server refused ${batch.size} exposures (HTTP ${failure.status}); dropping them",
                )
                return null
            }

            else -> {
                val wait = if (failure is SdkException.RetryAfter) failure.seconds * 1_000 else backoff.next()
                logger.debug("megooflags: sending exposures failed (${failureReason(failure)}); will retry")
                retryIn(wait)
                return batch
            }
        }
    }

    /**
     * split sends [batch] as two halves; a single item cannot be split and is dropped. When the first half must be
     * retried, the second is kept behind it.
     */
    private suspend fun split(batch: List<Exposure>): List<Exposure>? {
        if (batch.size <= 1) {
            logger.warn("megooflags: the server refused an exposure as too large (HTTP 413); dropping it")
            return null
        }
        val half = batch.size / 2
        val second = batch.subList(half, batch.size)
        send(batch.subList(0, half))?.let { return it + second }
        return send(second)
    }

    private fun retryIn(wait: Long) {
        synchronized(lock) {
            if (stopped) return
            retry?.cancel()
            val job =
                scope.launch(start = CoroutineStart.LAZY) {
                    delay(wait)
                    synchronized(lock) {
                        if (retry !== coroutineContext[Job]) return@launch
                        retry = null
                    }
                    flush()
                }
            retry = job
            job.start()
        }
    }
}
