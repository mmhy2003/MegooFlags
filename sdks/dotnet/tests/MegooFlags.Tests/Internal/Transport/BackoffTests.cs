using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using MegooFlags.Internal.Transport;
using Xunit;

namespace MegooFlags.Tests.Internal.Transport;

public class BackoffTests
{
    private static readonly TimeSpan _base = TimeSpan.FromSeconds(3);
    private static readonly TimeSpan _max = TimeSpan.FromSeconds(60);

    [Fact]
    public void DoublesFromBaseUpToMax()
    {
        var b = new Backoff(_base, _max, () => 1.0);
        var waits = new List<double>();
        for (var i = 0; i < 7; i++)
        {
            waits.Add(b.Next().TotalSeconds);
        }
        Assert.Equal([3, 6, 12, 24, 48, 60, 60], waits);
    }

    [Fact]
    public void ResetStartsOver()
    {
        var b = new Backoff(_base, _max, () => 1.0);
        b.Next();
        b.Next();
        b.Reset();
        Assert.Equal(_base, b.Next());
    }

    [Fact]
    public void JittersFromTheSecondWait()
    {
        var b = new Backoff(_base, _max, () => 0.0);
        Assert.Equal(TimeSpan.FromMilliseconds(3000), b.Next());
        Assert.Equal(TimeSpan.FromMilliseconds(4800), b.Next());
        Assert.Equal(TimeSpan.FromMilliseconds(9600), b.Next());
    }

    [Fact]
    public void FirstWaitIsNeverAboveMax()
    {
        Assert.Equal(_max, new Backoff(TimeSpan.FromSeconds(90), _max).Next());
    }

    [Fact]
    public void NeverGoesAboveMax()
    {
        var b = new Backoff(_base, _max);
        for (var i = 0; i < 200; i++)
        {
            var w = b.Next();
            Assert.True(w > TimeSpan.Zero);
            Assert.True(w <= _max, w.ToString());
        }
    }

    [Fact]
    public void BaseCanBeReplaced()
    {
        var b = new Backoff(_base, _max, () => 1.0);
        b.Base = TimeSpan.FromSeconds(1);
        Assert.Equal(TimeSpan.FromSeconds(1), b.Next());
        Assert.Equal(TimeSpan.FromSeconds(2), b.Next());
    }

    [Fact]
    public async Task SleepReturnsTrueAfterTheWait()
    {
        Assert.True(await Backoff.SleepAsync(TimeSpan.FromMilliseconds(1), CancellationToken.None));
    }

    [Fact]
    public async Task SleepEndsEarlyWhenCancelled()
    {
        using var cts = new CancellationTokenSource(TimeSpan.FromMilliseconds(10));
        var sw = Stopwatch.StartNew();
        Assert.False(await Backoff.SleepAsync(TimeSpan.FromHours(1), cts.Token));
        Assert.True(sw.Elapsed < TimeSpan.FromSeconds(5));
    }

    [Fact]
    public async Task SleepReturnsFalseAtOnceWhenAlreadyCancelled()
    {
        using var cts = new CancellationTokenSource();
        cts.Cancel();
        Assert.False(await Backoff.SleepAsync(TimeSpan.FromHours(1), cts.Token));
    }
}
