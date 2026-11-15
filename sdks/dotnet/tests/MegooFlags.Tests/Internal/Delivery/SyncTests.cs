using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using MegooFlags.Internal.Delivery;
using MegooFlags.Internal.Json;
using MegooFlags.Internal.Transport;
using Xunit;
using static MegooFlags.Tests.RulesetGen;

namespace MegooFlags.Tests.Internal.Delivery;

public sealed class SyncTests : IAsyncLifetime
{
    private const string Key = "mf_server_sync-secret-key";
    private const string StreamPath = "/sdk/v1/stream";
    private const string RulesetPath = "/sdk/v1/ruleset";

    private readonly FakeServer _server = FakeServer.Start();
    private readonly string _dir = Directory.CreateTempSubdirectory("megooflags-sync-test-").FullName;
    private readonly Store _store = new();
    private readonly Cache _cache;
    private readonly object _lock = new();
    private readonly List<(Snapshot? Old, Snapshot Next)> _changes = new();
    private readonly List<(Sync.Status Status, Exception? Error)> _statuses = new();
    private readonly List<string> _warnings = new();
    private readonly List<Sync> _syncs = new();

    public SyncTests()
    {
        _cache = new Cache(_dir, Key);
    }

    public Task InitializeAsync() => Task.CompletedTask;

    public async Task DisposeAsync()
    {
        foreach (var s in _syncs)
        {
            await s.StopAsync();
        }
        _server.Stop();
        Directory.Delete(_dir, recursive: true);
    }

    private static string Raw(string environment) =>
        JsonTree.Write(Obj("schema_version", 1L, "project", "p", "environment", environment, "flags", Obj(), "segments", Obj()));

    private static Dictionary<string, string> ETag(string etag) => new() { ["ETag"] = etag };

    private static Sync.Tuning Fast() => new()
    {
        RetryBase = TimeSpan.FromMilliseconds(20),
        RetryMax = TimeSpan.FromMilliseconds(20),
        PollInterval = TimeSpan.FromMilliseconds(20),
    };

    private Sync New(bool disableStream = false, Sync.Tuning? tuning = null)
    {
        var s = new Sync(new Sync.Options(_server.Url, Key, _store)
        {
            Cache = _cache,
            DisableStream = disableStream,
            Tuning = tuning ?? Fast(),
            Warn = m =>
            {
                lock (_lock)
                {
                    _warnings.Add(m);
                }
            },
            OnChange = (old, next) =>
            {
                lock (_lock)
                {
                    _changes.Add((old, next));
                }
            },
            OnStatus = (status, err) =>
            {
                lock (_lock)
                {
                    _statuses.Add((status, err));
                }
            },
        });
        _syncs.Add(s);
        return s;
    }

    private List<Sync.Status> Statuses()
    {
        lock (_lock)
        {
            return _statuses.Select(s => s.Status).ToList();
        }
    }

    private int Changes()
    {
        lock (_lock)
        {
            return _changes.Count;
        }
    }

    private List<string> Warnings()
    {
        lock (_lock)
        {
            return _warnings.ToList();
        }
    }

    [Fact]
    public async Task OfflineBuildsNoUrlsAndStartDoesNothing()
    {
        // "http://[" makes new Uri throw: an offline Sync must not build the ruleset or stream URL.
        var s = new Sync(new Sync.Options("http://[", Key, _store) { Offline = true, Tuning = Fast() });
        _syncs.Add(s);
        Assert.Null(s.Apply(Raw("bootstrap"), "", SnapshotSource.Bootstrap));
        Assert.Equal("bootstrap", _store.Load()!.Ruleset.Environment);
        s.Start();
        await Task.Delay(100);
        await s.StopAsync();
        Assert.Empty(_server.Requests());
    }

    [Fact]
    public async Task AStreamPutUpdatesTheStoreAndTheCache()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        New().Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        conn.Write("event: put\nid: \"s1\"\ndata: " + Raw("streamed") + "\n\n");
        await Await.UntilAsync(() => Changes() == 1);
        var snap = _store.Load()!;
        Assert.Equal("streamed", snap.Ruleset.Environment);
        Assert.Equal("\"s1\"", snap.ETag);
        Assert.Equal(SnapshotSource.Stream, snap.Source);
        var cached = _cache.Load()!;
        Assert.Equal("\"s1\"", cached.ETag);
        Assert.Equal(JsonTree.Parse(Raw("streamed")), cached.Ruleset);
        Assert.Equal([Sync.Status.Ready], Statuses());
    }

    [Fact]
    public async Task SendsTheStoresETagAsLastEventId()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var s = New();
        s.Apply(Raw("boot"), "\"b1\"", SnapshotSource.Bootstrap);
        s.Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        Assert.Equal("\"b1\"", conn.Request.Header("Last-Event-ID"));
    }

    [Fact]
    public void ABootstrapOrCacheApplyDoesNotWriteTheCache()
    {
        var s = New();
        Assert.Null(s.Apply(Raw("boot"), "\"b1\"", SnapshotSource.Bootstrap));
        Assert.Null(s.Apply(Raw("cached"), "\"c1\"", SnapshotSource.Cache));
        Assert.Equal(SnapshotSource.Cache, _store.Load()!.Source);
        Assert.Null(_cache.Load());
        Assert.Equal(2, _changes.Count);
        Assert.Null(_changes[0].Old);
        Assert.Same(_changes[0].Next, _changes[1].Old);
        Assert.Same(_store.Load(), _changes[1].Next);
    }

    [Fact]
    public void AnInvalidRulesetKeepsThePreviousOne()
    {
        var s = New();
        s.Apply(Raw("one"), "\"1\"", SnapshotSource.Poll);
        Assert.NotNull(s.Apply("{\"schema_version\":99}", "\"2\"", SnapshotSource.Poll));
        Assert.Equal("\"1\"", _store.Load()!.ETag);
        Assert.Equal("\"1\"", _cache.Load()!.ETag);
        Assert.Equal(1, Changes());
        Assert.Contains(Warnings(), w => w.Contains("invalid ruleset"));
    }

    [Fact]
    public void LogsEachRulesetWarningOnce()
    {
        var bad = JsonTree.Write(RulesetJson([RuleFlag("re", Cl("email", "matches", false, "(?<n>x)"))], Obj()));
        var s = New();
        Assert.Null(s.Apply(bad, "\"1\"", SnapshotSource.Stream));
        Assert.Null(s.Apply(bad, "\"2\"", SnapshotSource.Stream));
        var regex = Assert.Single(Warnings(), w => w.Contains("regex"));
        Assert.Contains("flags.re", regex);
    }

    [Fact]
    public async Task AnInvalidPutKeepsThePreviousRulesetAndTheStream()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var s = New();
        s.Apply(Raw("boot"), "\"b1\"", SnapshotSource.Bootstrap);
        s.Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        conn.Write("event: put\nid: \"x\"\ndata: {\"schema_version\":99}\n\n");
        conn.Write("event: put\nid: \"s2\"\ndata: " + Raw("good") + "\n\n");
        await Await.UntilAsync(() => Changes() == 2);
        Assert.Equal("good", _store.Load()!.Ruleset.Environment);
        Assert.Single(_server.ReceivedFor(StreamPath));
        Assert.Contains(Warnings(), w => w.Contains("invalid ruleset"));
    }

    [Fact]
    public async Task PollsAfterTwoStreamFailuresAndStopsPollingOnReconnect()
    {
        _server.Script(StreamPath, FakeServer.Reply.Status(500), FakeServer.Reply.Status(500), FakeServer.Reply.Stream());
        _server.Script(RulesetPath, FakeServer.Reply.Of(200, ETag("\"p1\""), Raw("polled")));
        var t = Fast();
        t.RetryBase = t.RetryMax = TimeSpan.FromMilliseconds(100);
        t.PollInterval = TimeSpan.FromMilliseconds(10);
        New(tuning: t).Start();
        await _server.NextStreamAsync(Await.Timeout);
        await Await.UntilAsync(() => Statuses().Contains(Sync.Status.Ready));
        var polls = _server.ReceivedFor(RulesetPath);
        Assert.NotEmpty(polls);
        Assert.True(polls[0].At >= _server.ReceivedFor(StreamPath)[1].At, "polled before the second stream failure");
        Assert.Equal(SnapshotSource.Poll, _store.Load()!.Source);
        Assert.Equal("\"p1\"", _cache.Load()!.ETag);
        Assert.Equal([Sync.Status.Stale, Sync.Status.Stale, Sync.Status.Ready], Statuses());
        // A poll already in flight at the reconnect may still land; none starts after it.
        await Task.Delay(50);
        var count = _server.ReceivedFor(RulesetPath).Count;
        await Task.Delay(150);
        Assert.Equal(count, _server.ReceivedFor(RulesetPath).Count);
    }

    [Fact]
    public async Task DoesNotPollAfterASingleStreamFailure()
    {
        _server.Script(StreamPath, FakeServer.Reply.Status(500), FakeServer.Reply.Stream());
        _server.Script(RulesetPath, FakeServer.Reply.Of(200, Raw("polled")));
        New().Start();
        await _server.NextStreamAsync(Await.Timeout);
        await Task.Delay(100);
        Assert.Empty(_server.ReceivedFor(RulesetPath));
        Assert.Equal([Sync.Status.Stale, Sync.Status.Ready], Statuses());
    }

    [Fact]
    public async Task KeepsPollsAtLeastThePollIntervalApart()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Status(304));
        var t = Fast();
        t.PollInterval = TimeSpan.FromMilliseconds(150);
        New(disableStream: true, tuning: t).Start();
        await Await.UntilAsync(() => _server.ReceivedFor(RulesetPath).Count >= 4);
        var at = _server.ReceivedFor(RulesetPath).Select(r => r.At).ToList();
        for (var i = 1; i < at.Count; i++)
        {
            Assert.True(at[i] - at[i - 1] >= TimeSpan.FromMilliseconds(140), "polls " + (at[i] - at[i - 1]) + " apart");
        }
    }

    [Fact]
    public async Task KeepsThePollIntervalAcrossRestartsOfThePollLoop()
    {
        // A failure starts polling, the reconnect stops it, the next failures start it again.
        _server.Script(StreamPath, FakeServer.Reply.Status(500), FakeServer.Reply.Stream(), FakeServer.Reply.Status(500));
        _server.Script(RulesetPath, FakeServer.Reply.Status(304));
        var t = Fast();
        t.RetryBase = t.RetryMax = TimeSpan.FromMilliseconds(200);
        t.PollAfterFailures = 1;
        t.PollInterval = TimeSpan.FromMilliseconds(500);
        New(tuning: t).Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        await Await.UntilAsync(() => _server.ReceivedFor(RulesetPath).Count == 1);
        conn.End();
        await Await.UntilAsync(() => _server.ReceivedFor(RulesetPath).Count == 2);
        var at = _server.ReceivedFor(RulesetPath).Select(r => r.At).ToList();
        Assert.True(at[1] - at[0] >= TimeSpan.FromMilliseconds(480), "polls " + (at[1] - at[0]) + " apart");
    }

    [Fact]
    public async Task WaitsTheRetryAfterOfAPoll()
    {
        _server.Script(
            RulesetPath,
            FakeServer.Reply.Of(429, new Dictionary<string, string> { ["Retry-After"] = "1" }, ""),
            FakeServer.Reply.Status(304));
        New(disableStream: true).Start();
        await Await.UntilAsync(() => _server.ReceivedFor(RulesetPath).Count == 2);
        var at = _server.ReceivedFor(RulesetPath).Select(r => r.At).ToList();
        Assert.True(at[1] - at[0] >= TimeSpan.FromMilliseconds(900), "polled again after " + (at[1] - at[0]));
    }

    [Fact]
    public async Task WithDisableStreamPollsOnlyAndReportsStaleThenReady()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Status(500), FakeServer.Reply.Of(200, ETag("\"p1\""), Raw("polled")));
        New(disableStream: true).Start();
        await Await.UntilAsync(() => Statuses().Contains(Sync.Status.Ready));
        Assert.Equal([Sync.Status.Stale, Sync.Status.Ready], Statuses().Take(2));
        lock (_lock)
        {
            Assert.IsType<HttpStatusException>(_statuses[0].Error);
            Assert.Null(_statuses[1].Error);
        }
        Assert.Equal("polled", _store.Load()!.Ruleset.Environment);
        Assert.Empty(_server.ReceivedFor(StreamPath));
    }

    [Fact]
    public async Task ReportsRevokedAsFatalOnceAndStops()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        _server.Script(RulesetPath, FakeServer.Reply.Status(304));
        New().Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        conn.Write("event: revoked\ndata: {}\n\n");
        await Await.UntilAsync(() => Statuses().Contains(Sync.Status.Fatal));
        await Task.Delay(150);
        Assert.Equal([Sync.Status.Ready, Sync.Status.Fatal], Statuses());
        lock (_lock)
        {
            Assert.Equal("revoked", Assert.IsType<FatalException>(_statuses[1].Error).Reason);
        }
        Assert.Single(_server.ReceivedFor(StreamPath));
        Assert.Empty(_server.ReceivedFor(RulesetPath));
    }

    [Fact]
    public async Task A403OnAPollIsFatalAndStopsPolling()
    {
        _server.Script(RulesetPath, FakeServer.Reply.Status(403));
        New(disableStream: true).Start();
        await Await.UntilAsync(() => Statuses().Contains(Sync.Status.Fatal));
        await Task.Delay(150);
        lock (_lock)
        {
            var (status, err) = Assert.Single(_statuses);
            Assert.Equal(Sync.Status.Fatal, status);
            Assert.Equal(403, Assert.IsType<FatalException>(err).Status);
        }
        Assert.Single(_server.ReceivedFor(RulesetPath));
    }

    [Fact]
    public async Task AFatalPollStopsTheStream()
    {
        _server.Script(StreamPath, FakeServer.Reply.Status(500));
        _server.Script(RulesetPath, FakeServer.Reply.Status(401));
        New().Start();
        await Await.UntilAsync(() => Statuses().Contains(Sync.Status.Fatal));
        await Task.Delay(100);
        var streams = _server.ReceivedFor(StreamPath).Count;
        await Task.Delay(150);
        Assert.Equal(streams, _server.ReceivedFor(StreamPath).Count);
        Assert.Single(Statuses(), s => s == Sync.Status.Fatal);
        Assert.Equal(Sync.Status.Fatal, Statuses()[^1]);
    }

    [Fact]
    public async Task StopIsSafeBeforeStartAndIdempotent()
    {
        var s = New();
        await s.StopAsync();
        await s.StopAsync();
        s.Start();
        await Task.Delay(100);
        Assert.Empty(_server.Requests());
        Assert.Empty(Statuses());
    }

    [Fact]
    public async Task StopEndsTheWorkersAndSilencesCallbacks()
    {
        _server.Script(StreamPath, FakeServer.Reply.Status(500), FakeServer.Reply.Status(500), FakeServer.Reply.Stream());
        _server.Script(RulesetPath, FakeServer.Reply.Status(304));
        var s = New();
        s.Start();
        var conn = await _server.NextStreamAsync(Await.Timeout);
        await Await.UntilAsync(() => Statuses().Contains(Sync.Status.Ready));
        var before = Statuses().Count;
        var stop = s.StopAsync();
        Assert.True(await Task.WhenAny(stop, Task.Delay(Await.Timeout)) == stop, "StopAsync did not finish");
        await s.StopAsync();
        await conn.AwaitClosedAsync(Await.Timeout);
        Assert.Null(s.Apply(Raw("late"), "\"l\"", SnapshotSource.Stream));
        await Task.Delay(100);
        Assert.Equal(before, Statuses().Count);
        Assert.Equal(0, Changes());
        Assert.Null(_store.Load());
        var requests = _server.Requests().Count;
        await Task.Delay(100);
        Assert.Equal(requests, _server.Requests().Count);
    }
}
