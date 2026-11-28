using System;
using MegooFlags.Internal.Engine;

namespace MegooFlags.Internal.Delivery;

/// <summary>Where a ruleset came from.</summary>
internal enum SnapshotSource
{
    Bootstrap,
    Cache,
    Stream,
    Poll,
}

/// <summary>Names of <see cref="SnapshotSource"/> values.</summary>
internal static class SnapshotSources
{
    /// <summary>The lowercase name: bootstrap, cache, stream or poll.</summary>
    public static string Name(this SnapshotSource source) => source switch
    {
        SnapshotSource.Bootstrap => "bootstrap",
        SnapshotSource.Cache => "cache",
        SnapshotSource.Stream => "stream",
        SnapshotSource.Poll => "poll",
        _ => throw new ArgumentOutOfRangeException(nameof(source)),
    };
}

/// <summary>One parsed ruleset with the ETag ("" when the source gave none) and the source it came from.</summary>
internal sealed record Snapshot(Ruleset Ruleset, string ETag, SnapshotSource Source);
