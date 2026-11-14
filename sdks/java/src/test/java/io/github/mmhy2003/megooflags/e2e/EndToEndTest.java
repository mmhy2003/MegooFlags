package io.github.mmhy2003.megooflags.e2e;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import dev.openfeature.sdk.Client;
import dev.openfeature.sdk.EvaluationContext;
import dev.openfeature.sdk.EventDetails;
import dev.openfeature.sdk.ImmutableContext;
import dev.openfeature.sdk.OpenFeatureAPI;
import io.github.mmhy2003.megooflags.MegooFlagsConfig;
import io.github.mmhy2003.megooflags.MegooFlagsProvider;
import io.github.mmhy2003.megooflags.internal.json.Json;
import java.io.IOException;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.BooleanSupplier;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Runs the provider against a running MegooFlags stack. Set MEGOOFLAGS_E2E_URL (for example
 * http://localhost:8080) and MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh prints one) to run it:
 * {@code mvn -B -ntp test -Pe2e}.
 */
@Tag("e2e")
class EndToEndTest {
    private static final String BASE =
            System.getenv().getOrDefault("MEGOOFLAGS_E2E_URL", "").replaceAll("/+$", "");
    private static final String TOKEN = System.getenv().getOrDefault("MEGOOFLAGS_API_TOKEN", "");
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(10);

    private final HttpClient http = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(REQUEST_TIMEOUT)
            .build();
    private final OpenFeatureAPI api = OpenFeatureAPI.getInstance();
    private String project;

    @TempDir
    Path tmp;

    /** A management API response: the status and the parsed JSON body. */
    private static final class Response {
        final int status;
        final Map<String, Object> body;

        Response(int status, Map<String, Object> body) {
            this.status = status;
            this.body = body;
        }
    }

    @BeforeEach
    void createProject() throws Exception {
        assumeTrue(!BASE.isEmpty() && !TOKEN.isEmpty(), "MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are not set");
        byte[] random = new byte[4];
        new SecureRandom().nextBytes(random);
        StringBuilder p = new StringBuilder("sdkjava-");
        for (byte b : random) {
            p.append(String.format("%02x", b & 0xff));
        }
        Map<String, Object> env = new LinkedHashMap<>();
        env.put("key", "e2e");
        env.put("name", "E2E");
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("key", p.toString());
        body.put("name", p.toString());
        body.put("environments", List.of(env));
        must(201, "POST", "/api/projects", body);
        project = p.toString();
    }

    @AfterEach
    void deleteProject() throws Exception {
        api.shutdown();
        if (project != null) {
            call("DELETE", "/api/projects/" + project, null);
        }
    }

    @Test
    void endToEnd() throws Exception {
        String flagPath = "/api/projects/" + project + "/flags/e2e-flag/environments/e2e";
        must(
                201,
                "POST",
                "/api/projects/" + project + "/flags",
                map("key", "e2e-flag", "name", "E2E", "type", "boolean"));
        must(200, "POST", flagPath + "/toggle", map("enabled", true));
        Map<String, Object> created = must(
                201,
                "POST",
                "/api/projects/" + project + "/environments/e2e/sdk-keys",
                map("name", "java-e2e", "kind", "server"));
        Object key = created.get("key");
        assertTrue(key instanceof String && !((String) key).isEmpty(), "POST sdk-keys returned no key");
        String sdkKey = (String) key;

        Path cacheDir = tmp.resolve("cache");
        MegooFlagsProvider provider = new MegooFlagsProvider(MegooFlagsConfig.builder()
                .sdkKey(sdkKey)
                .baseUrl(BASE)
                .cacheDir(cacheDir)
                .flushInterval(Duration.ofSeconds(10))
                .build());
        api.setProviderAndWait(project, provider);
        Client client = api.getClient(project);
        List<EventDetails> changes = Collections.synchronizedList(new ArrayList<>());
        client.onProviderConfigurationChanged(changes::add);
        EvaluationContext ctx = new ImmutableContext("user-1");

        // Check 1: the downloaded ruleset is evaluated.
        assertTrue(client.getBooleanValue("e2e-flag", false, ctx));

        // Check 2: a toggle arrives over SSE within 5 s.
        must(200, "POST", flagPath + "/toggle", map("enabled", false));
        until(
                "e2e-flag false after the toggle",
                () -> !client.getBooleanValue("e2e-flag", false, ctx),
                Duration.ofSeconds(5),
                Duration.ofMillis(50));
        until("a configuration-changed event", () -> !changes.isEmpty(), Duration.ofSeconds(5), Duration.ofMillis(50));

        // Check 3: evaluation counts are sent at shutdown and accepted.
        for (int i = 0; i < 20; i++) {
            client.getBooleanValue("e2e-flag", false, ctx);
        }
        provider.shutdown();
        until(
                "evaluations total >= 21",
                () -> {
                    Object total = must(200, "GET", flagPath + "/evaluations?range=24h", null)
                            .get("total");
                    return total instanceof Long && (Long) total >= 21;
                },
                Duration.ofSeconds(10),
                Duration.ofMillis(500));

        // Check 4: with the API unreachable, a new provider serves the cache.
        MegooFlagsProvider offline = new MegooFlagsProvider(MegooFlagsConfig.builder()
                .sdkKey(sdkKey)
                .baseUrl("http://127.0.0.1:" + closedPort())
                .cacheDir(cacheDir)
                .initTimeout(Duration.ofSeconds(1))
                .build());
        api.setProviderAndWait(project + "-offline", offline);
        assertFalse(api.getClient(project + "-offline").getBooleanValue("e2e-flag", true, ctx));
    }

    private static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            m.put((String) kv[i], kv[i + 1]);
        }
        return m;
    }

    /** Calls the management API; body null sends none. */
    private Response call(String method, String path, Object body) throws IOException, InterruptedException {
        HttpRequest.Builder req = HttpRequest.newBuilder(URI.create(BASE + path))
                .timeout(REQUEST_TIMEOUT)
                .header("Authorization", "Bearer " + TOKEN);
        if (body != null) {
            req.header("Content-Type", "application/json")
                    .method(method, HttpRequest.BodyPublishers.ofString(Json.write(body)));
        } else {
            req.method(method, HttpRequest.BodyPublishers.noBody());
        }
        HttpResponse<String> res = http.send(req.build(), HttpResponse.BodyHandlers.ofString());
        Map<String, Object> out = new LinkedHashMap<>();
        if (!res.body().isEmpty()) {
            Object parsed = Json.parse(res.body());
            if (parsed instanceof Map) {
                @SuppressWarnings("unchecked")
                Map<String, Object> m = (Map<String, Object>) parsed;
                out = m;
            }
        }
        return new Response(res.statusCode(), out);
    }

    /** Calls the management API and fails unless the status is want. */
    private Map<String, Object> must(int want, String method, String path, Object body) {
        Response res;
        try {
            res = call(method, path, body);
        } catch (IOException e) {
            throw new AssertionError(method + " " + path + ": " + e, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new AssertionError(method + " " + path + ": interrupted", e);
        }
        assertEquals(want, res.status, method + " " + path + ": " + Json.write(res.body));
        return res.body;
    }

    /** Polls check every interval until it holds or timeout passes. */
    private static void until(String what, BooleanSupplier check, Duration timeout, Duration interval)
            throws InterruptedException {
        long deadline = System.nanoTime() + timeout.toNanos();
        while (!check.getAsBoolean()) {
            if (System.nanoTime() > deadline) {
                fail(what + ": not within " + timeout.toMillis() + " ms");
            }
            Thread.sleep(interval.toMillis());
        }
    }

    /** A local port nothing listens on. */
    private static int closedPort() throws IOException {
        try (ServerSocket s = new ServerSocket(0, 1, InetAddress.getLoopbackAddress())) {
            return s.getLocalPort();
        }
    }
}
