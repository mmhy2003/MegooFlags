package io.github.mmhy2003.megooflags;

import dev.openfeature.sdk.ErrorCode;
import dev.openfeature.sdk.EvaluationContext;
import dev.openfeature.sdk.EventProvider;
import dev.openfeature.sdk.ImmutableMetadata;
import dev.openfeature.sdk.ImmutableStructure;
import dev.openfeature.sdk.Metadata;
import dev.openfeature.sdk.ProviderEvaluation;
import dev.openfeature.sdk.ProviderEventDetails;
import dev.openfeature.sdk.Reason;
import dev.openfeature.sdk.Value;
import dev.openfeature.sdk.exceptions.FatalError;
import dev.openfeature.sdk.exceptions.GeneralError;
import io.github.mmhy2003.megooflags.internal.BuildInfo;
import io.github.mmhy2003.megooflags.internal.delivery.Cache;
import io.github.mmhy2003.megooflags.internal.delivery.Snapshot;
import io.github.mmhy2003.megooflags.internal.delivery.Snapshot.Source;
import io.github.mmhy2003.megooflags.internal.delivery.Store;
import io.github.mmhy2003.megooflags.internal.delivery.Sync;
import io.github.mmhy2003.megooflags.internal.engine.Evaluator;
import io.github.mmhy2003.megooflags.internal.engine.Result;
import io.github.mmhy2003.megooflags.internal.engine.Ruleset;
import io.github.mmhy2003.megooflags.internal.events.Batcher;
import io.github.mmhy2003.megooflags.internal.events.Evaluation;
import io.github.mmhy2003.megooflags.internal.transport.Transport;
import java.io.IOException;
import java.lang.System.Logger.Level;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * The MegooFlags OpenFeature provider. It evaluates flags against the ruleset it holds, with no
 * network call per evaluation, and keeps that ruleset current over the stream (or by polling).
 *
 * <pre>{@code
 * MegooFlagsProvider provider = new MegooFlagsProvider(MegooFlagsConfig.builder()
 *         .sdkKey("mf_server_…")
 *         .baseUrl("https://flags.example.com")
 *         .build());
 * OpenFeatureAPI.getInstance().setProviderAndWait(provider);
 * }</pre>
 */
public final class MegooFlagsProvider extends EventProvider {
    /** The SDK version. */
    public static final String VERSION = BuildInfo.VERSION;

    /** Bounds the final events flush. */
    private static final Duration SHUTDOWN_TIMEOUT = Duration.ofSeconds(5);

    private static final Metadata METADATA = () -> "MegooFlags";

    /** Shortened Sync timings; only tests set it. */
    static volatile Sync.Tuning syncTuning;

    private final MegooFlagsConfig cfg;
    private final System.Logger logger;
    /** Why the config is invalid, or null. */
    private final String configError;

    private final Store store = new Store();
    private final Sync sync;
    private final Batcher batcher;

    private final AtomicBoolean fatalOnce = new AtomicBoolean();
    private final AtomicBoolean shutdownOnce = new AtomicBoolean();

    /** Guards the state below; initialize waits on it for the first ruleset. */
    private final Object mu = new Object();

    private boolean started;
    /** Set by shutdown(): nothing is emitted from then on. */
    private volatile boolean shutDown;
    /** The refused-key message, once the key was refused. */
    private volatile String fatalMessage;
    /** How many initialize calls wait for the first ruleset. */
    private int waiting;
    /** initialize returned the timeout error. */
    private boolean initTimedOut;
    /** STALE was emitted and not yet followed by READY. */
    private boolean stale;

    /**
     * Validates cfg and loads the bootstrap file, then (when that gave no ruleset) the cache. It never
     * touches the network. An invalid config is logged, and every evaluation then returns the
     * default with PROVIDER_FATAL.
     *
     * @param cfg the provider's settings
     */
    public MegooFlagsProvider(MegooFlagsConfig cfg) {
        this.cfg = cfg;
        this.logger = cfg.logger();
        this.configError = cfg.validate();
        if (configError != null) {
            logger.log(Level.ERROR, "megooflags: invalid config: {0}", configError);
            sync = null;
            batcher = null;
            return;
        }
        Cache cache = new Cache(cfg.cacheDir(), cfg.sdkKey());
        // Offline uses Sync only to apply the bootstrap file and the cache; initialize never starts it.
        Sync.Options syncOptions = new Sync.Options(cfg.baseUrl(), cfg.sdkKey(), store)
                .httpClient(cfg.httpClient())
                .logger(logger)
                .disableStream(cfg.disableStream())
                .pollInterval(cfg.pollInterval())
                .cache(cache)
                .listener(new Sync.Listener() {
                    @Override
                    public void onChange(Snapshot old, Snapshot next) {
                        MegooFlagsProvider.this.onChange(old, next);
                    }

                    @Override
                    public void onStatus(Sync.Status status, IOException err) {
                        MegooFlagsProvider.this.onStatus(status, err);
                    }
                });
        Sync.Tuning tuning = syncTuning;
        if (tuning != null) {
            syncOptions.tuning(tuning);
        }
        sync = new Sync(syncOptions);
        batcher = cfg.offline()
                ? null
                : new Batcher(new Batcher.Options(Transport.url(cfg.baseUrl(), "events"), cfg.sdkKey())
                        .httpClient(cfg.httpClient())
                        .logger(logger)
                        .flushInterval(cfg.flushInterval())
                        .sendContext(cfg.sendContext())
                        .onFatal(this::onFatal));
        if (cfg.bootstrapFile() != null) {
            try {
                String raw = Files.readString(cfg.bootstrapFile(), StandardCharsets.UTF_8);
                sync.apply(raw, "", Source.BOOTSTRAP); // apply logs an invalid ruleset
            } catch (IOException e) {
                logger.log(Level.WARNING, "megooflags: could not read the bootstrap file: {0}", e.getMessage());
            }
        }
        if (store.load() == null) {
            try {
                Cache.Entry entry = cache.load();
                if (entry != null) {
                    // The decoded tree, not rewritten as JSON: a number such as 1e400 reads as
                    // Infinity, which JSON cannot hold.
                    sync.applyTree(entry.ruleset(), entry.etag(), Source.CACHE);
                }
            } catch (IOException e) {
                logger.log(Level.WARNING, "megooflags: could not read the ruleset cache: {0}", e.getMessage());
            }
        }
    }

    @Override
    public Metadata getMetadata() {
        return METADATA;
    }

    /**
     * Starts the event sender and, unless offline, the ruleset sync. With a ruleset from the
     * bootstrap file or the cache it returns at once and the network refreshes it in the
     * background. Otherwise it waits up to initTimeout for the first ruleset; on timeout it throws,
     * keeps retrying and emits PROVIDER_READY when a ruleset arrives. An invalid config or a refused
     * key throws FatalError.
     */
    @Override
    public void initialize(EvaluationContext evaluationContext) {
        if (configError != null) {
            throw new FatalError(configError);
        }
        synchronized (mu) {
            if (shutDown) {
                throw new GeneralError("megooflags: the provider was shut down");
            }
            if (fatalMessage != null) {
                throw new FatalError(fatalMessage);
            }
            if (!started) {
                started = true;
                if (batcher != null) {
                    batcher.start();
                }
                if (!cfg.offline()) {
                    sync.start();
                }
            }
        }
        if (store.load() != null) {
            return;
        }
        if (cfg.offline()) {
            throw new GeneralError(
                    "megooflags: offline, and neither the bootstrap file nor the cache gave a valid ruleset");
        }
        awaitFirstRuleset();
    }

    private void awaitFirstRuleset() {
        long deadline = System.nanoTime() + cfg.initTimeout().toNanos();
        synchronized (mu) {
            waiting++;
            try {
                while (store.load() == null && fatalMessage == null && !shutDown) {
                    long left = deadline - System.nanoTime();
                    if (left <= 0) {
                        initTimedOut = true;
                        throw new GeneralError("megooflags: no ruleset within "
                                + cfg.initTimeout().toMillis() + " ms");
                    }
                    mu.wait(Math.max(left / 1_000_000, 1));
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new GeneralError("megooflags: interrupted while waiting for the first ruleset");
            } finally {
                waiting--;
            }
            if (store.load() != null) {
                return;
            }
            if (fatalMessage != null) {
                throw new FatalError(fatalMessage);
            }
            throw new GeneralError("megooflags: the provider was shut down");
        }
    }

    /**
     * Stops the sync and sends the buffered events within 5 s. Idempotent. The last ruleset keeps
     * being served.
     */
    @Override
    public void shutdown() {
        if (shutdownOnce.compareAndSet(false, true)) {
            synchronized (mu) {
                shutDown = true;
                mu.notifyAll();
            }
            if (sync != null) {
                sync.stop();
            }
            if (batcher != null && !batcher.shutdown(SHUTDOWN_TIMEOUT)) {
                logger.log(Level.WARNING, "megooflags: could not send all events before shutdown");
            }
        }
        super.shutdown();
    }

    @Override
    public ProviderEvaluation<Boolean> getBooleanEvaluation(String key, Boolean defaultValue, EvaluationContext ctx) {
        Result res = evaluate(key, defaultValue, ctx, "boolean");
        if (res == null) {
            return notReady(defaultValue);
        }
        if (res.errorCode != null) {
            return error(defaultValue, res);
        }
        return detail((Boolean) res.value, res);
    }

    @Override
    public ProviderEvaluation<String> getStringEvaluation(String key, String defaultValue, EvaluationContext ctx) {
        Result res = evaluate(key, defaultValue, ctx, "string");
        if (res == null) {
            return notReady(defaultValue);
        }
        if (res.errorCode != null) {
            return error(defaultValue, res);
        }
        return detail((String) res.value, res);
    }

    /**
     * Resolves a number flag as an Integer. A served number that is not integral, or is outside the
     * int range, is the default with TYPE_MISMATCH.
     */
    @Override
    public ProviderEvaluation<Integer> getIntegerEvaluation(String key, Integer defaultValue, EvaluationContext ctx) {
        Result res = evaluate(key, defaultValue, ctx, "number");
        if (res == null) {
            return notReady(defaultValue);
        }
        if (res.errorCode != null) {
            return error(defaultValue, res);
        }
        if (res.variant == null) {
            return detail(defaultValue, res);
        }
        Integer v = toInt(res.value);
        if (v == null) {
            return ProviderEvaluation.<Integer>builder()
                    .value(defaultValue)
                    .variant(res.variant)
                    .reason(Reason.ERROR.toString())
                    .errorCode(ErrorCode.TYPE_MISMATCH)
                    .errorMessage("megooflags: flag '" + key + "' is not an integer in the int range")
                    .flagMetadata(metadata(res))
                    .build();
        }
        return detail(v, res);
    }

    @Override
    public ProviderEvaluation<Double> getDoubleEvaluation(String key, Double defaultValue, EvaluationContext ctx) {
        Result res = evaluate(key, defaultValue, ctx, "number");
        if (res == null) {
            return notReady(defaultValue);
        }
        if (res.errorCode != null) {
            return error(defaultValue, res);
        }
        Double v = res.variant == null ? defaultValue : (Double) ((Number) res.value).doubleValue();
        return detail(v, res);
    }

    /** Resolves a flag of any type as a Value; json flags are the intended use. */
    @Override
    public ProviderEvaluation<Value> getObjectEvaluation(String key, Value defaultValue, EvaluationContext ctx) {
        Result res = evaluate(key, defaultValue, ctx, "json");
        if (res == null) {
            return notReady(defaultValue);
        }
        if (res.errorCode != null) {
            return error(defaultValue, res);
        }
        return detail(res.variant == null ? defaultValue : toValue(res.value), res);
    }

    /**
     * Runs the engine on the current ruleset, or returns null when there is none yet. The context
     * is copied, with the targeting key as the key attribute. No I/O.
     */
    private Result evaluate(String flagKey, Object defaultValue, EvaluationContext ctx, String expectedType) {
        Snapshot snap = store.load();
        if (snap == null) {
            return null;
        }
        Ruleset ruleset = snap.ruleset();
        Map<String, Object> attrs = new HashMap<>();
        if (ctx != null) {
            attrs.putAll(ctx.asObjectMap());
            attrs.remove(EvaluationContext.TARGETING_KEY);
            String targetingKey = ctx.getTargetingKey();
            if (targetingKey != null) {
                attrs.put("key", targetingKey);
            }
        }
        Result res = Evaluator.evaluate(ruleset, flagKey, attrs, defaultValue, expectedType);
        record(flagKey, res, attrs);
        return res;
    }

    /** Queues the evaluation for the batcher without blocking. */
    private void record(String flagKey, Result res, Map<String, Object> attrs) {
        if (batcher == null) {
            return;
        }
        Object key = attrs.get("key");
        boolean served = res.variant != null && res.errorCode == null;
        batcher.record(new Evaluation(
                flagKey,
                served ? res.variant : "",
                served,
                res.reason,
                key instanceof String ? (String) key : "",
                cfg.sendContext() ? jsonMap(attrs) : null,
                Instant.now()));
    }

    /** A copy of a context map the events body can hold: an Instant becomes its ISO-8601 text. */
    private static Map<String, Object> jsonMap(Map<?, ?> m) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (Map.Entry<?, ?> e : m.entrySet()) {
            out.put(String.valueOf(e.getKey()), jsonValue(e.getValue()));
        }
        return out;
    }

    private static Object jsonValue(Object v) {
        if (v instanceof Map) {
            return jsonMap((Map<?, ?>) v);
        }
        if (v instanceof Collection) {
            List<Object> out = new ArrayList<>();
            for (Object o : (Collection<?>) v) {
                out.add(jsonValue(o));
            }
            return out;
        }
        if (v instanceof Instant) {
            return v.toString();
        }
        return v;
    }

    /** The result while there is no ruleset. */
    private <T> ProviderEvaluation<T> notReady(T defaultValue) {
        String fatal = configError != null ? configError : fatalMessage;
        return ProviderEvaluation.<T>builder()
                .value(defaultValue)
                .reason(Reason.ERROR.toString())
                .errorCode(fatal != null ? ErrorCode.PROVIDER_FATAL : ErrorCode.PROVIDER_NOT_READY)
                .errorMessage(fatal != null ? fatal : "megooflags: no ruleset is loaded yet")
                .build();
    }

    /** The default with the engine's error. */
    private static <T> ProviderEvaluation<T> error(T defaultValue, Result res) {
        return ProviderEvaluation.<T>builder()
                .value(defaultValue)
                .variant(res.variant)
                .reason(res.reason)
                .errorCode(errorCode(res.errorCode))
                .errorMessage(res.errorMessage)
                .flagMetadata(metadata(res))
                .build();
    }

    /** value with the engine's variant, reason (PREREQUISITE_FAILED passes through) and metadata. */
    private static <T> ProviderEvaluation<T> detail(T value, Result res) {
        return ProviderEvaluation.<T>builder()
                .value(value)
                .variant(res.variant)
                .reason(res.reason)
                .flagMetadata(metadata(res))
                .build();
    }

    private static ImmutableMetadata metadata(Result res) {
        ImmutableMetadata.ImmutableMetadataBuilder b = ImmutableMetadata.builder();
        if (res.ruleIndex != null) {
            b.addInteger("rule_index", res.ruleIndex);
        }
        if (res.prerequisite != null) {
            b.addString("prerequisite", res.prerequisite);
        }
        return b.build();
    }

    private static ErrorCode errorCode(String code) {
        switch (code) {
            case Result.FLAG_NOT_FOUND:
                return ErrorCode.FLAG_NOT_FOUND;
            case Result.TYPE_MISMATCH:
                return ErrorCode.TYPE_MISMATCH;
            case Result.PARSE_ERROR:
                return ErrorCode.PARSE_ERROR;
            case Result.INVALID_CONTEXT:
                return ErrorCode.INVALID_CONTEXT;
            default:
                return ErrorCode.GENERAL;
        }
    }

    /** The int of an integral number in the int range, or null. */
    private static Integer toInt(Object v) {
        if (v instanceof Long) {
            long l = (Long) v;
            return l >= Integer.MIN_VALUE && l <= Integer.MAX_VALUE ? (Integer) (int) l : null;
        }
        if (v instanceof Double) {
            double d = (Double) v;
            return d == Math.rint(d) && d >= Integer.MIN_VALUE && d <= Integer.MAX_VALUE ? (Integer) (int) d : null;
        }
        return null;
    }

    /**
     * Converts the engine's JSON tree to a Value: an integer that fits an int is an Integer, a larger
     * one stays a Long.
     */
    private static Value toValue(Object v) {
        if (v == null) {
            return new Value();
        }
        if (v instanceof String) {
            return new Value((String) v);
        }
        if (v instanceof Boolean) {
            return new Value((Boolean) v);
        }
        if (v instanceof Long) {
            long l = (Long) v;
            return l >= Integer.MIN_VALUE && l <= Integer.MAX_VALUE ? new Value((int) l) : new Value(l);
        }
        if (v instanceof Double) {
            return new Value((Double) v);
        }
        if (v instanceof Map) {
            Map<String, Value> attrs = new LinkedHashMap<>();
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                attrs.put((String) e.getKey(), toValue(e.getValue()));
            }
            return new Value(new ImmutableStructure(attrs));
        }
        if (v instanceof List) {
            List<Value> items = new ArrayList<>();
            for (Object o : (List<?>) v) {
                items.add(toValue(o));
            }
            return new Value(items);
        }
        throw new IllegalStateException(
                "megooflags: unexpected JSON value " + v.getClass().getName());
    }

    /**
     * Runs after each apply. The first ruleset ends the wait in initialize (and emits READY when that
     * wait had timed out); a later one is a configuration change.
     */
    private void onChange(Snapshot old, Snapshot next) {
        if (old == null) {
            boolean timedOut;
            synchronized (mu) {
                mu.notifyAll();
                timedOut = initTimedOut;
            }
            if (timedOut) {
                emit(() -> emitProviderReady(ProviderEventDetails.builder()
                        .message("megooflags: ruleset loaded")
                        .build()));
            }
            return;
        }
        List<String> changed = flagChanges(old.ruleset(), next.ruleset());
        emit(() -> emitProviderConfigurationChanged(ProviderEventDetails.builder()
                .message("megooflags: ruleset updated")
                .flagsChanged(changed)
                .build()));
    }

    /** Maps the delivery status to events: STALE while disconnected with a ruleset, READY on reconnect. */
    private void onStatus(Sync.Status status, IOException err) {
        switch (status) {
            case STALE: {
                boolean emit;
                synchronized (mu) {
                    emit = !stale && store.load() != null && fatalMessage == null;
                    stale = stale || emit;
                }
                if (emit) {
                    String why = err == null ? "" : ": " + err.getMessage();
                    emit(() -> emitProviderStale(ProviderEventDetails.builder()
                            .message("megooflags: disconnected; serving the last ruleset" + why)
                            .build()));
                }
                return;
            }
            case READY: {
                boolean emit;
                synchronized (mu) {
                    emit = stale;
                    stale = false;
                }
                if (emit) {
                    emit(() -> emitProviderReady(ProviderEventDetails.builder()
                            .message("megooflags: reconnected")
                            .build()));
                }
                return;
            }
            default:
                onFatal(err);
        }
    }

    /**
     * Handles a refused key once: stops event sending and the sync and emits PROVIDER_ERROR with
     * PROVIDER_FATAL. A waiting initialize throws FatalError instead of the event. The last ruleset
     * keeps being served.
     */
    private void onFatal(IOException err) {
        if (!fatalOnce.compareAndSet(false, true)) {
            return;
        }
        String message = err != null ? err.getMessage() : "megooflags: the SDK key was refused";
        boolean initWaits;
        synchronized (mu) {
            fatalMessage = message;
            initWaits = waiting > 0;
            mu.notifyAll();
        }
        logger.log(Level.ERROR, "megooflags: the server refused the SDK key; serving the last ruleset: {0}", message);
        if (batcher != null) {
            batcher.stop();
        }
        // onFatal may run on a Sync worker, which Sync.stop waits for.
        Thread stopper = new Thread(sync::stop, "megooflags-fatal");
        stopper.setDaemon(true);
        stopper.start();
        if (!initWaits) {
            emit(() -> emitProviderError(ProviderEventDetails.builder()
                    .errorCode(ErrorCode.PROVIDER_FATAL)
                    .message(message)
                    .build()));
        }
    }

    /** Emits an event unless the provider was shut down. */
    private void emit(Runnable fn) {
        if (shutDown) {
            return;
        }
        try {
            fn.run();
        } catch (RejectedExecutionException e) {
            // shutdown() stopped the emitter between the check and the emit
        }
    }

    /** The sorted keys of the flags that were added, removed, or have a different version. */
    private static List<String> flagChanges(Ruleset old, Ruleset next) {
        List<String> keys = new ArrayList<>();
        for (Map.Entry<String, Ruleset.Flag> e : next.flags.entrySet()) {
            Ruleset.Flag before = old.flags.get(e.getKey());
            if (before == null || before.version != e.getValue().version) {
                keys.add(e.getKey());
            }
        }
        for (String k : old.flags.keySet()) {
            if (!next.flags.containsKey(k)) {
                keys.add(k);
            }
        }
        Collections.sort(keys);
        return keys;
    }
}
