using System;
using System.Globalization;
using System.Linq;
using System.Net.Http;
using System.Net.Http.Headers;

namespace MegooFlags.Internal.Transport;

/// <summary>
/// HTTP helpers shared by the ruleset download, the stream and the event sender: request headers, status mapping
/// and Retry-After parsing.
/// </summary>
internal static class Transport
{
    /// <summary>The User-Agent of every SDK request.</summary>
    public const string UserAgent = "megooflags-dotnet/" + BuildInfo.Version;

    /// <summary>The timeout of one ruleset poll.</summary>
    public static readonly TimeSpan PollTimeout = TimeSpan.FromSeconds(10);

    /// <summary>The timeout of one events POST.</summary>
    public static readonly TimeSpan EventsTimeout = TimeSpan.FromSeconds(10);

    // The three HTTP-date forms of RFC 9110 section 5.6.7: IMF-fixdate, RFC 850 and asctime (its day of the month
    // may be padded with a space, removed before parsing).
    private static readonly string[] _imfAndRfc850 = ["r", "dddd, dd'-'MMM'-'yy HH':'mm':'ss 'GMT'"];
    private const string AscTime = "ddd MMM d HH':'mm':'ss yyyy";

    private static readonly CultureInfo _httpCulture = HttpCulture();

    /// <summary>Joins the base URL, without its trailing slashes, and an endpoint (ruleset, stream, events).</summary>
    public static Uri Url(string baseUrl, string endpoint) => new(baseUrl.TrimEnd('/') + "/sdk/v1/" + endpoint);

    /// <summary>
    /// A request carrying the SDK key in the Authorization header (never in the URL), the user agent and
    /// <c>Accept: application/json</c>; <c>Content-Type: application/json</c> only with a body.
    /// </summary>
    public static HttpRequestMessage CreateRequest(HttpMethod method, Uri url, string key, byte[]? body)
    {
        var req = new HttpRequestMessage(method, url);
        req.Headers.Authorization = new AuthenticationHeaderValue("Bearer", key);
        req.Headers.UserAgent.ParseAdd(UserAgent);
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (body != null)
        {
            req.Content = new ByteArrayContent(body);
            req.Content.Headers.ContentType = new MediaTypeHeaderValue("application/json");
        }
        return req;
    }

    /// <summary>The stream request: a GET asking for text/event-stream without caching.</summary>
    public static HttpRequestMessage CreateStreamRequest(Uri url, string key)
    {
        var req = CreateRequest(HttpMethod.Get, url, key, null);
        req.Headers.Accept.Clear();
        req.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("text/event-stream"));
        req.Headers.CacheControl = new CacheControlHeaderValue { NoCache = true };
        return req;
    }

    /// <summary>
    /// Maps a response status to an error: null for 2xx and 304, <see cref="FatalException"/> for 401 and 403,
    /// <see cref="RetryAfterException"/> for 429 and <see cref="HttpStatusException"/> otherwise.
    /// </summary>
    public static TransportException? StatusError(int status, HttpResponseHeaders headers, DateTimeOffset now)
    {
        if (status is >= 200 and < 300 or 304)
        {
            return null;
        }
        if (status is 401 or 403)
        {
            return new FatalException(status);
        }
        if (status == 429)
        {
            var value = headers.TryGetValues("Retry-After", out var values) ? values.FirstOrDefault() : null;
            return new RetryAfterException(ParseRetryAfter(value, now) ?? TimeSpan.Zero);
        }
        return new HttpStatusException(status);
    }

    /// <summary>
    /// Reads a Retry-After value given as delta-seconds or an HTTP-date. A negative number or a date in the past is
    /// zero; a missing or invalid value is null.
    /// </summary>
    public static TimeSpan? ParseRetryAfter(string? value, DateTimeOffset now)
    {
        if (value == null)
        {
            return null;
        }
        var v = value.Trim();
        if (IsInteger(v))
        {
            if (!long.TryParse(v, NumberStyles.AllowLeadingSign, CultureInfo.InvariantCulture, out var seconds)
                || seconds > TimeSpan.MaxValue.TotalSeconds)
            {
                return null;
            }
            return TimeSpan.FromSeconds(Math.Max(seconds, 0));
        }
        const DateTimeStyles styles = DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal;
        if (DateTimeOffset.TryParseExact(v, _imfAndRfc850, _httpCulture, styles, out var at)
            || DateTimeOffset.TryParseExact(v.Replace("  ", " "), AscTime, _httpCulture, styles, out at))
        {
            var d = at - now;
            return d < TimeSpan.Zero ? TimeSpan.Zero : d;
        }
        return null;
    }

    private static bool IsInteger(string s)
    {
        var start = s.Length > 0 && (s[0] == '+' || s[0] == '-') ? 1 : 0;
        if (start == s.Length)
        {
            return false;
        }
        for (var i = start; i < s.Length; i++)
        {
            if (s[i] is < '0' or > '9')
            {
                return false;
            }
        }
        return true;
    }

    // Two-digit years as Go's time.Parse reads them: 69 to 99 are 19xx, 00 to 68 are 20xx.
    private static CultureInfo HttpCulture()
    {
        var c = (CultureInfo)CultureInfo.InvariantCulture.Clone();
        c.DateTimeFormat.Calendar = new GregorianCalendar { TwoDigitYearMax = 2068 };
        return CultureInfo.ReadOnly(c);
    }
}
