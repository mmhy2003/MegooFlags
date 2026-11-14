package io.github.mmhy2003.megooflags.internal.delivery;

import static io.github.mmhy2003.megooflags.RulesetGen.cl;
import static io.github.mmhy2003.megooflags.RulesetGen.ruleFlag;
import static io.github.mmhy2003.megooflags.RulesetGen.rulesetJson;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.github.mmhy2003.megooflags.Await;
import io.github.mmhy2003.megooflags.FakeServer;
import io.github.mmhy2003.megooflags.FakeServer.Received;
import io.github.mmhy2003.megooflags.FakeServer.Reply;
import io.github.mmhy2003.megooflags.FakeServer.SseConnection;
import io.github.mmhy2003.megooflags.RecordingLogger;
import io.github.mmhy2003.megooflags.internal.delivery.Snapshot.Source;
import io.github.mmhy2003.megooflags.internal.delivery.Sync.Status;
import io.github.mmhy2003.megooflags.internal.engine.ParseException;
import io.github.mmhy2003.megooflags.internal.json.Json;
import io.github.mmhy2003.megooflags.internal.transport.FatalException;
import java.io.IOException;
import java.lang.System.Logger.Level;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class SyncTest {
    private static final String KEY = "mf_server_sync-key-value";
    private static final String STREAM = "/sdk/v1/stream";
    private static final String RULESET = "/sdk/v1/ruleset";
    private static final Duration WAIT = Duration.ofSeconds(3);

    @TempDir
    Path dir;

    private FakeServer server;
    private Store store;
    private Cache cache;
    private Sync sync;
    private RecordingLogger logger;
    private final List<Snapshot[]> changes = Collections.synchronizedList(new ArrayList<>());
    private final List<Status> statuses = Collections.synchronizedList(new ArrayList<>());
    private final List<IOException> statusErrors = Collections.synchronizedList(new ArrayList<>());

    private static String raw(String env) {
        Map<String, Object> rs = rulesetJson(List.of(), Map.of());
        rs.put("environment", env);
        return Json.write(rs);
    }

    private static String put(String id, String data) {
        return "event: put\nid: " + id + "\ndata: " + data + "\n\n";
    }

    @BeforeEach
    void setUp() throws Exception {
        server = FakeServer.start();
        store = new Store();
        cache = new Cache(dir, KEY);
        logger = new RecordingLogger();
    }

    @AfterEach
    void tearDown() {
        if (sync != null) {
            sync.stop();
        }
        server.stop();
    }

    private Sync.Tuning tuning() {
        return new Sync.Tuning()
                .retryBase(Duration.ofMillis(10))
                .retryMax(Duration.ofMillis(50))
                .pollInterval(Duration.ofMillis(20));
    }

    private Sync.Options options() {
        return new Sync.Options(server.url(), KEY, store)
                .logger(logger)
                .cache(cache)
                .listener(new Sync.Listener() {
                    @Override
                    public void onChange(Snapshot old, Snapshot next) {
                        changes.add(new Snapshot[] {old, next});
                    }

                    @Override
                    public void onStatus(Status status, IOException err) {
                        statusErrors.add(err);
                        statuses.add(status);
                    }
                })
                .tuning(tuning());
    }

    private Sync newSync(Sync.Options o) {
        sync = new Sync(o);
        return sync;
    }

    private Sync newSync() {
        return newSync(options());
    }

    private List<String> files() throws IOException {
        try (Stream<Path> s = Files.list(dir)) {
            return s.map(p -> p.getFileName().toString()).collect(Collectors.toList());
        }
    }

    private List<Status> statuses() {
        synchronized (statuses) {
            return new ArrayList<>(statuses);
        }
    }

    @Test
    void aBootstrapApplyDoesNotWriteTheCache() throws Exception {
        assertNull(newSync().apply(raw("boot"), "", Source.BOOTSTRAP));
        assertEquals(Source.BOOTSTRAP, store.load().source());
        assertEquals("boot", store.load().ruleset().environment);
        assertEquals(List.of(), files());
        assertEquals(1, changes.size());
        assertNull(changes.get(0)[0]);
    }

    @Test
    void anInvalidRulesetKeepsThePreviousOne() {
        Sync s = newSync();
        s.apply(raw("one"), "\"1\"", Source.POLL);
        ParseException err = s.apply("{\"schema_version\":99}", "\"2\"", Source.POLL);
        assertNotNull(err);
        assertEquals("\"1\"", store.load().etag());
        assertEquals(1, changes.size());
        assertTrue(String.join("\n", logger.at(Level.WARNING)).contains("invalid ruleset"));
    }

    @Test
    void logsEachRulesetWarningOnce() {
        String bad =
                Json.write(rulesetJson(List.of(ruleFlag("re", cl("email", "matches", false, "(?<n>x)"))), Map.of()));
        Sync s = newSync();
        s.apply(bad, "\"1\"", Source.STREAM);
        s.apply(bad, "\"2\"", Source.STREAM);
        List<String> regex = logger.at(Level.WARNING).stream()
                .filter(w -> w.contains("regex"))
                .collect(Collectors.toList());
        assertEquals(1, regex.size());
        assertTrue(regex.get(0).contains("flags.re"), regex.get(0));
    }

    @Test
    void aStreamPutUpdatesTheStoreAndTheCache() throws Exception {
        server.reply(STREAM, Reply.stream());
        newSync().start();
        SseConnection conn = server.nextStream(WAIT);
        conn.write(put("\"e1\"", raw("streamed")));
        Await.until(() -> changes.size() == 1);
        assertEquals("\"e1\"", store.load().etag());
        assertEquals(Source.STREAM, store.load().source());
        Cache.Entry e = cache.load();
        assertEquals("\"e1\"", e.etag());
        assertEquals(Json.parse(raw("streamed")), e.ruleset());
        assertEquals(List.of(Status.READY), statuses());
    }

    @Test
    void sendsTheStoresEtagAsLastEventId() throws Exception {
        server.reply(STREAM, Reply.stream());
        Sync s = newSync();
        s.apply(raw("boot"), "\"boot-etag\"", Source.BOOTSTRAP);
        s.start();
        assertEquals("\"boot-etag\"", server.nextStream(WAIT).request.header("Last-Event-ID"));
    }

    @Test
    void anInvalidPutKeepsThePreviousRulesetAndTheStream() throws Exception {
        server.reply(STREAM, Reply.stream());
        newSync().start();
        SseConnection conn = server.nextStream(WAIT);
        conn.write(put("\"1\"", raw("one")));
        conn.write(put("\"2\"", "{nope"));
        conn.write(put("\"3\"", raw("three")));
        Await.until(() -> changes.size() == 2);
        assertEquals("\"3\"", store.load().etag());
        assertEquals(1, server.received(STREAM).size());
    }

    @Test
    void pollsAfterTwoStreamFailuresAndStopsPollingOnReconnect() throws Exception {
        server.reply(STREAM, Reply.status(500), Reply.status(500), Reply.stream());
        server.reply(RULESET, Reply.of(200, Map.of("ETag", "\"p1\""), raw("polled")));
        newSync(options()
                        .tuning(new Sync.Tuning()
                                .retryBase(Duration.ofMillis(60))
                                .retryMax(Duration.ofMillis(60))
                                .pollInterval(Duration.ofMillis(10))))
                .start();
        server.nextStream(WAIT);
        Await.until(() -> statuses().contains(Status.READY));
        List<Received> polls = server.received(RULESET);
        assertTrue(polls.size() >= 1);
        // The first poll came after the second stream failure.
        assertTrue(polls.get(0).at >= server.received(STREAM).get(1).at);
        assertEquals(Source.POLL, store.load().source());
        assertEquals("\"p1\"", cache.load().etag());
        assertEquals(List.of(Status.STALE, Status.STALE, Status.READY), statuses());
        // A poll already in flight at the reconnect may still land; none starts after it.
        Thread.sleep(50);
        int count = server.received(RULESET).size();
        Thread.sleep(100);
        assertEquals(count, server.received(RULESET).size());
    }

    @Test
    void theDefaultClientSpeaksHttp11WithoutAnUpgrade() throws Exception {
        server.reply(STREAM, Reply.status(500), Reply.status(500), Reply.stream());
        server.reply(RULESET, Reply.of(200, raw("polled")));
        newSync().start();
        server.nextStream(WAIT);
        Await.until(() -> !server.received(RULESET).isEmpty());

        assertNull(server.received(STREAM).get(0).header("Upgrade"));
        assertNull(server.received(RULESET).get(0).header("Upgrade"));
    }

    @Test
    void doesNotPollAfterASingleStreamFailure() throws Exception {
        server.reply(STREAM, Reply.status(500), Reply.stream());
        server.reply(RULESET, Reply.of(200, raw("polled")));
        newSync().start();
        server.nextStream(WAIT);
        Thread.sleep(50);
        assertEquals(0, server.received(RULESET).size());
    }

    @Test
    void keepsPollsAtLeastThePollIntervalApart() throws Exception {
        server.reply(RULESET, Reply.status(304));
        newSync(options().disableStream(true).tuning(new Sync.Tuning().pollInterval(Duration.ofMillis(100))))
                .start();
        Thread.sleep(350);
        List<Received> polls = server.received(RULESET);
        assertTrue(polls.size() >= 3 && polls.size() <= 4, "polls: " + polls.size());
        // Measured on arrival: the first request also opens the connection, which can shorten the
        // first gap by a few milliseconds.
        for (int i = 1; i < polls.size(); i++) {
            long gap = polls.get(i).at - polls.get(i - 1).at;
            assertTrue(gap >= 85, "gap " + gap);
        }
    }

    @Test
    void waitsTheRetryAfterOfAPoll() throws Exception {
        server.reply(RULESET, Reply.of(429, Map.of("Retry-After", "1"), ""), Reply.status(304));
        newSync(options().disableStream(true)).start();
        Await.until(Duration.ofSeconds(3), () -> server.received(RULESET).size() == 2);
        List<Received> polls = server.received(RULESET);
        assertTrue(polls.get(1).at - polls.get(0).at >= 950);
    }

    @Test
    void withDisableStreamPollsOnlyAndReportsStaleThenReady() throws Exception {
        server.reply(
                RULESET, Reply.status(500), Reply.of(200, Map.of("ETag", "\"v1\""), raw("polled")), Reply.status(304));
        newSync(options().disableStream(true)).start();
        Await.until(() -> statuses().size() >= 3);
        assertEquals(
                List.of(Status.STALE, Status.READY, Status.READY), statuses().subList(0, 3));
        assertEquals(0, server.received(STREAM).size());
        assertEquals("\"v1\"", store.load().etag());
    }

    @Test
    void reportsRevokedAsFatalOnceAndStops() throws Exception {
        server.reply(STREAM, Reply.stream());
        server.reply(RULESET, Reply.of(200, raw("polled")));
        newSync().start();
        SseConnection conn = server.nextStream(WAIT);
        conn.write("event: revoked\ndata: {}\n\n");
        Await.until(() -> statuses().contains(Status.FATAL));
        Thread.sleep(80);
        assertEquals(List.of(Status.READY, Status.FATAL), statuses());
        assertEquals("revoked", ((FatalException) statusErrors.get(1)).reason());
        assertEquals(1, server.received(STREAM).size());
        assertEquals(0, server.received(RULESET).size());
    }

    @Test
    void a403OnAPollIsFatalAndStopsPolling() throws Exception {
        server.reply(RULESET, Reply.status(403));
        newSync(options().disableStream(true)).start();
        Await.until(() -> statuses().contains(Status.FATAL));
        Thread.sleep(80);
        assertEquals(List.of(Status.FATAL), statuses());
        assertEquals(403, ((FatalException) statusErrors.get(0)).status());
        assertEquals(1, server.received(RULESET).size());
    }

    @Test
    void aFatalPollStopsTheStream() throws Exception {
        server.reply(STREAM, Reply.status(500));
        server.reply(RULESET, Reply.status(401));
        newSync(options()
                        .tuning(new Sync.Tuning()
                                .retryBase(Duration.ofMillis(30))
                                .retryMax(Duration.ofMillis(30))
                                .pollInterval(Duration.ofMillis(10))))
                .start();
        Await.until(() -> statuses().contains(Status.FATAL));
        int streams = server.received(STREAM).size();
        Thread.sleep(100);
        assertEquals(streams, server.received(STREAM).size());
        assertEquals(1, statuses().stream().filter(s -> s == Status.FATAL).count());
    }

    @Test
    void stopIsSafeBeforeStartAndIdempotent() {
        Sync s = newSync();
        s.stop();
        s.stop();
    }

    @Test
    void stopEndsTheWorkersAndSilencesCallbacks() throws Exception {
        server.reply(STREAM, Reply.stream());
        Sync s = newSync();
        s.start();
        SseConnection conn = server.nextStream(WAIT);
        long started = System.nanoTime();
        s.stop();
        s.stop();
        assertTrue(System.nanoTime() - started < TimeUnit.MILLISECONDS.toNanos(500));
        conn.awaitClosed(WAIT);
        int before = statuses().size();
        s.start();
        Thread.sleep(50);
        assertEquals(before, statuses().size());
        assertEquals(1, server.received(STREAM).size());
        for (Thread t : Thread.getAllStackTraces().keySet()) {
            assertTrue(!t.getName().startsWith("megooflags-") || !t.isAlive(), "still running: " + t.getName());
        }
    }

    @Test
    void workerThreadsAreDaemonsNamedMegooflags() throws Exception {
        server.reply(STREAM, Reply.stream());
        newSync().start();
        server.nextStream(WAIT);
        List<Thread> ours = Thread.getAllStackTraces().keySet().stream()
                .filter(t -> t.getName().startsWith("megooflags-"))
                .collect(Collectors.toList());
        assertTrue(ours.stream().anyMatch(t -> t.getName().equals("megooflags-stream")), ours.toString());
        assertTrue(ours.stream().allMatch(Thread::isDaemon));
    }
}
