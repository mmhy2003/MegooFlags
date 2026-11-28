// Keeps the store current for the current context: the first values (bootstrap, cache, then the network),
// refreshes on stream changes, the polling fallback, context changes and the stop rules.
package io.github.mmhy2003.megooflags.internal

import dev.openfeature.kotlin.sdk.EvaluationContext
import io.github.mmhy2003.megooflags.MegooFlagsConfig
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeout
import java.util.concurrent.atomic.AtomicLong
import kotlin.random.Random

/** What Sync reports. The calls run on Sync's scope or the caller's thread, under Sync's lock: return quickly. */
internal interface SyncEvents {
    /** Values arrived after start failed, or the stream is back after stale. */
    fun ready()

    /** The stream, connected before, keeps failing. */
    fun stale()

    /** A refresh changed these flags (sorted) of the previous values. */
    fun changed(flags: List<String>)

    /** The key was refused after start; everything has stopped. */
    fun fatal(error: SdkException)

    /** A refresh failed; it is retried. */
    fun error(error: Throwable)

    /** The key was refused, during start or after: stop what Sync does not own (the batcher). */
    fun fatalStop()
}

/** The ways start, a refresh or a context change fail without a server answer. */
internal class SyncException(
    message: String,
) : Exception(message)

private const val OFFLINE_MESSAGE = "megooflags: offline mode needs bootstrap values or a cache entry"

/**
 * Sync runs its refreshes, retries and poller on [scope]. [streamer] builds the streamer with Sync's hooks; offline
 * it is never called, nor is [evaluator]. The cache is not used with disableCache. Every method may be called from
 * any thread.
 */
internal class Sync(
    private val scope: CoroutineScope,
    private val config: MegooFlagsConfig,
    private val store: Store,
    cache: Cache,
    evaluator: Evaluates,
    streamer: ((StreamerHooks) -> StreamControl)?,
    private val events: SyncEvents,
    random: Random = Random.Default,
) {
    /** A refresh() caller waiting for the first run numbered [run] or later. */
    private class Waiter(
        val run: Long,
        val done: CompletableDeferred<Unit>,
    )

    /** The hooks the streamer is built with. */
    val streamerHooks =
        StreamerHooks(
            onConnected = { onConnected() },
            onChanged = { refresh() },
            onFatal = { stopAll(it) },
            onFailure = { n, _ -> onStreamFailure(n) },
        )

    private val cache = cache.takeUnless { config.disableCache }
    private val evaluator = evaluator.takeUnless { config.offline }
    private val streamer = if (config.offline) null else streamer?.invoke(streamerHooks)
    private val logger = config.logger
    private val poller = Poller(scope, config.pollInterval) { refresh() }
    private val backoff = Backoff(1_000, 60_000, random)
    private val lock = Any()

    /** Incremented by every context change; older answers are dropped. */
    private val generation = AtomicLong()

    /** Written under lock; read without it by [body], so flag reads never wait on cache I/O. */
    @Volatile
    private var currentBody: Map<String, Any?> = emptyMap()

    // Everything below is guarded by lock.
    private var hash = contextHash(emptyMap())

    /** The number of evaluate runs started. */
    private var runs = 0L
    private var inFlight: Job? = null
    private var queued = false
    private val waiters = mutableListOf<Waiter>()
    private var retry: Job? = null

    /** Set once stopped or fatal: the error later refreshes fail with. */
    private var halted: Exception? = null
    private var starting = false

    /** start failed without a fatal error: emit ready when values arrive. */
    private var readyPending = false
    private var deliveryStarted = false
    private var connected = false
    private var stale = false
    private var paused = false
    private var pollerWasRunning = false

    /** body is the request context of the current evaluation context. */
    val body: Map<String, Any?> get() = currentBody

    /**
     * start serves bootstrap values, then a cache entry for [context]. With values it returns at once and refreshes
     * in the background; without, it waits for the first refresh up to initTimeout. It throws the SdkException.Fatal
     * of a refused key, the error of another failure, or a SyncException on timeout or offline without values;
     * refreshes keep retrying unless fatal.
     */
    suspend fun start(context: EvaluationContext?) {
        val first =
            synchronized(lock) {
                starting = true
                setBody(toRequestContext(context))
                val key = targetingKeyOf(currentBody)
                config.bootstrap?.let { store.set(StoredResults(it, null, Source.BOOTSTRAP, hash, key)) }
                cache?.load(hash)?.let { store.set(it.copy(targetingKey = key)) }
                if (evaluator == null) {
                    starting = false
                    if (store.get() == null) throw SyncException(OFFLINE_MESSAGE)
                    return
                }
                val first = refresh()
                first.invokeOnCompletion { synchronized(lock) { startDelivery() } }
                if (store.get() != null) {
                    starting = false
                    return
                }
                first
            }
        try {
            withTimeout(config.initTimeout) { first.await() }
        } catch (e: TimeoutCancellationException) {
            failedStart(SyncException("megooflags: no flag values within ${config.initTimeout}"))
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            failedStart(e)
        } finally {
            synchronized(lock) { starting = false }
        }
    }

    /** failedStart throws [e] unless values arrived meanwhile; otherwise ready follows when they do. */
    private fun failedStart(e: Exception) {
        synchronized(lock) {
            if (halted == null && store.get() != null) return
            readyPending = halted == null
            throw e
        }
    }

    /**
     * refresh evaluates the current context. At most one evaluate is in flight and one queued; calls while one is
     * queued share it. The result completes when a run started after the call stores values for the current
     * context, and fails when that run fails (a retry follows unless fatal). Offline it is complete at once.
     */
    fun refresh(): Deferred<Unit> =
        synchronized(lock) {
            halted?.let { return CompletableDeferred<Unit>().apply { completeExceptionally(it) } }
            if (evaluator == null) return CompletableDeferred(Unit)
            val done = CompletableDeferred<Unit>()
            waiters.add(Waiter(runs + 1, done))
            kick()
            done
        }

    /**
     * setContext switches to [context]. A new targeting key clears the store and the cache entry at once; other
     * changes keep serving the old values until the new ones arrive. It returns when the new context's values are
     * stored and throws when that evaluate fails, while retries continue. Offline or stopped it fetches nothing,
     * but a new targeting key still clears the store and the cache entry.
     */
    suspend fun setContext(context: EvaluationContext?) {
        val done =
            synchronized(lock) {
                val next = toRequestContext(context)
                if (targetingKeyOf(next) != targetingKeyOf(currentBody)) {
                    store.set(null)
                    cache?.remove()
                }
                if (evaluator != null && halted == null) {
                    generation.incrementAndGet()
                    // The answer in flight is for the old context: drop it and evaluate the new one at once.
                    inFlight?.cancel()
                    inFlight = null
                    queued = false
                }
                setBody(next)
                refresh()
            }
        done.await()
    }

    /** pause closes the stream and stops the poller (the app is hidden). */
    fun pause() {
        synchronized(lock) {
            if (halted != null || paused) return
            paused = true
            pollerWasRunning = poller.running
            poller.stop()
            streamer?.pause()
        }
    }

    /** resume reopens the stream, and the poller when it was running. */
    fun resume() {
        synchronized(lock) {
            if (halted != null || !paused) return
            paused = false
            if (!deliveryStarted) {
                startDelivery()
                return
            }
            streamer?.resume()
            if (pollerWasRunning) poller.start()
        }
    }

    /** stop ends the stream, the poller and every refresh for good. */
    fun stop() {
        synchronized(lock) {
            halt(SyncException("megooflags: provider shut down"))
        }
    }

    private fun setBody(b: Map<String, Any?>) {
        currentBody = b
        hash = contextHash(b)
    }

    /** kick starts a run, or queues one behind the run in flight. Call it holding the lock. */
    private fun kick() {
        val evaluator = evaluator ?: return
        if (halted != null) return
        if (inFlight != null) {
            queued = true
            return
        }
        retry?.cancel()
        retry = null
        val run = ++runs
        val gen = generation.get()
        val body = currentBody
        val hash = hash
        val etag = store.get()?.takeIf { it.contextHash == hash }?.etag
        val job =
            scope.launch(start = CoroutineStart.LAZY) {
                val self = coroutineContext[Job]
                val outcome =
                    try {
                        Result.success(evaluator.evaluate(body, etag))
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        Result.failure(e)
                    }
                finished(self, run, gen, body, hash, outcome)
            }
        inFlight = job
        job.start()
    }

    private fun finished(
        self: Job?,
        run: Long,
        gen: Long,
        body: Map<String, Any?>,
        hash: String,
        outcome: Result<EvaluateOutcome>,
    ) {
        synchronized(lock) {
            // Another run replaced this one (a context change), or Sync stopped.
            if (inFlight !== self) return
            inFlight = null
            if (halted != null) return
            if (gen == generation.get()) {
                val error = outcome.fold({ apply(it, body, hash) }, { it })
                if (error == null) settle(run, null) else failed(run, error)
            }
            if (queued && halted == null) {
                queued = false
                kick()
            }
        }
    }

    /** apply stores an answer for the current context; it returns an error for a 304 it cannot use. */
    private fun apply(
        outcome: EvaluateOutcome,
        body: Map<String, Any?>,
        hash: String,
    ): Throwable? {
        val old = store.get()
        when (outcome) {
            is EvaluateOutcome.NotModified -> {
                if (old == null || old.contextHash != hash) {
                    return SdkException.InvalidResponse("304 without values")
                }
                store.set(old.copy(etag = outcome.etag ?: old.etag, source = Source.NETWORK))
            }

            is EvaluateOutcome.Ok -> {
                val next = StoredResults(outcome.results, outcome.etag, Source.NETWORK, hash, targetingKeyOf(body))
                store.set(next)
                cache?.save(next)
                if (readyPending) {
                    readyPending = false
                    emit { events.ready() }
                } else if (old != null) {
                    val changed = changedFlags(old, next)
                    if (changed.isNotEmpty()) emit { events.changed(changed) }
                }
            }
        }
        backoff.reset()
        return null
    }

    private fun failed(
        run: Long,
        error: Throwable,
    ) {
        if (error is SdkException.Fatal) {
            stopAll(error)
            return
        }
        logger.debug("megooflags: evaluate failed: ${failureReason(error)}")
        emit { events.error(error) }
        settle(run, error)
        if (queued) return
        val wait = if (error is SdkException.RetryAfter) error.seconds * 1_000 else backoff.next()
        val job =
            scope.launch(start = CoroutineStart.LAZY) {
                delay(wait)
                synchronized(lock) {
                    if (retry !== coroutineContext[Job]) return@launch
                    retry = null
                    kick()
                }
            }
        retry = job
        job.start()
    }

    /** settle completes, with [error] or not, the waiters served by [run]. */
    private fun settle(
        run: Long,
        error: Throwable?,
    ) {
        val served = waiters.filter { it.run <= run }
        waiters.removeAll(served)
        for (w in served) {
            if (error == null) w.done.complete(Unit) else w.done.completeExceptionally(error)
        }
    }

    /** startDelivery opens the stream, or starts the poller with disableStream; once, and not while paused. */
    private fun startDelivery() {
        if (halted != null || deliveryStarted || paused) return
        deliveryStarted = true
        if (config.disableStream || streamer == null) {
            poller.start()
        } else {
            streamer.start()
        }
    }

    private fun onConnected() {
        synchronized(lock) {
            if (halted != null) return
            poller.stop()
            connected = true
            if (stale) {
                stale = false
                emit { events.ready() }
            }
        }
    }

    private fun onStreamFailure(consecutive: Int) {
        synchronized(lock) {
            if (halted != null || consecutive < 2) return
            if (!paused) poller.start()
            if (connected && !stale) {
                stale = true
                emit { events.stale() }
            }
        }
    }

    /** stopAll stops everything for a refused key and keeps the store. While start runs, it reports the error. */
    private fun stopAll(error: SdkException) {
        synchronized(lock) {
            if (halted != null) return
            halt(error)
            emit { events.fatalStop() }
            if (!starting) emit { events.fatal(error) }
        }
    }

    private fun halt(error: Exception) {
        if (halted != null) return
        halted = error
        inFlight?.cancel()
        inFlight = null
        retry?.cancel()
        retry = null
        queued = false
        poller.stop()
        streamer?.stop()
        val all = waiters.toList()
        waiters.clear()
        for (w in all) w.done.completeExceptionally(error)
    }

    /** emit runs an event callback; an exception it throws is logged, never propagated. */
    private inline fun emit(call: () -> Unit) {
        try {
            call()
        } catch (e: Exception) {
            logger.error("megooflags: event callback failed: ${e::class.java.simpleName}")
        }
    }
}
