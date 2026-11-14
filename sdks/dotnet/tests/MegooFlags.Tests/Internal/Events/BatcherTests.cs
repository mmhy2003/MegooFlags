using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal.Events;
using MegooFlags.Internal.Json;
using MegooFlags.Internal.Transport;
using Xunit;
using HttpTransport = MegooFlags.Internal.Transport.Transport;

namespace MegooFlags.Tests.Internal.Events;

public sealed class BatcherTests : IDisposable
{
    private const string Key = "mf_server_batcher-key-value";
    private const string EventsPath = "/sdk/v1/events";

    private readonly FakeServer _server = FakeServer.Start();
    private readonly List<Batcher> _batchers = new();
    private readonly ManualResetEventSlim _release = new(false);
    private readonly object _lock = new();
    private readonly List<Dictionary<string, object?>> _accepted = new();
    private readonly List<string> _warnings = new();

    public void Dispose()
    {
        _release.Set();
        foreach (var b in _batchers)
        {
            b.Stop();
        }
        _server.Stop();
        _release.Dispose();
    }

    /// <summary>Answers each events POST with status(n, body), n counting from 1, and records the 2xx bodies.</summary>
    private void Respond(Func<int, Dictionary<string, object?>, FakeServer.Reply> reply)
    {
        var n = 0;
        _server.Script(EventsPath, FakeServer.Reply.Respond(r =>
        {
            var body = (Dictionary<string, object?>)JsonTree.Parse(r.Body)!;
            var answer = reply(Interlocked.Increment(ref n), body);
            return answer;
        }));
    }

    private void Accept() => Respond((_, body) => Accepted(body));

    private FakeServer.Reply Accepted(Dictionary<string, object?> body)
    {
        lock (_lock)
        {
            _accepted.Add(body);
        }
        return FakeServer.Reply.Status(202);
    }

    private Batcher New(Action<Batcher.Options>? configure = null, bool start = true)
    {
        var o = new Batcher.Options(HttpTransport.Url(_server.Url, "events"), Key)
        {
            FlushInterval = TimeSpan.FromMilliseconds(50),
            RetryBase = TimeSpan.FromMilliseconds(10),
            RetryMax = TimeSpan.FromMilliseconds(40),
            Warn = m =>
            {
                lock (_lock)
                {
                    _warnings.Add(m);
                }
            },
        };
        configure?.Invoke(o);
        var b = new Batcher(o);
        _batchers.Add(b);
        if (start)
        {
            b.Start();
        }
        return b;
    }

    private static Evaluation Eval(string flag, string contextKey, IDictionary<string, object?>? context = null) =>
        new(flag, "on", true, "STATIC", contextKey, context, DateTimeOffset.UtcNow);

    private static List<Dictionary<string, object?>> Items(Dictionary<string, object?> body, string list) =>
        ((List<object?>)body[list]!).Cast<Dictionary<string, object?>>().ToList();

    /// <summary>What the server accepted: the number of exposures and the count per flag.</summary>
    private (int Exposures, Dictionary<string, long> Counts) Totals()
    {
        lock (_lock)
        {
            var counts = new Dictionary<string, long>();
            var exposures = 0;
            foreach (var body in _accepted)
            {
                exposures += Items(body, "exposures").Count;
                foreach (var c in Items(body, "counts"))
                {
                    var flag = (string)c["flag_key"]!;
                    counts[flag] = counts.GetValueOrDefault(flag) + (long)c["count"]!;
                }
            }
            return (exposures, counts);
        }
    }

    private int RequestCount => _server.ReceivedFor(EventsPath).Count;

    private static async Task ShutdownAsync(Batcher b) =>
        Assert.True(await b.ShutdownAsync(TimeSpan.FromSeconds(5)), "shutdown sent everything");

    [Fact]
    public async Task SendsBearerUserAgentAndJson()
    {
        Accept();
        var b = New();
        b.Record(Eval("f", "u1"));
        await Await.UntilAsync(() => RequestCount == 1, "one request");
        await ShutdownAsync(b);

        var r = _server.ReceivedFor(EventsPath)[0];
        Assert.Equal("POST", r.Method);
        Assert.Equal("Bearer " + Key, r.Header("Authorization"));
        Assert.Equal(HttpTransport.UserAgent, r.Header("User-Agent"));
        Assert.Equal("application/json", r.Header("Content-Type"));
        Assert.DoesNotContain(Key, r.Uri, StringComparison.Ordinal);
        var body = (Dictionary<string, object?>)JsonTree.Parse(r.Body)!;
        var exposure = Assert.Single(Items(body, "exposures"));
        Assert.Equal("f", exposure["flag_key"]);
        Assert.Equal("on", exposure["variant"]);
        Assert.Equal("STATIC", exposure["reason"]);
        var count = Assert.Single(Items(body, "counts"));
        Assert.Equal(1L, count["count"]);
    }

    [Fact]
    public async Task FlushesOnTheInterval()
    {
        Accept();
        var b = New();
        b.Record(Eval("f", "u1"));
        await Await.UntilAsync(() => RequestCount == 1, "a flush on the interval");

        var (exposures, counts) = Totals();
        Assert.Equal(1, exposures);
        Assert.Equal(1, counts["f"]);
        await ShutdownAsync(b);
    }

    [Fact]
    public async Task FlushesAt1000PendingItems()
    {
        Accept();
        var b = New(o => o.FlushInterval = TimeSpan.FromHours(1));
        for (var i = 0; i < 1000; i++)
        {
            b.Record(Eval("f", "u" + i));
        }
        await Await.UntilAsync(() => RequestCount >= 1, "a flush at 1000 exposures");

        var first = (Dictionary<string, object?>)JsonTree.Parse(_server.ReceivedFor(EventsPath)[0].Body)!;
        Assert.Equal(1000, Items(first, "exposures").Count);
    }

    [Fact]
    public async Task ShutdownFlushesAndIsIdempotent()
    {
        Accept();
        var b = New(o => o.FlushInterval = TimeSpan.FromHours(1));
        b.Record(Eval("f", "u1"));
        b.Record(Eval("f", "u2"));
        b.Record(Eval("g", "u1"));
        await ShutdownAsync(b);

        var (exposures, counts) = Totals();
        Assert.Equal(3, exposures);
        Assert.Equal(2, counts["f"]);
        Assert.Equal(1, counts["g"]);
        await ShutdownAsync(b);
        Assert.Equal(1, RequestCount);
    }

    [Fact]
    public async Task ShutdownGivesUpAtTheDeadline()
    {
        Respond((_, body) =>
        {
            _release.Wait();
            return Accepted(body);
        });
        var b = New(o => o.FlushInterval = TimeSpan.FromHours(1));
        b.Record(Eval("f", "u1"));

        var sw = Stopwatch.StartNew();
        var sent = await b.ShutdownAsync(TimeSpan.FromMilliseconds(200));
        Assert.False(sent);
        Assert.InRange(sw.Elapsed, TimeSpan.FromMilliseconds(150), TimeSpan.FromSeconds(1));
    }

    [Fact]
    public async Task StopSendsNothingAndIsIdempotent()
    {
        Accept();
        var b = New(o => o.FlushInterval = TimeSpan.FromHours(1));
        b.Record(Eval("f", "u1"));
        b.Stop();
        b.Stop();
        Assert.True(await b.ShutdownAsync(TimeSpan.FromSeconds(1)));
        Assert.Equal(0, RequestCount);
    }

    [Fact]
    public void AFullQueueDropsAndCountsWithoutBlocking()
    {
        var b = New(o => o.QueueSize = 1, start: false);
        var sw = Stopwatch.StartNew();
        b.Record(Eval("f", "u1"));
        b.Record(Eval("f", "u2"));
        Assert.True(sw.Elapsed < TimeSpan.FromMilliseconds(100), "Record took " + sw.Elapsed);
        Assert.Equal(1, b.Dropped);
    }

    [Fact]
    public async Task RecordAfterShutdownOrStopIsDropped()
    {
        Accept();
        var b = New();
        await ShutdownAsync(b);
        b.Record(Eval("f", "u1"));
        b.Stop();
        b.Record(Eval("f", "u2"));
        Assert.Equal(2, b.Dropped);
        Assert.Equal(0, RequestCount);
    }

    [Fact]
    public async Task Status401StopsSendingAndCallsOnFatalOnce()
    {
        Respond((_, _) => FakeServer.Reply.Status(401));
        var fatals = new List<FatalException>();
        var b = New(o => o.OnFatal = e =>
        {
            lock (fatals)
            {
                fatals.Add(e);
            }
        });
        b.Record(Eval("f", "u1"));
        await Await.UntilAsync(() => RequestCount == 1, "one request");
        await Await.UntilAsync(() =>
        {
            lock (fatals)
            {
                return fatals.Count == 1;
            }
        }, "OnFatal");
        b.Record(Eval("g", "u1"));
        await Task.Delay(200);
        await b.ShutdownAsync(TimeSpan.FromSeconds(5));

        Assert.Equal(1, RequestCount);
        var e = Assert.Single(fatals);
        Assert.Equal(401, e.Status);
    }

    [Fact]
    public async Task OnFatalMayCallStopAndShutdown()
    {
        Respond((_, _) => FakeServer.Reply.Status(403));
        var returned = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        Batcher? b = null;
        b = New(o => o.OnFatal = _ =>
        {
            b!.Stop();
            returned.SetResult(b.ShutdownAsync(TimeSpan.FromSeconds(5)).GetAwaiter().GetResult());
        });
        b.Record(Eval("f", "u1"));
        Assert.True(await returned.Task.WaitAsync(TimeSpan.FromSeconds(2)));
    }

    [Fact]
    public async Task Status413SplitsTheBatch()
    {
        Respond((_, body) =>
            Items(body, "exposures").Count + Items(body, "counts").Count > 10 ? FakeServer.Reply.Status(413) : Accepted(body));
        var b = New(o => o.FlushInterval = TimeSpan.FromHours(1));
        for (var i = 0; i < 25; i++)
        {
            b.Record(Eval("f", "u" + i));
        }
        await ShutdownAsync(b);

        var (exposures, counts) = Totals();
        Assert.Equal(25, exposures);
        Assert.Equal(25, counts["f"]);
        lock (_lock)
        {
            Assert.All(_accepted, a => Assert.InRange(Items(a, "exposures").Count + Items(a, "counts").Count, 1, 10));
        }
    }

    [Fact]
    public async Task ABodyOver1MBIsSplitBeforeSending()
    {
        Accept();
        var b = New(o =>
        {
            o.FlushInterval = TimeSpan.FromHours(1);
            o.SendContext = true;
        });
        var big = new Dictionary<string, object?> { ["blob"] = new string('x', 2000) };
        for (var i = 0; i < 1000; i++)
        {
            b.Record(Eval("f", "u" + i, big));
        }
        await ShutdownAsync(b);

        var requests = _server.ReceivedFor(EventsPath);
        Assert.True(requests.Count >= 2, "the batch was split");
        Assert.All(requests, r => Assert.True(Encoding.UTF8.GetByteCount(r.Body) <= Batcher.MaxBodyBytes));
        Assert.Equal(1000, Totals().Exposures);
    }

    [Fact]
    public async Task ASingleOversizedItemIsDropped()
    {
        Accept();
        var b = New(o =>
        {
            o.FlushInterval = TimeSpan.FromHours(1);
            o.SendContext = true;
        });
        b.Record(Eval("f", "u1", new Dictionary<string, object?> { ["blob"] = new string('x', 1_100_000) }));
        await ShutdownAsync(b);

        var (exposures, counts) = Totals();
        Assert.Equal(0, exposures);
        Assert.Equal(1, counts["f"]);
        Assert.All(_server.ReceivedFor(EventsPath), r => Assert.True(Encoding.UTF8.GetByteCount(r.Body) <= Batcher.MaxBodyBytes));
        lock (_lock)
        {
            Assert.Contains(_warnings, w => w.Contains("too large", StringComparison.Ordinal));
        }
    }

    [Fact]
    public async Task Status422DropsTheBatch()
    {
        Respond((_, _) => FakeServer.Reply.Status(422));
        var b = New();
        b.Record(Eval("f", "u1"));
        await Await.UntilAsync(() => RequestCount == 1, "one request");
        await Task.Delay(200); // four intervals
        await ShutdownAsync(b);

        Assert.Equal(1, RequestCount);
        lock (_lock)
        {
            Assert.Contains(_warnings, w => w.Contains("refused", StringComparison.Ordinal));
        }
    }

    [Fact]
    public async Task Status429RetriesAfterRetryAfter()
    {
        Respond((n, body) => n == 1
            ? FakeServer.Reply.Of(429, new Dictionary<string, string> { ["Retry-After"] = "1" }, "")
            : Accepted(body));
        var b = New();
        b.Record(Eval("f", "u1"));
        await Await.UntilAsync(() => RequestCount == 2, "the retry");

        var requests = _server.ReceivedFor(EventsPath);
        Assert.True(requests[1].At - requests[0].At >= TimeSpan.FromSeconds(0.95), "resent after " + (requests[1].At - requests[0].At));
        Assert.Equal(requests[0].Body, requests[1].Body);
        await ShutdownAsync(b);
    }

    [Fact]
    public async Task Status500RetriesWithBackoffAndMergesNewCounts()
    {
        Respond((n, body) => n <= 2 ? FakeServer.Reply.Status(503) : Accepted(body));
        var b = New();
        for (var i = 0; i < 5; i++)
        {
            b.Record(Eval("f", "u1"));
        }
        await Await.UntilAsync(() => RequestCount >= 1, "the first request");
        for (var i = 0; i < 3; i++)
        {
            b.Record(Eval("f", "u1"));
        }
        await Await.UntilAsync(() => RequestCount >= 3, "two retries");
        await ShutdownAsync(b);

        var (exposures, counts) = Totals();
        Assert.Equal(1, exposures);
        Assert.Equal(8, counts["f"]);
    }

    [Fact]
    public async Task ASplitStopsAfterAFatalStatus()
    {
        Respond((_, body) =>
            Items(body, "exposures").Count + Items(body, "counts").Count > 10 ? FakeServer.Reply.Status(413) : FakeServer.Reply.Status(401));
        var b = New(o => o.FlushInterval = TimeSpan.FromHours(1));
        for (var i = 0; i < 25; i++)
        {
            b.Record(Eval("f", "u" + i));
        }
        await b.ShutdownAsync(TimeSpan.FromSeconds(5));

        // 26 items: 413; 13: 413; 6: 401, and no attempt after it.
        Assert.Equal(3, RequestCount);
    }

    [Fact]
    public async Task UsesTheTimeProviderForTheFlushTimer()
    {
        Accept();
        var time = new ManualTime();
        var b = New(o =>
        {
            o.FlushInterval = TimeSpan.FromSeconds(30);
            o.Time = time;
        });
        b.Record(Eval("f", "u1"));
        await Task.Delay(100);
        Assert.Equal(0, RequestCount);

        time.Advance(TimeSpan.FromSeconds(30));
        await Await.UntilAsync(() => RequestCount == 1, "a flush when the time provider reaches the interval");
        await ShutdownAsync(b);
    }

    /// <summary>A clock that moves only when the test advances it; its timers fire then.</summary>
    private sealed class ManualTime : TimeProvider
    {
        private readonly object _lock = new();
        private readonly List<ManualTimer> _timers = new();
        private DateTimeOffset _now = new(2026, 11, 10, 9, 0, 0, TimeSpan.Zero);

        public override DateTimeOffset GetUtcNow()
        {
            lock (_lock)
            {
                return _now;
            }
        }

        public override long TimestampFrequency => TimeSpan.TicksPerSecond;

        public override long GetTimestamp() => GetUtcNow().UtcTicks;

        public override ITimer CreateTimer(TimerCallback callback, object? state, TimeSpan dueTime, TimeSpan period)
        {
            var t = new ManualTimer(this, callback, state);
            t.Change(dueTime, period);
            return t;
        }

        public void Advance(TimeSpan d)
        {
            List<ManualTimer> due;
            lock (_lock)
            {
                _now += d;
                due = _timers.Where(t => t.Due <= _now).ToList();
                foreach (var t in due)
                {
                    _timers.Remove(t);
                }
            }
            foreach (var t in due)
            {
                t.Fire();
            }
        }

        private sealed class ManualTimer(ManualTime owner, TimerCallback callback, object? state) : ITimer
        {
            public DateTimeOffset Due { get; private set; }

            public bool Change(TimeSpan dueTime, TimeSpan period)
            {
                lock (owner._lock)
                {
                    owner._timers.Remove(this);
                    if (dueTime != Timeout.InfiniteTimeSpan)
                    {
                        Due = owner._now + dueTime;
                        owner._timers.Add(this);
                    }
                }
                return true;
            }

            public void Fire() => callback(state);

            public void Dispose()
            {
                lock (owner._lock)
                {
                    owner._timers.Remove(this);
                }
            }

            public ValueTask DisposeAsync()
            {
                Dispose();
                return ValueTask.CompletedTask;
            }
        }
    }
}
