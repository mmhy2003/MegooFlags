using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using MegooFlags.Internal.Delivery;
using MegooFlags.Internal.Json;
using Xunit;

namespace MegooFlags.Tests.Internal.Delivery;

public sealed class CacheTests : IDisposable
{
    private const string Key = "mf_server_secret-key-123";
    private const string Raw = "{\"project\":\"p\",\"environment\":\"e\",\"flags\":{},\"segments\":{}}";

    private readonly string _dir = Directory.CreateTempSubdirectory("megooflags-cache-test-").FullName;

    public void Dispose() => Directory.Delete(_dir, recursive: true);

    private static string FileName(string key)
    {
        var hex = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(key))).ToLowerInvariant();
        return "megooflags-" + hex[..16] + ".json";
    }

    private List<string> Entries() =>
        Directory.EnumerateFileSystemEntries(_dir).Select(p => Path.GetFileName(p)).Order(StringComparer.Ordinal).ToList();

    [Fact]
    public void RoundTripsTheRulesetAndTheETag()
    {
        var c = new Cache(_dir, Key);
        c.Save(Raw, "\"v1\"");
        var e = c.Load()!;
        Assert.Equal(JsonTree.Parse(Raw), e.Ruleset);
        Assert.Equal("\"v1\"", e.ETag);
    }

    [Fact]
    public void WritesETagAndRulesetWithTheRulesetEmbeddedAsReceived()
    {
        new Cache(_dir, Key).Save(Raw, "\"v1\"");
        var text = File.ReadAllText(Path.Combine(_dir, FileName(Key)), Encoding.UTF8);
        Assert.Contains("\"ruleset\":" + Raw, text);
        var file = Assert.IsType<Dictionary<string, object?>>(JsonTree.Parse(text));
        Assert.Equal(["etag", "ruleset"], file.Keys.Order(StringComparer.Ordinal));
        Assert.Equal("\"v1\"", file["etag"]);
        Assert.Equal(JsonTree.Parse(Raw), file["ruleset"]);
    }

    [Fact]
    public void ReadsAFileWrittenByTheGoSdk()
    {
        File.WriteAllText(Path.Combine(_dir, FileName(Key)), "{\"etag\":\"\\\"g\\\"\",\"ruleset\":" + Raw + "}\n");
        var e = new Cache(_dir, Key).Load()!;
        Assert.Equal(JsonTree.Parse(Raw), e.Ruleset);
        Assert.Equal("\"g\"", e.ETag);
    }

    [Fact]
    public void NamesTheFileAfter16HexCharactersOfTheKeyHash()
    {
        new Cache(_dir, Key).Save("{}", "x");
        var names = Entries();
        Assert.Equal([FileName(Key)], names);
        Assert.Matches(new Regex("^megooflags-[0-9a-f]{16}\\.json\\z"), names[0]);
        Assert.DoesNotContain(Key, names[0]);
        Assert.DoesNotContain(Key, File.ReadAllText(Path.Combine(_dir, names[0])));
        Assert.NotEqual(FileName("another-key"), FileName(Key));
    }

    [Fact]
    public void MissingFileIsNotFound()
    {
        Assert.Null(new Cache(_dir, Key).Load());
    }

    [Fact]
    public void MissingDirectoryIsNotFound()
    {
        Assert.Null(new Cache(Path.Combine(_dir, "nope"), Key).Load());
    }

    [Theory]
    [InlineData("{not json")]
    [InlineData("[]")]
    [InlineData("{\"etag\":1,\"ruleset\":{}}")]
    [InlineData("{\"etag\":\"x\"}")]
    [InlineData("")]
    public void CorruptFileIsAnError(string bad)
    {
        File.WriteAllText(Path.Combine(_dir, FileName(Key)), bad);
        var e = Assert.Throws<IOException>(() => new Cache(_dir, Key).Load());
        Assert.StartsWith("megooflags: corrupt cache file ", e.Message);
        Assert.DoesNotContain(Key, e.Message);
    }

    [Fact]
    public void MissingETagIsEmpty()
    {
        File.WriteAllText(Path.Combine(_dir, FileName(Key)), "{\"ruleset\":{}}");
        Assert.Equal("", new Cache(_dir, Key).Load()!.ETag);
    }

    [Fact]
    public void InvalidUtf8IsAnError()
    {
        File.WriteAllBytes(Path.Combine(_dir, FileName(Key)), [(byte)'{', (byte)'"', 0xff, (byte)'"', (byte)'}']);
        var e = Assert.Throws<IOException>(() => new Cache(_dir, Key).Load());
        Assert.StartsWith("megooflags: corrupt cache file ", e.Message);
    }

    [UnixFact]
    [UnsupportedOSPlatform("windows")]
    public void UnreadableFileIsAnError()
    {
        var path = Path.Combine(_dir, FileName(Key));
        File.WriteAllText(path, "{\"ruleset\":{}}");
        File.SetUnixFileMode(path, UnixFileMode.None);
        try
        {
            if (TryRead(path))
            {
                return; // running as root: file modes do not apply
            }
            var e = Assert.Throws<IOException>(() => new Cache(_dir, Key).Load());
            Assert.StartsWith("megooflags: cannot read cache file ", e.Message);
        }
        finally
        {
            File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        }
    }

    private static bool TryRead(string path)
    {
        try
        {
            File.ReadAllBytes(path);
            return true;
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
    }

    [UnixFact]
    [UnsupportedOSPlatform("windows")]
    public void FileModeIs0600()
    {
        new Cache(_dir, Key).Save(Raw, "e");
        Assert.Equal(UnixFileMode.UserRead | UnixFileMode.UserWrite, File.GetUnixFileMode(Path.Combine(_dir, FileName(Key))));
    }

    [Fact]
    public void AtomicReplaceLeavesNoTempFile()
    {
        var c = new Cache(_dir, Key);
        c.Save(Raw, "a");
        c.Save("{\"flags\":{}}", "b");
        Assert.Equal([FileName(Key)], Entries());
        Assert.Equal("b", c.Load()!.ETag);
        Assert.Equal(JsonTree.Parse("{\"flags\":{}}"), c.Load()!.Ruleset);
    }

    [Fact]
    public void InvalidRawIsRefusedAndWritesNothing()
    {
        var c = new Cache(_dir, Key);
        var e = Assert.Throws<IOException>(() => c.Save("{oops", "a"));
        Assert.StartsWith("megooflags: cannot cache an invalid ruleset", e.Message);
        Assert.Empty(Entries());
    }

    [Fact]
    public void CreatesTheDirectory()
    {
        var nested = Path.Combine(_dir, "a", "b");
        new Cache(nested, Key).Save(Raw, "e");
        Assert.True(File.Exists(Path.Combine(nested, FileName(Key))));
        if (!OperatingSystem.IsWindows())
        {
            Assert.Equal(UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute, File.GetUnixFileMode(nested));
        }
    }

    [Fact]
    public void NoCacheDirectoryLoadsNothingAndSavesNothing()
    {
        var c = new Cache(null, Key);
        Assert.Null(c.Load());
        c.Save(Raw, "e");
        Assert.Null(c.Load());
        Assert.Empty(Entries());
    }
}

/// <summary>A fact that runs on Unix only (file modes).</summary>
internal sealed class UnixFactAttribute : FactAttribute
{
    public UnixFactAttribute()
    {
        if (OperatingSystem.IsWindows())
        {
            Skip = "Unix file modes only";
        }
    }
}
