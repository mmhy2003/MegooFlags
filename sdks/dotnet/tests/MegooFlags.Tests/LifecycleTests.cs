using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal.Delivery;
using MegooFlags.Internal.Json;
using Microsoft.Extensions.Logging;
using OpenFeature;
using OpenFeature.Constant;
using OpenFeature.Error;
using OpenFeature.Model;
using Xunit;
using static MegooFlags.Tests.RulesetGen;

namespace MegooFlags.Tests;

[Collection(OpenFeatureCollection.Name)]
public sealed class LifecycleTests : IAsyncLifetime
{
    private const string Key = "mf_server_lifecycle-secret-value";
    private const string StreamPath = "/sdk/v1/stream";
    private const string EventsPath = "/sdk/v1/events";
    private static readonly TimeSpan _wait = TimeSpan.FromSeconds(5);
    private static readonly ProviderEventTypes[] _eventTypes =
    [
        ProviderEventTypes.ProviderReady,
        ProviderEventTypes.ProviderStale,
        ProviderEventTypes.ProviderError,
        ProviderEventTypes.ProviderConfigurationChanged,
    ];

    private static int _domains;

    private readonly FakeServer _server = FakeServer.Start();
    private readonly string _dir = Directory.CreateTempSubdirectory("megooflags-lifecycle-test-").FullName;

    public LifecycleTests()
    {
        _server.Script(EventsPath, FakeServer.Reply.Status(202));
    }

    public Task InitializeAsync() => Task.CompletedTask;

    public async Task DisposeAsync()
    {
        await Api.Instance.ShutdownAsync();
        _server.Stop();
        Directory.Delete(_dir, recursive: true);
    }

    private static string Put(string id, string data) => "event: put\nid: " + id + "\ndata: " + data + "\n\n";

    private static Sync.Tuning Fast() => new()
    {
        RetryBase = TimeSpan.FromMilliseconds(20),
        RetryMax = TimeSpan.FromMilliseconds(200),
        StableAfter = TimeSpan.FromMilliseconds(100),
        DeadTimeout = TimeSpan.FromSeconds(5),
    };

    private MegooFlagsOptions Options(
        string? sdkKey = null,
        string? bootstrapFile = null,
        string? cacheDir = null,
        bool offline = false,
        TimeSpan initTimeout = default,
        bool sendContext = false,
        ILogger? logger = null) => new()
        {
            SdkKey = sdkKey ?? Key,
            BaseUrl = _server.Url,
            BootstrapFile = bootstrapFile,
            CacheDir = cacheDir,
            Offline = offline,
            InitTimeout = initTimeout,
            SendContext = sendContext,
            Logger = logger ?? new RecordingLogger(),
        };

    private string WriteBootstrapFile(string ruleset)
    {
        var file = Path.Combine(_dir, "ruleset.json");
        File.WriteAllText(file, ruleset);
        return file;
    }

    /// <summary>A provider registered under its own domain, initialising in the background, with its events.</summary>
    private sealed class Registration
    {
        private readonly List<(ProviderEventTypes Type, ProviderEventPayload? Payload)> _events = new();

        public Registration(MegooFlagsProvider provider)
        {
            Provider = provider;
            var domain = "lifecycle-" + Interlocked.Increment(ref _domains);
            Client = Api.Instance.GetClient(domain);
            foreach (var type in _eventTypes)
            {
                Client.AddHandler(type, e =>
                {
                    lock (_events)
                    {
                        _events.Add((type, e));
                    }
                });
            }
            Ready = Task.Run(() => Api.Instance.SetProviderAsync(domain, provider));
        }

        public MegooFlagsProvider Provider { get; }

        public FeatureClient Client { get; }

        /// <summary>Completes when SetProviderAsync returns; it never throws for a failed initialisation.</summary>
        public Task Ready { get; }

        public Task AwaitReadyAsync() => Ready.WaitAsync(_wait);

        public List<(ProviderEventTypes Type, ProviderEventPayload? Payload)> Events()
        {
            lock (_events)
            {
                return _events.ToList();
            }
        }

        public List<ProviderEventPayload?> OfType(ProviderEventTypes type) =>
            Events().Where(e => e.Type == type).Select(e => e.Payload).ToList();

        /// <summary>Waits for the n-th (1-based) event of type.</summary>
        public async Task<ProviderEventPayload> NextAsync(ProviderEventTypes type, int n)
        {
            await Await.UntilAsync(() => OfType(type).Count >= n, "event " + n + " of " + type);
            return OfType(type)[n - 1]!;
        }

        public async Task<string> StringAsync(string flag = "string-flag", EvaluationContext? ctx = null) =>
            (await Provider.ResolveStringValueAsync(flag, "x", ctx ?? EvaluationContext.Empty)).Value;
    }

    private static Registration Register(MegooFlagsOptions options) => new(new MegooFlagsProvider(options, Fast()));

    [Fact]
    public async Task InitializeWaitsForTheFirstPut()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var r = Register(Options());
        var conn = await _server.NextStreamAsync(_wait);
        await Task.Delay(100);
        Assert.False(r.Ready.IsCompleted);
        conn.Write(Put("e1", ProviderRuleset()));
        await r.AwaitReadyAsync();
        Assert.Equal(ProviderStatus.Ready, r.Client.ProviderStatus);
        Assert.Equal("blue", await r.Client.GetStringValueAsync("string-flag", "x"));
        await r.NextAsync(ProviderEventTypes.ProviderReady, 1);
        await Task.Delay(50);
        Assert.Single(r.Events());
    }

    [Fact]
    public async Task InitializeTimesOutKeepsRetryingAndEmitsReadyWithTheRuleset()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var r = Register(Options(initTimeout: TimeSpan.FromMilliseconds(200)));
        var conn = await _server.NextStreamAsync(_wait);
        await r.AwaitReadyAsync();
        Assert.Equal(ProviderStatus.Error, r.Client.ProviderStatus);
        var error = await r.NextAsync(ProviderEventTypes.ProviderError, 1);
        Assert.Contains("megooflags: no ruleset within 200 ms", error.Message);
        var d = await r.Client.GetStringDetailsAsync("string-flag", "x");
        Assert.Equal("x", d.Value);
        Assert.Equal(ErrorType.ProviderNotReady, d.ErrorType);

        conn.Write(Put("e1", ProviderRuleset()));
        var ready = await r.NextAsync(ProviderEventTypes.ProviderReady, 1);
        Assert.Equal("MegooFlags", ready.ProviderName);
        Assert.False(string.IsNullOrEmpty(ready.Message));
        Assert.Equal(ProviderStatus.Ready, r.Client.ProviderStatus);
        Assert.Equal("blue", await r.Client.GetStringValueAsync("string-flag", "x"));
    }

    [Fact]
    public async Task IsReadyFromTheBootstrapFileThenRefreshedByTheStream()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var r = Register(Options(bootstrapFile: WriteBootstrapFile(ProviderRuleset())));
        await r.AwaitReadyAsync();
        Assert.Equal(ProviderStatus.Ready, r.Client.ProviderStatus);
        Assert.Equal("blue", await r.Client.GetStringValueAsync("string-flag", "x"));

        var conn = await _server.NextStreamAsync(_wait);
        conn.Write(Put("e2", ChangedProviderRuleset()));
        var changed = await r.NextAsync(ProviderEventTypes.ProviderConfigurationChanged, 1);
        Assert.Equal(new[] { "new-flag", "string-flag", "targeted" }, changed.FlagsChanged);
        Assert.False(string.IsNullOrEmpty(changed.Message));
        Assert.Equal("MegooFlags", changed.ProviderName);
        Assert.Equal("red", await r.Client.GetStringValueAsync("string-flag", "x"));

        conn.Write(Put("e3", ChangedProviderRuleset()));
        var same = await r.NextAsync(ProviderEventTypes.ProviderConfigurationChanged, 2);
        Assert.Empty(same.FlagsChanged!);
    }

    [Fact]
    public async Task IsReadyFromTheCacheOfflineWithoutAnyRequest()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var first = Register(Options(cacheDir: _dir));
        (await _server.NextStreamAsync(_wait)).Write(Put("e2", ChangedProviderRuleset()));
        await first.AwaitReadyAsync();
        await Api.Instance.ShutdownAsync();
        var before = _server.Requests().Count;

        var r = Register(Options(cacheDir: _dir, offline: true));
        await r.AwaitReadyAsync();
        Assert.Equal(ProviderStatus.Ready, r.Client.ProviderStatus);
        Assert.Equal("red", await r.Client.GetStringValueAsync("string-flag", "x"));
        await Api.Instance.ShutdownAsync();
        Assert.Equal(before, _server.Requests().Count);
    }

    [Fact]
    public async Task PrefersTheBootstrapFileToTheCache()
    {
        new Cache(_dir, Key).Save(ChangedProviderRuleset(), "e2");
        var r = Register(Options(bootstrapFile: WriteBootstrapFile(ProviderRuleset()), cacheDir: _dir, offline: true));
        await r.AwaitReadyAsync();
        Assert.Equal("blue", await r.Client.GetStringValueAsync("string-flag", "x"));

        var missing = Register(Options(bootstrapFile: Path.Combine(_dir, "missing.json"), cacheDir: _dir, offline: true));
        await missing.AwaitReadyAsync();
        Assert.Equal(ProviderStatus.Ready, missing.Client.ProviderStatus);
        Assert.Equal("red", await missing.Client.GetStringValueAsync("string-flag", "x"));
    }

    [Fact]
    public async Task IgnoresACorruptCache()
    {
        new Cache(_dir, Key).Save(ProviderRuleset(), "e1");
        File.WriteAllText(Directory.GetFiles(_dir).Single(), "{not json");
        var log = new RecordingLogger();
        var r = Register(Options(cacheDir: _dir, offline: true, logger: log));
        await r.AwaitReadyAsync();
        Assert.Equal(ProviderStatus.Error, r.Client.ProviderStatus);
        Assert.NotEmpty(log.At(LogLevel.Warning));
    }

    [Fact]
    public async Task FailsOfflineWithoutARuleset()
    {
        var r = Register(Options(cacheDir: _dir, offline: true));
        await r.AwaitReadyAsync();
        Assert.Equal(ProviderStatus.Error, r.Client.ProviderStatus);
        var error = await r.NextAsync(ProviderEventTypes.ProviderError, 1);
        Assert.Contains("offline", error.Message);
        await Assert.ThrowsAsync<GeneralException>(() => r.Provider.InitializeAsync(EvaluationContext.Empty));
    }

    [Fact]
    public async Task IsFatalForAClientKey()
    {
        var r = Register(Options(sdkKey: "mf_client_abc"));
        await r.AwaitReadyAsync();
        Assert.Equal(ProviderStatus.Fatal, r.Client.ProviderStatus);
        var e = await Assert.ThrowsAsync<ProviderFatalException>(() => r.Provider.InitializeAsync(EvaluationContext.Empty));
        Assert.Contains("client SDK key", e.Message);
        Assert.Empty(_server.Requests());
    }

    [Fact]
    public async Task IsFatalWhenTheServerRefusesTheKey()
    {
        _server.Script(StreamPath, FakeServer.Reply.Status(401));
        var r = Register(Options());
        await r.AwaitReadyAsync();
        Assert.Equal(ProviderStatus.Fatal, r.Client.ProviderStatus);
        var error = await r.NextAsync(ProviderEventTypes.ProviderError, 1);
        Assert.Contains("SDK key refused (401)", error.Message);
        Assert.DoesNotContain(Key, error.Message);
        var d = await r.Client.GetStringDetailsAsync("string-flag", "x");
        Assert.Equal("x", d.Value);
        Assert.Equal(ErrorType.ProviderFatal, d.ErrorType);
        var direct = await r.Provider.ResolveStringValueAsync("string-flag", "x", EvaluationContext.Empty);
        Assert.Equal(ErrorType.ProviderFatal, direct.ErrorType);
        await Assert.ThrowsAsync<ProviderFatalException>(() => r.Provider.InitializeAsync(EvaluationContext.Empty));
        await Task.Delay(100);
        Assert.Single(_server.ReceivedFor(StreamPath));
        Assert.Single(r.OfType(ProviderEventTypes.ProviderError));
    }

    [Fact]
    public async Task FailsAfterShutdown()
    {
        var p = new MegooFlagsProvider(Options(bootstrapFile: WriteBootstrapFile(ProviderRuleset())), Fast());
        await p.ShutdownAsync();
        var e = await Assert.ThrowsAsync<GeneralException>(() => p.InitializeAsync(EvaluationContext.Empty));
        Assert.Contains("shut down", e.Message);
    }

    [Fact]
    public async Task EmitsTheFatalErrorOnRevokedAndKeepsServing()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var r = Register(Options());
        var conn = await _server.NextStreamAsync(_wait);
        conn.Write(Put("e2", ChangedProviderRuleset()));
        await r.AwaitReadyAsync();
        await r.Client.GetStringValueAsync("string-flag", "x");

        conn.Write("event: revoked\ndata: {}\n\n");
        var ev = await r.NextAsync(ProviderEventTypes.ProviderError, 1);
        Assert.Equal(ErrorType.ProviderFatal, ev.ErrorType);
        Assert.Equal("MegooFlags", ev.ProviderName);
        Assert.False(string.IsNullOrEmpty(ev.Message));
        Assert.DoesNotContain(Key, ev.Message);
        await Await.UntilAsync(() => r.Client.ProviderStatus == ProviderStatus.Fatal, "the fatal status");
        // The provider keeps serving the last ruleset; the OpenFeature SDK puts a provider that reported
        // PROVIDER_FATAL in the Fatal state and returns the default itself.
        var direct = await r.Provider.ResolveStringValueAsync("string-flag", "x", EvaluationContext.Empty);
        Assert.Equal("red", direct.Value);
        Assert.Equal("red", direct.Variant);
        var d = await r.Client.GetStringDetailsAsync("string-flag", "x");
        Assert.Equal("x", d.Value);
        Assert.Equal(ErrorType.ProviderFatal, d.ErrorType);

        await Task.Delay(200);
        Assert.Single(_server.ReceivedFor(StreamPath));
        await Api.Instance.ShutdownAsync();
        Assert.Empty(_server.ReceivedFor(EventsPath));
        Assert.Single(r.OfType(ProviderEventTypes.ProviderError));
    }

    [Fact]
    public async Task EmitsStaleOnADisconnectAndReadyOnReconnect()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var r = Register(Options());
        var conn = await _server.NextStreamAsync(_wait);
        conn.Write(Put("e1", ProviderRuleset()));
        await r.AwaitReadyAsync();
        await r.NextAsync(ProviderEventTypes.ProviderReady, 1);

        conn.End();
        var stale = await r.NextAsync(ProviderEventTypes.ProviderStale, 1);
        Assert.False(string.IsNullOrEmpty(stale.Message));
        Assert.Equal("blue", await r.Client.GetStringValueAsync("string-flag", "x"));
        await r.NextAsync(ProviderEventTypes.ProviderReady, 2);
        Assert.Equal(ProviderStatus.Ready, r.Client.ProviderStatus);
        await Task.Delay(100);
        Assert.Single(r.OfType(ProviderEventTypes.ProviderStale));
        Assert.Equal(2, r.OfType(ProviderEventTypes.ProviderReady).Count);
        Assert.Equal(3, r.Events().Count);
    }

    [Fact]
    public async Task EmitsNothingAfterShutdown()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var r = Register(Options());
        var conn = await _server.NextStreamAsync(_wait);
        conn.Write(Put("e1", ProviderRuleset()));
        await r.AwaitReadyAsync();
        await r.NextAsync(ProviderEventTypes.ProviderReady, 1);
        await r.Provider.ShutdownAsync();
        try
        {
            conn.Write(Put("e2", ChangedProviderRuleset()));
        }
        catch (Exception e) when (e is IOException or System.Net.HttpListenerException or ObjectDisposedException)
        {
            // The provider already closed the stream.
        }
        await Task.Delay(100);
        Assert.Single(r.Events());
    }

    [Fact]
    public async Task ASlowEventHandlerDoesNotDelayTheNextRuleset()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var r = Register(Options());
        var conn = await _server.NextStreamAsync(_wait);
        conn.Write(Put("e1", StringFlagRuleset("blue", 1)));
        await r.AwaitReadyAsync();

        using var release = new ManualResetEventSlim();
        var handled = 0;
        r.Client.AddHandler(ProviderEventTypes.ProviderConfigurationChanged, _ =>
        {
            Interlocked.Increment(ref handled);
            release.Wait(TimeSpan.FromSeconds(30));
        });
        try
        {
            // The OpenFeature SDK runs handlers one at a time and holds only a few events in its bounded channels,
            // so a provider writing to them directly would block once the first handler is stuck.
            for (var v = 2; v <= 8; v++)
            {
                var variation = v % 2 == 0 ? "red" : "blue";
                conn.Write(Put("e" + v, StringFlagRuleset(variation, v)));
                var want = variation;
                var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(1);
                while (await r.StringAsync() != want)
                {
                    Assert.True(DateTime.UtcNow < deadline, "version " + v + " was not applied within 1 s");
                    await Task.Delay(5);
                }
            }
            Assert.Equal(1, Volatile.Read(ref handled));
        }
        finally
        {
            release.Set();
        }
        await Await.UntilAsync(() => Volatile.Read(ref handled) == 7, "every configuration change");
        Assert.Equal(7, r.OfType(ProviderEventTypes.ProviderConfigurationChanged).Count);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task SendsTheEventsOnShutdown(bool sendContext)
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var r = Register(Options(sendContext: sendContext));
        (await _server.NextStreamAsync(_wait)).Write(Put("e1", ProviderRuleset()));
        await r.AwaitReadyAsync();
        var alice = EvaluationContext.Builder()
            .SetTargetingKey("alice")
            .Set("plan", "pro")
            .Set("since", new DateTime(2026, 1, 2, 3, 4, 5, DateTimeKind.Utc))
            .Build();
        for (var i = 0; i < 3; i++)
        {
            await r.Client.GetStringValueAsync("string-flag", "x", alice);
        }
        await r.Client.GetStringValueAsync("string-flag", "x", EvaluationContext.Builder().SetTargetingKey("bob").Build());
        await r.Client.GetBooleanValueAsync("bool-flag", false, alice);
        await r.Client.GetStringValueAsync("missing", "x", alice);
        await Api.Instance.ShutdownAsync();

        var counts = new Dictionary<string, long>();
        var exposures = new List<Dictionary<string, object?>>();
        foreach (var req in _server.ReceivedFor(EventsPath))
        {
            Assert.Equal("Bearer " + Key, req.Header("Authorization"));
            var body = (Dictionary<string, object?>)JsonTree.Parse(req.Body)!;
            foreach (var c in ((List<object?>)body["counts"]!).Cast<Dictionary<string, object?>>())
            {
                var k = c["flag_key"] + "/" + (c["variant"] ?? "null");
                counts[k] = counts.GetValueOrDefault(k) + (long)c["count"]!;
            }
            exposures.AddRange(((List<object?>)body["exposures"]!).Cast<Dictionary<string, object?>>());
        }
        Assert.Equal(
            new Dictionary<string, long> { ["string-flag/blue"] = 4, ["bool-flag/on"] = 1, ["missing/null"] = 1 },
            counts);
        Assert.Equal(
            new[] { "string-flag/blue", "string-flag/blue", "bool-flag/on" },
            exposures.Select(e => e["flag_key"] + "/" + e["variant"]));
        if (sendContext)
        {
            Assert.Equal(
                Obj("key", "alice", "plan", "pro", "since", "2026-01-02T03:04:05Z"),
                (Dictionary<string, object?>)exposures[0]["context"]!);
            Assert.Equal(Obj("key", "bob"), (Dictionary<string, object?>)exposures[1]["context"]!);
        }
        else
        {
            Assert.All(exposures, e => Assert.False(e.ContainsKey("context")));
        }
    }

    [Fact]
    public async Task ShutdownIsIdempotentAndTheLastRulesetIsStillServed()
    {
        _server.Script(StreamPath, FakeServer.Reply.Stream());
        var r = Register(Options());
        (await _server.NextStreamAsync(_wait)).Write(Put("e1", ProviderRuleset()));
        await r.AwaitReadyAsync();
        await r.Client.GetStringValueAsync("string-flag", "x");
        var other = Task.Run(() => r.Provider.ShutdownAsync());
        await r.Provider.ShutdownAsync();
        await other.WaitAsync(_wait);
        await r.Provider.ShutdownAsync();
        Assert.Single(_server.ReceivedFor(EventsPath));

        var ctx = EvaluationContext.Builder().SetTargetingKey("later").Build();
        Assert.Equal("blue", await r.StringAsync(ctx: ctx));
        await r.Provider.ShutdownAsync();
        await Task.Delay(50);
        Assert.Single(_server.ReceivedFor(EventsPath));
    }
}
