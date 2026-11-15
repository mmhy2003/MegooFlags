using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MegooFlags.Internal.Engine;
using MegooFlags.Internal.Json;
using Xunit;
using static MegooFlags.Tests.RulesetGen;

namespace MegooFlags.Tests;

/// <summary>The language-neutral conformance suite every SDK must pass (spec/conformance/README.md).</summary>
public class ConformanceTests
{
    private static readonly string _conformanceDir = Path.Combine(FindRepoRoot(), "spec", "conformance");

    private static readonly Lazy<Dictionary<string, Action>> _checks = new(LoadChecks);

    /// <summary>FindRepoRoot walks up from the test binaries to the directory that contains spec/.</summary>
    private static string FindRepoRoot()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            if (Directory.Exists(Path.Combine(dir.FullName, "spec", "conformance")))
            {
                return dir.FullName;
            }
        }
        throw new DirectoryNotFoundException("no spec/conformance above " + AppContext.BaseDirectory);
    }

    /// <summary>LoadCaseFile reads a case file and refuses any schema_version other than the integer 1.</summary>
    internal static Dictionary<string, object?> LoadCaseFile(string path)
    {
        var f = (Dictionary<string, object?>)JsonTree.Parse(File.ReadAllText(path))!;
        f.TryGetValue("schema_version", out var version);
        if (version is not long v || v != 1)
        {
            throw new InvalidDataException(
                Path.GetFileName(path) + ": unsupported schema_version " + JsonTree.Write(version));
        }
        return f;
    }

    /// <summary>
    /// SameJson is JSON equality: 1 == 1.0, but true != 1 and "1" != 1; arrays element by element, objects key by
    /// key, {} is not [].
    /// </summary>
    internal static bool SameJson(object? a, object? b)
    {
        if (a == null || b == null)
        {
            return a == null && b == null;
        }
        if (a is IReadOnlyList<object?> || b is IReadOnlyList<object?>)
        {
            if (a is not IReadOnlyList<object?> x || b is not IReadOnlyList<object?> y || x.Count != y.Count)
            {
                return false;
            }
            for (var i = 0; i < x.Count; i++)
            {
                if (!SameJson(x[i], y[i]))
                {
                    return false;
                }
            }
            return true;
        }
        if (a is IReadOnlyDictionary<string, object?> || b is IReadOnlyDictionary<string, object?>)
        {
            if (a is not IReadOnlyDictionary<string, object?> m
                || b is not IReadOnlyDictionary<string, object?> n
                || m.Count != n.Count)
            {
                return false;
            }
            foreach (var kv in m)
            {
                if (!n.TryGetValue(kv.Key, out var other) || !SameJson(kv.Value, other))
                {
                    return false;
                }
            }
            return true;
        }
        return Values.JsonEqual(a, b);
    }

    private static List<string> CaseFiles() =>
        Directory.GetFiles(Path.Combine(_conformanceDir, "cases"), "*.json")
            .Order(StringComparer.Ordinal)
            .ToList();

    private static List<object?> Vectors() =>
        (List<object?>)LoadCaseFile(Path.Combine(_conformanceDir, "bucketing.json"))["vectors"]!;

    /// <summary>LoadChecks builds one check per &lt;stem&gt;::&lt;name&gt; and per bucketing::&lt;input&gt;.</summary>
    private static Dictionary<string, Action> LoadChecks()
    {
        var checks = new Dictionary<string, Action>(StringComparer.Ordinal);
        foreach (var path in CaseFiles())
        {
            var stem = Path.GetFileNameWithoutExtension(path);
            var f = LoadCaseFile(path);
            Ruleset? rs = null;
            ParseException? error = null;
            try
            {
                rs = RulesetParser.Parse(f.GetValueOrDefault("ruleset"));
            }
            catch (ParseException e)
            {
                error = e;
            }
            foreach (var item in (List<object?>)(f.GetValueOrDefault("tests") ?? new List<object?>()))
            {
                var c = (Dictionary<string, object?>)item!;
                checks.Add(stem + "::" + c["name"], () =>
                {
                    Assert.True(rs != null, "ruleset does not parse: " + error?.Message);
                    Check(rs!, c);
                });
            }
        }
        foreach (var item in Vectors())
        {
            var v = (Dictionary<string, object?>)item!;
            var input = (string)v["input"]!;
            checks.Add("bucketing::" + input, () =>
            {
                var dot = input.IndexOf('.', StringComparison.Ordinal);
                Assert.Equal((long)v["bucket"]!, Bucketing.Bucket(input[..dot], input[(dot + 1)..]));
            });
        }
        return checks;
    }

    private static void Check(Ruleset rs, Dictionary<string, object?> c)
    {
        var r = Evaluator.Evaluate(
            rs,
            (string)c["flag"]!,
            c.GetValueOrDefault("context"),
            c.GetValueOrDefault("default"),
            (string?)c.GetValueOrDefault("expected_type"));
        var want = (Dictionary<string, object?>)c["expected"]!;
        Assert.True(
            SameJson(r.Value, want["value"]),
            "value: got " + JsonTree.Write(r.Value) + ", want " + JsonTree.Write(want["value"]));
        Assert.Equal((string?)want["variant"], r.Variant);
        Assert.Equal((string?)want["reason"], r.Reason);
        Assert.Equal((string?)want["error_code"], r.ErrorCode);
        var meta = r.Metadata();
        foreach (var kv in (Dictionary<string, object?>)want["metadata"]!)
        {
            Assert.True(meta.TryGetValue(kv.Key, out var got), "metadata " + kv.Key + " missing");
            Assert.True(SameJson(got, kv.Value), "metadata " + kv.Key + ": got " + JsonTree.Write(got));
        }
    }

    public static TheoryData<string> Ids() => new(_checks.Value.Keys);

    [Theory(DisplayName = "conformance")]
    [MemberData(nameof(Ids))]
    public void Passes(string id)
    {
        _checks.Value[id]();
    }

    [Fact]
    public void HasCaseFiles()
    {
        Assert.NotEmpty(CaseFiles());
        Assert.Contains(_checks.Value.Keys, id => id.StartsWith("operators_regex::", StringComparison.Ordinal));
    }

    [Fact]
    public void HasAtLeast20BucketingVectors()
    {
        Assert.True(Vectors().Count >= 20);
    }

    [Theory]
    [InlineData("2")]
    [InlineData("1.0")]
    public void RefusesACaseFileWithAnotherSchemaVersion(string version)
    {
        var dir = Directory.CreateTempSubdirectory("megooflags-conformance-");
        try
        {
            var path = Path.Combine(dir.FullName, "case.json");
            File.WriteAllText(
                path,
                "{\"schema_version\": " + version + ", \"description\": \"\", \"ruleset\": {}, \"tests\": []}");
            var e = Assert.Throws<InvalidDataException>(() => LoadCaseFile(path));
            Assert.Equal("case.json: unsupported schema_version " + version, e.Message);
        }
        finally
        {
            dir.Delete(true);
        }
    }

    private static readonly (object? A, object? B, bool Want)[] _pairs =
    [
        (1L, 1.0, true),
        (true, 1L, false),
        ("1", 1L, false),
        (null, null, true),
        (null, false, false),
        (List(1L, Obj("a", true)), List(1.0, Obj("a", true)), true),
        (List(1L), List(1L, 2L), false),
        (Obj("a", 1L), Obj("a", 1L, "b", 2L), false),
        (Obj("a", 0L), Obj("a", false), false),
    ];

    public static TheoryData<int> Pairs() => new(Enumerable.Range(0, _pairs.Length));

    [Theory]
    [MemberData(nameof(Pairs))]
    public void SameJsonComparesLikeTheReferenceRunner(int index)
    {
        var (a, b, want) = _pairs[index];
        Assert.Equal(want, SameJson(a, b));
        Assert.Equal(want, SameJson(b, a));
    }

    [Fact]
    public void SameJsonDoesNotTakeAnEmptyObjectForAnEmptyArray()
    {
        Assert.False(SameJson(Obj(), List()));
        Assert.False(SameJson(List(), Obj()));
    }
}
