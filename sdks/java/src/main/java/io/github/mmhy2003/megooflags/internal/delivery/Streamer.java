package io.github.mmhy2003.megooflags.internal.delivery;

import io.github.mmhy2003.megooflags.internal.transport.Backoff;
import io.github.mmhy2003.megooflags.internal.transport.FatalException;
import io.github.mmhy2003.megooflags.internal.transport.HttpStatusException;
import io.github.mmhy2003.megooflags.internal.transport.RetryAfterException;
import io.github.mmhy2003.megooflags.internal.transport.Transport;
import java.io.IOException;
import java.io.InputStream;
import java.lang.System.Logger.Level;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * The SSE client for GET /sdk/v1/stream: reconnects with backoff until it is cancelled or the
 * server refuses the key.
 */
public final class Streamer {
    /** What the streamer reports. Callbacks run on the streaming thread. */
    public interface Listener {
        /** Receives a put event; false means an invalid ruleset. */
        boolean onPut(String raw, String id);

        /** The server answered 200. */
        void onConnected();

        /** A connection ended (or failed) for the failures-th time in a row; a retry follows. */
        void onDisconnected(IOException err, int failures);
    }

    /** A stream silent for the dead timeout. */
    public static final class DeadConnectionException extends IOException {
        private static final long serialVersionUID = 1L;

        DeadConnectionException(Duration timeout) {
            super("megooflags: stream sent nothing for " + timeout.toMillis() + " ms (dead connection)");
        }
    }

    private final HttpClient client;
    private final URI url;
    private final String sdkKey;
    private final System.Logger logger;
    private final Supplier<String> lastEventId;
    private final Listener listener;
    private final Duration deadTimeout;
    private final Duration stableAfter;
    private final Backoff backoff;
    private final ScheduledExecutorService scheduler;

    private final Exchange exchange = new Exchange();

    /**
     * deadTimeout is the longest silence before reconnecting (45 s), stableAfter how long a
     * connection must last to reset the backoff (60 s); the server's retry field replaces the
     * backoff's base. The watchdog runs on scheduler.
     */
    public Streamer(
            HttpClient client,
            URI url,
            String sdkKey,
            System.Logger logger,
            Supplier<String> lastEventId,
            Listener listener,
            Duration deadTimeout,
            Duration stableAfter,
            Backoff backoff,
            ScheduledExecutorService scheduler) {
        this.client = client;
        this.url = url;
        this.sdkKey = sdkKey;
        this.logger = logger;
        this.lastEventId = lastEventId;
        this.listener = listener;
        this.deadTimeout = deadTimeout;
        this.stableAfter = stableAfter;
        this.backoff = backoff;
        this.scheduler = scheduler;
    }

    /**
     * Streams on the calling thread until cancel is counted down, returning null, or until the
     * server refuses the key, returning the refusal. A caller that cancels also calls close(), so
     * a connection in progress ends at once.
     */
    public FatalException run(CountDownLatch cancel) {
        int failures = 0;
        while (true) {
            Outcome o = connect(cancel);
            if (cancelled(cancel)) {
                return null;
            }
            if (o.err instanceof FatalException) {
                return (FatalException) o.err;
            }
            if (o.connectedAt != 0 && System.nanoTime() - o.connectedAt >= stableAfter.toNanos()) {
                backoff.reset();
                failures = 0;
            }
            failures++;
            Duration wait = backoff.next();
            if (o.err instanceof RetryAfterException) {
                Duration after = ((RetryAfterException) o.err).after();
                if (after.compareTo(wait) > 0) {
                    wait = after;
                }
            }
            logger.log(
                    Level.DEBUG,
                    "megooflags: stream disconnected ({0}), failures {1}, retry in {2} ms",
                    o.err.getMessage(),
                    failures,
                    wait.toMillis());
            try {
                listener.onDisconnected(o.err, failures);
            } catch (RuntimeException e) {
                logger.log(Level.ERROR, "megooflags: stream callback failed", e);
            }
            if (!Backoff.sleep(wait, cancel)) {
                return null;
            }
        }
    }

    /**
     * Ends the connection in progress, whether it waits for the response headers or reads the
     * body, and makes every later connection fail: call it after counting down run's cancel latch.
     */
    public void close() {
        exchange.close();
    }

    private static boolean cancelled(CountDownLatch cancel) {
        return cancel.getCount() == 0 || Thread.currentThread().isInterrupted();
    }

    /** The end of one connection: the error, and when it was answered 200 (System.nanoTime, 0 if never). */
    private static final class Outcome {
        final long connectedAt;
        final IOException err;

        Outcome(long connectedAt, IOException err) {
            this.connectedAt = connectedAt;
            this.err = err;
        }
    }

    private Outcome connect(CountDownLatch cancel) {
        HttpRequest.Builder req = Transport.streamRequest(url, sdkKey, Transport.USER_AGENT);
        String id = lastEventId.get();
        if (!id.isEmpty()) {
            req.setHeader("Last-Event-ID", id);
        }
        HttpResponse<InputStream> res;
        try {
            res = exchange.send(client, req.build());
        } catch (IOException e) {
            return new Outcome(0, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return new Outcome(0, new IOException("megooflags: stream interrupted", e));
        }
        InputStream in = res.body();
        try {
            if (cancelled(cancel)) {
                return new Outcome(0, new IOException("megooflags: stream cancelled"));
            }
            IOException bad = Transport.statusError(res.statusCode(), res.headers(), Instant.now());
            if (bad == null && res.statusCode() != 200) {
                bad = new HttpStatusException(res.statusCode());
            }
            if (bad != null) {
                return new Outcome(0, bad);
            }
            long connectedAt = System.nanoTime();
            listener.onConnected();
            Watchdog dog = new Watchdog(in);
            try {
                SseReader.read(in, new SseReader.Callbacks() {
                    @Override
                    public void onLine() {
                        dog.lastLine = System.nanoTime();
                    }

                    @Override
                    public void onRetry(long millis) {
                        backoff.setBase(Duration.ofMillis(millis));
                    }

                    @Override
                    public void onEvent(SseReader.Event event) throws IOException {
                        Streamer.this.onEvent(event);
                    }
                });
                return new Outcome(connectedAt, new IOException("megooflags: stream ended"));
            } catch (IOException e) {
                return new Outcome(connectedAt, dog.dead ? new DeadConnectionException(deadTimeout) : e);
            } finally {
                dog.done = true;
            }
        } finally {
            exchange.release();
        }
    }

    private void onEvent(SseReader.Event event) throws IOException {
        switch (event.name()) {
            case "put":
                if (!listener.onPut(event.data(), event.id())) {
                    logger.log(
                            Level.WARNING,
                            "megooflags: stream sent an invalid ruleset (event {0}); keeping the previous one",
                            event.id());
                }
                break;
            case "revoked":
                throw FatalException.revoked();
            default:
                break;
        }
    }

    /**
     * Closes the body when no line arrived for the dead timeout. It checks when the timeout would
     * end and reschedules itself for the rest, so a line costs only a timestamp.
     */
    private final class Watchdog implements Runnable {
        private final InputStream in;
        volatile long lastLine = System.nanoTime();
        volatile boolean dead;
        volatile boolean done;

        Watchdog(InputStream in) {
            this.in = in;
            schedule(deadTimeout.toNanos());
        }

        private void schedule(long nanos) {
            try {
                scheduler.schedule(this, nanos, TimeUnit.NANOSECONDS);
            } catch (RejectedExecutionException e) {
                // The scheduler is shut down: the stream is being stopped.
            }
        }

        @Override
        public void run() {
            if (done) {
                return;
            }
            long idle = System.nanoTime() - lastLine;
            if (idle >= deadTimeout.toNanos()) {
                dead = true;
                Exchange.closeQuietly(in);
            } else {
                schedule(deadTimeout.toNanos() - idle);
            }
        }
    }
}
