using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using MegooFlags.Internal.Json;

namespace MegooFlags.Internal.Delivery;

/// <summary>
/// The last-known ruleset on disk: the file <c>&lt;dir&gt;/megooflags-&lt;first 16 hex of sha256(key)&gt;.json</c>
/// holding <c>{"etag", "ruleset"}</c>, the Go SDK's format, so a cache directory can be shared between SDKs. The
/// key itself is never written to disk. Without a directory, load finds nothing and save does nothing.
/// </summary>
internal sealed class Cache
{
    /// <summary>A cached ruleset: the decoded ruleset tree (see <see cref="JsonTree.Parse"/>) and its ETag.</summary>
    public sealed record Entry(object? Ruleset, string ETag);

    private const UnixFileMode DirectoryMode = UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute;
    private const UnixFileMode CacheFileMode = UnixFileMode.UserRead | UnixFileMode.UserWrite;

    private static readonly UTF8Encoding _strictUtf8 = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);

    private readonly string? _dir;
    private readonly string? _path;

    /// <summary>A cache in dir for sdkKey; dir may be null for no cache.</summary>
    public Cache(string? dir, string sdkKey)
    {
        _dir = dir;
        _path = dir == null ? null : Path.Combine(dir, FileName(sdkKey));
    }

    /// <summary>
    /// Returns the cached ruleset and ETag, or null when there is no file. Throws <see cref="IOException"/> for a
    /// file it cannot read or that is corrupt.
    /// </summary>
    public Entry? Load()
    {
        if (_path == null)
        {
            return null;
        }
        string text;
        try
        {
            text = _strictUtf8.GetString(File.ReadAllBytes(_path));
        }
        catch (Exception e) when (e is FileNotFoundException or DirectoryNotFoundException)
        {
            return null;
        }
        catch (DecoderFallbackException)
        {
            throw Corrupt("not UTF-8");
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            throw new IOException("megooflags: cannot read cache file " + _path + ": " + e.Message, e);
        }
        object? file;
        try
        {
            file = JsonTree.Parse(text);
        }
        catch (JsonException e)
        {
            throw Corrupt(e.Message);
        }
        if (file is not Dictionary<string, object?> m)
        {
            throw Corrupt("not a JSON object");
        }
        var etag = m.TryGetValue("etag", out var t) ? t : "";
        if (etag is not string etagText)
        {
            throw Corrupt("etag is not a string");
        }
        if (!m.TryGetValue("ruleset", out var ruleset))
        {
            throw Corrupt("no ruleset");
        }
        return new Entry(ruleset, etagText);
    }

    /// <summary>
    /// Writes the ruleset JSON text, embedded as received, and its ETag to a temp file in the cache directory and
    /// moves it over the cache file, so a reader never sees a partial write. Throws <see cref="IOException"/> when
    /// raw is not JSON or the write fails, and leaves no temp file behind.
    /// </summary>
    public void Save(string raw, string etag)
    {
        if (_dir == null || _path == null)
        {
            return;
        }
        try
        {
            JsonTree.Parse(raw);
        }
        catch (JsonException e)
        {
            throw new IOException("megooflags: cannot cache an invalid ruleset: " + e.Message, e);
        }
        var content = Encoding.UTF8.GetBytes("{\"etag\":" + JsonTree.Write(etag) + ",\"ruleset\":" + raw + "}\n");
        try
        {
            Write(content);
        }
        catch (UnauthorizedAccessException e)
        {
            throw new IOException("megooflags: cannot write cache file " + _path + ": " + e.Message, e);
        }
    }

    private void Write(byte[] content)
    {
        if (OperatingSystem.IsWindows())
        {
            Directory.CreateDirectory(_dir!);
        }
        else
        {
            Directory.CreateDirectory(_dir!, DirectoryMode);
        }
        var tmp = Path.Combine(_dir!, ".megooflags-" + Guid.NewGuid().ToString("N") + ".tmp");
        var options = new FileStreamOptions { Mode = FileMode.CreateNew, Access = FileAccess.Write };
        if (!OperatingSystem.IsWindows())
        {
            options.UnixCreateMode = CacheFileMode;
        }
        try
        {
            using (var fs = new FileStream(tmp, options))
            {
                fs.Write(content);
                fs.Flush(flushToDisk: true);
            }
            if (!OperatingSystem.IsWindows())
            {
                File.SetUnixFileMode(tmp, CacheFileMode);
            }
            File.Move(tmp, _path!, overwrite: true);
        }
        catch
        {
            File.Delete(tmp);
            throw;
        }
    }

    private IOException Corrupt(string why) => new("megooflags: corrupt cache file " + _path + ": " + why);

    private static string FileName(string sdkKey)
    {
        var sum = SHA256.HashData(Encoding.UTF8.GetBytes(sdkKey));
        return "megooflags-" + Convert.ToHexString(sum, 0, 8).ToLowerInvariant() + ".json";
    }
}
