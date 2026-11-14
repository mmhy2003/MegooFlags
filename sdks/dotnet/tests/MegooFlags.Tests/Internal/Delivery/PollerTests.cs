using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal.Delivery;
using MegooFlags.Internal.Transport;
using Xunit;
using HttpTransport = MegooFlags.Internal.Transport.Transport;

namespace MegooFlags.Tests.Internal.Delivery;

public sealed class PollerTests : IDisposable
{
    private const string Key = "mf_server_poll-secret-key";
    private const string RulesetPath = "/sdk/v1/ruleset";

    private readonly FakeServer _server = FakeServer.Start();
    private readonly HttpClient _client = new() { Timeout = Timeout.InfiniteTimeSpan };
    private readonly List<(string Raw, string ETag)> _received = new();
    private string _etag = "";

    public void Dispose()
    {
        _server.Stop();
        _client.Dispose();
    }

    private Poller New(bool valid = true, TimeSpan? timeout = null) =>
        new(
            _client,
            HttpTransport.Url(_server.Url, "ruleset"),
            Key,
            () => _etag,
            (raw, etag) =>
            {
                _received.Add((raw, etag));
                return valid;
            },
            timeout ?? TimeSpan.FromSeconds(10));

    private static Dictionary<string, string> ETag(string etag) => new() { ["ETag"] = etag };

    [Fact]
    public async Task PollsWithoutIfNoneMatchFirstThenWithTheETag()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Of(200, ETag("\"v1\""), "{}"), FakeServer.Reply.Status(304));
        var p = New();
        await p.PollAsync(CancellationToken.None);
        _etag = "\"v1\"";
        await p.PollAsync(CancellationToken.None);
        var reqs = _server.ReceivedFor(RulesetPath);
        Assert.Null(reqs[0].Header("If-None-Match"));
        Assert.Equal("\"v1\"", reqs[1].Header("If-None-Match"));
    }

    [Fact]
    public async Task SendsTheKeyOnlyInAuthorization()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Status(304));
        await New().PollAsync(CancellationToken.None);
        var req = Assert.Single(_server.ReceivedFor(RulesetPath));
        Assert.Equal("GET", req.Method);
        Assert.Equal("Bearer " + Key, req.Header("Authorization"));
        Assert.Equal("application/json", req.Header("Accept"));
        Assert.Equal(HttpTransport.UserAgent, req.Header("User-Agent"));
        Assert.Equal(RulesetPath, req.Uri);
    }

    [Fact]
    public async Task HandsA200BodyAndItsETagToOnRuleset()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Of(200, ETag("\"v2\""), "{\"a\":\"é\"}"));
        await New().PollAsync(CancellationToken.None);
        Assert.Equal([("{\"a\":\"é\"}", "\"v2\"")], _received);
    }

    [Fact]
    public async Task PassesAnEmptyETagWhenTheResponseHasNone()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Of(200, "{}"));
        await New().PollAsync(CancellationToken.None);
        Assert.Equal([("{}", "")], _received);
    }

    [Fact]
    public async Task Treats304AsSuccessWithoutCallingOnRuleset()
    {
        _etag = "\"v1\"";
        _server.Script(RulesetPath, FakeServer.Reply.Status(304));
        await New().PollAsync(CancellationToken.None);
        Assert.Empty(_received);
    }

    [Fact]
    public async Task FailsOnAnInvalidRuleset()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Of(200, "{}"));
        var e = await Assert.ThrowsAsync<IOException>(() => New(valid: false).PollAsync(CancellationToken.None));
        Assert.Contains("invalid ruleset", e.Message);
    }

    [Theory]
    [InlineData(401)]
    [InlineData(403)]
    public async Task FailsWithAFatalErrorOnARefusedKey(int status)
    {
        _server.Script(RulesetPath, FakeServer.Reply.Status(status));
        var e = await Assert.ThrowsAsync<FatalException>(() => New().PollAsync(CancellationToken.None));
        Assert.Equal(status, e.Status);
        Assert.DoesNotContain(Key, e.Message);
    }

    [Fact]
    public async Task ReportsTheRetryAfterOfA429()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Of(429, new Dictionary<string, string> { ["Retry-After"] = "7" }, ""));
        var e = await Assert.ThrowsAsync<RetryAfterException>(() => New().PollAsync(CancellationToken.None));
        Assert.Equal(TimeSpan.FromSeconds(7), e.After);
    }

    [Fact]
    public async Task FailsWithAnHttpErrorOn500()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Status(500));
        var e = await Assert.ThrowsAsync<HttpStatusException>(() => New().PollAsync(CancellationToken.None));
        Assert.Equal(500, e.Status);
    }

    [Fact]
    public async Task TimesOutWhenTheHeadersNeverCome()
    {
        using var release = new ManualResetEventSlim();
        _server.Script(RulesetPath, FakeServer.Reply.Respond(_ =>
        {
            release.Wait(Await.Timeout);
            return FakeServer.Reply.Status(304);
        }));
        var sw = Stopwatch.StartNew();
        await Assert.ThrowsAsync<TimeoutException>(
            () => New(timeout: TimeSpan.FromMilliseconds(200)).PollAsync(CancellationToken.None));
        Assert.True(sw.Elapsed < TimeSpan.FromSeconds(2), "timed out after " + sw.Elapsed);
        release.Set();
    }

    [Fact]
    public async Task TimesOutWhenTheBodyNeverEnds()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Stream());
        await Assert.ThrowsAsync<TimeoutException>(
            () => New(timeout: TimeSpan.FromMilliseconds(200)).PollAsync(CancellationToken.None));
        Assert.Empty(_received);
    }

    [Fact]
    public async Task ACancelledPollThrowsOperationCanceled()
    {
        using var release = new ManualResetEventSlim();
        _server.Script(RulesetPath, FakeServer.Reply.Respond(_ =>
        {
            release.Wait(Await.Timeout);
            return FakeServer.Reply.Status(304);
        }));
        using var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(100));
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => New().PollAsync(cts.Token));
        release.Set();
    }
}
