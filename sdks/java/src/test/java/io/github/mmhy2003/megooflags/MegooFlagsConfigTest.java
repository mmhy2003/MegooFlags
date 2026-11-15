package io.github.mmhy2003.megooflags;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertSame;

import java.net.http.HttpClient;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.Test;

class MegooFlagsConfigTest {
    private static final String KEY = "mf_server_config-secret-value";

    private static MegooFlagsConfig.Builder base() {
        return MegooFlagsConfig.builder().sdkKey(KEY).baseUrl("https://flags.test");
    }

    @Test
    void fillsInTheDefaults() {
        MegooFlagsConfig c = base().build();
        assertEquals(KEY, c.sdkKey());
        assertEquals(Duration.ofSeconds(5), c.initTimeout());
        assertEquals(Duration.ofSeconds(30), c.pollInterval());
        assertEquals(Duration.ofSeconds(30), c.flushInterval());
        assertFalse(c.offline());
        assertFalse(c.disableStream());
        assertFalse(c.sendContext());
        assertNull(c.bootstrapFile());
        assertNull(c.cacheDir());
        assertNull(c.httpClient());
        assertEquals("io.github.mmhy2003.megooflags", c.logger().getName());
    }

    @Test
    void keepsWhatItIsGiven() {
        HttpClient client = HttpClient.newHttpClient();
        RecordingLogger logger = new RecordingLogger();
        MegooFlagsConfig c = base().bootstrapFile(Path.of("/tmp/r.json"))
                .cacheDir(Path.of("/tmp/c"))
                .offline(true)
                .initTimeout(Duration.ofMillis(200))
                .disableStream(true)
                .sendContext(true)
                .httpClient(client)
                .logger(logger)
                .build();
        assertEquals(Path.of("/tmp/r.json"), c.bootstrapFile());
        assertEquals(Path.of("/tmp/c"), c.cacheDir());
        assertEquals(Duration.ofMillis(200), c.initTimeout());
        assertEquals(true, c.offline());
        assertEquals(true, c.disableStream());
        assertEquals(true, c.sendContext());
        assertSame(client, c.httpClient());
        assertSame(logger, c.logger());
    }

    @Test
    void clampsTheIntervals() {
        MegooFlagsConfig low = base().pollInterval(Duration.ofSeconds(1))
                .flushInterval(Duration.ofSeconds(1))
                .build();
        assertEquals(Duration.ofSeconds(15), low.pollInterval());
        assertEquals(Duration.ofSeconds(10), low.flushInterval());
        MegooFlagsConfig mid = base().pollInterval(Duration.ofSeconds(45))
                .flushInterval(Duration.ofSeconds(45))
                .build();
        assertEquals(Duration.ofSeconds(45), mid.pollInterval());
        assertEquals(Duration.ofSeconds(45), mid.flushInterval());
        MegooFlagsConfig high = base().pollInterval(Duration.ofMinutes(10))
                .flushInterval(Duration.ofMinutes(10))
                .build();
        assertEquals(Duration.ofMinutes(10), high.pollInterval());
        assertEquals(Duration.ofSeconds(60), high.flushInterval());
    }

    @Test
    void treatsNullZeroAndNegativeDurationsAsUnset() {
        for (Duration d : Arrays.asList(null, Duration.ZERO, Duration.ofSeconds(-1))) {
            MegooFlagsConfig c =
                    base().initTimeout(d).pollInterval(d).flushInterval(d).build();
            assertEquals(Duration.ofSeconds(5), c.initTimeout());
            assertEquals(Duration.ofSeconds(30), c.pollInterval());
            assertEquals(Duration.ofSeconds(30), c.flushInterval());
        }
    }

    @Test
    void removesTrailingSlashesFromTheBaseUrl() {
        assertEquals("http://h:1", base().baseUrl("http://h:1//").build().baseUrl());
    }

    @Test
    void acceptsAServerKeyWithAnHttpOrHttpsBaseUrl() {
        assertNull(base().build().validate());
        assertNull(base().baseUrl("http://127.0.0.1:8080/").build().validate());
    }

    @Test
    void refusesAClientKey() {
        assertEquals(
                "megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)",
                base().sdkKey("mf_client_abc").build().validate());
    }

    @Test
    void refusesAKeyThatIsNotAServerKey() {
        for (String key : Arrays.asList(null, "", "abc", "mf_serverx")) {
            assertEquals(
                    "megooflags: invalid SDK key; use a server key (mf_server_…)",
                    base().sdkKey(key).build().validate(),
                    String.valueOf(key));
        }
    }

    @Test
    void refusesABaseUrlThatIsNotHttpOrHttpsWithAHost() {
        for (String url : Arrays.asList(
                null, "", "flags.test", "ftp://flags.test", "file:///tmp/x", "http://", "http:///x", "http://a b")) {
            assertEquals(
                    "megooflags: baseUrl must be an http or https URL",
                    base().baseUrl(url).build().validate(),
                    String.valueOf(url));
        }
    }

    @Test
    void needsABootstrapFileOrACacheDirectoryOffline() {
        MegooFlagsConfig.Builder offline =
                MegooFlagsConfig.builder().sdkKey(KEY).offline(true);
        assertEquals(
                "megooflags: offline needs a bootstrapFile or a cacheDir",
                offline.build().validate());
        assertNull(offline.cacheDir(Path.of("/tmp/x")).build().validate());
        assertNull(MegooFlagsConfig.builder()
                .sdkKey(KEY)
                .offline(true)
                .bootstrapFile(Path.of("/tmp/r.json"))
                .build()
                .validate());
    }

    @Test
    void neverPutsTheKeyInAMessage() {
        for (String key : List.of(KEY, "mf_client_client-secret-value", "not-a-key-secret")) {
            for (MegooFlagsConfig c : List.of(
                    MegooFlagsConfig.builder().sdkKey(key).build(),
                    MegooFlagsConfig.builder().sdkKey(key).baseUrl("nope").build(),
                    MegooFlagsConfig.builder().sdkKey(key).offline(true).build())) {
                String msg = String.valueOf(c.validate());
                assertFalse(msg.contains(key), msg);
                assertFalse(msg.contains("secret"), msg);
                assertFalse(c.toString().contains("secret"), c.toString());
            }
        }
    }
}
