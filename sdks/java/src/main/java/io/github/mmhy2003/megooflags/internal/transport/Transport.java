package io.github.mmhy2003.megooflags.internal.transport;

import io.github.mmhy2003.megooflags.internal.BuildInfo;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.time.DateTimeException;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeFormatterBuilder;
import java.time.format.ResolverStyle;
import java.time.format.SignStyle;
import java.time.format.TextStyle;
import java.time.temporal.ChronoField;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * HTTP helpers shared by the ruleset download, the stream and the event sender: request headers,
 * status mapping and Retry-After parsing.
 */
public final class Transport {
    /** The User-Agent of every SDK request. */
    public static final String USER_AGENT = "megooflags-java/" + BuildInfo.VERSION;

    private static final Pattern TRAILING_SLASHES = Pattern.compile("/+$");
    private static final Pattern INTEGER = Pattern.compile("[+-]?[0-9]+");

    // The three HTTP-date forms of RFC 9110 §5.6.7: IMF-fixdate, RFC 850 and asctime.
    private static final List<DateTimeFormatter> HTTP_DATES = List.of(
            DateTimeFormatter.RFC_1123_DATE_TIME.withResolverStyle(ResolverStyle.STRICT),
            new DateTimeFormatterBuilder()
                    .appendText(ChronoField.DAY_OF_WEEK, TextStyle.FULL)
                    .appendLiteral(", ")
                    .appendValue(ChronoField.DAY_OF_MONTH, 2)
                    .appendLiteral('-')
                    .appendText(ChronoField.MONTH_OF_YEAR, TextStyle.SHORT)
                    .appendLiteral('-')
                    // Two-digit years as Go's time.Parse reads them: 69 to 99 are 19xx.
                    .appendValueReduced(ChronoField.YEAR, 2, 2, 1969)
                    .appendLiteral(' ')
                    .append(DateTimeFormatter.ofPattern("HH:mm:ss"))
                    .appendLiteral(" GMT")
                    .toFormatter(Locale.US)
                    .withResolverStyle(ResolverStyle.STRICT)
                    .withZone(ZoneOffset.UTC),
            new DateTimeFormatterBuilder()
                    .appendText(ChronoField.DAY_OF_WEEK, TextStyle.SHORT)
                    .appendLiteral(' ')
                    .appendText(ChronoField.MONTH_OF_YEAR, TextStyle.SHORT)
                    .appendLiteral(' ')
                    .padNext(2)
                    .appendValue(ChronoField.DAY_OF_MONTH)
                    .appendLiteral(' ')
                    .append(DateTimeFormatter.ofPattern("HH:mm:ss"))
                    .appendLiteral(' ')
                    .appendValue(ChronoField.YEAR, 4, 4, SignStyle.NOT_NEGATIVE)
                    .toFormatter(Locale.US)
                    .withResolverStyle(ResolverStyle.STRICT)
                    .withZone(ZoneOffset.UTC));

    private Transport() {}

    /**
     * The client used when the config gives none. HTTP/1.1 only: the JDK's default asks for an h2c
     * upgrade on plain http, which servers such as uvicorn refuse (400) on a request with a body.
     */
    public static HttpClient defaultClient() {
        return HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1).build();
    }

    /** Joins the base URL, without its trailing slashes, and an endpoint (ruleset, stream, events). */
    public static URI url(String baseUrl, String endpoint) {
        return URI.create(TRAILING_SLASHES.matcher(baseUrl).replaceAll("") + "/sdk/v1/" + endpoint);
    }

    /**
     * A request carrying the SDK key in the Authorization header (never in the URL), the user agent
     * and Accept: application/json; Content-Type: application/json only with a body. Callers add
     * the timeout and any further headers.
     */
    public static HttpRequest.Builder request(String method, URI url, String key, String userAgent, byte[] body) {
        HttpRequest.Builder b = HttpRequest.newBuilder(url)
                .method(
                        method,
                        body == null
                                ? HttpRequest.BodyPublishers.noBody()
                                : HttpRequest.BodyPublishers.ofByteArray(body))
                .setHeader("Authorization", "Bearer " + key)
                .setHeader("User-Agent", userAgent)
                .setHeader("Accept", "application/json");
        if (body != null) {
            b.setHeader("Content-Type", "application/json");
        }
        return b;
    }

    /** The stream request: a GET asking for text/event-stream without caching. */
    public static HttpRequest.Builder streamRequest(URI url, String key, String userAgent) {
        return request("GET", url, key, userAgent, null)
                .setHeader("Accept", "text/event-stream")
                .setHeader("Cache-Control", "no-cache");
    }

    /**
     * Maps a response status to an error: null for 2xx and 304, {@link FatalException} for 401 and
     * 403, {@link RetryAfterException} for 429 and {@link HttpStatusException} otherwise.
     */
    public static IOException statusError(int status, HttpHeaders headers, Instant now) {
        if ((status >= 200 && status < 300) || status == 304) {
            return null;
        }
        if (status == 401 || status == 403) {
            return new FatalException(status);
        }
        if (status == 429) {
            Duration after = parseRetryAfter(headers.firstValue("Retry-After").orElse(null), now);
            return new RetryAfterException(after == null ? Duration.ZERO : after);
        }
        return new HttpStatusException(status);
    }

    /**
     * Reads a Retry-After value given as delta-seconds or an HTTP-date. A negative number or a date
     * in the past is zero; a missing or invalid value is null.
     */
    public static Duration parseRetryAfter(String value, Instant now) {
        if (value == null) {
            return null;
        }
        String v = value.strip();
        if (INTEGER.matcher(v).matches()) {
            try {
                return Duration.ofSeconds(Math.max(Long.parseLong(v), 0));
            } catch (NumberFormatException e) {
                return null;
            }
        }
        for (DateTimeFormatter f : HTTP_DATES) {
            try {
                Instant at = Instant.from(f.parse(v));
                Duration d = Duration.between(now, at);
                return d.isNegative() ? Duration.ZERO : d;
            } catch (DateTimeException e) {
                // Try the next form.
            }
        }
        return null;
    }
}
