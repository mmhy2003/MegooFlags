using System.Collections.Generic;

namespace MegooFlags.Tests;

/// <summary>Ruleset builders for the engine tests and the p99 guard. Every dictionary and list is mutable.</summary>
internal static class RulesetGen
{
    private const int BenchSegments = 50;

    /// <summary>Obj is a mutable JSON object of alternating keys and values.</summary>
    public static Dictionary<string, object?> Obj(params object?[] keysAndValues)
    {
        var m = new Dictionary<string, object?>();
        for (var i = 0; i < keysAndValues.Length; i += 2)
        {
            m[(string)keysAndValues[i]!] = keysAndValues[i + 1];
        }
        return m;
    }

    /// <summary>List is a mutable JSON array.</summary>
    public static List<object?> List(params object?[] items) => new(items);

    /// <summary>
    /// FlagJson is a minimal enabled string flag with variations "on" and "off", off_variation "off" and fallthrough
    /// "on". fields (alternating keys and values) override or add fields.
    /// </summary>
    public static Dictionary<string, object?> FlagJson(string key, params object?[] fields)
    {
        var f = Obj(
            "key", key,
            "type", "string",
            "enabled", true,
            "version", 1L,
            "variations", List(Obj("key", "on", "value", "on"), Obj("key", "off", "value", "off")),
            "off_variation", "off",
            "prerequisites", List(),
            "targets", List(),
            "rules", List(),
            "fallthrough", Obj("variation", "on"));
        foreach (var kv in Obj(fields))
        {
            f[kv.Key] = kv.Value;
        }
        return f;
    }

    /// <summary>RuleFlag has one rule of clauses serving "on", and fallthrough "off".</summary>
    public static Dictionary<string, object?> RuleFlag(string key, params object?[] clauses) =>
        FlagJson(
            key,
            "rules", List(Obj("clauses", List(clauses), "serve", Obj("variation", "on"))),
            "fallthrough", Obj("variation", "off"));

    /// <summary>Cl is a clause.</summary>
    public static Dictionary<string, object?> Cl(string attribute, string op, bool negate, params object?[] values) =>
        Obj("attribute", attribute, "operator", op, "negate", negate, "values", List(values));

    /// <summary>RulesetJson wraps flags (keyed by their key) and segments in a ruleset.</summary>
    public static Dictionary<string, object?> RulesetJson(
        IEnumerable<Dictionary<string, object?>> flags,
        Dictionary<string, object?> segments)
    {
        var byKey = new Dictionary<string, object?>();
        foreach (var f in flags)
        {
            byKey[(string)f["key"]!] = f;
        }
        return Obj("schema_version", 1L, "project", "p", "environment", "e", "flags", byKey, "segments", segments);
    }

    /// <summary>FlagKeys is flag-0 to flag-(n-1).</summary>
    public static List<string> FlagKeys(int n)
    {
        var keys = new List<string>(n);
        for (var i = 0; i < n; i++)
        {
            keys.Add("flag-" + i);
        }
        return keys;
    }

    /// <summary>
    /// GenRuleset is a deterministic ruleset of n flags and 50 segments. Even flags are booleans with three rules
    /// (in, matches, semver_gt), a 100-key target list and a three-way fallthrough rollout; odd flags are strings
    /// with an in_segment rule and a before rule. Every 10th flag has a prerequisite on the flag before it.
    /// </summary>
    public static Dictionary<string, object?> GenRuleset(int n)
    {
        var plans = new object?[20];
        for (var i = 0; i < plans.Length; i++)
        {
            plans[i] = "plan-" + i;
        }
        var flags = new List<Dictionary<string, object?>>(n);
        for (var i = 0; i < n; i++)
        {
            var key = "flag-" + i;
            Dictionary<string, object?> f;
            if (i % 2 == 0)
            {
                var targets = List();
                for (var k = 0; k < 100; k++)
                {
                    targets.Add("user-" + (i + k * 37) % 1000);
                }
                f = FlagJson(
                    key,
                    "type", "boolean",
                    "variations", List(
                        Obj("key", "on", "value", true),
                        Obj("key", "off", "value", false),
                        Obj("key", "alt", "value", false)),
                    "targets", List(Obj("variation", "alt", "values", targets)),
                    "rules", List(
                        BenchRule("on", Cl("plan", "in", false, plans)),
                        BenchRule("off", Cl("email", "matches", false, "^user-[0-9]+$")),
                        BenchRule("alt", Cl("app_version", "semver_gt", false, "1.5.0"))),
                    "fallthrough", Obj("rollout", Obj(
                        "bucket_by", "key",
                        "weights", List(
                            Obj("variation", "on", "weight", 33334L),
                            Obj("variation", "off", "weight", 33333L),
                            Obj("variation", "alt", "weight", 33333L)))));
            }
            else
            {
                f = FlagJson(
                    key,
                    "variations", List(
                        Obj("key", "on", "value", "on"),
                        Obj("key", "off", "value", "off"),
                        Obj("key", "alt", "value", "alt")),
                    "rules", List(
                        BenchRule("alt", Cl("key", "in_segment", false, "segment-" + i % BenchSegments)),
                        BenchRule("off", Cl("signed_up", "before", false, "2025-01-01T00:00:00Z"))));
            }
            if (i > 0 && i % 10 == 0)
            {
                f["prerequisites"] = List(Obj("flag", "flag-" + (i - 1), "variation", "on"));
            }
            flags.Add(f);
        }
        var segments = new Dictionary<string, object?>();
        for (var s = 0; s < BenchSegments; s++)
        {
            var included = List();
            for (var k = 0; k < 1000; k++)
            {
                included.Add("user-" + (s * 1000 + k));
            }
            segments["segment-" + s] = Obj(
                "included", included,
                "excluded", List(),
                "rules", List(Obj("clauses", List(Cl("org", "in", false, "org-" + s)))));
        }
        var rs = RulesetJson(flags, segments);
        rs["project"] = "bench";
        rs["environment"] = "bench";
        return rs;
    }

    /// <summary>BenchContexts are 100 contexts that hit every rule shape of <see cref="GenRuleset"/>.</summary>
    public static List<Dictionary<string, object?>> BenchContexts()
    {
        var ctxs = new List<Dictionary<string, object?>>(100);
        for (var j = 0; j < 100; j++)
        {
            ctxs.Add(Obj(
                "key", "user-" + j * 503,
                "plan", "plan-" + j % 40,
                "email", j % 3 == 0 ? "user-" + j : "user" + j + "@example.com",
                "app_version", "1." + j % 10 + ".0",
                "org", "org-" + j % 100));
        }
        return ctxs;
    }

    private static Dictionary<string, object?> BenchRule(string variation, Dictionary<string, object?> clause) =>
        Obj("description", "", "clauses", List(clause), "serve", Obj("variation", variation));
}
