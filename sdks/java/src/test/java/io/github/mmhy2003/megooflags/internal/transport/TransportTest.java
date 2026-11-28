package io.github.mmhy2003.megooflags.internal.transport;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertNull;

import io.github.mmhy2003.megooflags.FakeServer;
import io.github.mmhy2003.megooflags.FakeServer.Received;
import io.github.mmhy2003.megooflags.FakeServer.Reply;
import io.github.mmhy2003.megooflags.internal.BuildInfo;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;

class TransportTest {
    private static final String KEY = "mf_server_secret-key-123";
    private static final Instant NOW = Instant.parse("2026-11-14T12:00:00Z");

    private FakeServer server;

    @BeforeEach
    void startServer() throws Exception {
        server = FakeServer.start();
    }

    @AfterEach
    void stopServer() {
        server.stop();
    }

    private Received send(HttpRequest request) throws Exception {
        server.reply(request.uri().getPath(), Reply.status(204));
        HttpClient client = HttpClient.newHttpClient();
        HttpResponse<Void> res = client.send(request, HttpResponse.BodyHandlers.discarding());
        assertEquals(204, res.statusCode());
        List<Received> got = server.requests();
        assertEquals(1, got.size());
        return got.get(0);
    }

    @Test
    void userAgent() {
        assertEquals("megooflags-java/" + BuildInfo.VERSION, Transport.USER_AGENT);
    }

    @Test
    void sendsTheKeyAsABearerTokenWithTheUserAgent() throws Exception {
        URI url = Transport.url(server.url(), "ruleset");
        Received r = send(
                Transport.request("GET", url, KEY, Transport.USER_AGENT, null).build());
        assertEquals("GET", r.method);
        assertEquals("/sdk/v1/ruleset", r.path);
        assertEquals("Bearer " + KEY, r.header("Authorization"));
        assertEquals("megooflags-java/" + BuildInfo.VERSION, r.header("User-Agent"));
        assertEquals("application/json", r.header("Accept"));
        assertNull(r.header("Content-Type"));
        assertFalse(r.uri.contains(KEY));
    }

    @Test
    void addsContentTypeOnlyWithABody() throws Exception {
        URI url = Transport.url(server.url(), "events");
        byte[] body = "{\"a\":1}".getBytes(StandardCharsets.UTF_8);
        Received r = send(
                Transport.request("POST", url, KEY, Transport.USER_AGENT, body).build());
        assertEquals("POST", r.method);
        assertEquals("application/json", r.header("Content-Type"));
        assertEquals("{\"a\":1}", r.body);
        assertEquals("Bearer " + KEY, r.header("Authorization"));
    }

    @Test
    void streamRequestAsksForAnEventStreamWithoutCaching() throws Exception {
        URI url = Transport.url(server.url(), "stream");
        HttpRequest req =
                Transport.streamRequest(url, KEY, Transport.USER_AGENT).build();
        assertEquals(List.of("text/event-stream"), req.headers().allValues("Accept"));
        Received r = send(req);
        assertEquals("text/event-stream", r.header("Accept"));
        assertEquals("no-cache", r.header("Cache-Control"));
        assertEquals("Bearer " + KEY, r.header("Authorization"));
        assertNull(r.header("Content-Type"));
    }

    @ParameterizedTest
    @CsvSource({
        "https://flags.example.com, ruleset, https://flags.example.com/sdk/v1/ruleset",
        "https://flags.example.com/, stream, https://flags.example.com/sdk/v1/stream",
        "https://flags.example.com/base//, events, https://flags.example.com/base/sdk/v1/events"
    })
    void url(String base, String endpoint, String want) {
        URI url = Transport.url(base, endpoint);
        assertEquals(want, url.toString());
        assertFalse(url.toString().contains(KEY));
    }

    private static HttpHeaders headers(Map<String, List<String>> map) {
        return HttpHeaders.of(map, (k, v) -> true);
    }

    @ParameterizedTest
    @ValueSource(ints = {200, 204, 304})
    void successIsNotAnError(int status) {
        assertNull(Transport.statusError(status, headers(Map.of()), NOW));
    }

    @ParameterizedTest
    @ValueSource(ints = {401, 403})
    void refusedKeyIsFatal(int status) {
        FatalException e =
                assertInstanceOf(FatalException.class, Transport.statusError(status, headers(Map.of()), NOW));
        assertEquals(status, e.status());
        assertEquals("megooflags: SDK key refused (" + status + ")", e.getMessage());
    }

    @Test
    void revokedIsFatal() {
        FatalException e = FatalException.revoked();
        assertEquals(0, e.status());
        assertEquals("revoked", e.reason());
        assertEquals("megooflags: SDK key refused (revoked)", e.getMessage());
    }

    @Test
    void tooManyRequestsCarriesRetryAfter() {
        RetryAfterException e = assertInstanceOf(
                RetryAfterException.class,
                Transport.statusError(429, headers(Map.of("Retry-After", List.of("7"))), NOW));
        assertEquals(Duration.ofSeconds(7), e.after());
        assertEquals("megooflags: rate limited, retry after 7s", e.getMessage());
    }

    @Test
    void tooManyRequestsWithoutRetryAfterWaitsZero() {
        RetryAfterException e =
                assertInstanceOf(RetryAfterException.class, Transport.statusError(429, headers(Map.of()), NOW));
        assertEquals(Duration.ZERO, e.after());
    }

    @ParameterizedTest
    @ValueSource(ints = {404, 413, 422, 500, 503})
    void otherStatusesAreHttpErrors(int status) {
        HttpStatusException e =
                assertInstanceOf(HttpStatusException.class, Transport.statusError(status, headers(Map.of()), NOW));
        assertEquals(status, e.status());
        assertEquals("megooflags: HTTP " + status, e.getMessage());
    }

    private static String httpDate(Instant at) {
        return DateTimeFormatter.RFC_1123_DATE_TIME.format(ZonedDateTime.ofInstant(at, ZoneOffset.UTC));
    }

    @Test
    void retryAfterValues() {
        assertEquals(Duration.ofSeconds(5), Transport.parseRetryAfter("5", NOW));
        assertEquals(Duration.ZERO, Transport.parseRetryAfter("0", NOW));
        assertEquals(Duration.ZERO, Transport.parseRetryAfter("-1", NOW));
        assertEquals(Duration.ofSeconds(7), Transport.parseRetryAfter(" 7 ", NOW));
        assertEquals(Duration.ofSeconds(30), Transport.parseRetryAfter(httpDate(NOW.plusSeconds(30)), NOW));
        assertEquals(Duration.ofSeconds(30), Transport.parseRetryAfter("Sat, 14 Nov 2026 12:00:30 GMT", NOW));
        assertEquals(Duration.ZERO, Transport.parseRetryAfter(httpDate(NOW.minusSeconds(3600)), NOW));
        assertEquals(Duration.ofSeconds(30), Transport.parseRetryAfter("Saturday, 14-Nov-26 12:00:30 GMT", NOW));
        assertEquals(Duration.ofSeconds(30), Transport.parseRetryAfter("Sat Nov 14 12:00:30 2026", NOW));
        assertEquals(
                Duration.ofSeconds(30),
                Transport.parseRetryAfter("Sat Nov  7 12:00:30 2026", NOW.minus(Duration.ofDays(7))));
    }

    @ParameterizedTest
    @ValueSource(
            strings = {
                "abc",
                "",
                "1.5",
                "5s",
                "99999999999999999999",
                "Sat, 14 Nov 2026 12:00:30",
                "Sat, 31 Feb 2026 12:00:30 GMT",
                "Sat, 14 Nov 2026 25:00:30 GMT"
            })
    void invalidRetryAfterIsNone(String value) {
        assertNull(Transport.parseRetryAfter(value, NOW));
    }

    @Test
    void missingRetryAfterIsNone() {
        assertNull(Transport.parseRetryAfter(null, NOW));
    }
}
