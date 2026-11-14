using System.Collections.Generic;

namespace MegooFlags.Internal.Engine;

/// <summary>The reasons of a <see cref="Result"/> (evaluation.md).</summary>
internal static class Reasons
{
    public const string Static = "STATIC";
    public const string Default = "DEFAULT";
    public const string TargetingMatch = "TARGETING_MATCH";
    public const string Split = "SPLIT";
    public const string Disabled = "DISABLED";
    public const string PrerequisiteFailed = "PREREQUISITE_FAILED";
    public const string Error = "ERROR";
}

/// <summary>The error codes of a <see cref="Result"/> with reason ERROR.</summary>
internal static class ErrorCodes
{
    public const string FlagNotFound = "FLAG_NOT_FOUND";
    public const string TypeMismatch = "TYPE_MISMATCH";
    public const string ParseError = "PARSE_ERROR";
    public const string InvalidContext = "INVALID_CONTEXT";
    public const string General = "GENERAL";
}

/// <summary>
/// The resolution of one flag (evaluation.md).
/// </summary>
/// <param name="Value">The served variation's value, or the default when no variation was served.</param>
/// <param name="Variant">The served variation, or null when the default was returned.</param>
/// <param name="Reason">One of <see cref="Reasons"/>.</param>
/// <param name="ErrorCode">One of <see cref="ErrorCodes"/>; null unless the reason is ERROR.</param>
/// <param name="ErrorMessage">Empty unless the reason is ERROR.</param>
/// <param name="RuleIndex">The matching rule, or null.</param>
/// <param name="Prerequisite">The failed prerequisite flag, or null.</param>
internal sealed record Result(
    object? Value,
    string? Variant,
    string Reason,
    string? ErrorCode,
    string ErrorMessage,
    int? RuleIndex,
    string? Prerequisite)
{
    /// <summary>
    /// Metadata is {"rule_index": n} and {"prerequisite": key} when set, empty otherwise. It is built on each call,
    /// so the evaluation itself does not allocate it.
    /// </summary>
    public Dictionary<string, object?> Metadata()
    {
        var m = new Dictionary<string, object?>();
        if (RuleIndex is int i)
        {
            m["rule_index"] = i;
        }
        if (Prerequisite != null)
        {
            m["prerequisite"] = Prerequisite;
        }
        return m;
    }
}
