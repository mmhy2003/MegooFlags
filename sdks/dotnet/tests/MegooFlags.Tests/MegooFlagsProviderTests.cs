using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal;
using MegooFlags.Internal.Json;
using Microsoft.Extensions.Logging;
using OpenFeature;
using OpenFeature.Constant;
using OpenFeature.Model;
using Xunit;
using static MegooFlags.Tests.RulesetGen;

namespace MegooFlags.Tests;

/// <summary>
/// The tests that use the OpenFeature API singleton. They run one at a time: each one ends with Api.ShutdownAsync,
/// which shuts every registered provider down.
/// </summary>
[CollectionDefinition(Name, DisableParallelization = true)]
public sealed class OpenFeatureCollection
{
    public const string Name = "OpenFeature";
}

[Collection(OpenFeatureCollection.Name)]
public sealed class MegooFlagsProviderTests : IAsyncLifetime
{
    private const string Key = "mf_server_provider-secret-value";
    private static int _domains;

    private readonly string _dir = Directory.CreateTempSubdirectory("megooflags-provider-test-").FullName;
    private readonly string _bootstrapFile;

    public MegooFlagsProviderTests()
    {
        _bootstrapFile = Path.Combine(_dir, "ruleset.json");
        File.WriteAllText(_bootstrapFile, ProviderRuleset());
    }

    public Task InitializeAsync() => Task.CompletedTask;

    public async Task DisposeAsync()
    {
        await Api.Instance.ShutdownAsync();
        Directory.Delete(_dir, recursive: true);
    }

    private MegooFlagsOptions Offline(string? bootstrapFile = null, ILogger? logger = null) => new()
    {
        SdkKey = Key,
        Offline = true,
        BootstrapFile = bootstrapFile ?? _bootstrapFile,
        Logger = logger ?? new RecordingLogger(),
    };

    private static string NewDomain() => "provider-" + Interlocked.Increment(ref _domains);

    /// <summary>Registers provider under a new domain, waits for it and returns its client.</summary>
    private static async Task<FeatureClient> ClientAsync(MegooFlagsProvider provider)
    {
        var domain = NewDomain();
        await Api.Instance.SetProviderAsync(domain, provider);
        return Api.Instance.GetClient(domain);
    }

    private Task<FeatureClient> ClientAsync() => ClientAsync(new MegooFlagsProvider(Offline()));

    private static EvaluationContext Ctx(string? targetingKey, params object[] attributes)
    {
        var b = EvaluationContext.Builder();
        if (targetingKey != null)
        {
            b.SetTargetingKey(targetingKey);
        }
        for (var i = 0; i < attributes.Length; i += 2)
        {
            var name = (string)attributes[i];
            switch (attributes[i + 1])
            {
                case string s:
                    b.Set(name, s);
                    break;
                case int n:
                    b.Set(name, n);
                    break;
                case double d:
                    b.Set(name, d);
                    break;
                case bool flag:
                    b.Set(name, flag);
                    break;
                case DateTime t:
                    b.Set(name, t);
                    break;
                case Value v:
                    b.Set(name, v);
                    break;
                default:
                    throw new ArgumentException(name);
            }
        }
        return b.Build();
    }

    [Fact]
    public void NamesItselfAndExposesTheVersion()
    {
        var p = new MegooFlagsProvider(Offline());
        Assert.Equal("MegooFlags", p.GetMetadata().Name);
        Assert.Equal("0.1.0", MegooFlagsProvider.Version);
        Assert.Equal(BuildInfo.Version, MegooFlagsProvider.Version);
    }

    [Fact]
    public async Task IsReadyFromTheBootstrapFile()
    {
        var c = await ClientAsync();
        Assert.Equal(ProviderStatus.Ready, c.ProviderStatus);
    }

    [Theory]
    [InlineData("")]
    [InlineData("http://[")]
    public async Task BuildsNoEndpointUrlsOffline(string baseUrl)
    {
        // BaseUrl is optional offline and not validated: "http://[" makes new Uri throw on every platform, so
        // building the ruleset, stream or events URL would fail the constructor.
        var c = await ClientAsync(new MegooFlagsProvider(new MegooFlagsOptions
        {
            SdkKey = Key,
            BaseUrl = baseUrl,
            Offline = true,
            BootstrapFile = _bootstrapFile,
            Logger = new RecordingLogger(),
        }));
        Assert.Equal(ProviderStatus.Ready, c.ProviderStatus);
        Assert.True(await c.GetBooleanValueAsync("bool-flag", false, Ctx(null, "plan", "pro")));
    }

    [Fact]
    public async Task ResolvesABooleanFlag()
    {
        var c = await ClientAsync();
        var pro = await c.GetBooleanDetailsAsync("bool-flag", false, Ctx(null, "plan", "pro"));
        Assert.True(pro.Value);
        Assert.Equal("on", pro.Variant);
        Assert.Equal(Reason.TargetingMatch, pro.Reason);
        Assert.Equal(ErrorType.None, pro.ErrorType);
        Assert.Null(pro.ErrorMessage);
        Assert.Equal(0, pro.FlagMetadata!.GetInt("rule_index"));
        Assert.Null(pro.FlagMetadata.GetString("prerequisite"));

        var free = await c.GetBooleanDetailsAsync("bool-flag", true, EvaluationContext.Empty);
        Assert.False(free.Value);
        Assert.Equal("off", free.Variant);
        Assert.Equal(Reason.Default, free.Reason);
        Assert.True(free.FlagMetadata!.IsEmpty);
    }

    [Fact]
    public async Task ResolvesAStringFlag()
    {
        var d = await (await ClientAsync()).GetStringDetailsAsync("string-flag", "x");
        Assert.Equal("blue", d.Value);
        Assert.Equal("blue", d.Variant);
        Assert.Equal(Reason.Static, d.Reason);
    }

    [Fact]
    public async Task ResolvesADoubleFlag()
    {
        var c = await ClientAsync();
        var d = await c.GetDoubleDetailsAsync("number-flag", 1.0);
        Assert.Equal(2.5, d.Value);
        Assert.Equal("half", d.Variant);
        Assert.Equal(3.0, await c.GetDoubleValueAsync("int-flag", 1.0));
    }

    [Fact]
    public async Task ResolvesAnIntegerFlagWithAnIntegralValueInTheIntRange()
    {
        var c = await ClientAsync();
        var three = await c.GetIntegerDetailsAsync("int-flag", 7);
        Assert.Equal(3, three.Value);
        Assert.Equal("three", three.Variant);
        Assert.Equal(ErrorType.None, three.ErrorType);
        Assert.Equal(2, await c.GetIntegerValueAsync("int-flag", 7, Ctx("two")));

        foreach (var key in new[] { "half", "big" })
        {
            var d = await c.GetIntegerDetailsAsync("int-flag", 7, Ctx(key));
            Assert.Equal(7, d.Value);
            Assert.Equal(ErrorType.TypeMismatch, d.ErrorType);
            Assert.Equal(Reason.Error, d.Reason);
            Assert.Equal(key, d.Variant);
        }
        var half = await c.GetIntegerDetailsAsync("number-flag", 7);
        Assert.Equal(7, half.Value);
        Assert.Equal(ErrorType.TypeMismatch, half.ErrorType);
    }

    [Fact]
    public async Task ResolvesAnObjectFlag()
    {
        var d = await (await ClientAsync()).GetObjectDetailsAsync("json-flag", new Value("dflt"));
        Assert.Equal("cfg", d.Variant);
        var m = d.Value.AsStructure!;
        Assert.Equal(10.0, m.GetValue("limit").AsDouble);
        Assert.Equal(3_000_000_000.0, m.GetValue("big").AsDouble);
        Assert.Equal(0.5, m.GetValue("ratio").AsDouble);
        Assert.Equal(new[] { "a", "b" }, m.GetValue("tags").AsList!.Select(v => v.AsString));
        Assert.True(m.GetValue("none").IsNull);
        Assert.Equal(5, m.Count);
    }

    [Fact]
    public async Task ResolvesAFlagOfAnyTypeAsAValue()
    {
        var c = await ClientAsync();
        Assert.Equal("blue", (await c.GetObjectValueAsync("string-flag", new Value("dflt"))).AsString);
        Assert.Equal(2.5, (await c.GetObjectValueAsync("number-flag", new Value("dflt"))).AsDouble);
        Assert.Equal(false, (await c.GetObjectValueAsync("bool-flag", new Value("dflt"))).AsBoolean);
    }

    [Fact]
    public async Task MapsTheTargetingKeyToTheKeyAttribute()
    {
        var c = await ClientAsync();
        var alice = await c.GetStringDetailsAsync("targeted", "x", Ctx("alice"));
        Assert.Equal("hello alice", alice.Value);
        Assert.Equal("alice", alice.Variant);
        Assert.Equal(Reason.TargetingMatch, alice.Reason);
        Assert.Equal("hello", await c.GetStringValueAsync("targeted", "x", Ctx("bob", "key", "alice")));
        Assert.Equal("hello alice", await c.GetStringValueAsync("targeted", "x", Ctx(null, "key", "alice")));
    }

    [Fact]
    public async Task ReturnsTheDefaultWithFlagNotFoundForAMissingFlag()
    {
        var d = await (await ClientAsync()).GetStringDetailsAsync("missing", "fallback");
        Assert.Equal("fallback", d.Value);
        Assert.Equal(Reason.Error, d.Reason);
        Assert.Equal(ErrorType.FlagNotFound, d.ErrorType);
        Assert.Contains("missing", d.ErrorMessage);
    }

    [Fact]
    public async Task ReturnsTheDefaultWithTypeMismatchForTheWrongType()
    {
        var c = await ClientAsync();
        var b = await c.GetBooleanDetailsAsync("string-flag", true);
        Assert.True(b.Value);
        Assert.Equal(Reason.Error, b.Reason);
        Assert.Equal(ErrorType.TypeMismatch, b.ErrorType);
        var n = await c.GetDoubleDetailsAsync("json-flag", 7.0);
        Assert.Equal(7.0, n.Value);
        Assert.Equal(ErrorType.TypeMismatch, n.ErrorType);
    }

    [Fact]
    public async Task ReturnsTheDefaultItselfOnAnError()
    {
        var p = new MegooFlagsProvider(Offline());
        var d = await p.ResolveStringValueAsync("missing", "fallback", EvaluationContext.Empty);
        Assert.Equal("fallback", d.Value);
        Assert.Equal(Reason.Error, d.Reason);
        Assert.Equal(ErrorType.FlagNotFound, d.ErrorType);
        var i = await p.ResolveIntegerValueAsync("number-flag", 7, EvaluationContext.Empty);
        Assert.Equal(7, i.Value);
        Assert.Equal(ErrorType.TypeMismatch, i.ErrorType);
        Assert.Equal("half", i.Variant);
        var dflt = new Value("dflt");
        var o = await p.ResolveStructureValueAsync("missing", dflt, null);
        Assert.Same(dflt, o.Value);
        Assert.Equal(ErrorType.FlagNotFound, o.ErrorType);
    }

    [Fact]
    public async Task PassesThePrerequisiteFailedReasonAndMetadataThrough()
    {
        var d = await (await ClientAsync()).GetBooleanDetailsAsync("needs-prereq", true);
        Assert.False(d.Value);
        Assert.Equal("off", d.Variant);
        Assert.Equal("PREREQUISITE_FAILED", d.Reason);
        Assert.Equal("prereq-off", d.FlagMetadata!.GetString("prerequisite"));
        Assert.Equal(ErrorType.None, d.ErrorType);
    }

    [Fact]
    public async Task ComparesNumberAttributesAsNumbers()
    {
        var file = Path.Combine(_dir, "numbers.json");
        File.WriteAllText(file, JsonTree.Write(RulesetJson([RuleFlag("seats", Cl("seats", "gte", false, 10L))], Obj())));
        var c = await ClientAsync(new MegooFlagsProvider(Offline(file)));
        Assert.Equal("on", await c.GetStringValueAsync("seats", "d", Ctx(null, "seats", 12)));
        Assert.Equal("on", await c.GetStringValueAsync("seats", "d", Ctx(null, "seats", 10.0)));
        Assert.Equal("off", await c.GetStringValueAsync("seats", "d", Ctx(null, "seats", 9)));
    }

    [Fact]
    public async Task UnwrapsListAndStructureAttributes()
    {
        var file = Path.Combine(_dir, "nested.json");
        File.WriteAllText(file, JsonTree.Write(RulesetJson([RuleFlag("tags", Cl("tags", "in", false, "beta"))], Obj())));
        var c = await ClientAsync(new MegooFlagsProvider(Offline(file)));
        var tags = new Value(new List<Value> { new("alpha"), new("beta") });
        Assert.Equal("on", await c.GetStringValueAsync("tags", "d", Ctx(null, "tags", tags)));
        Assert.Equal("off", await c.GetStringValueAsync("tags", "d", Ctx(null, "tags", new Value(new List<Value> { new("alpha") }))));
        var nested = new Value(Structure.Builder().Set("a", new Value(true)).Build());
        Assert.Equal("off", await c.GetStringValueAsync("tags", "d", Ctx(null, "tags", nested)));
    }

    [Fact]
    public async Task MatchesNothingWithADateAttribute()
    {
        var d = await (await ClientAsync()).GetBooleanDetailsAsync("bool-flag", true, Ctx(null, "plan", DateTime.UtcNow));
        Assert.False(d.Value);
        Assert.Equal("off", d.Variant);
    }

    [Fact]
    public async Task IsNotReadyWithoutARuleset()
    {
        var p = new MegooFlagsProvider(Offline(Path.Combine(_dir, "missing.json")));
        var d = await p.ResolveStringValueAsync("string-flag", "x", EvaluationContext.Empty);
        Assert.Equal("x", d.Value);
        Assert.Equal(Reason.Error, d.Reason);
        Assert.Equal(ErrorType.ProviderNotReady, d.ErrorType);
    }

    [Fact]
    public async Task IsNotReadyAfterAnInvalidBootstrapFile()
    {
        var file = Path.Combine(_dir, "invalid.json");
        File.WriteAllText(file, "{\"schema_version\": 99}");
        var log = new RecordingLogger();
        var p = new MegooFlagsProvider(Offline(file, log));
        var domain = NewDomain();
        await Api.Instance.SetProviderAsync(domain, p);
        Assert.Equal(ProviderStatus.Error, Api.Instance.GetClient(domain).ProviderStatus);
        var d = await p.ResolveStringValueAsync("string-flag", "x", EvaluationContext.Empty);
        Assert.Equal("x", d.Value);
        Assert.Equal(ErrorType.ProviderNotReady, d.ErrorType);
        Assert.NotEmpty(log.At(LogLevel.Warning));
    }

    [Fact]
    public async Task EvaluatesTheDefaultWithProviderFatalForAnInvalidConfig()
    {
        var log = new RecordingLogger();
        var p = new MegooFlagsProvider(new MegooFlagsOptions
        {
            SdkKey = "mf_client_provider-secret",
            BaseUrl = "http://127.0.0.1:1",
            Logger = log,
        });
        var direct = await p.ResolveBooleanValueAsync("bool-flag", true, EvaluationContext.Empty);
        Assert.True(direct.Value);
        Assert.Equal(Reason.Error, direct.Reason);
        Assert.Equal(ErrorType.ProviderFatal, direct.ErrorType);
        Assert.Contains("client SDK key", direct.ErrorMessage);

        var domain = NewDomain();
        var errors = new List<ProviderEventPayload?>();
        var client = Api.Instance.GetClient(domain);
        client.AddHandler(ProviderEventTypes.ProviderError, e =>
        {
            lock (errors)
            {
                errors.Add(e);
            }
        });
        await Api.Instance.SetProviderAsync(domain, p);
        Assert.Equal(ProviderStatus.Fatal, client.ProviderStatus);
        await Await.UntilAsync(() =>
        {
            lock (errors)
            {
                return errors.Count == 1;
            }
        }, "the error event");
        Assert.Contains("client SDK key", errors[0]!.Message);
        var d = await client.GetBooleanDetailsAsync("bool-flag", true);
        Assert.True(d.Value);
        Assert.Equal(ErrorType.ProviderFatal, d.ErrorType);
        Assert.Single(log.At(LogLevel.Error));
        Assert.DoesNotContain(log.Lines(), l => l.Contains("provider-secret"));
    }
}
