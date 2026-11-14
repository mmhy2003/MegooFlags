using MegooFlags.Internal.Delivery;
using MegooFlags.Internal.Engine;
using Xunit;

namespace MegooFlags.Tests.Internal.Delivery;

public class StoreTests
{
    private static Ruleset NewRuleset() =>
        RulesetParser.Parse("{\"schema_version\":1,\"project\":\"p\",\"environment\":\"e\",\"flags\":{},\"segments\":{}}");

    [Fact]
    public void LoadBeforeSwapIsNull()
    {
        Assert.Null(new Store().Load());
    }

    [Fact]
    public void SwapReturnsTheOldSnapshot()
    {
        var s = new Store();
        var a = new Snapshot(NewRuleset(), "\"a\"", SnapshotSource.Bootstrap);
        var b = new Snapshot(NewRuleset(), "\"b\"", SnapshotSource.Stream);
        Assert.Null(s.Swap(a));
        Assert.Same(a, s.Load());
        Assert.Same(a, s.Swap(b));
        Assert.Same(b, s.Load());
        Assert.Equal("\"b\"", s.Load()!.ETag);
        Assert.Equal(SnapshotSource.Stream, s.Load()!.Source);
        Assert.Same(b.Ruleset, s.Load()!.Ruleset);
    }

    [Fact]
    public void SourceNamesMatchTheBlueprint()
    {
        Assert.Equal("bootstrap", SnapshotSource.Bootstrap.Name());
        Assert.Equal("cache", SnapshotSource.Cache.Name());
        Assert.Equal("stream", SnapshotSource.Stream.Name());
        Assert.Equal("poll", SnapshotSource.Poll.Name());
    }
}
