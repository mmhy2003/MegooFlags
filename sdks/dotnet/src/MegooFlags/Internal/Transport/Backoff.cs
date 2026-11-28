using System;
using System.Threading;
using System.Threading.Tasks;

namespace MegooFlags.Internal.Transport;

/// <summary>
/// Jittered exponential backoff. The first wait is min(base, max); the n-th is min(max, base * 2^n) scaled by a
/// factor in [0.8, 1.0]. The base can be replaced, for the stream's retry field. Not thread-safe: one worker owns it.
/// </summary>
internal sealed class Backoff
{
    private readonly TimeSpan _max;
    private readonly Func<double> _rand;
    private int _n;

    /// <summary>A backoff; rand returns a number in [0, 1] (tests pass a constant), by default a random one.</summary>
    public Backoff(TimeSpan baseDelay, TimeSpan max, Func<double>? rand = null)
    {
        Base = baseDelay;
        _max = max;
        _rand = rand ?? Random.Shared.NextDouble;
    }

    /// <summary>The first wait. Replacing it keeps the attempt counter.</summary>
    public TimeSpan Base { get; set; }

    /// <summary>Returns the next wait and advances the attempt counter.</summary>
    public TimeSpan Next()
    {
        if (_n == 0)
        {
            _n++;
            return Min(Base, _max);
        }
        // Double step by step so a large n cannot overflow.
        var d = Base;
        for (var i = 0; i < _n && d < _max; i++)
        {
            d *= 2;
        }
        d = Min(d, _max);
        if (_n < 64)
        {
            _n++;
        }
        return TimeSpan.FromTicks((long)Math.Round(d.Ticks * (0.8 + 0.2 * _rand())));
    }

    /// <summary>Makes the next wait the base again.</summary>
    public void Reset() => _n = 0;

    /// <summary>Waits for d and returns true, or returns false as soon as ct is cancelled.</summary>
    public static async Task<bool> SleepAsync(TimeSpan d, CancellationToken ct)
    {
        try
        {
            await Task.Delay(d, ct).ConfigureAwait(false);
            return true;
        }
        catch (OperationCanceledException)
        {
            return false;
        }
    }

    private static TimeSpan Min(TimeSpan a, TimeSpan b) => a <= b ? a : b;
}
