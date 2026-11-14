using System;
using System.Collections.Generic;

namespace MegooFlags.Internal.Engine;

/// <summary>Resolves flags of a <see cref="Ruleset"/> (evaluation.md). Pure: it takes no lock.</summary>
internal static class Evaluator
{
    /// <summary>
    /// The deepest prerequisite chain Evaluate follows; a deeper chain, or a cycle, is an error (evaluation.md §6).
    /// </summary>
    public const int MaxPrerequisiteDepth = 32;

    /// <summary>
    /// Evaluate resolves flagKey for context, which must be a <c>Dictionary&lt;string, object?&gt;</c>. Errors
    /// return defaultValue with reason ERROR. expectedType is "boolean", "string", "number" or "json", or null for
    /// no type check; the check applies only to a served variation.
    /// </summary>
    public static Result Evaluate(
        Ruleset ruleset,
        string flagKey,
        object? context,
        object? defaultValue,
        string? expectedType)
    {
        if (context is not Dictionary<string, object?> ctx)
        {
            return Error(defaultValue, ErrorCodes.InvalidContext, "context must be a JSON object");
        }
        if (!ruleset.Flags.TryGetValue(flagKey, out var flag))
        {
            return Error(defaultValue, ErrorCodes.FlagNotFound, "flag '" + flagKey + "' not found");
        }
        var res = EvalFlag(ruleset, flag, ctx, defaultValue, 0);
        if (res == null)
        {
            return Error(defaultValue, ErrorCodes.General, "prerequisite chain is too deep or has a cycle");
        }
        if (expectedType != null && res.Variant != null && !HasType(res.Value, expectedType))
        {
            return Error(defaultValue, ErrorCodes.TypeMismatch, "flag '" + flagKey + "' is not a " + expectedType);
        }
        return res;
    }

    /// <summary>EvalFlag returns null when the prerequisite chain is deeper than MaxPrerequisiteDepth.</summary>
    private static Result? EvalFlag(Ruleset rs, Flag flag, Dictionary<string, object?> ctx, object? def, int depth)
    {
        if (!flag.Enabled)
        {
            return ServeOff(flag, Reasons.Disabled, def, null);
        }
        foreach (var p in flag.Prerequisites)
        {
            var met = PrerequisiteMet(rs, p, ctx, depth + 1);
            if (met == null)
            {
                return null;
            }
            if (met == false)
            {
                return ServeOff(flag, Reasons.PrerequisiteFailed, def, p.Flag);
            }
        }
        if (ctx.TryGetValue("key", out var k) && k is string key)
        {
            foreach (var t in flag.Targets)
            {
                if (t.Values.Contains(key))
                {
                    return ServeVariation(flag, t.Variation, Reasons.TargetingMatch, def, null, null);
                }
            }
        }
        for (var i = 0; i < flag.Rules.Count; i++)
        {
            var rule = flag.Rules[i];
            if (AllClausesMatch(rs, rule.Clauses, ctx))
            {
                return ServeRule(flag, rule.Serve, ctx, Reasons.TargetingMatch, def, i);
            }
        }
        var reason = Reasons.Default;
        if (flag.Fallthrough.Rollout != null)
        {
            reason = Reasons.Split;
        }
        else if (flag.Targets.Count == 0 && flag.Rules.Count == 0 && flag.Prerequisites.Count == 0)
        {
            reason = Reasons.Static;
        }
        return ServeRule(flag, flag.Fallthrough, ctx, reason, def, null);
    }

    /// <summary>
    /// PrerequisiteMet reports whether p's flag serves p.Variation, or null when depth is over MaxPrerequisiteDepth.
    /// </summary>
    private static bool? PrerequisiteMet(Ruleset rs, Prerequisite p, Dictionary<string, object?> ctx, int depth)
    {
        if (depth > MaxPrerequisiteDepth)
        {
            return null;
        }
        if (!rs.Flags.TryGetValue(p.Flag, out var pf) || !pf.Enabled)
        {
            return false;
        }
        var res = EvalFlag(rs, pf, ctx, null, depth);
        if (res == null)
        {
            return null;
        }
        return res.Reason != Reasons.Error && p.Variation == res.Variant;
    }

    private static bool AllClausesMatch(Ruleset rs, IReadOnlyList<Clause> clauses, Dictionary<string, object?> ctx)
    {
        for (var i = 0; i < clauses.Count; i++)
        {
            if (!ClauseMatches(rs, clauses[i], ctx))
            {
                return false;
            }
        }
        return true;
    }

    private static bool ClauseMatches(Ruleset rs, Clause c, Dictionary<string, object?> ctx)
    {
        if (c.Op == ClauseOperator.InSegment)
        {
            var inAny = false;
            for (var i = 0; i < c.Prepared.Count; i++)
            {
                if (rs.Segments.TryGetValue((string)c.Prepared[i], out var s) && InSegment(rs, s, ctx))
                {
                    inAny = true;
                    break;
                }
            }
            return inAny != c.Negate;
        }
        if (!ctx.TryGetValue(c.Attribute, out var v) || v == null)
        {
            return false; // missing or null: false, negate not applied
        }
        var hit = false;
        if (v is IReadOnlyList<object?> items)
        {
            for (var i = 0; i < items.Count; i++)
            {
                if (MatchValue(c, items[i]))
                {
                    hit = true;
                    break;
                }
            }
        }
        else
        {
            hit = MatchValue(c, v);
        }
        return hit != c.Negate;
    }

    private static bool InSegment(Ruleset rs, Segment s, Dictionary<string, object?> ctx)
    {
        if (ctx.TryGetValue("key", out var k) && k is string key)
        {
            if (s.Excluded.Contains(key))
            {
                return false;
            }
            if (s.Included.Contains(key))
            {
                return true;
            }
        }
        foreach (var clauses in s.Rules)
        {
            if (AllClausesMatch(rs, clauses, ctx))
            {
                return true;
            }
        }
        return false;
    }

    /// <summary>
    /// MatchValue reports whether one element of the attribute matches the clause's prepared values (operators.py
    /// match_value).
    /// </summary>
    private static bool MatchValue(Clause c, object? e)
    {
        var prepared = c.Prepared;
        switch (c.Op)
        {
            case ClauseOperator.In:
                for (var i = 0; i < prepared.Count; i++)
                {
                    if (Values.JsonEqual(e, prepared[i]))
                    {
                        return true;
                    }
                }
                return false;
            case ClauseOperator.NotIn:
                if (!Values.IsScalar(e))
                {
                    return false;
                }
                for (var i = 0; i < prepared.Count; i++)
                {
                    if (Values.JsonEqual(e, prepared[i]))
                    {
                        return false;
                    }
                }
                return true;
            case ClauseOperator.Contains:
            case ClauseOperator.StartsWith:
            case ClauseOperator.EndsWith:
                if (e is not string text)
                {
                    return false;
                }
                for (var i = 0; i < prepared.Count; i++)
                {
                    if (StringMatch(c.Op, text, (string)prepared[i]))
                    {
                        return true;
                    }
                }
                return false;
            case ClauseOperator.Matches:
                if (e is not string subject)
                {
                    return false;
                }
                for (var i = 0; i < prepared.Count; i++)
                {
                    if (((PortableRegex)prepared[i]).Test(subject))
                    {
                        return true;
                    }
                }
                return false;
            case ClauseOperator.Lt:
            case ClauseOperator.Lte:
            case ClauseOperator.Gt:
            case ClauseOperator.Gte:
                if (!Values.IsNumber(e))
                {
                    return false;
                }
                for (var i = 0; i < prepared.Count; i++)
                {
                    var d = Values.CompareNumbers(e!, prepared[i]);
                    var ok = c.Op switch
                    {
                        ClauseOperator.Lt => d < 0,
                        ClauseOperator.Lte => d <= 0,
                        ClauseOperator.Gt => d > 0,
                        _ => d >= 0,
                    };
                    if (ok)
                    {
                        return true;
                    }
                }
                return false;
            case ClauseOperator.SemverEq:
            case ClauseOperator.SemverLt:
            case ClauseOperator.SemverGt:
                if (e is not string s || Semver.Parse(s) is not Semver version)
                {
                    return false;
                }
                var want = c.Op == ClauseOperator.SemverLt ? -1 : c.Op == ClauseOperator.SemverGt ? 1 : 0;
                for (var i = 0; i < prepared.Count; i++)
                {
                    if (Math.Sign(Semver.Compare(version, (Semver)prepared[i])) == want)
                    {
                        return true;
                    }
                }
                return false;
            case ClauseOperator.Before:
            case ClauseOperator.After:
                if (Dates.ToMillis(e) is not long t)
                {
                    return false;
                }
                for (var i = 0; i < prepared.Count; i++)
                {
                    var ms = (long)prepared[i];
                    if (c.Op == ClauseOperator.Before ? t < ms : t > ms)
                    {
                        return true;
                    }
                }
                return false;
            default:
                return false; // in_segment is handled by ClauseMatches
        }
    }

    /// <summary>
    /// StringMatch is contains, starts_with or ends_with by code point: a match that would split a surrogate pair of
    /// s does not count.
    /// </summary>
    private static bool StringMatch(ClauseOperator op, string s, string p)
    {
        var n = p.Length;
        switch (op)
        {
            case ClauseOperator.StartsWith:
                return s.StartsWith(p, StringComparison.Ordinal) && !SplitsPair(s, n);
            case ClauseOperator.EndsWith:
                return s.EndsWith(p, StringComparison.Ordinal) && !SplitsPair(s, s.Length - n);
            default:
                for (var i = s.IndexOf(p, StringComparison.Ordinal);
                     i >= 0;
                     i = i + 1 > s.Length ? -1 : s.IndexOf(p, i + 1, StringComparison.Ordinal))
                {
                    if (!SplitsPair(s, i) && !SplitsPair(s, i + n))
                    {
                        return true;
                    }
                }
                return false;
        }
    }

    /// <summary>SplitsPair reports whether index i of s falls between the two halves of a surrogate pair.</summary>
    private static bool SplitsPair(string s, int i) =>
        i > 0 && i < s.Length && char.IsHighSurrogate(s[i - 1]) && char.IsLowSurrogate(s[i]);

    /// <summary>HasType reports whether a served value has the OpenFeature type asked for.</summary>
    private static bool HasType(object? v, string expectedType) => expectedType switch
    {
        "boolean" => v is bool,
        "string" => v is string,
        "number" => Values.IsNumber(v), // a bool is not a number
        _ => true, // json accepts anything, null included
    };

    private static Result ServeOff(Flag flag, string reason, object? def, string? prerequisite)
    {
        if (flag.OffVariation == null)
        {
            return new Result(def, null, reason, null, "", null, prerequisite);
        }
        return ServeVariation(flag, flag.OffVariation, reason, def, null, prerequisite);
    }

    private static Result ServeVariation(
        Flag flag,
        string? variation,
        string reason,
        object? def,
        int? ruleIndex,
        string? prerequisite)
    {
        // The parser guarantees known variations; this keeps a bad Ruleset from throwing.
        if (variation == null || !flag.TryGetVariation(variation, out var v))
        {
            return new Result(def, null, reason, null, "", ruleIndex, prerequisite);
        }
        return new Result(v.Value, variation, reason, null, "", ruleIndex, prerequisite);
    }

    private static Result ServeRule(
        Flag flag,
        Serve s,
        Dictionary<string, object?> ctx,
        string reason,
        object? def,
        int? ruleIndex)
    {
        if (s.Rollout == null)
        {
            return ServeVariation(flag, s.Variation, reason, def, ruleIndex, null);
        }
        var bucket = 0;
        ctx.TryGetValue(s.Rollout.BucketBy, out var attribute);
        if (Bucketing.BucketText(attribute) is string text)
        {
            bucket = Bucketing.Bucket(flag.Key, text);
        }
        return ServeVariation(flag, Bucketing.PickVariation(s.Rollout.Weights, bucket), reason, def, ruleIndex, null);
    }

    private static Result Error(object? def, string code, string message) =>
        new(def, null, Reasons.Error, code, message, null, null);
}
