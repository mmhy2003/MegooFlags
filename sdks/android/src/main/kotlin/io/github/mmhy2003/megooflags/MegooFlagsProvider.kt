// The OpenFeature provider: wires the configuration, store, cache, sync, streamer, exposures, batcher and background
// signal together, maps the sync events to provider events and serves flag reads from the store.
package io.github.mmhy2003.megooflags

import android.os.Looper
import dev.openfeature.kotlin.sdk.EvaluationContext
import dev.openfeature.kotlin.sdk.FeatureProvider
import dev.openfeature.kotlin.sdk.Hook
import dev.openfeature.kotlin.sdk.ProviderEvaluation
import dev.openfeature.kotlin.sdk.ProviderMetadata
import dev.openfeature.kotlin.sdk.Value
import dev.openfeature.kotlin.sdk.events.OpenFeatureProviderEvents
import dev.openfeature.kotlin.sdk.events.OpenFeatureProviderEvents.EventDetails
import dev.openfeature.kotlin.sdk.exceptions.ErrorCode
import dev.openfeature.kotlin.sdk.exceptions.OpenFeatureError
import io.github.mmhy2003.megooflags.internal.BackgroundController
import io.github.mmhy2003.megooflags.internal.Backoff
import io.github.mmhy2003.megooflags.internal.Batcher
import io.github.mmhy2003.megooflags.internal.Cache
import io.github.mmhy2003.megooflags.internal.EvaluateOutcome
import io.github.mmhy2003.megooflags.internal.Evaluates
import io.github.mmhy2003.megooflags.internal.Evaluator
import io.github.mmhy2003.megooflags.internal.Exposures
import io.github.mmhy2003.megooflags.internal.HttpClient
import io.github.mmhy2003.megooflags.internal.SdkException
import io.github.mmhy2003.megooflags.internal.Store
import io.github.mmhy2003.megooflags.internal.Streamer
import io.github.mmhy2003.megooflags.internal.Sync
import io.github.mmhy2003.megooflags.internal.SyncEvents
import io.github.mmhy2003.megooflags.internal.UrlConnectionClient
import io.github.mmhy2003.megooflags.internal.failureReason
import io.github.mmhy2003.megooflags.internal.resolveBoolean
import io.github.mmhy2003.megooflags.internal.resolveDouble
import io.github.mmhy2003.megooflags.internal.resolveInt
import io.github.mmhy2003.megooflags.internal.resolveLong
import io.github.mmhy2003.megooflags.internal.resolveObject
import io.github.mmhy2003.megooflags.internal.resolveString
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.time.Duration.Companion.seconds

/** How long shutdown waits for the last exposures. */
private val CLOSE_TIMEOUT = 5.seconds

private object Metadata : ProviderMetadata {
    override val name: String = "MegooFlags"
}

/** Offline, Sync never evaluates; this stands in for the evaluator a missing baseUrl cannot build. */
private object NoEvaluator : Evaluates {
    override suspend fun evaluate(
        body: Map<String, Any?>,
        etag: String?,
    ): EvaluateOutcome = throw IllegalStateException("megooflags: offline")
}

private fun isFatal(e: Throwable): Boolean = e is SdkException.Fatal || e is SdkException.Revoked

/** openFeatureError is the error initialisation or a context change reports for [e]. */
private fun openFeatureError(e: Exception): OpenFeatureError =
    if (isFatal(e)) {
        OpenFeatureError.ProviderFatalError(e.message!!)
    } else {
        OpenFeatureError.GeneralError(e.message ?: "megooflags: ${failureReason(e)}")
    }

/** onMainThread is true on Android's main thread; on the JVM (unit tests) there is no main looper. */
private fun onMainThread(): Boolean = Looper.getMainLooper()?.thread === Thread.currentThread()

/**
 * MegooFlagsProvider evaluates flags remotely with a client key and serves them synchronously from the last values
 * for the current context. Build its configuration with [MegooFlagsConfig.Builder].
 */
public class MegooFlagsProvider internal constructor(
    private val config: MegooFlagsConfig,
    client: HttpClient,
    dispatcher: CoroutineDispatcher,
) : FeatureProvider {
    public constructor(config: MegooFlagsConfig) : this(config, UrlConnectionClient(), Dispatchers.IO)

    override val hooks: List<Hook<*>> = emptyList()
    override val metadata: ProviderMetadata = Metadata

    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val events = MutableSharedFlow<OpenFeatureProviderEvents>(extraBufferCapacity = 64)
    private val store = Store()
    private val shutDown = AtomicBoolean(false)
    private val exposures = if (config.offline) null else Exposures(config.sendContext, config.logger)

    private val batcher: Batcher? =
        exposures?.let { ex ->
            config.baseUrl?.let { url ->
                Batcher(
                    scope = scope,
                    client = client,
                    baseUrl = url,
                    sdkKey = config.sdkKey,
                    exposures = ex,
                    flushInterval = config.flushInterval,
                    logger = config.logger,
                    onFatal = { onBatcherFatal(it) },
                    io = dispatcher,
                )
            }
        }

    private val sync: Sync =
        Sync(
            scope = scope,
            config = config,
            store = store,
            cache = Cache(config.sdkKey, config.cacheDir, config.logger),
            evaluator = config.baseUrl?.let { Evaluator(it, config.sdkKey, client, dispatcher) } ?: NoEvaluator,
            streamer =
                config.baseUrl?.let { url ->
                    { hooks ->
                        Streamer(
                            scope = scope,
                            client = client,
                            baseUrl = url,
                            sdkKey = config.sdkKey,
                            backoff = Backoff(3_000, 60_000),
                            hooks = hooks,
                            logger = config.logger,
                            io = dispatcher,
                        )
                    }
                },
            events =
                object : SyncEvents {
                    override fun ready() {
                        emit(OpenFeatureProviderEvents.ProviderReady())
                    }

                    override fun stale() {
                        emit(OpenFeatureProviderEvents.ProviderStale())
                    }

                    override fun changed(flags: List<String>) {
                        emit(OpenFeatureProviderEvents.ProviderConfigurationChanged(EventDetails(flagsChanged = flags.toSet())))
                    }

                    override fun fatal(error: SdkException) {
                        emitFatal(error)
                    }

                    // Sync logs and retries; a failed context change is reported by onContextSet.
                    override fun error(error: Throwable) {}

                    override fun fatalStop() {
                        stopDelivery()
                    }
                },
        )

    private val background =
        BackgroundController(
            scope = scope,
            source = config.lifecycle,
            flush = { batcher?.flush() },
            pause = { sync.pause() },
            resume = { sync.resume() },
        )

    /** observe emits ready, stale, configuration changed (with the changed flags) and fatal errors. */
    override fun observe(): Flow<OpenFeatureProviderEvents> = events.asSharedFlow()

    /**
     * initialize serves bootstrap or cached values at once, or waits for the first evaluation up to initTimeout. A
     * refused key throws ProviderFatalError; a timeout or another failure throws GeneralError while retries go on,
     * and ProviderReady follows when values arrive.
     */
    override suspend fun initialize(initialContext: EvaluationContext?) {
        batcher?.start()
        background.start()
        try {
            sync.start(initialContext)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            throw openFeatureError(e)
        }
    }

    /**
     * onContextSet fetches the values for [newContext]. A new targeting key serves defaults until they arrive; a
     * failure throws (retries go on).
     */
    override suspend fun onContextSet(
        oldContext: EvaluationContext?,
        newContext: EvaluationContext,
    ) {
        try {
            sync.setContext(newContext)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            throw openFeatureError(e)
        }
    }

    /**
     * shutdown stops the sync and the background signal and sends the last exposures within 5 seconds: blocking off
     * the main thread, in the background on it. A second call returns at once.
     */
    override fun shutdown() {
        if (!shutDown.compareAndSet(false, true)) return
        background.stop()
        sync.stop()
        val batcher = batcher
        if (batcher == null) {
            scope.cancel()
        } else if (onMainThread()) {
            scope.launch {
                withTimeoutOrNull(CLOSE_TIMEOUT) { batcher.close(CLOSE_TIMEOUT) }
                scope.cancel()
            }
        } else {
            runBlocking { withTimeoutOrNull(CLOSE_TIMEOUT) { batcher.close(CLOSE_TIMEOUT) } }
            scope.cancel()
        }
    }

    override fun getBooleanEvaluation(
        key: String,
        defaultValue: Boolean,
        context: EvaluationContext?,
    ): ProviderEvaluation<Boolean> = read(key, resolveBoolean(store.get(), key, defaultValue))

    override fun getStringEvaluation(
        key: String,
        defaultValue: String,
        context: EvaluationContext?,
    ): ProviderEvaluation<String> = read(key, resolveString(store.get(), key, defaultValue))

    override fun getIntegerEvaluation(
        key: String,
        defaultValue: Int,
        context: EvaluationContext?,
    ): ProviderEvaluation<Int> = read(key, resolveInt(store.get(), key, defaultValue))

    override fun getLongEvaluation(
        key: String,
        defaultValue: Long,
        context: EvaluationContext?,
    ): ProviderEvaluation<Long> = read(key, resolveLong(store.get(), key, defaultValue))

    override fun getDoubleEvaluation(
        key: String,
        defaultValue: Double,
        context: EvaluationContext?,
    ): ProviderEvaluation<Double> = read(key, resolveDouble(store.get(), key, defaultValue))

    override fun getObjectEvaluation(
        key: String,
        defaultValue: Value,
        context: EvaluationContext?,
    ): ProviderEvaluation<Value> = read(key, resolveObject(store.get(), key, defaultValue))

    /** read records an exposure for a served value: no error and a variant. */
    private fun <T> read(
        flag: String,
        evaluation: ProviderEvaluation<T>,
    ): ProviderEvaluation<T> {
        val variant = evaluation.variant
        if (exposures != null && evaluation.errorCode == null && variant != null) {
            exposures.record(flag, variant, evaluation.reason ?: "", sync.body)
        }
        return evaluation
    }

    private fun emit(event: OpenFeatureProviderEvents) {
        if (!events.tryEmit(event)) config.logger.warn("megooflags: provider event dropped: ${event::class.simpleName}")
    }

    private fun emitFatal(error: SdkException) {
        emit(OpenFeatureProviderEvents.ProviderError(EventDetails(message = error.message, errorCode = ErrorCode.PROVIDER_FATAL)))
    }

    /** stopDelivery stops the exposures and the background signal for a refused key. */
    private fun stopDelivery() {
        background.stop()
        batcher?.stop()
    }

    /** The events endpoint refused the key: stop everything and report it. */
    private fun onBatcherFatal(error: SdkException) {
        sync.stop()
        background.stop()
        emitFatal(error)
    }
}
