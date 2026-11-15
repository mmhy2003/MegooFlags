using System.Collections.Generic;
using MegooFlags.Internal.Engine;
using Xunit;

namespace MegooFlags.Tests.Internal.Engine;

public class BucketingTests
{
    [Fact]
    public void BucketText()
    {
        Assert.Equal("u", Bucketing.BucketText("u"));
        Assert.Equal("42", Bucketing.BucketText(42L));
        Assert.Equal("42", Bucketing.BucketText(42.0));
        Assert.Equal("0", Bucketing.BucketText(-0.0));
        Assert.Equal("-9007199254740991", Bucketing.BucketText(-9007199254740991L));
        Assert.Equal("9007199254740991", Bucketing.BucketText(9007199254740991.0));
        Assert.Null(Bucketing.BucketText(42.5));
        Assert.Null(Bucketing.BucketText(true));
        Assert.Null(Bucketing.BucketText(null));
        Assert.Null(Bucketing.BucketText(new List<object?> { 1L }));
        Assert.Null(Bucketing.BucketText(9007199254740992L));
        Assert.Null(Bucketing.BucketText(9007199254740992.0));
        Assert.Null(Bucketing.BucketText(double.NaN));
        Assert.Null(Bucketing.BucketText(double.PositiveInfinity));
    }

    [Fact]
    public void Bucket()
    {
        Assert.Equal(68258, Bucketing.Bucket("f", "u"));
    }

    [Fact]
    public void PickVariation()
    {
        var weights = new List<WeightedVariation> { new("a", 30000), new("b", 40000), new("c", 30000) };
        Assert.Equal("a", Bucketing.PickVariation(weights, 29999));
        Assert.Equal("b", Bucketing.PickVariation(weights, 30000));
        Assert.Equal("b", Bucketing.PickVariation(weights, 68258));
        Assert.Equal("c", Bucketing.PickVariation(weights, 99999));
        Assert.Equal("b", Bucketing.PickVariation(new List<WeightedVariation> { new("a", 0), new("b", 100000) }, 0));
    }
}
