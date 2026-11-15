using System;
using System.Collections.Frozen;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using MegooFlags.Internal.Json;

namespace MegooFlags.Internal.Engine;

/// <summary>
/// Validates a ruleset as in evaluation.md §11 and prepares its clause values. Unknown fields are ignored.
/// Prerequisite flags and segment keys are not checked: a missing flag fails the prerequisite and a missing segment
/// is never matched.
/// </summary>
internal sealed class RulesetParser
{
    /// <summary>The only ruleset schema_version this engine accepts.</summary>
    public const long SchemaVersion = 1;

    private static readonly string[] _flagTypes = ["boolean", "json", "number", "string"];

    private readonly List<string> _warnings = [];

    private RulesetParser()
    {
    }

    /// <summary>Parse parses JSON text; invalid JSON is a <see cref="ParseException"/> with path "ruleset".</summary>
    public static Ruleset Parse(string json)
    {
        object? tree;
        try
        {
            tree = JsonTree.Parse(json);
        }
        catch (System.Text.Json.JsonException e)
        {
            throw new ParseException("ruleset", "invalid JSON: " + e.Message);
        }
        return Parse(tree);
    }

    /// <summary>
    /// Parse validates a decoded tree as <see cref="JsonTree.Parse"/> returns it: objects are
    /// <c>Dictionary&lt;string, object?&gt;</c>, arrays <c>List&lt;object?&gt;</c>, an integer is a long (a double
    /// such as 1.0 is not an integer).
    /// </summary>
    public static Ruleset Parse(object? tree) => new RulesetParser().ParseRuleset(tree);

    private Ruleset ParseRuleset(object? data)
    {
        var root = AsObject(data, "ruleset");
        var version = AsInt(Get(root, "schema_version", ""), "schema_version");
        if (version != SchemaVersion)
        {
            throw new ParseException("schema_version", "must be " + SchemaVersion);
        }
        var project = AsString(Get(root, "project", ""), "project");
        var environment = AsString(Get(root, "environment", ""), "environment");
        var flags = AsObject(Get(root, "flags", ""), "flags");
        var segments = AsObject(Get(root, "segments", ""), "segments");
        var parsedFlags = new Dictionary<string, Flag>(flags.Count, StringComparer.Ordinal);
        // Sorted, so the first problem reported is deterministic.
        foreach (var key in flags.Keys.Order(StringComparer.Ordinal))
        {
            parsedFlags[key] = ParseFlag(key, flags[key], "flags." + key);
        }
        var parsedSegments = new Dictionary<string, Segment>(segments.Count, StringComparer.Ordinal);
        foreach (var key in segments.Keys.Order(StringComparer.Ordinal))
        {
            parsedSegments[key] = ParseSegment(segments[key], "segments." + key);
        }
        return new Ruleset(
            project,
            environment,
            parsedFlags.ToFrozenDictionary(StringComparer.Ordinal),
            parsedSegments.ToFrozenDictionary(StringComparer.Ordinal),
            _warnings.ToArray().AsReadOnly());
    }

    private Flag ParseFlag(string key, object? value, string path)
    {
        var obj = AsObject(value, path);
        if (Get(obj, "key", path) is not string k || k != key)
        {
            throw new ParseException(path + ".key", "must equal '" + key + "'");
        }
        var type = AsString(Get(obj, "type", path), path + ".type");
        if (Array.IndexOf(_flagTypes, type) < 0)
        {
            throw new ParseException(path + ".type", "must be one of " + string.Join(", ", _flagTypes));
        }
        var variations = ParseVariations(Get(obj, "variations", path), path + ".variations");
        var keys = new HashSet<string>(variations.Select(v => v.Key), StringComparer.Ordinal);
        var off = Get(obj, "off_variation", path);
        var offVariation = off == null ? null : Known(off, keys, path + ".off_variation");
        var prerequisites = AsList(Get(obj, "prerequisites", path), path + ".prerequisites", false);
        var targets = AsList(Get(obj, "targets", path), path + ".targets", false);
        var rules = AsList(Get(obj, "rules", path), path + ".rules", false);
        var enabled = AsBool(Get(obj, "enabled", path), path + ".enabled");
        var version = AsInt(Get(obj, "version", path), path + ".version");

        var parsedPrerequisites = new Prerequisite[prerequisites.Count];
        for (var i = 0; i < prerequisites.Count; i++)
        {
            var itemPath = path + ".prerequisites[" + i + "]";
            var p = AsObject(prerequisites[i], itemPath);
            parsedPrerequisites[i] = new Prerequisite(
                AsString(Get(p, "flag", itemPath), itemPath + ".flag"),
                AsString(Get(p, "variation", itemPath), itemPath + ".variation"));
        }
        var parsedTargets = new Target[targets.Count];
        for (var i = 0; i < targets.Count; i++)
        {
            var itemPath = path + ".targets[" + i + "]";
            var t = AsObject(targets[i], itemPath);
            parsedTargets[i] = new Target(
                Known(Get(t, "variation", itemPath), keys, itemPath + ".variation"),
                StringSet(Get(t, "values", itemPath), itemPath + ".values"));
        }
        var parsedRules = new Rule[rules.Count];
        for (var i = 0; i < rules.Count; i++)
        {
            parsedRules[i] = ParseRule(rules[i], keys, path + ".rules[" + i + "]");
        }
        var fallthrough = ParseServe(Get(obj, "fallthrough", path), keys, path + ".fallthrough");
        return new Flag(
            key,
            type,
            enabled,
            version,
            variations,
            offVariation,
            parsedPrerequisites.AsReadOnly(),
            parsedTargets.AsReadOnly(),
            parsedRules.AsReadOnly(),
            fallthrough);
    }

    private static ReadOnlyCollection<Variation> ParseVariations(object? value, string path)
    {
        var items = AsList(value, path, true);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var variations = new Variation[items.Count];
        for (var i = 0; i < items.Count; i++)
        {
            var itemPath = path + "[" + i + "]";
            var obj = AsObject(items[i], itemPath);
            var key = AsString(Get(obj, "key", itemPath), itemPath + ".key");
            if (!seen.Add(key))
            {
                throw new ParseException(itemPath + ".key", "duplicate variation '" + key + "'");
            }
            var raw = Get(obj, "value", itemPath);
            var integral = Integral(raw);
            variations[i] = new Variation(key, Freeze(raw), integral.HasValue, integral ?? 0);
        }
        return variations.AsReadOnly();
    }

    /// <summary>Freeze is a deep copy of a JSON value with read-only dictionaries and lists.</summary>
    private static object? Freeze(object? v)
    {
        switch (v)
        {
            case Dictionary<string, object?> map:
                var obj = new Dictionary<string, object?>(map.Count, StringComparer.Ordinal);
                foreach (var kv in map)
                {
                    obj[kv.Key] = Freeze(kv.Value);
                }
                return new ReadOnlyDictionary<string, object?>(obj);
            case List<object?> list:
                var items = new object?[list.Count];
                for (var i = 0; i < items.Length; i++)
                {
                    items[i] = Freeze(list[i]);
                }
                return items.AsReadOnly();
            default:
                return v;
        }
    }

    /// <summary>
    /// Integral is the exact long of a number with an integral value: an integer, or a double with an integral value
    /// in the long range. Anything else is null.
    /// </summary>
    private static long? Integral(object? raw)
    {
        if (Values.IsInteger(raw))
        {
            return Values.ToLong(raw!);
        }
        if (raw is double or float)
        {
            var d = Values.ToDouble(raw);
            // -2^63 and 2^63 are exact doubles; NaN fails the first test, the infinities the second.
            if (d == Math.Floor(d) && d >= -9223372036854775808.0 && d < 9223372036854775808.0)
            {
                return (long)d;
            }
        }
        return null;
    }

    private Rule ParseRule(object? value, HashSet<string> variations, string path)
    {
        var obj = AsObject(value, path);
        var description = "";
        if (obj.TryGetValue("description", out var d))
        {
            description = AsString(d, path + ".description");
        }
        var clauses = ParseClauses(Get(obj, "clauses", path), path + ".clauses", false);
        return new Rule(description, clauses, ParseServe(Get(obj, "serve", path), variations, path + ".serve"));
    }

    private static Serve ParseServe(object? value, HashSet<string> variations, string path)
    {
        var obj = AsObject(value, path);
        var hasVariation = obj.TryGetValue("variation", out var variation);
        var hasRollout = obj.TryGetValue("rollout", out var rollout);
        if (hasVariation == hasRollout)
        {
            throw new ParseException(path, "must have exactly one of variation or rollout");
        }
        if (hasVariation)
        {
            return new Serve(Known(variation, variations, path + ".variation"), null);
        }
        return new Serve(null, ParseRollout(rollout, variations, path + ".rollout"));
    }

    private static Rollout ParseRollout(object? value, HashSet<string> variations, string path)
    {
        var obj = AsObject(value, path);
        var bucketBy = AsString(Get(obj, "bucket_by", path), path + ".bucket_by");
        var weightsPath = path + ".weights";
        var items = AsList(Get(obj, "weights", path), weightsPath, true);
        var weights = new WeightedVariation[items.Count];
        long sum = 0;
        for (var i = 0; i < items.Count; i++)
        {
            var itemPath = weightsPath + "[" + i + "]";
            var w = AsObject(items[i], itemPath);
            var variation = Known(Get(w, "variation", itemPath), variations, itemPath + ".variation");
            var weight = AsInt(Get(w, "weight", itemPath), itemPath + ".weight");
            if (weight < 0 || weight > Bucketing.Buckets)
            {
                throw new ParseException(itemPath + ".weight", "must be between 0 and " + Bucketing.Buckets);
            }
            weights[i] = new WeightedVariation(variation, (int)weight);
            sum += weight;
        }
        if (sum != Bucketing.Buckets)
        {
            throw new ParseException(weightsPath, "must add up to " + Bucketing.Buckets);
        }
        return new Rollout(bucketBy, weights.AsReadOnly());
    }

    private ReadOnlyCollection<Clause> ParseClauses(object? value, string path, bool inSegment)
    {
        var items = AsList(value, path, true);
        var clauses = new Clause[items.Count];
        for (var i = 0; i < items.Count; i++)
        {
            clauses[i] = ParseClause(items[i], path + "[" + i + "]", inSegment);
        }
        return clauses.AsReadOnly();
    }

    private Clause ParseClause(object? value, string path, bool inSegment)
    {
        var obj = AsObject(value, path);
        var attribute = AsString(Get(obj, "attribute", path), path + ".attribute");
        var name = AsString(Get(obj, "operator", path), path + ".operator");
        if (!ClauseOperators.TryParse(name, out var op))
        {
            throw new ParseException(path + ".operator", "unknown operator " + JsonTree.Write(name));
        }
        if (inSegment && op == ClauseOperator.InSegment)
        {
            throw new ParseException(path + ".operator", "in_segment is not allowed in a segment rule");
        }
        var valuesPath = path + ".values";
        var raw = AsList(Get(obj, "values", path), valuesPath, true);
        var values = new object[raw.Count];
        for (var i = 0; i < raw.Count; i++)
        {
            if (!Values.IsScalar(raw[i]))
            {
                throw new ParseException(valuesPath + "[" + i + "]", "must be a string, number or boolean");
            }
            values[i] = raw[i]!;
        }
        var negate = false;
        if (obj.TryGetValue("negate", out var n))
        {
            negate = AsBool(n, path + ".negate");
        }
        return new Clause(attribute, op, negate, values.AsReadOnly(), Prepare(op, values, valuesPath));
    }

    /// <summary>Prepare keeps the values op can use, converted for it, and leaves out the others.</summary>
    private ReadOnlyCollection<object> Prepare(ClauseOperator op, object[] values, string valuesPath)
    {
        var output = new List<object>(values.Length);
        for (var i = 0; i < values.Length; i++)
        {
            var v = values[i];
            switch (op)
            {
                case ClauseOperator.In:
                case ClauseOperator.NotIn:
                    output.Add(v);
                    break;
                case ClauseOperator.Contains:
                case ClauseOperator.StartsWith:
                case ClauseOperator.EndsWith:
                case ClauseOperator.InSegment:
                    if (v is string)
                    {
                        output.Add(v);
                    }
                    break;
                case ClauseOperator.Matches:
                    if (v is string pattern)
                    {
                        if (PortableRegex.TryCompile(pattern, out var regex, out var error))
                        {
                            output.Add(regex);
                        }
                        else
                        {
                            _warnings.Add(valuesPath + "[" + i + "]: regex does not compile: " + error);
                        }
                    }
                    break;
                case ClauseOperator.Lt:
                case ClauseOperator.Lte:
                case ClauseOperator.Gt:
                case ClauseOperator.Gte:
                    if (Values.IsNumber(v))
                    {
                        output.Add(v);
                    }
                    break;
                case ClauseOperator.SemverEq:
                case ClauseOperator.SemverLt:
                case ClauseOperator.SemverGt:
                    if (v is string s && Semver.Parse(s) is Semver version)
                    {
                        output.Add(version);
                    }
                    break;
                case ClauseOperator.Before:
                case ClauseOperator.After:
                    // Only a string is a date here; a number value never matches.
                    if (v is string date && Dates.ParseRfc3339Millis(date) is long ms)
                    {
                        output.Add(ms);
                    }
                    break;
                default:
                    throw new InvalidOperationException("operator " + op);
            }
        }
        return output.ToArray().AsReadOnly();
    }

    private Segment ParseSegment(object? value, string path)
    {
        var obj = AsObject(value, path);
        var rulesPath = path + ".rules";
        var rules = AsList(Get(obj, "rules", path), rulesPath, false);
        var included = StringSet(Get(obj, "included", path), path + ".included");
        var excluded = StringSet(Get(obj, "excluded", path), path + ".excluded");
        var parsedRules = new IReadOnlyList<Clause>[rules.Count];
        for (var i = 0; i < rules.Count; i++)
        {
            var rulePath = rulesPath + "[" + i + "]";
            var rule = AsObject(rules[i], rulePath);
            parsedRules[i] = ParseClauses(Get(rule, "clauses", rulePath), rulePath + ".clauses", true);
        }
        return new Segment(included, excluded, parsedRules.AsReadOnly());
    }

    /// <summary>Get returns obj[name], failing with "&lt;path&gt;.&lt;name&gt;: is required" when absent.</summary>
    private static object? Get(Dictionary<string, object?> obj, string name, string path)
    {
        if (!obj.TryGetValue(name, out var v))
        {
            throw new ParseException(path.Length == 0 ? name : path + "." + name, "is required");
        }
        return v;
    }

    private static Dictionary<string, object?> AsObject(object? v, string path) =>
        v as Dictionary<string, object?> ?? throw new ParseException(path, "must be an object");

    private static List<object?> AsList(object? v, string path, bool nonEmpty)
    {
        if (v is not List<object?> list)
        {
            throw new ParseException(path, "must be an array");
        }
        if (nonEmpty && list.Count == 0)
        {
            throw new ParseException(path, "must not be empty");
        }
        return list;
    }

    private static string AsString(object? v, string path) =>
        v as string ?? throw new ParseException(path, "must be a string");

    private static bool AsBool(object? v, string path) =>
        v is bool b ? b : throw new ParseException(path, "must be a boolean");

    /// <summary>AsInt requires an integer: a double such as 1.0 is not one, nor is a boolean.</summary>
    private static long AsInt(object? v, string path) =>
        Values.IsInteger(v) ? Values.ToLong(v!) : throw new ParseException(path, "must be an integer");

    private static FrozenSet<string> StringSet(object? v, string path)
    {
        var items = AsList(v, path, false);
        var set = new HashSet<string>(items.Count, StringComparer.Ordinal);
        for (var i = 0; i < items.Count; i++)
        {
            set.Add(AsString(items[i], path + "[" + i + "]"));
        }
        return set.ToFrozenSet(StringComparer.Ordinal);
    }

    private static string Known(object? v, HashSet<string> variations, string path)
    {
        var key = AsString(v, path);
        if (!variations.Contains(key))
        {
            throw new ParseException(path, "unknown variation '" + key + "'");
        }
        return key;
    }
}
