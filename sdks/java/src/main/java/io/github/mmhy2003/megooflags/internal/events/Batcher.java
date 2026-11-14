package io.github.mmhy2003.megooflags.internal.events;

import io.github.mmhy2003.megooflags.internal.events.Aggregator.Batch;
import io.github.mmhy2003.megooflags.internal.json.Json;
import io.github.mmhy2003.megooflags.internal.transport.Backoff;
import io.github.mmhy2003.megooflags.internal.transport.FatalException;
import io.github.mmhy2003.megooflags.internal.transport.HttpStatusException;
import io.github.mmhy2003.megooflags.internal.transport.RetryAfterException;
import io.github.mmhy2003.megooflags.internal.transport.Transport;
import java.io.IOException;
import java.lang.System.Logger.Level;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;

/**
 * Aggregates evaluations and posts them to POST /sdk/v1/events from one daemon thread
 * (megooflags-events), which alone owns the aggregator and the sender. record() never blocks.
 */
public final class Batcher {
    /** The largest events body the server accepts. */
    static final int MAX_BODY_BYTES = 1_000_000;

    private static final Duration SEND_TIMEOUT = Duration.ofSeconds(10);

    /** Wakes the thread from its queue wait on stop or shutdown; never aggregated. */
    private static final Evaluation WAKE = new Evaluation("", "", false, "", "", null, Instant.EPOCH);

    /** Sizes and retry timings; only tests change them. */
    public static final class Tuning {
        int queueSize = 10000;
        int dedupSize = 10000;
        int maxPending = 10000;
        int maxItems = 1000;
        Duration retryBase = Duration.ofSeconds(2);
        Duration retryMax = Duration.ofSeconds(60);

        public Tuning queueSize(int n) {
            queueSize = n;
            return this;
        }

        public Tuning dedupSize(int n) {
            dedupSize = n;
            return this;
        }

        public Tuning maxPending(int n) {
            maxPending = n;
            return this;
        }

        /** The most exposures and count items in one batch, and the pending size that flushes. */
        public Tuning maxItems(int n) {
            maxItems = n;
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
    }

    /** The settings of a Batcher. */
    public static final class Options {
        final URI url;
        final String sdkKey;
        HttpClient httpClient;
        System.Logger logger = System.getLogger("io.github.mmhy2003.megooflags");
        Duration flushInterval = Duration.ofSeconds(30);
        boolean sendContext;
        Consumer<FatalException> onFatal = e -> {};
        Clock clock = Clock.systemUTC();
        Tuning tuning = new Tuning();

        /** url is the full events URL (Transport.url(base, "events")). */
        public Options(URI url, String sdkKey) {
            this.url = url;
            this.sdkKey = sdkKey;
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

        /** Used as given (the provider clamps it); 30 s. */
        public Options flushInterval(Duration d) {
            flushInterval = d;
            return this;
        }

        public Options sendContext(boolean b) {
            sendContext = b;
            return this;
        }

        /**
         * Called once, on its own thread, after a 401 or 403 stopped sending; it may call stop() or
         * shutdown().
         */
        public Options onFatal(Consumer<FatalException> fn) {
            onFatal = fn;
            return this;
        }

        /** The time compared with the next retry and used for Retry-After dates. */
        public Options clock(Clock c) {
            clock = c;
            return this;
        }

        public Options tuning(Tuning t) {
            tuning = t;
            return this;
        }
    }

    /** What one send leaves: the part to keep for a retry, or null when nothing must be retried. */
    private static final Batch DONE = null;

    private final URI url;
    private final String sdkKey;
    private final HttpClient client;
    private final System.Logger logger;
    private final Duration flushInterval;
    private final Consumer<FatalException> onFatal;
    private final Clock clock;
    private final int maxItems;
    private final ArrayBlockingQueue<Evaluation> queue;
    private final Aggregator agg;
    private final Backoff backoff;

    private final AtomicLong dropped = new AtomicLong();
    private final AtomicLong aggDropped = new AtomicLong();

    private final Object lock = new Object();
    /** Set by stop() and shutdown(): record() drops from then on. */
    private volatile boolean closed;
    /** Set by stop() and by a fatal status: nothing more is sent. */
    private volatile boolean stopped;
    /** Set by shutdown(): the thread drains, then ends. */
    private volatile boolean shuttingDown;
    /** System.nanoTime by which the drain ends. */
    private volatile long shutdownDeadline;
    /** Counted down by stop(): ends a retry wait. */
    private final CountDownLatch stopLatch = new CountDownLatch(1);
    /** Counted down when the thread ends. */
    private final CountDownLatch loopDone = new CountDownLatch(1);

    private final AtomicBoolean fatalOnce = new AtomicBoolean();
    private boolean started;
    /** The thread, once started. Guarded by lock. */
    private Thread worker;
    /** The request in flight, cancelled by stop(). Guarded by lock. */
    private CompletableFuture<HttpResponse<Void>> inFlight;
    /** Whether the last drain sent everything; written by the thread before loopDone. */
    private volatile boolean drained = true;
    /** Thread only. */
    private Instant nextAttempt = Instant.MIN;

    public Batcher(Options o) {
        url = o.url;
        sdkKey = o.sdkKey;
        client = o.httpClient != null ? o.httpClient : Transport.defaultClient();
        logger = o.logger;
        flushInterval = o.flushInterval;
        onFatal = o.onFatal;
        clock = o.clock;
        maxItems = o.tuning.maxItems;
        queue = new ArrayBlockingQueue<>(o.tuning.queueSize);
        agg = new Aggregator(o.tuning.dedupSize, o.tuning.maxPending, o.sendContext);
        backoff = new Backoff(o.tuning.retryBase, o.tuning.retryMax);
    }

    /** Starts the thread. Only the first call does anything, and none after stop() or shutdown(). */
    public void start() {
        synchronized (lock) {
            if (started || closed) {
                return;
            }
            started = true;
            worker = new Thread(this::run, "megooflags-events");
            worker.setDaemon(true);
            worker.start();
        }
    }

    /** Queues ev without blocking. A full queue, or a stopped or shut-down Batcher, drops and counts it. */
    public void record(Evaluation ev) {
        if (closed || !queue.offer(ev)) {
            dropped.incrementAndGet();
        }
    }

    /** How many evaluations or exposures were dropped. */
    public long dropped() {
        return dropped.get() + aggDropped.get();
    }

    /**
     * Stops accepting, sends what is buffered until it is all sent or the timeout ends, then stops.
     * Waits out a retry delay when it fits. Returns false when the timeout ended first. Idempotent.
     */
    public boolean shutdown(Duration timeout) {
        long deadline = System.nanoTime() + timeout.toNanos();
        synchronized (lock) {
            closed = true;
            if (!started) {
                return true;
            }
            if (!shuttingDown) {
                shutdownDeadline = deadline;
                shuttingDown = true;
            }
        }
        wake();
        try {
            if (!loopDone.await(Math.max(deadline - System.nanoTime(), 0), TimeUnit.NANOSECONDS)) {
                return false;
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }
        return drained;
    }

    /** Stops without sending what is buffered, ends a request in flight and waits for the thread. Idempotent. */
    public void stop() {
        Thread t;
        synchronized (lock) {
            signalStop();
            t = worker;
        }
        if (t == null || t == Thread.currentThread()) {
            return;
        }
        try {
            loopDone.await();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /** stop() without the wait, so the thread can call it. */
    private void signalStop() {
        synchronized (lock) {
            closed = true;
            stopped = true;
            if (inFlight != null) {
                inFlight.cancel(false);
            }
        }
        stopLatch.countDown();
        wake();
    }

    /** Ends the thread's queue wait. When the queue is full, the wait returns at once anyway. */
    private void wake() {
        queue.offer(WAKE);
    }

    private void run() {
        try {
            loop();
        } catch (RuntimeException e) {
            logger.log(Level.ERROR, "megooflags: events worker failed", e);
        } finally {
            loopDone.countDown();
        }
    }

    private void loop() {
        long nextTick = System.nanoTime() + flushInterval.toNanos();
        while (!stopped) {
            if (shuttingDown) {
                drain();
                return;
            }
            Evaluation ev;
            try {
                ev = queue.poll(Math.max(nextTick - System.nanoTime(), 0), TimeUnit.NANOSECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
            if (ev != null && ev != WAKE) {
                add(ev);
                if (agg.pendingExposures() >= maxItems || agg.pendingCounts() >= maxItems) {
                    flush(Long.MAX_VALUE);
                }
            }
            if (System.nanoTime() - nextTick >= 0) {
                flush(Long.MAX_VALUE);
                nextTick = System.nanoTime() + flushInterval.toNanos();
            }
        }
    }

    private void add(Evaluation ev) {
        agg.add(ev);
        aggDropped.set(agg.dropped());
    }

    private boolean empty() {
        return agg.pendingExposures() == 0 && agg.pendingCounts() == 0;
    }

    /** Empties the queue, then sends everything until the deadline, waiting out a retry delay when it fits. */
    private void drain() {
        long deadline = shutdownDeadline;
        Evaluation ev;
        while ((ev = queue.poll()) != null) {
            if (ev != WAKE) {
                add(ev);
            }
        }
        while (!stopped && !empty()) {
            long left = deadline - System.nanoTime();
            Instant now = clock.instant();
            Duration wait = now.isBefore(nextAttempt) ? Duration.between(now, nextAttempt) : Duration.ZERO;
            if (left <= 0 || wait.compareTo(Duration.ofNanos(left)) >= 0) {
                drained = false;
                return;
            }
            if (!wait.isZero() && !Backoff.sleep(wait, stopLatch)) {
                drained = false;
                return;
            }
            flush(deadline);
        }
    }

    /**
     * Sends pending items in batches of at most maxItems per list. Does nothing before nextAttempt
     * and stops at the first failure. deadline (System.nanoTime) bounds each request.
     */
    private void flush(long deadline) {
        if (clock.instant().isBefore(nextAttempt)) {
            return;
        }
        while (!stopped && !empty()) {
            Batch unsent = send(agg.take(maxItems), deadline);
            if (unsent != DONE) {
                agg.restore(unsent);
                return;
            }
        }
    }

    /** Posts batch, splitting it when it is too large. Returns the part to keep for a retry, or DONE. */
    private Batch send(Batch batch, long deadline) {
        byte[] body;
        try {
            body = Json.write(batch.toJson()).getBytes(StandardCharsets.UTF_8);
        } catch (RuntimeException e) {
            logger.log(Level.ERROR, "megooflags: cannot encode events; dropping the batch: {0}", e.getMessage());
            return DONE;
        }
        if (body.length > MAX_BODY_BYTES) {
            return split(batch, deadline, "the body is over 1 MB");
        }

        HttpResponse<Void> res;
        try {
            res = post(body, deadline);
        } catch (IOException e) {
            retryIn(backoff.next());
            logger.log(Level.DEBUG, "megooflags: sending events failed; will retry: {0}", e.getMessage());
            return batch;
        }
        int status = res.statusCode();
        IOException err = Transport.statusError(status, res.headers(), clock.instant());
        if (err == null) {
            backoff.reset();
            return DONE;
        }
        if (err instanceof FatalException) {
            signalStop();
            if (fatalOnce.compareAndSet(false, true)) {
                // On its own thread, so it may call stop() or shutdown(), which wait for this one.
                FatalException fatal = (FatalException) err;
                Thread t = new Thread(() -> onFatal.accept(fatal), "megooflags-events-fatal");
                t.setDaemon(true);
                t.start();
            }
            return DONE;
        }
        if (err instanceof RetryAfterException) {
            Duration after = ((RetryAfterException) err).after();
            Duration next = backoff.next();
            retryIn(after.compareTo(next) > 0 ? after : next);
            logger.log(
                    Level.DEBUG,
                    "megooflags: events rate limited; will retry after {0} ms",
                    String.valueOf(after.toMillis()));
            return batch;
        }
        if (status == 413) {
            return split(batch, deadline, "the server refused the body as too large");
        }
        if (err instanceof HttpStatusException && status >= 500) {
            retryIn(backoff.next());
            logger.log(
                    Level.DEBUG, "megooflags: sending events failed with HTTP {0}; will retry", String.valueOf(status));
            return batch;
        }
        logger.log(
                Level.WARNING,
                "megooflags: the server refused an events batch (HTTP {0}); dropping {1} exposures and {2} counts",
                String.valueOf(status),
                String.valueOf(batch.exposures.size()),
                String.valueOf(batch.counts.size()));
        return DONE;
    }

    /**
     * Sends the request, bounded by the send timeout and the deadline. Throws an IOException for a
     * network error, a timeout, or a request ended by stop().
     */
    private HttpResponse<Void> post(byte[] body, long deadline) throws IOException {
        long timeout = Math.min(SEND_TIMEOUT.toNanos(), deadline - System.nanoTime());
        if (timeout <= 0) {
            throw new IOException("megooflags: shutdown deadline reached");
        }
        HttpRequest req = Transport.request("POST", url, sdkKey, Transport.USER_AGENT, body)
                .timeout(Duration.ofNanos(timeout))
                .build();
        // The thread waits on a copy that stop() can cancel; an abandoned response is discarded
        // when it arrives, which releases its connection.
        CompletableFuture<HttpResponse<Void>> wait;
        synchronized (lock) {
            if (stopped) {
                throw new IOException("megooflags: events sender stopped");
            }
            wait = client.sendAsync(req, HttpResponse.BodyHandlers.discarding()).copy();
            inFlight = wait;
        }
        try {
            return wait.get(timeout, TimeUnit.NANOSECONDS);
        } catch (TimeoutException e) {
            wait.cancel(false);
            throw new IOException("megooflags: sending events timed out", e);
        } catch (CancellationException e) {
            throw new IOException("megooflags: events sender stopped", e);
        } catch (ExecutionException e) {
            Throwable cause = e.getCause();
            throw cause instanceof IOException ? (IOException) cause : new IOException(cause);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            wait.cancel(false);
            throw new IOException("megooflags: events sender interrupted", e);
        } finally {
            synchronized (lock) {
                inFlight = null;
            }
        }
    }

    /**
     * Sends batch as two halves. A single item cannot be split and is dropped. When the first half
     * must be retried, the second is kept unsent too.
     */
    private Batch split(Batch batch, long deadline, String why) {
        if (batch.size() <= 1) {
            logger.log(Level.WARNING, "megooflags: an events item is too large; dropping it ({0})", why);
            return DONE;
        }
        int h = batch.size() / 2;
        int ne = Math.min(h, batch.exposures.size());
        int nc = h - ne;
        Batch first = new Batch(
                new ArrayList<>(batch.exposures.subList(0, ne)), new ArrayList<>(batch.counts.subList(0, nc)));
        Batch second = new Batch(
                new ArrayList<>(batch.exposures.subList(ne, batch.exposures.size())),
                new ArrayList<>(batch.counts.subList(nc, batch.counts.size())));
        Batch unsent = send(first, deadline);
        if (stopped) {
            return DONE;
        }
        if (unsent != DONE) {
            List<Aggregator.Exposure> e = new ArrayList<>(unsent.exposures);
            e.addAll(second.exposures);
            List<Aggregator.Count> c = new ArrayList<>(unsent.counts);
            c.addAll(second.counts);
            return new Batch(e, c);
        }
        return send(second, deadline);
    }

    private void retryIn(Duration d) {
        nextAttempt = clock.instant().plus(d);
    }
}
