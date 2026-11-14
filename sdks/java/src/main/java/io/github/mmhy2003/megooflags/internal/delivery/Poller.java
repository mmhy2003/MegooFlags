package io.github.mmhy2003.megooflags.internal.delivery;

import io.github.mmhy2003.megooflags.internal.transport.HttpStatusException;
import io.github.mmhy2003.megooflags.internal.transport.Transport;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Supplier;

/** One download of GET /sdk/v1/ruleset. The poll loop and its interval belong to Sync. */
public final class Poller {
    /** Receives a 200 body and its ETag ("" when none); false means an invalid ruleset. */
    public interface Listener {
        boolean onRuleset(String raw, String etag);
    }

    private final HttpClient client;
    private final URI url;
    private final String sdkKey;
    private final Supplier<String> etag;
    private final Listener listener;
    private final Duration timeout;
    private final ScheduledExecutorService scheduler;

    private final Exchange exchange = new Exchange();

    /**
     * timeout bounds the whole request, body included (10 s): HttpRequest.timeout covers the wait
     * for the headers, and a task on scheduler closes the body when the rest runs out.
     */
    public Poller(
            HttpClient client,
            URI url,
            String sdkKey,
            Supplier<String> etag,
            Listener listener,
            Duration timeout,
            ScheduledExecutorService scheduler) {
        this.client = client;
        this.url = url;
        this.sdkKey = sdkKey;
        this.etag = etag;
        this.listener = listener;
        this.timeout = timeout;
        this.scheduler = scheduler;
    }

    /**
     * Downloads the ruleset once. A 304 keeps what we have; a 200 goes to the listener. Throws the
     * statusError for other statuses, a timeout error after the timeout, the client's error for a
     * network failure, and an IOException once the poller is closed.
     */
    public void poll() throws IOException, InterruptedException {
        long deadline = System.nanoTime() + timeout.toNanos();
        HttpRequest.Builder req = Transport.request("GET", url, sdkKey, Transport.USER_AGENT, null)
                .timeout(timeout);
        String tag = etag.get();
        if (!tag.isEmpty()) {
            req.setHeader("If-None-Match", tag);
        }
        HttpResponse<InputStream> res;
        try {
            res = exchange.send(client, req.build());
        } catch (HttpTimeoutException e) {
            throw timedOut();
        }
        InputStream in = res.body();
        AtomicBoolean timedOut = new AtomicBoolean();
        ScheduledFuture<?> timer = null;
        try {
            int status = res.statusCode();
            if (status == 304) {
                return;
            }
            if (status != 200) {
                IOException bad = Transport.statusError(status, res.headers(), Instant.now());
                throw bad != null ? bad : new HttpStatusException(status);
            }
            try {
                timer = scheduler.schedule(
                        () -> {
                            timedOut.set(true);
                            Exchange.closeQuietly(in);
                        },
                        Math.max(deadline - System.nanoTime(), 0),
                        TimeUnit.NANOSECONDS);
            } catch (RejectedExecutionException e) {
                throw new IOException("megooflags: poller closed", e);
            }
            String raw;
            try {
                raw = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            } catch (IOException e) {
                if (timedOut.get()) {
                    throw timedOut();
                }
                throw e;
            }
            if (!listener.onRuleset(raw, res.headers().firstValue("ETag").orElse(""))) {
                throw new IOException("megooflags: invalid ruleset");
            }
        } finally {
            if (timer != null) {
                timer.cancel(false);
            }
            exchange.release();
        }
    }

    /** Aborts a poll in flight and makes every later poll fail. */
    public void close() {
        exchange.close();
    }

    private IOException timedOut() {
        return new IOException("megooflags: ruleset poll timed out after " + timeout.toMillis() + " ms");
    }
}
