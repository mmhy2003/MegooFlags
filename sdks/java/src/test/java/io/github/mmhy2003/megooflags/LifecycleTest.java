package io.github.mmhy2003.megooflags;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import dev.openfeature.sdk.Client;
import dev.openfeature.sdk.ErrorCode;
import dev.openfeature.sdk.EventDetails;
import dev.openfeature.sdk.FlagEvaluationDetails;
import dev.openfeature.sdk.ImmutableContext;
import dev.openfeature.sdk.MutableContext;
import dev.openfeature.sdk.OpenFeatureAPI;
import dev.openfeature.sdk.ProviderEvaluation;
import dev.openfeature.sdk.ProviderEvent;
import dev.openfeature.sdk.exceptions.FatalError;
import dev.openfeature.sdk.exceptions.GeneralError;
import io.github.mmhy2003.megooflags.FakeServer.Reply;
import io.github.mmhy2003.megooflags.FakeServer.SseConnection;
import io.github.mmhy2003.megooflags.internal.delivery.Cache;
import io.github.mmhy2003.megooflags.internal.delivery.Sync;
import io.github.mmhy2003.megooflags.internal.json.Json;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class LifecycleTest {
    private static final String KEY = "mf_server_lifecycle-secret-value";
    private static final String STREAM = "/sdk/v1/stream";
    private static final String EVENTS = "/sdk/v1/events";
    private static final Duration WAIT = Duration.ofSeconds(3);
    private static final AtomicInteger DOMAINS = new AtomicInteger();

    @TempDir
    Path dir;

    private final OpenFeatureAPI api = OpenFeatureAPI.getInstance();
    private FakeServer server;
    /** Every provider event since the test started: global handlers, one provider at a time. */
    private final List<Seen> events = Collections.synchronizedList(new ArrayList<>());

    private static final class Seen {
        final ProviderEvent type;
        final EventDetails details;

        Seen(ProviderEvent type, EventDetails details) {
            this.type = type;
            this.details = details;
        }
    }

    @BeforeEach
    void start() throws IOException {
        MegooFlagsProvider.syncTuning = new Sync.Tuning()
                .retryBase(Duration.ofMillis(20))
                .retryMax(Duration.ofMillis(200))
                .stableAfter(Duration.ofMillis(100))
                .deadTimeout(Duration.ofSeconds(5));
        server = FakeServer.start();
        server.reply(EVENTS, Reply.status(202));
        api.onProviderReady(d -> events.add(new Seen(ProviderEvent.PROVIDER_READY, d)));
        api.onProviderStale(d -> events.add(new Seen(ProviderEvent.PROVIDER_STALE, d)));
        api.onProviderError(d -> events.add(new Seen(ProviderEvent.PROVIDER_ERROR, d)));
        api.onProviderConfigurationChanged(d -> events.add(new Seen(ProviderEvent.PROVIDER_CONFIGURATION_CHANGED, d)));
    }

    @AfterEach
    void stop() {
        api.shutdown();
        server.stop();
        MegooFlagsProvider.syncTuning = null;
    }

    private static String put(String id, String data) {
        return "event: put\nid: " + id + "\ndata: " + data + "\n\n";
    }

    private MegooFlagsConfig.Builder config() {
        return MegooFlagsConfig.builder().sdkKey(KEY).baseUrl(server.url()).logger(new RecordingLogger());
    }

    private List<Seen> ofType(ProviderEvent type) {
        List<Seen> out = new ArrayList<>();
        synchronized (events) {
            for (Seen s : events) {
                if (s.type == type) {
                    out.add(s);
                }
            }
        }
        return out;
    }

    /** next waits for the n-th (1-based) event of type. */
    private Seen next(ProviderEvent type, int n) throws InterruptedException {
        Await.until(WAIT, () -> ofType(type).size() >= n);
        return ofType(type).get(n - 1);
    }

    /** A provider registered under its own domain, initialising in the background. */
    private final class Registration {
        final String domain = "lifecycle-" + DOMAINS.incrementAndGet();
        final MegooFlagsProvider provider;
        final CompletableFuture<Void> ready;
        final Client client;

        Registration(MegooFlagsProvider provider) {
            this.provider = provider;
            this.client = api.getClient(domain);
            this.ready = CompletableFuture.runAsync(() -> api.setProviderAndWait(domain, provider));
        }

        void awaitReady() throws Exception {
            ready.get(WAIT.toMillis(), TimeUnit.MILLISECONDS);
        }

        /** The exception setProviderAndWait threw. */
        Throwable failure() {
            CompletionException e = assertThrows(CompletionException.class, ready::join);
            return e.getCause();
        }
    }

    private Registration register(MegooFlagsConfig cfg) {
        return new Registration(new MegooFlagsProvider(cfg));
    }

    private static boolean settled(CompletableFuture<?> f, long millis) throws InterruptedException {
        Thread.sleep(millis);
        return f.isDone();
    }

    @Test
    void initializeWaitsForTheFirstPut() throws Exception {
        server.reply(STREAM, Reply.stream());
        Registration r = register(config().build());
        SseConnection conn = server.nextStream(WAIT);
        assertFalse(settled(r.ready, 100));
        conn.write(put("e1", RulesetGen.providerRuleset()));
        r.awaitReady();
        assertEquals("blue", r.client.getStringValue("string-flag", "x"));
        next(ProviderEvent.PROVIDER_READY, 1);
        Thread.sleep(50);
        assertEquals(1, events.size());
    }

    @Test
    void initializeTimesOutKeepsRetryingAndEmitsReadyWithTheRuleset() throws Exception {
        server.reply(STREAM, Reply.stream());
        Registration r = register(config().initTimeout(Duration.ofMillis(200)).build());
        SseConnection conn = server.nextStream(WAIT);
        Throwable err = r.failure();
        assertInstanceOf(GeneralError.class, err);
        assertEquals("megooflags: no ruleset within 200 ms", err.getMessage());
        FlagEvaluationDetails<String> d = r.client.getStringDetails("string-flag", "x");
        assertEquals("x", d.getValue());
        assertEquals(ErrorCode.PROVIDER_NOT_READY, d.getErrorCode());

        conn.write(put("e1", RulesetGen.providerRuleset()));
        next(ProviderEvent.PROVIDER_READY, 1);
        assertNotNull(ofType(ProviderEvent.PROVIDER_READY).get(0).details.getMessage());
        assertEquals("blue", r.client.getStringValue("string-flag", "x"));
    }

    @Test
    void isReadyFromTheBootstrapFileThenRefreshedByTheStream() throws Exception {
        server.reply(STREAM, Reply.stream());
        Path bootstrapFile = dir.resolve("ruleset.json");
        Files.writeString(bootstrapFile, RulesetGen.providerRuleset());
        Registration r = register(config().bootstrapFile(bootstrapFile).build());
        r.awaitReady();
        assertEquals("blue", r.client.getStringValue("string-flag", "x"));

        SseConnection conn = server.nextStream(WAIT);
        conn.write(put("e2", RulesetGen.changedProviderRuleset()));
        Seen changed = next(ProviderEvent.PROVIDER_CONFIGURATION_CHANGED, 1);
        assertEquals(List.of("new-flag", "string-flag", "targeted"), changed.details.getFlagsChanged());
        assertNotNull(changed.details.getMessage());
        assertEquals("red", r.client.getStringValue("string-flag", "x"));

        conn.write(put("e3", RulesetGen.changedProviderRuleset()));
        Seen same = next(ProviderEvent.PROVIDER_CONFIGURATION_CHANGED, 2);
        assertEquals(List.of(), same.details.getFlagsChanged());
    }

    @Test
    void isReadyFromTheCacheOfflineWithoutAnyRequest() throws Exception {
        server.reply(STREAM, Reply.stream());
        Registration first = register(config().cacheDir(dir).build());
        server.nextStream(WAIT).write(put("e2", RulesetGen.changedProviderRuleset()));
        first.awaitReady();
        api.shutdown();
        int before = server.requests().size();

        Registration r = register(config().cacheDir(dir).offline(true).build());
        r.awaitReady();
        assertEquals("red", r.client.getStringValue("string-flag", "x"));
        api.shutdown();
        assertEquals(before, server.requests().size());
    }

    @Test
    void prefersTheBootstrapFileToTheCache() throws Exception {
        new Cache(dir, KEY).save(RulesetGen.changedProviderRuleset(), "e2");
        Path bootstrapFile = dir.resolve("ruleset.json");
        Files.writeString(bootstrapFile, RulesetGen.providerRuleset());
        Registration r = register(config().bootstrapFile(bootstrapFile)
                .cacheDir(dir)
                .offline(true)
                .build());
        r.awaitReady();
        assertEquals("blue", r.client.getStringValue("string-flag", "x"));

        Registration missing = register(config().bootstrapFile(dir.resolve("missing.json"))
                .cacheDir(dir)
                .offline(true)
                .build());
        missing.awaitReady();
        assertEquals("red", missing.client.getStringValue("string-flag", "x"));
    }

    @Test
    void ignoresACorruptCache() throws Exception {
        new Cache(dir, KEY).save(RulesetGen.providerRuleset(), "e1");
        try (var files = Files.list(dir)) {
            Files.writeString(files.findFirst().orElseThrow(), "{not json");
        }
        RecordingLogger log = new RecordingLogger();
        Registration r =
                register(config().cacheDir(dir).offline(true).logger(log).build());
        assertInstanceOf(GeneralError.class, r.failure());
        assertFalse(log.at(System.Logger.Level.WARNING).isEmpty());
    }

    @Test
    void failsOfflineWithoutARuleset() {
        Registration r = register(config().cacheDir(dir).offline(true).build());
        Throwable err = r.failure();
        assertInstanceOf(GeneralError.class, err);
        assertFalse(err instanceof FatalError);
    }

    @Test
    void isFatalForAClientKey() {
        Registration r = register(config().sdkKey("mf_client_abc").build());
        assertInstanceOf(FatalError.class, r.failure());
        assertEquals(0, server.requests().size());
    }

    @Test
    void isFatalWhenTheServerRefusesTheKey() throws Exception {
        server.reply(STREAM, Reply.status(401));
        Registration r = register(config().build());
        Throwable err = r.failure();
        assertInstanceOf(FatalError.class, err);
        assertFalse(err.getMessage().contains(KEY));
        FlagEvaluationDetails<String> d = r.client.getStringDetails("string-flag", "x");
        assertEquals("x", d.getValue());
        assertEquals(ErrorCode.PROVIDER_FATAL, d.getErrorCode());
        ProviderEvaluation<String> direct = r.provider.getStringEvaluation("string-flag", "x", new ImmutableContext());
        assertEquals(ErrorCode.PROVIDER_FATAL, direct.getErrorCode());
        Thread.sleep(100);
        assertEquals(1, server.received(STREAM).size());
    }

    @Test
    void failsAfterShutdown() throws Exception {
        Path bootstrapFile = dir.resolve("ruleset.json");
        Files.writeString(bootstrapFile, RulesetGen.providerRuleset());
        MegooFlagsProvider p =
                new MegooFlagsProvider(config().bootstrapFile(bootstrapFile).build());
        p.shutdown();
        GeneralError err = assertThrows(GeneralError.class, () -> p.initialize(new ImmutableContext()));
        assertTrue(err.getMessage().contains("shut down"), err.getMessage());
    }

    @Test
    void emitsTheFatalErrorOnRevokedAndKeepsServing() throws Exception {
        server.reply(STREAM, Reply.stream());
        Registration r = register(config().build());
        SseConnection conn = server.nextStream(WAIT);
        conn.write(put("e2", RulesetGen.changedProviderRuleset()));
        r.awaitReady();
        r.client.getStringValue("string-flag", "x");

        conn.write("event: revoked\ndata: {}\n\n");
        Seen ev = next(ProviderEvent.PROVIDER_ERROR, 1);
        assertEquals(ErrorCode.PROVIDER_FATAL, ev.details.getErrorCode());
        assertNotNull(ev.details.getMessage());
        assertFalse(ev.details.getMessage().contains(KEY));
        // The provider keeps serving the last ruleset; the OpenFeature SDK puts a provider that
        // reported PROVIDER_FATAL in the FATAL state and returns the default itself.
        ProviderEvaluation<String> direct = r.provider.getStringEvaluation("string-flag", "x", new ImmutableContext());
        assertEquals("red", direct.getValue());
        assertEquals("red", direct.getVariant());
        FlagEvaluationDetails<String> d = r.client.getStringDetails("string-flag", "x");
        assertEquals("x", d.getValue());
        assertEquals(ErrorCode.PROVIDER_FATAL, d.getErrorCode());

        Thread.sleep(200);
        assertEquals(1, server.received(STREAM).size());
        api.shutdown();
        assertEquals(0, server.received(EVENTS).size());
        assertEquals(1, ofType(ProviderEvent.PROVIDER_ERROR).size());
    }

    @Test
    void emitsStaleOnADisconnectAndReadyOnReconnect() throws Exception {
        server.reply(STREAM, Reply.stream());
        Registration r = register(config().build());
        SseConnection conn = server.nextStream(WAIT);
        conn.write(put("e1", RulesetGen.providerRuleset()));
        r.awaitReady();
        next(ProviderEvent.PROVIDER_READY, 1);

        conn.end();
        Seen stale = next(ProviderEvent.PROVIDER_STALE, 1);
        assertNotNull(stale.details.getMessage());
        assertEquals("blue", r.client.getStringValue("string-flag", "x"));
        next(ProviderEvent.PROVIDER_READY, 2);
        Thread.sleep(100);
        assertEquals(1, ofType(ProviderEvent.PROVIDER_STALE).size());
        assertEquals(2, ofType(ProviderEvent.PROVIDER_READY).size());
        assertEquals(3, events.size());
    }

    @Test
    void emitsNothingAfterShutdown() throws Exception {
        server.reply(STREAM, Reply.stream());
        Registration r = register(config().build());
        SseConnection conn = server.nextStream(WAIT);
        conn.write(put("e1", RulesetGen.providerRuleset()));
        r.awaitReady();
        next(ProviderEvent.PROVIDER_READY, 1);
        r.provider.shutdown();
        try {
            conn.write(put("e2", RulesetGen.changedProviderRuleset()));
        } catch (IOException e) {
            // the provider already closed the stream
        }
        Thread.sleep(100);
        assertEquals(1, events.size());
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    void sendsTheEventsOnShutdown(boolean sendContext) throws Exception {
        server.reply(STREAM, Reply.stream());
        Registration r = register(config().sendContext(sendContext).build());
        server.nextStream(WAIT).write(put("e1", RulesetGen.providerRuleset()));
        r.awaitReady();
        Instant since = Instant.parse("2026-01-02T03:04:05Z");
        MutableContext alice = new MutableContext("alice").add("plan", "pro").add("since", since);
        for (int i = 0; i < 3; i++) {
            r.client.getStringValue("string-flag", "x", alice);
        }
        r.client.getStringValue("string-flag", "x", new MutableContext("bob"));
        r.client.getBooleanValue("bool-flag", false, alice);
        r.client.getStringValue("missing", "x", alice);
        api.shutdown();

        Map<String, Long> counts = new LinkedHashMap<>();
        List<Map<?, ?>> exposures = new ArrayList<>();
        for (FakeServer.Received req : server.received(EVENTS)) {
            Map<?, ?> body = (Map<?, ?>) Json.parse(req.body);
            for (Object c : (List<?>) body.get("counts")) {
                Map<?, ?> m = (Map<?, ?>) c;
                counts.merge(m.get("flag_key") + "/" + m.get("variant"), (Long) m.get("count"), Long::sum);
            }
            for (Object e : (List<?>) body.get("exposures")) {
                exposures.add((Map<?, ?>) e);
            }
            assertEquals("Bearer " + KEY, req.header("Authorization"));
        }
        assertEquals(Map.of("string-flag/blue", 4L, "bool-flag/on", 1L, "missing/null", 1L), counts);
        List<String> seen = new ArrayList<>();
        for (Map<?, ?> e : exposures) {
            seen.add(e.get("flag_key") + "/" + e.get("variant"));
        }
        assertEquals(List.of("string-flag/blue", "string-flag/blue", "bool-flag/on"), seen);
        if (sendContext) {
            assertEquals(
                    Map.of("key", "alice", "plan", "pro", "since", "2026-01-02T03:04:05Z"),
                    exposures.get(0).get("context"));
            assertEquals(Map.of("key", "bob"), exposures.get(1).get("context"));
        } else {
            for (Map<?, ?> e : exposures) {
                assertFalse(e.containsKey("context"));
            }
        }
    }

    @Test
    void shutdownIsIdempotentAndTheLastRulesetIsStillServed() throws Exception {
        server.reply(STREAM, Reply.stream());
        Registration r = register(config().build());
        server.nextStream(WAIT).write(put("e1", RulesetGen.providerRuleset()));
        r.awaitReady();
        r.client.getStringValue("string-flag", "x");
        CompletableFuture<Void> other = CompletableFuture.runAsync(r.provider::shutdown);
        r.provider.shutdown();
        other.get(WAIT.toMillis(), TimeUnit.MILLISECONDS);
        r.provider.shutdown();
        assertEquals(1, server.received(EVENTS).size());

        ProviderEvaluation<String> d = r.provider.getStringEvaluation("string-flag", "x", new MutableContext("later"));
        assertEquals("blue", d.getValue());
        r.provider.shutdown();
        Thread.sleep(50);
        assertEquals(1, server.received(EVENTS).size());
    }
}
