package io.github.mmhy2003.megooflags.internal.delivery;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.github.mmhy2003.megooflags.FakeServer;
import io.github.mmhy2003.megooflags.FakeServer.Received;
import io.github.mmhy2003.megooflags.FakeServer.Reply;
import io.github.mmhy2003.megooflags.internal.transport.FatalException;
import io.github.mmhy2003.megooflags.internal.transport.HttpStatusException;
import io.github.mmhy2003.megooflags.internal.transport.RetryAfterException;
import io.github.mmhy2003.megooflags.internal.transport.Transport;
import java.io.IOException;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class PollerTest {
    private static final String KEY = "mf_server_poller-key-value";
    private static final String RULESET = "/sdk/v1/ruleset";
    private static final HttpClient CLIENT = HttpClient.newHttpClient();

    private FakeServer server;
    private ScheduledExecutorService scheduler;
    private volatile String etag;
    private final List<String[]> got = new ArrayList<>();

    @BeforeEach
    void setUp() throws Exception {
        server = FakeServer.start();
        scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "test-watchdog");
            t.setDaemon(true);
            return t;
        });
        etag = "";
    }

    @AfterEach
    void tearDown() {
        server.stop();
        scheduler.shutdownNow();
    }

    private Poller poller(Duration timeout) {
        return new Poller(
                CLIENT,
                Transport.url(server.url(), "ruleset"),
                KEY,
                () -> etag,
                (raw, e) -> {
                    got.add(new String[] {raw, e});
                    if (raw.equals("invalid")) {
                        return false;
                    }
                    etag = e;
                    return true;
                },
                timeout,
                scheduler);
    }

    private Poller poller() {
        return poller(Duration.ofSeconds(10));
    }

    @Test
    void pollsWithoutIfNoneMatchFirstThenWithTheEtag() throws Exception {
        server.reply(RULESET, Reply.of(200, Map.of("ETag", "\"v1\""), "{}"), Reply.status(304));
        Poller p = poller();
        p.poll();
        p.poll();
        List<Received> polls = server.received(RULESET);
        Received first = polls.get(0);
        assertNull(first.header("If-None-Match"));
        assertEquals("Bearer " + KEY, first.header("Authorization"));
        assertEquals("application/json", first.header("Accept"));
        assertEquals(Transport.USER_AGENT, first.header("User-Agent"));
        assertFalse(first.uri.contains(KEY));
        assertEquals("\"v1\"", polls.get(1).header("If-None-Match"));
    }

    @Test
    void handsA200BodyAndItsEtagToOnRuleset() throws Exception {
        server.reply(RULESET, Reply.of(200, Map.of("ETag", "\"v2\""), "{\"flags\":{}}"));
        poller().poll();
        assertEquals(1, got.size());
        assertEquals("{\"flags\":{}}", got.get(0)[0]);
        assertEquals("\"v2\"", got.get(0)[1]);
    }

    @Test
    void passesAnEmptyEtagWhenTheResponseHasNone() throws Exception {
        server.reply(RULESET, Reply.of(200, "{}"));
        poller().poll();
        assertEquals("", got.get(0)[1]);
    }

    @Test
    void treats304AsSuccessWithoutCallingOnRuleset() throws Exception {
        etag = "\"v1\"";
        server.reply(RULESET, Reply.status(304));
        poller().poll();
        assertTrue(got.isEmpty());
    }

    @Test
    void failsOnAnInvalidRuleset() {
        server.reply(RULESET, Reply.of(200, "invalid"));
        IOException err = assertThrows(IOException.class, () -> poller().poll());
        assertTrue(err.getMessage().contains("invalid ruleset"), err.getMessage());
    }

    @ParameterizedTest
    @ValueSource(ints = {401, 403})
    void failsWithAFatalErrorOnARefusedKey(int status) {
        server.reply(RULESET, Reply.status(status));
        FatalException err = assertThrows(FatalException.class, () -> poller().poll());
        assertEquals(status, err.status());
    }

    @Test
    void reportsTheRetryAfterOfA429() {
        server.reply(RULESET, Reply.of(429, Map.of("Retry-After", "7"), ""));
        RetryAfterException err = assertThrows(RetryAfterException.class, () -> poller().poll());
        assertEquals(Duration.ofSeconds(7), err.after());
    }

    @Test
    void failsWithAnHttpErrorOn500() {
        server.reply(RULESET, Reply.of(500, "oops"));
        HttpStatusException err = assertThrows(HttpStatusException.class, () -> poller().poll());
        assertEquals(500, err.status());
    }

    @Test
    void timesOutWhenTheBodyNeverEnds() {
        // A held-open response sends its headers but never finishes its body.
        server.reply(RULESET, Reply.stream());
        long started = System.nanoTime();
        IOException err = assertThrows(
                IOException.class, () -> poller(Duration.ofMillis(100)).poll());
        assertTrue(err.getMessage().contains("timed out"), err.getMessage());
        assertTrue(System.nanoTime() - started < TimeUnit.SECONDS.toNanos(2));
        assertTrue(got.isEmpty());
    }

    @Test
    void timesOutWhenTheHeadersNeverCome() {
        server.reply(RULESET, Reply.respond(req -> {
            try {
                Thread.sleep(5000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return Reply.status(304);
        }));
        long started = System.nanoTime();
        IOException err = assertThrows(
                IOException.class, () -> poller(Duration.ofMillis(100)).poll());
        assertTrue(err.getMessage().contains("timed out"), err.getMessage());
        assertTrue(System.nanoTime() - started < TimeUnit.SECONDS.toNanos(2));
    }

    @Test
    void closeAbortsAPollInFlight() throws Exception {
        server.reply(RULESET, Reply.stream());
        Poller p = poller();
        Future<Exception> done = pollInBackground(p);
        server.nextStream(Duration.ofSeconds(3));
        Thread.sleep(50);
        p.close();
        assertTrue(done.get(1, TimeUnit.SECONDS) instanceof IOException);
    }

    @Test
    void closeAbortsAPollWaitingForTheHeaders() throws Exception {
        CountDownLatch arrived = new CountDownLatch(1);
        server.reply(RULESET, Reply.respond(req -> {
            arrived.countDown();
            try {
                Thread.sleep(5000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return Reply.status(304);
        }));
        Poller p = poller();
        Future<Exception> done = pollInBackground(p);
        assertTrue(arrived.await(3, TimeUnit.SECONDS));
        p.close();
        assertNotNull(done.get(1, TimeUnit.SECONDS));
    }

    @Test
    void pollFailsAfterClose() {
        server.reply(RULESET, Reply.status(304));
        Poller p = poller();
        p.close();
        assertThrows(IOException.class, p::poll);
        assertEquals(0, server.received(RULESET).size());
    }

    /** Runs one poll on a daemon thread; the future holds what it threw, or null. */
    private static Future<Exception> pollInBackground(Poller p) {
        CompletableFuture<Exception> done = new CompletableFuture<>();
        Thread t = new Thread(() -> {
            try {
                p.poll();
                done.complete(null);
            } catch (Exception e) {
                done.complete(e);
            }
        });
        t.setDaemon(true);
        t.start();
        return done;
    }
}
