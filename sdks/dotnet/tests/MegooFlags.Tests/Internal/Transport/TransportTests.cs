using System;
using System.Globalization;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using MegooFlags.Internal;
using MegooFlags.Internal.Transport;
using Xunit;
using MfTransport = MegooFlags.Internal.Transport.Transport;

namespace MegooFlags.Tests.Internal.Transport;

public sealed class TransportTests : IDisposable
{
    private const string Key = "mf_server_secret-key-123";
    private static readonly DateTimeOffset _now = new(2026, 11, 14, 12, 0, 0, TimeSpan.Zero);

    private readonly FakeServer _server = FakeServer.Start();

    public void Dispose() => _server.Stop();

    private async Task<FakeServer.Received> SendAsync(HttpRequestMessage request)
    {
        _server.Script(request.RequestUri!.AbsolutePath, FakeServer.Reply.Status(204));
        using var client = new HttpClient();
        using var res = await client.SendAsync(request);
        Assert.Equal(204, (int)res.StatusCode);
        return Assert.Single(_server.Requests());
    }

    [Fact]
    public void UserAgent()
    {
        Assert.Equal("megooflags-dotnet/" + BuildInfo.Version, MfTransport.UserAgent);
    }

    [Fact]
    public void Timeouts()
    {
        Assert.Equal(TimeSpan.FromSeconds(10), MfTransport.PollTimeout);
        Assert.Equal(TimeSpan.FromSeconds(10), MfTransport.EventsTimeout);
    }

    [Fact]
    public async Task SendsTheKeyAsABearerTokenWithTheUserAgent()
    {
        var url = MfTransport.Url(_server.Url, "ruleset");
        var r = await SendAsync(MfTransport.CreateRequest(HttpMethod.Get, url, Key, null));
        Assert.Equal("GET", r.Method);
        Assert.Equal("/sdk/v1/ruleset", r.Path);
        Assert.Equal("Bearer " + Key, r.Header("Authorization"));
        Assert.Equal("megooflags-dotnet/" + BuildInfo.Version, r.Header("User-Agent"));
        Assert.Equal("application/json", r.Header("Accept"));
        Assert.Null(r.Header("Content-Type"));
        Assert.DoesNotContain(Key, r.Uri);
    }

    [Fact]
    public async Task AddsContentTypeOnlyWithABody()
    {
        var url = MfTransport.Url(_server.Url, "events");
        var body = Encoding.UTF8.GetBytes("{\"a\":1}");
        var r = await SendAsync(MfTransport.CreateRequest(HttpMethod.Post, url, Key, body));
        Assert.Equal("POST", r.Method);
        Assert.Equal("application/json", r.Header("Content-Type"));
        Assert.Equal("{\"a\":1}", r.Body);
        Assert.Equal("Bearer " + Key, r.Header("Authorization"));
    }

    [Fact]
    public async Task StreamRequestAsksForAnEventStreamWithoutCaching()
    {
        var url = MfTransport.Url(_server.Url, "stream");
        var req = MfTransport.CreateStreamRequest(url, Key);
        Assert.Equal("text/event-stream", Assert.Single(req.Headers.Accept).MediaType);
        var r = await SendAsync(req);
        Assert.Equal("text/event-stream", r.Header("Accept"));
        Assert.Equal("no-cache", r.Header("Cache-Control"));
        Assert.Equal("Bearer " + Key, r.Header("Authorization"));
        Assert.Null(r.Header("Content-Type"));
    }

    [Theory]
    [InlineData("https://flags.example.com", "ruleset", "https://flags.example.com/sdk/v1/ruleset")]
    [InlineData("https://flags.example.com/", "stream", "https://flags.example.com/sdk/v1/stream")]
    [InlineData("https://flags.example.com/base//", "events", "https://flags.example.com/base/sdk/v1/events")]
    public void Url(string baseUrl, string endpoint, string want)
    {
        var url = MfTransport.Url(baseUrl, endpoint);
        Assert.Equal(want, url.ToString());
    }

    private static HttpResponseMessage Response(string? retryAfter = null)
    {
        var res = new HttpResponseMessage();
        if (retryAfter != null)
        {
            res.Headers.TryAddWithoutValidation("Retry-After", retryAfter);
        }
        return res;
    }

    [Theory]
    [InlineData(200)]
    [InlineData(204)]
    [InlineData(304)]
    public void SuccessIsNotAnError(int status)
    {
        using var res = Response();
        Assert.Null(MfTransport.StatusError(status, res.Headers, _now));
    }

    [Theory]
    [InlineData(401)]
    [InlineData(403)]
    public void RefusedKeyIsFatal(int status)
    {
        using var res = Response();
        var e = Assert.IsType<FatalException>(MfTransport.StatusError(status, res.Headers, _now));
        Assert.Equal(status, e.Status);
        Assert.Equal("", e.Reason);
        Assert.Equal("megooflags: SDK key refused (" + status + ")", e.Message);
    }

    [Fact]
    public void RevokedIsFatal()
    {
        var e = FatalException.Revoked();
        Assert.Equal(0, e.Status);
        Assert.Equal("revoked", e.Reason);
        Assert.Equal("megooflags: SDK key refused (revoked)", e.Message);
    }

    [Fact]
    public void TooManyRequestsCarriesRetryAfter()
    {
        using var res = Response("7");
        var e = Assert.IsType<RetryAfterException>(MfTransport.StatusError(429, res.Headers, _now));
        Assert.Equal(TimeSpan.FromSeconds(7), e.After);
        Assert.Equal("megooflags: rate limited, retry after 7s", e.Message);
    }

    [Fact]
    public void TooManyRequestsReadsAnHttpDate()
    {
        using var res = Response("Sat, 14 Nov 2026 12:00:30 GMT");
        var e = Assert.IsType<RetryAfterException>(MfTransport.StatusError(429, res.Headers, _now));
        Assert.Equal(TimeSpan.FromSeconds(30), e.After);
    }

    [Fact]
    public void TooManyRequestsWithoutRetryAfterWaitsZero()
    {
        using var res = Response();
        var e = Assert.IsType<RetryAfterException>(MfTransport.StatusError(429, res.Headers, _now));
        Assert.Equal(TimeSpan.Zero, e.After);
        Assert.Equal("megooflags: rate limited, retry after 0s", e.Message);
    }

    [Fact]
    public void RetryAfterMessageKeepsAFraction()
    {
        Assert.Equal(
            "megooflags: rate limited, retry after 1.5s",
            new RetryAfterException(TimeSpan.FromMilliseconds(1500)).Message);
    }

    [Theory]
    [InlineData(404)]
    [InlineData(413)]
    [InlineData(422)]
    [InlineData(500)]
    [InlineData(503)]
    public void OtherStatusesAreHttpErrors(int status)
    {
        using var res = Response();
        var e = Assert.IsType<HttpStatusException>(MfTransport.StatusError(status, res.Headers, _now));
        Assert.Equal(status, e.Status);
        Assert.Equal("megooflags: HTTP " + status, e.Message);
    }

    private static string HttpDate(DateTimeOffset at) => at.ToString("r", CultureInfo.InvariantCulture);

    [Fact]
    public void RetryAfterValues()
    {
        Assert.Equal(TimeSpan.FromSeconds(5), MfTransport.ParseRetryAfter("5", _now));
        Assert.Equal(TimeSpan.Zero, MfTransport.ParseRetryAfter("0", _now));
        Assert.Equal(TimeSpan.Zero, MfTransport.ParseRetryAfter("-1", _now));
        Assert.Equal(TimeSpan.FromSeconds(7), MfTransport.ParseRetryAfter(" 7 ", _now));
        Assert.Equal(TimeSpan.FromSeconds(30), MfTransport.ParseRetryAfter(HttpDate(_now.AddSeconds(30)), _now));
        Assert.Equal(TimeSpan.FromSeconds(30), MfTransport.ParseRetryAfter("Sat, 14 Nov 2026 12:00:30 GMT", _now));
        Assert.Equal(TimeSpan.Zero, MfTransport.ParseRetryAfter(HttpDate(_now.AddHours(-1)), _now));
        Assert.Equal(TimeSpan.FromSeconds(30), MfTransport.ParseRetryAfter("Saturday, 14-Nov-26 12:00:30 GMT", _now));
        Assert.Equal(TimeSpan.FromSeconds(30), MfTransport.ParseRetryAfter("Sat Nov 14 12:00:30 2026", _now));
        Assert.Equal(
            TimeSpan.FromSeconds(30), MfTransport.ParseRetryAfter("Sat Nov  7 12:00:30 2026", _now.AddDays(-7)));
        Assert.Equal(
            TimeSpan.FromSeconds(30), MfTransport.ParseRetryAfter("Sat Nov 7 12:00:30 2026", _now.AddDays(-7)));
    }

    [Theory]
    [InlineData("abc")]
    [InlineData("")]
    [InlineData("1.5")]
    [InlineData("5s")]
    [InlineData("99999999999999999999")]
    [InlineData("Sat, 14 Nov 2026 12:00:30")]
    [InlineData("Sat, 31 Feb 2026 12:00:30 GMT")]
    [InlineData("Sat, 14 Nov 2026 25:00:30 GMT")]
    [InlineData("Sun, 14 Nov 2026 12:00:30 GMT")]
    public void InvalidRetryAfterIsNone(string value)
    {
        Assert.Null(MfTransport.ParseRetryAfter(value, _now));
    }

    [Fact]
    public void MissingRetryAfterIsNone()
    {
        Assert.Null(MfTransport.ParseRetryAfter(null, _now));
    }
}
