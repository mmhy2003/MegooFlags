package io.github.mmhy2003.megooflags.internal.events;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.github.mmhy2003.megooflags.Await;
import io.github.mmhy2003.megooflags.FakeServer;
import io.github.mmhy2003.megooflags.FakeServer.Received;
import io.github.mmhy2003.megooflags.FakeServer.Reply;
import io.github.mmhy2003.megooflags.RecordingLogger;
import io.github.mmhy2003.megooflags.internal.json.Json;
import io.github.mmhy2003.megooflags.internal.transport.FatalException;
import io.github.mmhy2003.megooflags.internal.transport.Transport;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BiFunction;
import java.util.function.Consumer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class BatcherTest {
    private static final String KEY = "mf_server_batcher-key-value";
    private static final String PATH = "/sdk/v1/events";

    private FakeServer server;
    private final RecordingLogger logger = new RecordingLogger();
    private final List<Batcher> batchers = new ArrayList<>();
    /** The bodies the server answered with a 2xx. */
    private final List<Map<String, Object>> accepted = Collections.synchronizedList(new ArrayList<>());

    @BeforeEach
    void startServer() throws IOException {
        server = FakeServer.start();
    }

    @AfterEach
    void stopAll() {
        for (Batcher b : batchers) {
            b.stop();
        }
        server.stop();
    }

    /** Answers each events request (n counts from 1) with the status fn picks. */
    private void respond(BiFunction<Integer, Map<String, Object>, Reply> fn) {
        AtomicInteger n = new AtomicInteger();
        server.reply(PATH, Reply.respond(r -> fn.apply(n.incrementAndGet(), body(r))));
    }

    private void respondStatus(int status) {
        respond((n, b) -> accept(Reply.status(status), status, b));
    }

    private Reply accept(Reply reply, int status, Map<String, Object> body) {
        if (status >= 200 && status < 300) {
            accepted.add(body);
        }
        return reply;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> body(Received r) {
        return (Map<String, Object>) Json.parse(r.body);
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> list(Map<String, Object> body, String name) {
        return (List<Map<String, Object>>) body.get(name);
    }

    private static int items(Map<String, Object> body) {
        return list(body, "exposures").size() + list(body, "counts").size();
    }

    private int acceptedExposures() {
        int n = 0;
        synchronized (accepted) {
            for (Map<String, Object> b : accepted) {
                n += list(b, "exposures").size();
            }
        }
        return n;
    }

    private Map<String, Long> acceptedCounts() {
        Map<String, Long> out = new HashMap<>();
        synchronized (accepted) {
            for (Map<String, Object> b : accepted) {
                for (Map<String, Object> c : list(b, "counts")) {
                    out.merge((String) c.get("flag_key"), (Long) c.get("count"), Long::sum);
                }
            }
        }
        return out;
    }

    private List<Received> requests() {
        return server.received(PATH);
    }

    private Batcher batcher(Consumer<Batcher.Options> mod) {
        Batcher.Options o = new Batcher.Options(Transport.url(server.url(), "events"), KEY)
                .logger(logger)
                .flushInterval(Duration.ofMillis(50))
                .tuning(new Batcher.Tuning().retryBase(Duration.ofMillis(10)).retryMax(Duration.ofMillis(40)));
        if (mod != null) {
            mod.accept(o);
        }
        Batcher b = new Batcher(o);
        batchers.add(b);
        return b;
    }

    private static Evaluation eval(String flag, String ctxKey) {
        return new Evaluation(flag, "on", true, "STATIC", ctxKey, null, Instant.now());
    }

    private static void shutdown(Batcher b) {
        assertTrue(b.shutdown(Duration.ofSeconds(5)), "shutdown did not send everything");
    }

    @Test
    void sendsBearerUserAgentAndJson() throws Exception {
        respondStatus(202);
        Batcher b = batcher(null);
        b.start();
        b.record(eval("f", "u1"));
        Await.until(() -> requests().size() == 1);
        shutdown(b);

        Received r = requests().get(0);
        assertEquals("POST", r.method);
        assertEquals("Bearer " + KEY, r.header("Authorization"));
        assertEquals(Transport.USER_AGENT, r.header("User-Agent"));
        assertEquals("application/json", r.header("Content-Type"));
        assertFalse(r.uri.contains(KEY));
        Map<String, Object> body = body(r);
        assertEquals(List.of("exposures", "counts"), new ArrayList<>(body.keySet()));
        assertEquals(2, items(body));
    }

    @Test
    void flushesOnTheInterval() throws Exception {
        respondStatus(202);
        Batcher b = batcher(null);
        b.start();
        b.record(eval("f", "u1"));
        Await.until(() -> acceptedExposures() == 1);

        assertEquals(Map.of("f", 1L), acceptedCounts());
        shutdown(b);
    }

    @Test
    void flushesAt1000PendingItems() throws Exception {
        respondStatus(202);
        Batcher b = batcher(o -> o.flushInterval(Duration.ofHours(1)));
        b.start();
        for (int i = 0; i < 1000; i++) {
            b.record(eval("f", "u" + i));
        }
        Await.until(() -> requests().size() >= 1);
        assertEquals(1000, list(body(requests().get(0)), "exposures").size());
    }

    @Test
    void shutdownFlushesAndIsIdempotent() {
        respondStatus(202);
        Batcher b = batcher(o -> o.flushInterval(Duration.ofHours(1)));
        b.start();
        b.record(eval("f", "u1"));
        b.record(eval("f", "u2"));
        b.record(eval("g", "u1"));
        shutdown(b);

        assertEquals(3, acceptedExposures());
        assertEquals(Map.of("f", 2L, "g", 1L), acceptedCounts());
        assertTrue(b.shutdown(Duration.ofSeconds(1)));
        assertEquals(1, requests().size());
    }

    @Test
    void shutdownEndsAtTheDeadline() {
        CountDownLatch release = new CountDownLatch(1);
        respond((n, body) -> {
            try {
                release.await(5, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return Reply.status(202);
        });
        try {
            Batcher b = batcher(o -> o.flushInterval(Duration.ofHours(1)));
            b.start();
            b.record(eval("f", "u1"));

            long start = System.nanoTime();
            assertFalse(b.shutdown(Duration.ofMillis(200)));
            long elapsed = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start);
            assertTrue(elapsed < 1000, "shutdown took " + elapsed + " ms, want about 200");
        } finally {
            release.countDown();
        }
    }

    @Test
    void stopSendsNothing() throws Exception {
        respondStatus(202);
        Batcher b = batcher(o -> o.flushInterval(Duration.ofHours(1)));
        b.start();
        b.record(eval("f", "u1"));
        b.stop();
        b.stop();
        assertTrue(b.shutdown(Duration.ofSeconds(1)));
        Thread.sleep(100);
        assertEquals(0, requests().size());
    }

    @Test
    void aFullQueueDropsWithoutBlocking() {
        Batcher b = batcher(o -> o.tuning(new Batcher.Tuning().queueSize(1)));
        long start = System.nanoTime();
        b.record(eval("f", "u1"));
        b.record(eval("f", "u2"));
        assertTrue(System.nanoTime() - start < TimeUnit.MILLISECONDS.toNanos(100));
        assertEquals(1, b.dropped());
    }

    @Test
    void recordAfterShutdownOrStopIsDropped() throws Exception {
        respondStatus(202);
        Batcher b = batcher(null);
        b.start();
        shutdown(b);
        b.record(eval("f", "u1"));
        b.stop();
        b.record(eval("f", "u2"));
        assertEquals(2, b.dropped());
        Thread.sleep(100);
        assertEquals(0, requests().size());
    }

    @Test
    void unauthorizedStopsSendingAndCallsOnFatalOnce() throws Exception {
        respondStatus(401);
        AtomicInteger fatals = new AtomicInteger();
        AtomicReference<FatalException> fatal = new AtomicReference<>();
        Batcher b = batcher(o -> o.onFatal(e -> {
            fatals.incrementAndGet();
            fatal.set(e);
        }));
        b.start();
        b.record(eval("f", "u1"));
        Await.until(() -> fatals.get() == 1);
        b.record(eval("g", "u1"));
        Thread.sleep(200);
        assertTrue(b.shutdown(Duration.ofSeconds(1)));

        assertEquals(1, requests().size());
        assertEquals(1, fatals.get());
        assertEquals(401, fatal.get().status());
    }

    @Test
    void onFatalMayCallStopAndShutdown() throws Exception {
        respondStatus(403);
        CountDownLatch returned = new CountDownLatch(1);
        AtomicReference<Batcher> self = new AtomicReference<>();
        AtomicReference<Boolean> result = new AtomicReference<>();
        Batcher b = batcher(o -> o.onFatal(e -> {
            self.get().stop();
            result.set(self.get().shutdown(Duration.ofSeconds(5)));
            returned.countDown();
        }));
        self.set(b);
        b.start();
        b.record(eval("f", "u1"));
        assertTrue(returned.await(2, TimeUnit.SECONDS), "stop and shutdown called from onFatal did not return");
        assertEquals(Boolean.TRUE, result.get());
    }

    @Test
    void tooLargeSplitsTheBatch() {
        respond((n, body) -> {
            int status = items(body) > 10 ? 413 : 202;
            return accept(Reply.status(status), status, body);
        });
        Batcher b = batcher(o -> o.flushInterval(Duration.ofHours(1)));
        b.start();
        for (int i = 0; i < 25; i++) {
            b.record(eval("f", "u" + i));
        }
        shutdown(b);

        assertEquals(25, acceptedExposures());
        assertEquals(Map.of("f", 25L), acceptedCounts());
        synchronized (accepted) {
            for (Map<String, Object> a : accepted) {
                int n = items(a);
                assertTrue(n > 0 && n <= 10, "accepted a batch of " + n + " items");
            }
        }
    }

    @Test
    void splitsABodyOver1MbBeforeSending() {
        respondStatus(202);
        Batcher b = batcher(o -> o.flushInterval(Duration.ofHours(1)).sendContext(true));
        b.start();
        Map<String, Object> big = Map.of("blob", "x".repeat(2000));
        for (int i = 0; i < 1000; i++) {
            b.record(new Evaluation("f", "on", true, "STATIC", "u" + i, big, Instant.now()));
        }
        shutdown(b);

        List<Received> rs = requests();
        assertTrue(rs.size() >= 2, "got " + rs.size() + " requests, want the batch split");
        for (Received r : rs) {
            int size = r.body.getBytes(StandardCharsets.UTF_8).length;
            assertTrue(size <= 1_000_000, "sent a body of " + size + " bytes");
        }
        assertEquals(1000, acceptedExposures());
    }

    @Test
    void aSingleOversizedItemIsDropped() throws Exception {
        respondStatus(202);
        Batcher b = batcher(o -> o.flushInterval(Duration.ofHours(1)).sendContext(true));
        b.start();
        b.record(new Evaluation("f", "on", true, "STATIC", "u1", Map.of("blob", "x".repeat(1_000_001)), Instant.now()));
        shutdown(b);

        // The exposure alone is too large; the count still goes.
        assertEquals(0, acceptedExposures());
        assertEquals(Map.of("f", 1L), acceptedCounts());
        assertEquals(
                List.of("megooflags: an events item is too large; dropping it (the body is over 1 MB)"),
                logger.at(System.Logger.Level.WARNING));
    }

    @Test
    void unprocessableDropsTheBatch() throws Exception {
        respondStatus(422);
        Batcher b = batcher(null);
        b.start();
        b.record(eval("f", "u1"));
        Await.until(() -> requests().size() == 1);
        Thread.sleep(200); // four intervals
        shutdown(b);
        assertEquals(1, requests().size(), "the batch is dropped, not resent");
        assertEquals(
                List.of("megooflags: the server refused an events batch (HTTP 422); dropping 1 exposures and 1 counts"),
                logger.at(System.Logger.Level.WARNING));
    }

    @Test
    void rateLimitedRetriesAfterRetryAfter() throws Exception {
        respond((n, body) ->
                n == 1 ? Reply.of(429, Map.of("Retry-After", "1"), "") : accept(Reply.status(202), 202, body));
        Batcher b = batcher(null);
        b.start();
        b.record(eval("f", "u1"));
        Await.until(Duration.ofSeconds(4), () -> requests().size() >= 2);

        List<Received> rs = requests();
        assertTrue(rs.get(1).at - rs.get(0).at >= 1000, "resent after " + (rs.get(1).at - rs.get(0).at) + " ms");
        assertEquals(body(rs.get(0)), body(rs.get(1)));
        shutdown(b);
    }

    @Test
    void serverErrorsRetryWithBackoff() throws Exception {
        respond((n, body) -> {
            int status = n <= 2 ? 503 : 202;
            return accept(Reply.status(status), status, body);
        });
        Batcher b = batcher(null);
        b.start();
        for (int i = 0; i < 5; i++) {
            b.record(eval("f", "u1"));
        }
        Await.until(() -> requests().size() >= 1);
        for (int i = 0; i < 3; i++) {
            b.record(eval("f", "u1"));
        }
        Await.until(() -> requests().size() >= 3);
        shutdown(b);

        assertEquals(1, acceptedExposures());
        assertEquals(Map.of("f", 8L), acceptedCounts());
    }

    @Test
    void noFlushBeforeTheNextAttempt() throws Exception {
        respond((n, body) -> {
            int status = n == 1 ? 500 : 202;
            return accept(Reply.status(status), status, body);
        });
        MutableClock clock = new MutableClock(Instant.parse("2026-11-10T09:00:00Z"));
        Batcher b = batcher(o -> o.clock(clock));
        b.start();
        b.record(eval("f", "u1"));
        Await.until(() -> requests().size() == 1);
        Thread.sleep(300); // six intervals, but the clock stands before the retry time
        assertEquals(1, requests().size());

        clock.advance(Duration.ofSeconds(1));
        Await.until(() -> requests().size() == 2);
        shutdown(b);
        assertEquals(1, acceptedExposures());
    }

    @Test
    void aSplitStopsAfterAFatalStatus() {
        respond((n, body) -> Reply.status(items(body) > 10 ? 413 : 401));
        Batcher b = batcher(o -> o.flushInterval(Duration.ofHours(1)));
        b.start();
        for (int i = 0; i < 25; i++) {
            b.record(eval("f", "u" + i));
        }
        assertTrue(b.shutdown(Duration.ofSeconds(5)));

        // 26 items: 413; 13: 413; 6: 401, and no attempt after it.
        assertEquals(3, requests().size());
    }

    /** A clock the test moves by hand. */
    private static final class MutableClock extends Clock {
        private final AtomicReference<Instant> now;

        MutableClock(Instant start) {
            now = new AtomicReference<>(start);
        }

        void advance(Duration d) {
            now.updateAndGet(t -> t.plus(d));
        }

        @Override
        public Instant instant() {
            return now.get();
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }
    }
}
