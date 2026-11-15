using System;
using System.Collections.Generic;
using System.Linq;
using MegooFlags.Internal.Events;
using MegooFlags.Internal.Json;
using Xunit;

namespace MegooFlags.Tests.Internal.Events;

public sealed class AggregatorTests
{
    private static readonly DateTimeOffset _t0 = new(2026, 11, 10, 9, 15, 2, TimeSpan.Zero);

    private static Evaluation Served(string flag, string variant, string key, DateTimeOffset at) =>
        new(flag, variant, true, "SPLIT", key, new Dictionary<string, object?> { ["key"] = key }, at);

    private static Evaluation Unserved(string flag, DateTimeOffset at, string key = "") =>
        new(flag, "", false, "ERROR", key, null, at);

    private static Aggregator New(int dedupSize = 100, int maxPending = 100, bool sendContext = false) =>
        new(dedupSize, maxPending, sendContext);

    private static CountItem CountOf(string flag, string? variant, string hour, long n) =>
        new(flag, variant, DateTimeOffset.Parse(hour, System.Globalization.CultureInfo.InvariantCulture).ToUnixTimeSeconds(), n);

    [Fact]
    public void LruEvictsTheLeastRecentlySeenKey()
    {
        var l = new Lru<string>(2);
        Assert.False(l.Seen("a"));
        Assert.False(l.Seen("b"));
        Assert.True(l.Seen("a"));
        // a is now the most recent, so inserting c evicts b.
        Assert.False(l.Seen("c"));
        Assert.True(l.Seen("a"));
        Assert.False(l.Seen("b"));
        Assert.Equal(2, l.Count);
    }

    [Fact]
    public void OneExposurePerKeyFlagVariantAndHour()
    {
        var a = New();
        a.Add(Served("f", "on", "u1", _t0));
        a.Add(Served("f", "on", "u1", _t0.AddMinutes(10)));
        Assert.Equal(1, a.PendingExposures);
        a.Add(Served("f", "on", "u1", _t0.AddHours(1)));
        Assert.Equal(2, a.PendingExposures);

        foreach (var other in new[] { Served("f", "off", "u1", _t0), Served("f", "on", "u2", _t0), Served("g", "on", "u1", _t0) })
        {
            var b = New();
            b.Add(Served("f", "on", "u1", _t0));
            b.Add(other);
            Assert.Equal(2, b.PendingExposures);
        }
    }

    [Fact]
    public void AnonymousContextsShareOneExposure()
    {
        var a = New();
        a.Add(Served("f", "on", "", _t0));
        a.Add(Served("f", "on", "", _t0));
        Assert.Equal(1, a.PendingExposures);
    }

    [Fact]
    public void CountsAddUpPerHour()
    {
        var a = New();
        for (var i = 0; i < 5; i++)
        {
            a.Add(Served("f", "on", "u" + i, _t0));
        }
        a.Add(Served("f", "on", "u1", _t0.AddHours(1)));
        a.Add(Served("f", "off", "u1", _t0));
        Assert.Equal(3, a.PendingCounts);

        var b = a.Take(1000);
        Assert.Equal(
            new[]
            {
                CountOf("f", "on", "2026-11-10T09:00:00Z", 5),
                CountOf("f", "on", "2026-11-10T10:00:00Z", 1),
                CountOf("f", "off", "2026-11-10T09:00:00Z", 1),
            },
            b.Counts);
        Assert.Equal(0, a.PendingExposures);
        Assert.Equal(0, a.PendingCounts);
    }

    [Fact]
    public void UnservedEvaluationsHaveANullVariantAndNoExposure()
    {
        var a = New();
        a.Add(Unserved("f", _t0, "u1"));
        a.Add(Unserved("f", _t0, "u2"));
        var b = a.Take(1000);
        Assert.Empty(b.Exposures);
        Assert.Equal(new[] { CountOf("f", null, "2026-11-10T09:00:00Z", 2) }, b.Counts);
    }

    [Fact]
    public void ContextOnlyWithSendContext()
    {
        var without = New(sendContext: false);
        without.Add(Served("f", "on", "u1", _t0));
        Assert.Null(without.Take(1000).Exposures[0].Context);

        var with = New(sendContext: true);
        with.Add(Served("f", "on", "u1", _t0));
        var ctx = with.Take(1000).Exposures[0].Context;
        Assert.NotNull(ctx);
        Assert.Equal("u1", ctx!["key"]);
    }

    [Fact]
    public void ExposureFieldsAndTimestampInUtcMilliseconds()
    {
        var a = New();
        var local = _t0.ToOffset(TimeSpan.FromHours(3)).AddMilliseconds(123);
        a.Add(Served("f", "on", "u1", local));
        var e = a.Take(1000).Exposures[0];
        Assert.Equal("f", e.FlagKey);
        Assert.Equal("on", e.Variant);
        Assert.Equal("SPLIT", e.Reason);
        Assert.Equal("2026-11-10T09:15:02.123Z", e.Timestamp);
    }

    [Fact]
    public void HourIsTheUtcHour()
    {
        var a = New();
        a.Add(Unserved("f", new DateTimeOffset(2026, 11, 10, 11, 59, 59, 999, TimeSpan.FromHours(2))));
        Assert.Equal("2026-11-10T09:00:00Z", a.Take(10).Counts[0].Hour);
    }

    [Fact]
    public void LruEvictionLetsAnExposureRepeat()
    {
        var a = New(dedupSize: 2, maxPending: 100);
        a.Add(Served("f", "on", "u1", _t0));
        a.Add(Served("f", "on", "u2", _t0));
        a.Add(Served("f", "on", "u3", _t0));
        a.Add(Served("f", "on", "u1", _t0));
        Assert.Equal(4, a.PendingExposures);
    }

    [Fact]
    public void MaxPendingDropsExposuresButNotCounts()
    {
        var a = New(dedupSize: 100, maxPending: 2);
        for (var i = 0; i < 5; i++)
        {
            a.Add(Served("f", "on", "u" + i, _t0));
        }
        a.Add(Served("f", "on", "u0", _t0)); // a duplicate is not a drop
        Assert.Equal(2, a.PendingExposures);
        Assert.Equal(3, a.Dropped);
        Assert.Equal(6, a.Take(1000).Counts.Single().Count);
    }

    [Fact]
    public void TakeLimitsBothListsAndKeepsOrder()
    {
        var a = New(dedupSize: 10000, maxPending: 10000);
        for (var i = 0; i < 1500; i++)
        {
            a.Add(Served("f" + i, "on", "u", _t0));
        }
        var b = a.Take(1000);
        Assert.Equal(1000, b.Exposures.Count);
        Assert.Equal(1000, b.Counts.Count);
        Assert.Equal("f0", b.Exposures[0].FlagKey);
        Assert.Equal("f999", b.Counts[999].FlagKey);

        b = a.Take(1000);
        Assert.Equal(500, b.Exposures.Count);
        Assert.Equal(500, b.Counts.Count);
        Assert.Equal("f1000", b.Exposures[0].FlagKey);
        Assert.Equal("f1000", b.Counts[0].FlagKey);
        Assert.Equal(0, a.PendingExposures);
        Assert.Equal(0, a.PendingCounts);
    }

    [Fact]
    public void TakeSplitsACountOverOneBillion()
    {
        // A count this large is only reached by restoring unsent batches over a long outage.
        var a = New();
        a.Restore(new Batch(new List<Exposure>(), new List<CountItem> { CountOf("f", "on", "2026-11-10T09:00:00Z", 2_500_000_000) }));
        Assert.Equal(new long[] { 1_000_000_000, 1_000_000_000, 500_000_000 }, a.Take(1000).Counts.Select(c => c.Count));

        // The item limit holds; the remainder stays pending for the next batch.
        a.Restore(new Batch(new List<Exposure>(), new List<CountItem> { CountOf("f", "on", "2026-11-10T09:00:00Z", 2_500_000_000) }));
        Assert.Equal(2, a.Take(2).Counts.Count);
        Assert.Equal(500_000_000, Assert.Single(a.Take(2).Counts).Count);
    }

    [Fact]
    public void RestorePutsTheBatchFirstAndMergesCounts()
    {
        var a = New();
        a.Add(Served("f", "on", "u1", _t0));
        a.Add(Served("f", "on", "u2", _t0));
        a.Add(Unserved("f", _t0));
        var sent = a.Take(1000);

        a.Add(Served("f", "on", "u3", _t0));
        a.Add(Served("g", "on", "u3", _t0));
        a.Restore(sent);

        var b = a.Take(1000);
        Assert.Equal(4, b.Exposures.Count);
        Assert.Same(sent.Exposures[0], b.Exposures[0]);
        Assert.Equal("f", b.Exposures[2].FlagKey);
        Assert.Equal("g", b.Exposures[3].FlagKey);
        Assert.Equal(
            new[]
            {
                CountOf("f", "on", "2026-11-10T09:00:00Z", 3),
                CountOf("f", null, "2026-11-10T09:00:00Z", 1),
                CountOf("g", "on", "2026-11-10T09:00:00Z", 1),
            },
            b.Counts);
    }

    [Fact]
    public void RestoreJoinsASplitCount()
    {
        var a = New();
        a.Restore(new Batch(new List<Exposure>(), new List<CountItem> { CountOf("f", "on", "2026-11-10T09:00:00Z", 2_500_000_000) }));
        a.Restore(a.Take(1000));
        Assert.Equal(1, a.PendingCounts);
        Assert.Equal(2_500_000_000, a.Take(1).Counts.Sum(c => c.Count) + a.Take(1000).Counts.Sum(c => c.Count));
    }

    [Fact]
    public void BodyHasTheSpecShape()
    {
        var a = New(sendContext: true);
        a.Add(Served("new-checkout", "on", "user-1", _t0));
        a.Add(Unserved("new-checkout", _t0));
        var body = (Dictionary<string, object?>)JsonTree.Parse(System.Text.Encoding.UTF8.GetString(a.Take(10).ToJson()))!;
        Assert.Equal(new[] { "counts", "exposures" }, body.Keys.Order(StringComparer.Ordinal));
        var exposure = (Dictionary<string, object?>)((List<object?>)body["exposures"]!).Single()!;
        Assert.Equal(new[] { "context", "flag_key", "reason", "timestamp", "variant" }, exposure.Keys.Order(StringComparer.Ordinal));
        var counts = ((List<object?>)body["counts"]!).Cast<Dictionary<string, object?>>().ToList();
        Assert.All(counts, c => Assert.Equal(new[] { "count", "flag_key", "hour", "variant" }, c.Keys.Order(StringComparer.Ordinal)));
        Assert.Null(counts[1]["variant"]);
        Assert.Equal(1L, counts[1]["count"]);
    }

    [Fact]
    public void BodyOmitsContextAndWritesEmptyLists()
    {
        var a = New();
        a.Add(Served("f", "on", "u", _t0));
        var exposuresOnly = new Batch(a.Take(10).Exposures, new List<CountItem>());
        Assert.Equal(
            """{"exposures":[{"flag_key":"f","variant":"on","reason":"SPLIT","timestamp":"2026-11-10T09:15:02.000Z"}],"counts":[]}""",
            System.Text.Encoding.UTF8.GetString(exposuresOnly.ToJson()));

        a = New();
        a.Add(Unserved("f", _t0));
        Assert.Equal(
            """{"exposures":[],"counts":[{"flag_key":"f","variant":null,"hour":"2026-11-10T09:00:00Z","count":1}]}""",
            System.Text.Encoding.UTF8.GetString(a.Take(10).ToJson()));
    }
}
