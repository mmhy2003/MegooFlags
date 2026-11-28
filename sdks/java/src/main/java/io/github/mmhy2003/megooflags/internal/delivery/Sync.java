package io.github.mmhy2003.megooflags.internal.delivery;

import io.github.mmhy2003.megooflags.internal.delivery.Snapshot.Source;
import io.github.mmhy2003.megooflags.internal.engine.ParseException;
import io.github.mmhy2003.megooflags.internal.engine.Ruleset;
import io.github.mmhy2003.megooflags.internal.engine.RulesetParser;
import io.github.mmhy2003.megooflags.internal.transport.Backoff;
import io.github.mmhy2003.megooflags.internal.transport.FatalException;
import io.github.mmhy2003.megooflags.internal.transport.RetryAfterException;
import io.github.mmhy2003.megooflags.internal.transport.Transport;
import java.io.IOException;
import java.lang.System.Logger.Level;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ScheduledThreadPoolExecutor;

/**
 * Keeps the Store current: the stream, with polling while the stream keeps failing (or polling
 * only, with disableStream). The stream, the poll loop and the watchdog run on daemon threads
 * named megooflags-*. No callback runs after stop() or after a fatal status.
 */
public final class Sync {
    /**
     * The delivery state: READY (the stream is connected, or with disableStream the last poll
     * succeeded), STALE (the stream or the poll failed; the last ruleset is still served) or FATAL
     * (the key was refused; Sync has stopped).
     */
    public enum Status {
        READY,
        STALE,
        FATAL
    }

    /** What Sync reports. Callbacks run on the worker threads, one at a time. */
    public interface Listener {
        /** A ruleset was installed; old is null the first time. */
        default void onChange(Snapshot old, Snapshot next) {}

        /** The status changed; err is the cause for STALE and FATAL, else null. */
        default void onStatus(Status status, IOException err) {}
    }

    /** Shortened timings; only tests set them. */
    public static final class Tuning {
        Duration deadTimeout = Duration.ofSeconds(45);
        Duration stableAfter = Duration.ofSeconds(60);
        Duration retryBase = Duration.ofSeconds(3);
        Duration retryMax = Duration.ofSeconds(60);
        int pollAfterFailures = 2;
        Duration pollInterval;

        public Tuning deadTimeout(Duration d) {
            deadTimeout = d;
            return this;
        }

        public Tuning stableAfter(Duration d) {
            stableAfter = d;
            return this;
        }

        public Tuning retryBase(Duration d) {
            retryBase = d;
            return this;
        }

        public Tuning retryMax(Duration d) {
            retryMax = d;
            return this;
        }

        /** The stream failures in a row before polling starts (2). */
        public Tuning pollAfterFailures(int n) {
            pollAfterFailures = n;
            return this;
        }

        /** Replaces the poll interval without any clamp. */
        public Tuning pollInterval(Duration d) {
            pollInterval = d;
            return this;
        }
    }

    /** The settings of a Sync. */
    public static final class Options {
        final String baseUrl;
        final String sdkKey;
        final Store store;
        HttpClient httpClient;
        System.Logger logger = System.getLogger("io.github.mmhy2003.megooflags");
        boolean disableStream;
        Duration pollInterval = Duration.ofSeconds(30);
        Cache cache;
        Listener listener = new Listener() {};
        Tuning tuning = new Tuning();

        public Options(String baseUrl, String sdkKey, Store store) {
            this.baseUrl = baseUrl;
            this.sdkKey = sdkKey;
            this.store = store;
        }

        /** The client for every request; a default client when not set. */
        public Options httpClient(HttpClient c) {
            httpClient = c;
            return this;
        }

        public Options logger(System.Logger l) {
            logger = l;
            return this;
        }

        public Options disableStream(boolean b) {
            disableStream = b;
            return this;
        }

        /** Used as given (the provider clamps it); 30 s. */
        public Options pollInterval(Duration d) {
            pollInterval = d;
            return this;
        }

        /** Receives each ruleset from the stream or a poll; none when not set. */
        public Options cache(Cache c) {
            cache = c;
            return this;
        }

        public Options listener(Listener l) {
            listener = l;
            return this;
        }

        public Options tuning(Tuning t) {
            tuning = t;
            return this;
        }
    }

    private static final Duration POLL_TIMEOUT = Duration.ofSeconds(10);
    private static final long JOIN_MILLIS = 1000;

    private final System.Logger logger;
    private final boolean disableStream;
    private final Duration pollInterval;
    private final int pollAfterFailures;
    private final Store store;
    private final Cache cache;
    private final Listener listener;
    private final ScheduledThreadPoolExecutor scheduler;
    private final Streamer streamer;
    private final Poller poller;

    /** Counted down by stop() and by a fatal status: every worker ends. */
    private final CountDownLatch cancel = new CountDownLatch(1);
    /** Guards the state below and runs the callbacks one at a time. */
    private final Object lock = new Object();

    private final Set<String> logged = ConcurrentHashMap.newKeySet();
    private final List<Thread> workers = new ArrayList<>();
    private boolean started;
    private volatile boolean stopped;
    private volatile boolean fatal;
    /** Set while a poll loop runs; counting it down ends that loop. */
    private CountDownLatch pollCancel;
    /** The earliest next poll (System.nanoTime), kept across poll-loop restarts. */
    private volatile long nextPoll = System.nanoTime();

    public Sync(Options o) {
        logger = o.logger;
        disableStream = o.disableStream;
        pollInterval = o.tuning.pollInterval != null ? o.tuning.pollInterval : o.pollInterval;
        pollAfterFailures = o.tuning.pollAfterFailures;
        store = o.store;
        cache = o.cache;
        listener = o.listener;
        HttpClient client = o.httpClient != null ? o.httpClient : Transport.defaultClient();
        scheduler = new ScheduledThreadPoolExecutor(1, r -> daemon("megooflags-watchdog", r));
        scheduler.setRemoveOnCancelPolicy(true);
        poller = new Poller(
                client,
                Transport.url(o.baseUrl, "ruleset"),
                o.sdkKey,
                this::etag,
                (raw, tag) -> apply(raw, tag, Source.POLL) == null,
                POLL_TIMEOUT,
                scheduler);
        streamer = new Streamer(
                client,
                Transport.url(o.baseUrl, "stream"),
                o.sdkKey,
                logger,
                this::etag,
                new Streamer.Listener() {
                    @Override
                    public boolean onPut(String raw, String id) {
                        return apply(raw, id, Source.STREAM) == null;
                    }

                    @Override
                    public void onConnected() {
                        Sync.this.onConnected();
                    }

                    @Override
                    public void onDisconnected(IOException err, int failures) {
                        Sync.this.onDisconnected(err, failures);
                    }
                },
                o.tuning.deadTimeout,
                o.tuning.stableAfter,
                new Backoff(o.tuning.retryBase, o.tuning.retryMax),
                scheduler);
    }

    private String etag() {
        Snapshot s = store.load();
        return s == null ? "" : s.etag();
    }

    private boolean halted() {
        return stopped || fatal;
    }

    /**
     * Parses raw and installs it. An invalid ruleset is logged and returned, and the store keeps
     * the previous one. A ruleset from the stream or a poll is also saved to the cache; a save
     * error is only logged. Each ruleset warning is logged once.
     */
    public ParseException apply(String raw, String etag, Source source) {
        return install(() -> RulesetParser.parse(raw), raw, etag, source);
    }

    /**
     * applyTree is apply for a ruleset already decoded as a JSON tree (see {@link
     * io.github.mmhy2003.megooflags.internal.json.Json#parse}), such as the cache entry. It never
     * writes the cache, so it is for the bootstrap and cache sources only.
     */
    public ParseException applyTree(Object tree, String etag, Source source) {
        if (source == Source.STREAM || source == Source.POLL) {
            throw new IllegalArgumentException("applyTree is not for " + source);
        }
        return install(() -> RulesetParser.parse(tree), null, etag, source);
    }

    private interface RulesetSource {
        Ruleset parse() throws ParseException;
    }

    private ParseException install(RulesetSource parser, String raw, String etag, Source source) {
        boolean live = source == Source.STREAM || source == Source.POLL;
        if (live && halted()) {
            return null;
        }
        Ruleset ruleset;
        try {
            ruleset = parser.parse();
        } catch (ParseException e) {
            logger.log(
                    Level.WARNING,
                    "megooflags: invalid ruleset from {0}; keeping the previous one: {1}",
                    source,
                    e.getMessage());
            return e;
        }
        Snapshot next = new Snapshot(ruleset, etag, source);
        Snapshot old = store.swap(next);
        if (live && cache != null) {
            try {
                cache.save(raw, etag);
            } catch (IOException e) {
                logger.log(Level.WARNING, "megooflags: could not save the ruleset cache: {0}", e.getMessage());
            }
        }
        for (String w : ruleset.warnings) {
            if (logged.add(w)) {
                logger.log(Level.WARNING, "megooflags: ruleset warning: {0}", w);
            }
        }
        synchronized (lock) {
            if (!(live && halted())) {
                call(() -> listener.onChange(old, next));
            }
        }
        return null;
    }

    /**
     * Starts the stream, or only the poll loop with disableStream, and returns at once. Only the
     * first call does anything, and none after stop().
     */
    public void start() {
        synchronized (lock) {
            if (started || halted()) {
                return;
            }
            started = true;
            if (disableStream) {
                startPolling();
                return;
            }
            spawn("megooflags-stream", () -> {
                FatalException err = streamer.run(cancel);
                if (err != null) {
                    fail(err);
                }
            });
        }
    }

    /**
     * Closes the stream and any poll in flight, ends the waits and waits up to a second for each
     * worker to end. The workers are never interrupted (see Exchange). Idempotent and safe before
     * start().
     */
    public void stop() {
        synchronized (lock) {
            if (stopped) {
                return;
            }
            stopped = true;
        }
        halt();
        List<Thread> threads;
        synchronized (lock) {
            threads = new ArrayList<>(workers);
        }
        for (Thread t : threads) {
            if (t == Thread.currentThread()) {
                continue;
            }
            try {
                t.join(JOIN_MILLIS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        scheduler.shutdownNow();
    }

    /**
     * Ends every worker: cancels the waits and closes the stream and the poller, which ends a
     * request waiting for its headers or reading its body.
     */
    private void halt() {
        cancel.countDown();
        synchronized (lock) {
            if (pollCancel != null) {
                pollCancel.countDown();
            }
        }
        streamer.close();
        poller.close();
    }

    private void onConnected() {
        stopPolling();
        status(Status.READY, null);
    }

    private void onDisconnected(IOException err, int failures) {
        status(Status.STALE, err);
        if (failures >= pollAfterFailures) {
            synchronized (lock) {
                startPolling();
            }
        }
    }

    /** Reports status unless Sync is stopping or has failed. */
    private void status(Status status, IOException err) {
        synchronized (lock) {
            if (!halted()) {
                call(() -> listener.onStatus(status, err));
            }
        }
    }

    /** Reports a refused key once and stops everything. */
    private void fail(FatalException err) {
        synchronized (lock) {
            if (halted()) {
                return;
            }
            fatal = true;
            call(() -> listener.onStatus(Status.FATAL, err));
        }
        halt();
    }

    private void call(Runnable fn) {
        try {
            fn.run();
        } catch (RuntimeException e) {
            logger.log(Level.ERROR, "megooflags: sync callback failed", e);
        }
    }

    /** Starts a poll loop unless one runs. Called with the lock held. */
    private void startPolling() {
        if (pollCancel != null || halted()) {
            return;
        }
        CountDownLatch loop = new CountDownLatch(1);
        pollCancel = loop;
        spawn("megooflags-poll", () -> pollLoop(loop));
    }

    private void stopPolling() {
        synchronized (lock) {
            if (pollCancel != null) {
                pollCancel.countDown();
                pollCancel = null;
            }
        }
    }

    private boolean ended(CountDownLatch loop) {
        return loop.getCount() == 0 || halted();
    }

    private void pollLoop(CountDownLatch loop) {
        while (!ended(loop)) {
            long now = System.nanoTime();
            long wait = nextPoll - now;
            if (wait > 0) {
                if (!Backoff.sleep(Duration.ofNanos(wait), loop)) {
                    return;
                }
                continue;
            }
            nextPoll = now + pollInterval.toNanos();
            IOException err = null;
            try {
                poller.poll();
            } catch (IOException e) {
                err = e;
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
            if (ended(loop)) {
                return;
            }
            if (err instanceof FatalException) {
                fail((FatalException) err);
                return;
            }
            if (err instanceof RetryAfterException) {
                Duration after = ((RetryAfterException) err).after();
                nextPoll = now + Math.max(after.toNanos(), pollInterval.toNanos());
            }
            if (err != null) {
                logger.log(Level.DEBUG, "megooflags: poll failed: {0}", err.getMessage());
            }
            if (disableStream) {
                status(err == null ? Status.READY : Status.STALE, err);
            }
        }
    }

    /** Starts a daemon worker that logs anything it throws. Called with the lock held. */
    private void spawn(String name, Runnable body) {
        Thread t = daemon(name, () -> {
            try {
                body.run();
            } catch (RuntimeException e) {
                logger.log(Level.ERROR, "megooflags: sync worker failed", e);
            }
        });
        workers.removeIf(w -> !w.isAlive());
        workers.add(t);
        t.start();
    }

    private static Thread daemon(String name, Runnable r) {
        Thread t = new Thread(r, name);
        t.setDaemon(true);
        return t;
    }
}
