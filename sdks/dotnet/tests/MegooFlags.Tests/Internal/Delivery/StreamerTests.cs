using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal.Delivery;
using MegooFlags.Internal.Transport;
using Xunit;
using HttpTransport = MegooFlags.Internal.Transport.Transport;

namespace MegooFlags.Tests.Internal.Delivery;

public sealed class StreamerTests : IDisposable
{
    private const string Key = "mf_server_stream-secret-key";
    private const string StreamPath = "/sdk/v1/stream";

    private readonly FakeServer _server = FakeServer.Start();
    private readonly HttpClient _client = new() { Timeout = Timeout.InfiniteTimeSpan };
    private readonly CancellationTokenSource _stop = new();
    private readonly Recorder _rec = new();
    private Task<FatalException?>? _run;

    public void Dispose()
    {
        _stop.Cancel();
        _run?.Wait(Await.Timeout);
        _server.Stop();
        _client.Dispose();
        _stop.Dispose();
    }

    private sealed record Disconnect(Exception Error, int Failures);

    private sealed class Recorder : Streamer.ICallbacks
    {
        private readonly object _lock = new();
        private readonly List<(string Raw, string Id)> _puts = new();
        private readonly List<Disconnect> _disconnects = new();
        private int _connected;

        public Func<string, bool> PutResult { get; set; } = _ => true;

        public List<(string Raw, string Id)> Puts
        {
            get
            {
                lock (_lock)
                {
                    return _puts.ToList();
                }
            }
        }

        public List<Disconnect> Disconnects
        {
            get
            {
                lock (_lock)
                {
                    return _disconnects.ToList();
                }
            }
        }

        public int Connected => Volatile.Read(ref _connected);

        public bool OnPut(string raw, string id)
        {
            lock (_lock)
            {
                _puts.Add((raw, id));
            }
            return PutResult(raw);
        }

        public void OnConnected() => Interlocked.Increment(ref _connected);

        public void OnDisconnected(Exception error, int failures)
        {
            lock (_lock)
            {
                _disconnects.Add(new Disconnect(error, failures));
            }
        }
    }

    private void Start(
        Func<string>? lastEventId = null,
        TimeSpan? deadTimeout = null,
        TimeSpan? stableAfter = null,
        Backoff? backoff = null)
    {
        var s = new Streamer(
            _client,
            HttpTransport.Url(_server.Url, "stream"),
            Key,
            lastEventId ?? (() => ""),
            _rec,
            _ => { },
            deadTimeout ?? TimeSpan.FromSeconds(5),
            stableAfter ?? TimeSpan.FromSeconds(60),
            backoff ?? new Backoff(TimeSpan.FromMilliseconds(50), TimeSpan.FromMilliseconds(50), () => 1));
        _run = Task.Run(() => s.RunAsync(_stop.Token));
    }

    private static Backoff Fixed(int ms) => new(TimeSpan.FromMilliseconds(ms), TimeSpan.FromMilliseconds(ms), () => 1);

    private List<DateTimeOffset> Arrivals() => _server.ReceivedFor(StreamPath).Select(r => r.At).ToList();

    [Fact]
    public async Task HandsPutEventsToOnPutAfterOnConnected()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        conn.Write("event: put\nid: \"e1\"\ndata: {\"a\":1}\n\n");
        await Await.UntilAsync(() => _rec.Puts.Count == 1);
        Assert.Equal(("{\"a\":1}", "\"e1\""), _rec.Puts[0]);
        Assert.Equal(1, _rec.Connected);
        Assert.Empty(_rec.Disconnects);
    }

    [Fact]
    public async Task SendsTheStreamHeadersWithTheKeyOnlyInAuthorization()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        Start();
        var req = (await _server.NextStreamAsync(Await.Timeout)).Request;
        Assert.Equal("GET", req.Method);
        Assert.Equal("Bearer " + Key, req.Header("Authorization"));
        Assert.Equal("text/event-stream", req.Header("Accept"));
        Assert.Equal("no-cache", req.Header("Cache-Control"));
        Assert.Equal(HttpTransport.UserAgent, req.Header("User-Agent"));
        Assert.Null(req.Header("Last-Event-ID"));
        Assert.Equal(StreamPath, req.Uri);
        Assert.DoesNotContain(Key, req.Uri);
    }

    [Fact]
    public async Task SendsLastEventIdWhenThereIsAnETag()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        Start(lastEventId: () => "\"etag-7\"");
        var req = (await _server.NextStreamAsync(Await.Timeout)).Request;
        Assert.Equal("\"etag-7\"", req.Header("Last-Event-ID"));
    }

    [Fact]
    public async Task StaysConnectedWhilePingsArrive()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        Start(deadTimeout: TimeSpan.FromMilliseconds(300));
        var conn = await _server.NextStreamAsync(Await.Timeout);
        for (var i = 0; i < 8; i++)
        {
            await Task.Delay(100);
            conn.Write(":ping\n");
        }
        Assert.Empty(_rec.Disconnects);
        Assert.Single(_server.ReceivedFor(StreamPath));
    }

    [Fact]
    public async Task ReconnectsAConnectionThatSendsNothing()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        Start(deadTimeout: TimeSpan.FromMilliseconds(200));
        await _server.NextStreamAsync(Await.Timeout);
        await _server.NextStreamAsync(Await.Timeout);
        var d = _rec.Disconnects[0];
        Assert.IsType<DeadConnectionException>(d.Error);
        Assert.Equal(1, d.Failures);
        Assert.Equal(2, _rec.Connected);
    }

    [Fact]
    public async Task IgnoresUnknownEventsAndKeepsReadingAfterAnInvalidPut()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        _rec.PutResult = raw => raw != "bad";
        Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        conn.Write("event: other\ndata: x\n\ndata: unnamed\n\nevent: put\ndata: bad\n\nevent: put\ndata: good\n\n");
        await Await.UntilAsync(() => _rec.Puts.Count == 2);
        Assert.Equal(["bad", "good"], _rec.Puts.Select(p => p.Raw));
        Assert.Empty(_rec.Disconnects);
        Assert.Single(_server.ReceivedFor(StreamPath));
    }

    [Fact]
    public async Task StopsWithAFatalErrorOnRevoked()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        conn.Write("event: revoked\ndata: {}\n\n");
        var err = await _run!.WaitAsync(Await.Timeout);
        Assert.NotNull(err);
        Assert.Equal("revoked", err.Reason);
        Assert.Empty(_rec.Disconnects);
        Assert.Single(_server.ReceivedFor(StreamPath));
    }

    [Theory]
    [InlineData(401)]
    [InlineData(403)]
    public async Task StopsWithAFatalErrorOnARefusedKey(int status)
    {
        _server.Script(StreamPath, FakeServer.Reply.Status(status));
        Start();
        var err = await _run!.WaitAsync(Await.Timeout);
        Assert.NotNull(err);
        Assert.Equal(status, err.Status);
        Assert.Equal(0, _rec.Connected);
        Assert.Empty(_rec.Disconnects);
        Assert.Single(_server.ReceivedFor(StreamPath));
    }

    [Fact]
    public async Task WaitsTheRetryAfterOfA429()
    {
        _server.Script(
            StreamPath,
            FakeServer.Reply.Of(429, new Dictionary<string, string> { ["Retry-After"] = "1" }, ""),
            FakeServer.Reply.Stream());
        Start();
        await _server.NextStreamAsync(Await.Timeout);
        var at = Arrivals();
        Assert.True(at[1] - at[0] >= TimeSpan.FromMilliseconds(900), "reconnected after " + (at[1] - at[0]));
        var d = Assert.Single(_rec.Disconnects);
        Assert.Equal(TimeSpan.FromSeconds(1), Assert.IsType<RetryAfterException>(d.Error).After);
    }

    [Fact]
    public async Task BacksOffWithGrowingWaitsOn5xx()
    {
        _server.Script(
            StreamPath,
            FakeServer.Reply.Status(500),
            FakeServer.Reply.Status(502),
            FakeServer.Reply.Status(503),
            FakeServer.Reply.Stream());
        Start(backoff: new Backoff(TimeSpan.FromMilliseconds(100), TimeSpan.FromSeconds(10), () => 1));
        await _server.NextStreamAsync(Await.Timeout);
        Assert.Equal([1, 2, 3], _rec.Disconnects.Select(d => d.Failures));
        Assert.Equal([500, 502, 503], _rec.Disconnects.Select(d => Assert.IsType<HttpStatusException>(d.Error).Status));
        var at = Arrivals();
        Assert.True(at[1] - at[0] >= TimeSpan.FromMilliseconds(90), "first wait " + (at[1] - at[0]));
        Assert.True(at[2] - at[1] >= TimeSpan.FromMilliseconds(190), "second wait " + (at[2] - at[1]));
        Assert.True(at[3] - at[2] >= TimeSpan.FromMilliseconds(390), "third wait " + (at[3] - at[2]));
    }

    [Fact]
    public async Task UsesTheServersRetryFieldAsTheBackoffBase()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        Start(backoff: new Backoff(TimeSpan.FromSeconds(20), TimeSpan.FromSeconds(60), () => 1));
        var conn = await _server.NextStreamAsync(Await.Timeout);
        conn.Write("retry: 50\n\n");
        await Task.Delay(50);
        conn.End();
        await _server.NextStreamAsync(Await.Timeout);
        var d = Assert.Single(_rec.Disconnects);
        Assert.IsAssignableFrom<IOException>(d.Error);
    }

    [Fact]
    public async Task ResetsTheBackoffAndTheFailuresAfterAStableConnection()
    {
        _server.Script(
            StreamPath,
            FakeServer.Reply.Status(500),
            FakeServer.Reply.Status(500),
            FakeServer.Reply.Status(500),
            FakeServer.Reply.Stream());
        Start(
            stableAfter: TimeSpan.FromMilliseconds(100),
            backoff: new Backoff(TimeSpan.FromMilliseconds(50), TimeSpan.FromSeconds(10), () => 1));
        var conn = await _server.NextStreamAsync(Await.Timeout);
        await Task.Delay(200);
        conn.End();
        await _server.NextStreamAsync(Await.Timeout);
        Assert.Equal([1, 2, 3, 1], _rec.Disconnects.Select(d => d.Failures));
        var at = Arrivals();
        // Without the reset the fourth wait would be 400 ms.
        Assert.True(at[4] - at[3] < TimeSpan.FromMilliseconds(300), "wait after a stable connection " + (at[4] - at[3]));
    }

    [Fact]
    public async Task ReturnsPromptlyWhenStoppedWhileConnected()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        var sw = Stopwatch.StartNew();
        _stop.Cancel();
        Assert.Null(await _run!.WaitAsync(Await.Timeout));
        Assert.True(sw.Elapsed < TimeSpan.FromSeconds(1), "stopped after " + sw.Elapsed);
        await conn.AwaitClosedAsync(Await.Timeout);
        Assert.Empty(_rec.Disconnects);
    }

    [Fact]
    public async Task ReturnsPromptlyWhenStoppedWhileWaitingToReconnect()
    {
        _server.Script(StreamPath, FakeServer.Reply.Status(500));
        Start(backoff: Fixed(30_000));
        await Await.UntilAsync(() => _rec.Disconnects.Count == 1);
        var sw = Stopwatch.StartNew();
        _stop.Cancel();
        Assert.Null(await _run!.WaitAsync(Await.Timeout));
        Assert.True(sw.Elapsed < TimeSpan.FromSeconds(1), "stopped after " + sw.Elapsed);
    }

    [Fact]
    public async Task ReturnsPromptlyWhenStoppedWhileWaitingForTheHeaders()
    {
        using var release = new ManualResetEventSlim();
        _server.Script(StreamPath, FakeServer.Reply.Respond(_ =>
        {
            release.Wait(Await.Timeout);
            return FakeServer.Reply.Status(500);
        }));
        Start();
        await Await.UntilAsync(() => _server.ReceivedFor(StreamPath).Count == 1);
        var sw = Stopwatch.StartNew();
        _stop.Cancel();
        Assert.Null(await _run!.WaitAsync(Await.Timeout));
        Assert.True(sw.Elapsed < TimeSpan.FromSeconds(1), "stopped after " + sw.Elapsed);
        release.Set();
        Assert.Empty(_rec.Disconnects);
    }
}
