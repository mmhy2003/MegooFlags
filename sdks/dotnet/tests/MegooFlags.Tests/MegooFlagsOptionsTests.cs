using System;
using System.Net.Http;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace MegooFlags.Tests;

public sealed class MegooFlagsOptionsTests
{
    private const string Key = "mf_server_options-secret-value";

    private static MegooFlagsOptions Base() => new() { SdkKey = Key, BaseUrl = "https://flags.test" };

    [Fact]
    public void FillsInTheDefaults()
    {
        var o = Base();
        Assert.Equal(Key, o.SdkKey);
        Assert.Equal(TimeSpan.FromSeconds(5), o.InitTimeout);
        Assert.Equal(TimeSpan.FromSeconds(30), o.PollInterval);
        Assert.Equal(TimeSpan.FromSeconds(30), o.FlushInterval);
        Assert.False(o.Offline);
        Assert.False(o.DisableStream);
        Assert.False(o.SendContext);
        Assert.Null(o.BootstrapFile);
        Assert.Null(o.CacheDir);
        Assert.Null(o.HttpClient);
        Assert.Same(NullLogger.Instance, o.Logger);
        Assert.Equal("", new MegooFlagsOptions().SdkKey);
        Assert.Equal("", new MegooFlagsOptions().BaseUrl);
    }

    [Fact]
    public void KeepsWhatItIsGiven()
    {
        using var client = new HttpClient();
        var logger = new RecordingLogger();
        var o = new MegooFlagsOptions
        {
            SdkKey = Key,
            BaseUrl = "https://flags.test",
            BootstrapFile = "/tmp/r.json",
            CacheDir = "/tmp/c",
            Offline = true,
            InitTimeout = TimeSpan.FromMilliseconds(200),
            DisableStream = true,
            SendContext = true,
            HttpClient = client,
            Logger = logger,
        };
        Assert.Equal("/tmp/r.json", o.BootstrapFile);
        Assert.Equal("/tmp/c", o.CacheDir);
        Assert.True(o.Offline);
        Assert.Equal(TimeSpan.FromMilliseconds(200), o.InitTimeout);
        Assert.True(o.DisableStream);
        Assert.True(o.SendContext);
        Assert.Same(client, o.HttpClient);
        Assert.Same(logger, o.Logger);
    }

    [Fact]
    public void ClampsTheIntervals()
    {
        var low = new MegooFlagsOptions { PollInterval = TimeSpan.FromSeconds(1), FlushInterval = TimeSpan.FromSeconds(1) };
        Assert.Equal(TimeSpan.FromSeconds(15), low.PollInterval);
        Assert.Equal(TimeSpan.FromSeconds(10), low.FlushInterval);
        var mid = new MegooFlagsOptions { PollInterval = TimeSpan.FromSeconds(45), FlushInterval = TimeSpan.FromSeconds(45) };
        Assert.Equal(TimeSpan.FromSeconds(45), mid.PollInterval);
        Assert.Equal(TimeSpan.FromSeconds(45), mid.FlushInterval);
        var high = new MegooFlagsOptions { PollInterval = TimeSpan.FromMinutes(10), FlushInterval = TimeSpan.FromMinutes(10) };
        Assert.Equal(TimeSpan.FromMinutes(10), high.PollInterval);
        Assert.Equal(TimeSpan.FromSeconds(60), high.FlushInterval);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(-1)]
    public void TreatsZeroAndNegativeDurationsAsUnset(int seconds)
    {
        var d = TimeSpan.FromSeconds(seconds);
        var o = new MegooFlagsOptions { InitTimeout = d, PollInterval = d, FlushInterval = d };
        Assert.Equal(TimeSpan.FromSeconds(5), o.InitTimeout);
        Assert.Equal(TimeSpan.FromSeconds(30), o.PollInterval);
        Assert.Equal(TimeSpan.FromSeconds(30), o.FlushInterval);
    }

    [Fact]
    public void TreatsNullAsUnset()
    {
        var o = new MegooFlagsOptions { SdkKey = null!, BaseUrl = null!, Logger = null! };
        Assert.Equal("", o.SdkKey);
        Assert.Equal("", o.BaseUrl);
        Assert.Same(NullLogger.Instance, o.Logger);
    }

    [Fact]
    public void RemovesTrailingSlashesFromTheBaseUrl()
    {
        Assert.Equal("http://h:1", new MegooFlagsOptions { BaseUrl = "http://h:1//" }.BaseUrl);
    }

    [Fact]
    public void AcceptsAServerKeyWithAnHttpOrHttpsBaseUrl()
    {
        Assert.Null(Base().Validate());
        Assert.Null(new MegooFlagsOptions { SdkKey = Key, BaseUrl = "http://127.0.0.1:8080/" }.Validate());
    }

    [Fact]
    public void RefusesAClientKey()
    {
        Assert.Equal(
            "megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)",
            new MegooFlagsOptions { SdkKey = "mf_client_abc", BaseUrl = "https://flags.test" }.Validate());
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("abc")]
    [InlineData("mf_serverx")]
    public void RefusesAKeyThatIsNotAServerKey(string? key)
    {
        Assert.Equal(
            "megooflags: invalid SDK key; use a server key (mf_server_…)",
            new MegooFlagsOptions { SdkKey = key!, BaseUrl = "https://flags.test" }.Validate());
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("flags.test")]
    [InlineData("/flags")]
    [InlineData("ftp://flags.test")]
    [InlineData("file:///tmp/x")]
    [InlineData("http://")]
    [InlineData("http:///x")]
    [InlineData("http://a b")]
    public void RefusesABaseUrlThatIsNotHttpOrHttpsWithAHost(string? url)
    {
        Assert.Equal(
            "megooflags: BaseUrl must be an http or https URL",
            new MegooFlagsOptions { SdkKey = Key, BaseUrl = url! }.Validate());
    }

    [Fact]
    public void NeedsABootstrapFileOrACacheDirectoryOffline()
    {
        Assert.Equal(
            "megooflags: Offline needs a BootstrapFile or a CacheDir",
            new MegooFlagsOptions { SdkKey = Key, Offline = true }.Validate());
        Assert.Null(new MegooFlagsOptions { SdkKey = Key, Offline = true, CacheDir = "/tmp/x" }.Validate());
        Assert.Null(new MegooFlagsOptions { SdkKey = Key, Offline = true, BootstrapFile = "/tmp/r.json" }.Validate());
    }

    [Theory]
    [InlineData(Key)]
    [InlineData("mf_client_client-secret-value")]
    [InlineData("not-a-key-secret")]
    public void NeverPutsTheKeyInAMessage(string key)
    {
        foreach (var o in new[]
        {
            new MegooFlagsOptions { SdkKey = key },
            new MegooFlagsOptions { SdkKey = key, BaseUrl = "nope" },
            new MegooFlagsOptions { SdkKey = key, Offline = true },
        })
        {
            var msg = o.Validate() ?? "";
            Assert.DoesNotContain("secret", msg);
            Assert.DoesNotContain("secret", o.ToString());
        }
    }
}
