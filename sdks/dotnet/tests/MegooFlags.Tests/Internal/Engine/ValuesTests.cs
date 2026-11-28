using System.Collections.Generic;
using MegooFlags.Internal.Engine;
using Xunit;

namespace MegooFlags.Tests.Internal.Engine;

public class ValuesTests
{
    [Fact]
    public void IsNumber()
    {
        Assert.True(Values.IsNumber(1L));
        Assert.True(Values.IsNumber(1.5));
        Assert.True(Values.IsNumber(3));
        Assert.False(Values.IsNumber(true));
        Assert.False(Values.IsNumber("1"));
        Assert.False(Values.IsNumber(null));
    }

    [Fact]
    public void IsScalar()
    {
        Assert.True(Values.IsScalar("a"));
        Assert.True(Values.IsScalar(false));
        Assert.True(Values.IsScalar(2L));
        Assert.True(Values.IsScalar(2.5));
        Assert.False(Values.IsScalar(null));
        Assert.False(Values.IsScalar(new List<object?>()));
        Assert.False(Values.IsScalar(new Dictionary<string, object?>()));
    }

    [Fact]
    public void JsonEqual()
    {
        Assert.True(Values.JsonEqual(1L, 1.0));
        Assert.False(Values.JsonEqual(true, 1L));
        Assert.False(Values.JsonEqual(1L, true));
        Assert.False(Values.JsonEqual("1", 1L));
        Assert.False(Values.JsonEqual(null, null));
        Assert.True(Values.JsonEqual(false, false));
        Assert.False(Values.JsonEqual(false, true));
        Assert.True(Values.JsonEqual("a", "a"));
        Assert.False(Values.JsonEqual(new List<object?>(), new List<object?>()));
    }

    [Fact]
    public void IntegersCompareExactly()
    {
        // 2^53 and 2^53+1 are the same double but different integers.
        Assert.False(Values.JsonEqual(9007199254740992L, 9007199254740993L));
        Assert.Equal(-1, Values.CompareNumbers(9007199254740992L, 9007199254740993L));
        Assert.Equal(1, Values.CompareNumbers(long.MaxValue, long.MaxValue - 1));
        Assert.Equal(0, Values.CompareNumbers(3L, 3));
    }

    [Fact]
    public void MixedNumbersCompareAsDoubles()
    {
        Assert.True(Values.JsonEqual(9007199254740993L, 9007199254740992.0));
        Assert.Equal(0, Values.CompareNumbers(2L, 2.0));
        Assert.Equal(-1, Values.CompareNumbers(2L, 2.5));
        Assert.Equal(1, Values.CompareNumbers(-0.5, -1L));
        Assert.Equal(0, Values.CompareNumbers(0.0, -0.0));
    }
}
