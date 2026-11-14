using System.Collections.Frozen;
using System.Collections.Generic;
using System.Diagnostics.CodeAnalysis;

namespace MegooFlags.Internal.Engine;

/// <summary>
/// A parsed, validated environment ruleset (spec/ruleset.schema.json). Every model object is immutable once
/// <see cref="RulesetParser"/> returns it, so a Ruleset is safe for concurrent use. Keys are compared ordinally.
/// Warnings are problems that do not make the ruleset invalid, such as a pattern that does not compile.
/// </summary>
internal sealed record Ruleset(
    string Project,
    string Environment,
    IReadOnlyDictionary<string, Flag> Flags,
    IReadOnlyDictionary<string, Segment> Segments,
    IReadOnlyList<string> Warnings);

/// <summary>
/// One flag of a ruleset. Variations are in the order of the ruleset (look one up with
/// <see cref="TryGetVariation"/>); OffVariation is null when off_variation is null.
/// </summary>
internal sealed record Flag(
    string Key,
    string Type,
    bool Enabled,
    long Version,
    IReadOnlyList<Variation> Variations,
    string? OffVariation,
    IReadOnlyList<Prerequisite> Prerequisites,
    IReadOnlyList<Target> Targets,
    IReadOnlyList<Rule> Rules,
    Serve Fallthrough)
{
    private readonly FrozenDictionary<string, Variation> _byKey = Index(Variations);

    /// <summary>TryGetVariation finds the variation called key.</summary>
    public bool TryGetVariation(string key, [NotNullWhen(true)] out Variation? variation) =>
        _byKey.TryGetValue(key, out variation);

    private static FrozenDictionary<string, Variation> Index(IReadOnlyList<Variation> variations)
    {
        var d = new Dictionary<string, Variation>(variations.Count, System.StringComparer.Ordinal);
        foreach (var v in variations)
        {
            d[v.Key] = v;
        }
        return d.ToFrozenDictionary(System.StringComparer.Ordinal);
    }
}

/// <summary>
/// One possible value of a flag. Value is plain JSON (read-only dictionaries and lists); IsInt and IntValue keep the
/// exact value of a number with an integral value that fits a long.
/// </summary>
internal sealed record Variation(string Key, object? Value, bool IsInt, long IntValue);

/// <summary>Requires flag Flag to serve variation Variation.</summary>
internal sealed record Prerequisite(string Flag, string Variation);

/// <summary>Serves Variation to the context keys in Values.</summary>
internal sealed record Target(string Variation, IReadOnlySet<string> Values);

/// <summary>Serves Serve when all of its clauses match.</summary>
internal sealed record Rule(string Description, IReadOnlyList<Clause> Clauses, Serve Serve);

/// <summary>Either a fixed variation or a rollout: exactly one is not null.</summary>
internal sealed record Serve(string? Variation, Rollout? Rollout);

/// <summary>Splits contexts by the bucket of attribute BucketBy.</summary>
internal sealed record Rollout(string BucketBy, IReadOnlyList<WeightedVariation> Weights);

/// <summary>
/// Matches a context that is included, or that is not excluded and matches any rule (each rule is a list of clauses
/// that must all match).
/// </summary>
internal sealed record Segment(
    IReadOnlySet<string> Included,
    IReadOnlySet<string> Excluded,
    IReadOnlyList<IReadOnlyList<Clause>> Rules);

/// <summary>The clause operators.</summary>
internal enum ClauseOperator
{
    In,
    NotIn,
    Contains,
    StartsWith,
    EndsWith,
    Matches,
    Lt,
    Lte,
    Gt,
    Gte,
    SemverEq,
    SemverLt,
    SemverGt,
    Before,
    After,
    InSegment,
}

/// <summary>The ruleset names of the <see cref="ClauseOperator"/> values.</summary>
internal static class ClauseOperators
{
    private static readonly FrozenDictionary<string, ClauseOperator> _byName = new Dictionary<string, ClauseOperator>
    {
        ["in"] = ClauseOperator.In,
        ["not_in"] = ClauseOperator.NotIn,
        ["contains"] = ClauseOperator.Contains,
        ["starts_with"] = ClauseOperator.StartsWith,
        ["ends_with"] = ClauseOperator.EndsWith,
        ["matches"] = ClauseOperator.Matches,
        ["lt"] = ClauseOperator.Lt,
        ["lte"] = ClauseOperator.Lte,
        ["gt"] = ClauseOperator.Gt,
        ["gte"] = ClauseOperator.Gte,
        ["semver_eq"] = ClauseOperator.SemverEq,
        ["semver_lt"] = ClauseOperator.SemverLt,
        ["semver_gt"] = ClauseOperator.SemverGt,
        ["before"] = ClauseOperator.Before,
        ["after"] = ClauseOperator.After,
        ["in_segment"] = ClauseOperator.InSegment,
    }.ToFrozenDictionary(System.StringComparer.Ordinal);

    private static readonly FrozenDictionary<ClauseOperator, string> _names =
        _byName.ToFrozenDictionary(kv => kv.Value, kv => kv.Key);

    /// <summary>TryParse finds the operator called name.</summary>
    public static bool TryParse(string name, out ClauseOperator op) => _byName.TryGetValue(name, out op);

    /// <summary>Name is the ruleset name of op.</summary>
    public static string Name(ClauseOperator op) => _names[op];
}

/// <summary>
/// Tests one context attribute with an operator. Values are the scalars of the ruleset: string, bool, long or double.
/// Prepared are the values the operator can use, the others left out (evaluation.md §11): every value for in and
/// not_in; string for contains, starts_with, ends_with and in_segment; <see cref="PortableRegex"/> for matches; long
/// or double for lt, lte, gt and gte; <see cref="Semver"/> for semver_*; long epoch milliseconds for before and after.
/// </summary>
internal sealed record Clause(
    string Attribute,
    ClauseOperator Op,
    bool Negate,
    IReadOnlyList<object> Values,
    IReadOnlyList<object> Prepared)
{
    /// <summary>Operator is the ruleset name of <see cref="Op"/>.</summary>
    public string Operator => ClauseOperators.Name(Op);
}
