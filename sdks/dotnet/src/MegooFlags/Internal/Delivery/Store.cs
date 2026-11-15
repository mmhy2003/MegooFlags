using System.Threading;

namespace MegooFlags.Internal.Delivery;

/// <summary>Holds the current snapshot. Readers never lock.</summary>
internal sealed class Store
{
    private Snapshot? _current;

    /// <summary>The current snapshot, or null before the first swap.</summary>
    public Snapshot? Load() => Volatile.Read(ref _current);

    /// <summary>Installs next and returns the snapshot it replaced (null the first time).</summary>
    public Snapshot? Swap(Snapshot next) => Interlocked.Exchange(ref _current, next);
}
