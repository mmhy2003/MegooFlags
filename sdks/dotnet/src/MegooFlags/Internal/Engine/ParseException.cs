using System;

namespace MegooFlags.Internal.Engine;

/// <summary>An invalid ruleset. The message is <c>&lt;json path&gt;: &lt;reason&gt;</c> for the first problem.</summary>
internal sealed class ParseException : Exception
{
    public ParseException(string path, string reason) : base(path + ": " + reason)
    {
        Path = path;
        Reason = reason;
    }

    /// <summary>Path is the JSON path of the problem, for example <c>flags.f.rules[0].serve</c>.</summary>
    public string Path { get; }

    public string Reason { get; }
}
