package io.github.mmhy2003.megooflags.internal.delivery;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.github.mmhy2003.megooflags.Await;
import io.github.mmhy2003.megooflags.FakeServer;
import io.github.mmhy2003.megooflags.FakeServer.Received;
import io.github.mmhy2003.megooflags.FakeServer.Reply;
import io.github.mmhy2003.megooflags.FakeServer.SseConnection;
import io.github.mmhy2003.megooflags.RecordingLogger;
import io.github.mmhy2003.megooflags.internal.transport.Backoff;
import io.github.mmhy2003.megooflags.internal.transport.FatalException;
import io.github.mmhy2003.megooflags.internal.transport.RetryAfterException;
import io.github.mmhy2003.megooflags.internal.transport.Transport;
import java.io.IOException;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Supplier;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class StreamerTest {
    private static final String KEY = "mf_server_streamer-key-value";
    private static final String STREAM = "/sdk/v1/stream";
    private static final Duration WAIT = Duration.ofSeconds(3);

    private static final HttpClient CLIENT = HttpClient.newHttpClient();

    private FakeServer server;
    private ScheduledExecutorService scheduler;
    private CountDownLatch cancel;
    private CompletableFuture<FatalException> running;
    private Streamer streamer;

    private final List<String[]> puts = Collections.synchronizedList(new ArrayList<>());
    private final AtomicInteger connected = new AtomicInteger();
    private final List<IOException> errors = Collections.synchronizedList(new ArrayList<>());
    private final List<Integer> failures = Collections.synchronizedList(new ArrayList<>());

    @BeforeEach
    void setUp() throws Exception {
        server = FakeServer.start();
        scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "test-watchdog");
            t.setDaemon(true);
            return t;
        });
        cancel = new CountDownLatch(1);
    }

    @AfterEach
    void tearDown() throws Exception {
        cancel.countDown();
        if (streamer != null) {
            streamer.close();
        }
        if (running != null) {
            running.get(5, TimeUnit.SECONDS);
        }
        server.stop();
        scheduler.shutdownNow();
    }

    private final class Options {
        Supplier<String> lastEventId = () -> "";
        Duration deadTimeout = Duration.ofSeconds(45);
        Duration stableAfter = Duration.ofSeconds(60);
        Backoff backoff = new Backoff(Duration.ofMillis(10), Duration.ofMillis(100), () -> 1);
    }

    private void start() {
        start(new Options());
    }

    private void start(Options o) {
        Streamer.Listener listener = new Streamer.Listener() {
            @Override
            public boolean onPut(String raw, String id) {
                puts.add(new String[] {raw, id});
                return !raw.equals("invalid");
            }

            @Override
            public void onConnected() {
                connected.incrementAndGet();
            }

            @Override
            public void onDisconnected(IOException err, int n) {
                errors.add(err);
                failures.add(n);
            }
        };
        streamer = new Streamer(
                CLIENT,
                Transport.url(server.url(), "stream"),
                KEY,
                new RecordingLogger(),
                o.lastEventId,
                listener,
                o.deadTimeout,
                o.stableAfter,
                o.backoff,
                scheduler);
        running = new CompletableFuture<>();
        Thread t = new Thread(() -> running.complete(streamer.run(cancel)), "test-streamer");
        t.setDaemon(true);
        t.start();
    }

    private void stop() {
        cancel.countDown();
        streamer.close();
    }

    @Test
    void connectsWithTheStreamHeadersAndHandsPutEventsToOnPut() throws Exception {
        server.reply(STREAM, Reply.stream());
        start();
        SseConnection conn = server.nextStream(WAIT);
        conn.write("event: put\nid: \"e1\"\ndata: {\"a\":1}\n\n");
        Await.until(() -> puts.size() == 1);
        assertEquals("{\"a\":1}", puts.get(0)[0]);
        assertEquals("\"e1\"", puts.get(0)[1]);
        assertEquals(1, connected.get());
        Received req = conn.request;
        assertEquals("GET", req.method);
        assertEquals("Bearer " + KEY, req.header("Authorization"));
        assertEquals("text/event-stream", req.header("Accept"));
        assertEquals("no-cache", req.header("Cache-Control"));
        assertEquals(Transport.USER_AGENT, req.header("User-Agent"));
        assertNull(req.header("Last-Event-ID"));
        assertEquals(STREAM, req.uri);
        assertFalse(req.uri.contains(KEY));
    }

    @Test
    void sendsLastEventIdWhenThereIsAnEtag() throws Exception {
        server.reply(STREAM, Reply.stream());
        Options o = new Options();
        o.lastEventId = () -> "\"etag-7\"";
        start(o);
        assertEquals("\"etag-7\"", server.nextStream(WAIT).request.header("Last-Event-ID"));
    }

    @Test
    void staysConnectedWhilePingsArrive() throws Exception {
        server.reply(STREAM, Reply.stream());
        Options o = new Options();
        o.deadTimeout = Duration.ofMillis(150);
        start(o);
        SseConnection conn = server.nextStream(WAIT);
        for (int i = 0; i < 8; i++) {
            Thread.sleep(50);
            conn.write(": ping\n");
        }
        assertEquals(1, server.received(STREAM).size());
        assertTrue(failures.isEmpty());
    }

    @Test
    void reconnectsAConnectionThatSendsNothing() throws Exception {
        server.reply(STREAM, Reply.stream());
        Options o = new Options();
        o.deadTimeout = Duration.ofMillis(50);
        start(o);
        server.nextStream(WAIT);
        server.nextStream(WAIT);
        assertEquals(1, failures.get(0));
        assertInstanceOf(Streamer.DeadConnectionException.class, errors.get(0));
        assertTrue(errors.get(0).getMessage().contains("dead"));
    }

    @Test
    void ignoresUnknownEventsAndKeepsReadingAfterAnInvalidPut() throws Exception {
        server.reply(STREAM, Reply.stream());
        start();
        SseConnection conn = server.nextStream(WAIT);
        conn.write("event: mystery\ndata: x\n\n");
        conn.write("data: unnamed\n\n");
        conn.write("event: put\nid: 1\ndata: invalid\n\n");
        conn.write("event: put\nid: 2\ndata: {}\n\n");
        Await.until(() -> puts.size() == 2);
        assertEquals("1", puts.get(0)[1]);
        assertEquals("2", puts.get(1)[1]);
        assertTrue(failures.isEmpty());
        assertEquals(1, server.received(STREAM).size());
    }

    @Test
    void stopsWithAFatalErrorOnRevoked() throws Exception {
        server.reply(STREAM, Reply.stream());
        start();
        SseConnection conn = server.nextStream(WAIT);
        conn.write("event: revoked\ndata: {}\n\n");
        FatalException err = running.get(3, TimeUnit.SECONDS);
        assertEquals("revoked", err.reason());
        assertTrue(failures.isEmpty());
        conn.awaitClosed(WAIT);
        assertEquals(1, server.received(STREAM).size());
    }

    @ParameterizedTest
    @ValueSource(ints = {401, 403})
    void stopsWithAFatalErrorOnARefusedKey(int status) throws Exception {
        server.reply(STREAM, Reply.status(status));
        start();
        FatalException err = running.get(3, TimeUnit.SECONDS);
        assertEquals(status, err.status());
        assertEquals(0, connected.get());
        assertTrue(failures.isEmpty());
        assertEquals(1, server.received(STREAM).size());
    }

    @Test
    void waitsTheRetryAfterOfA429() throws Exception {
        server.reply(STREAM, Reply.of(429, Map.of("Retry-After", "1"), ""), Reply.stream());
        start();
        server.nextStream(WAIT);
        List<Received> got = server.received(STREAM);
        assertTrue(got.get(1).at - got.get(0).at >= 950, "waited " + (got.get(1).at - got.get(0).at));
        assertInstanceOf(RetryAfterException.class, errors.get(0));
    }

    @Test
    void backsOffWithGrowingWaitsOn5xx() throws Exception {
        server.reply(
                STREAM, Reply.status(500), Reply.status(502), Reply.status(503), Reply.status(500), Reply.stream());
        Options o = new Options();
        o.backoff = new Backoff(Duration.ofMillis(40), Duration.ofMillis(1000), () -> 1);
        start(o);
        server.nextStream(WAIT);
        List<Received> got = server.received(STREAM);
        assertEquals(5, got.size());
        long[] want = {40, 80, 160, 320};
        for (int i = 0; i < want.length; i++) {
            long gap = got.get(i + 1).at - got.get(i).at;
            assertTrue(gap >= want[i] - 5, "gap " + i + " was " + gap);
        }
        assertEquals(List.of(1, 2, 3, 4), failures);
    }

    @Test
    void usesTheServersRetryFieldAsTheBackoffBase() throws Exception {
        server.reply(STREAM, Reply.stream());
        Options o = new Options();
        o.backoff = new Backoff(Duration.ofSeconds(5), Duration.ofSeconds(60), () -> 1);
        start(o);
        SseConnection conn = server.nextStream(WAIT);
        conn.write("retry: 20\n\n");
        Thread.sleep(50);
        conn.end();
        long started = System.nanoTime();
        server.nextStream(WAIT);
        assertTrue(System.nanoTime() - started < TimeUnit.SECONDS.toNanos(1));
    }

    @Test
    void resetsTheBackoffAfterAStableConnection() throws Exception {
        server.reply(STREAM, Reply.status(500), Reply.status(500), Reply.stream(), Reply.status(500), Reply.stream());
        Options o = new Options();
        o.stableAfter = Duration.ofMillis(50);
        start(o);
        SseConnection conn = server.nextStream(WAIT);
        Thread.sleep(80);
        conn.end();
        server.nextStream(WAIT);
        assertEquals(List.of(1, 2, 1, 2), failures);
    }

    @Test
    void returnsPromptlyWhenStopped() throws Exception {
        server.reply(STREAM, Reply.stream());
        start();
        SseConnection conn = server.nextStream(WAIT);
        long started = System.nanoTime();
        stop();
        assertNull(running.get(3, TimeUnit.SECONDS));
        assertTrue(System.nanoTime() - started < TimeUnit.MILLISECONDS.toNanos(500));
        conn.awaitClosed(WAIT);
        assertTrue(failures.isEmpty());
    }

    @Test
    void returnsAtOnceWhenStoppedWhileWaitingToReconnect() throws Exception {
        server.reply(STREAM, Reply.status(500));
        Options o = new Options();
        o.backoff = new Backoff(Duration.ofSeconds(60), Duration.ofSeconds(60));
        start(o);
        Await.until(() -> failures.size() == 1);
        stop();
        assertNull(running.get(1, TimeUnit.SECONDS));
    }

    @Test
    void returnsAtOnceWhenStoppedWhileConnecting() throws Exception {
        CountDownLatch arrived = new CountDownLatch(1);
        server.reply(STREAM, Reply.respond(req -> {
            arrived.countDown();
            try {
                Thread.sleep(5000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return Reply.status(500);
        }));
        start();
        assertTrue(arrived.await(3, TimeUnit.SECONDS));
        stop();
        assertNull(running.get(1, TimeUnit.SECONDS));
        assertTrue(failures.isEmpty());
    }
}
