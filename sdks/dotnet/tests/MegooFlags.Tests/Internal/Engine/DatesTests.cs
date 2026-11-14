using System.Collections.Generic;
using MegooFlags.Internal.Engine;
using Xunit;

namespace MegooFlags.Tests.Internal.Engine;

public class DatesTests
{
    [Theory]
    [InlineData("1970-01-01T00:00:00Z", 0L)]
    [InlineData("1970-01-01T00:00:00.123456789Z", 123L)]
    [InlineData("1970-01-01T00:00:00.5Z", 500L)]
    [InlineData("1970-01-01T01:00:00+01:00", 0L)]
    [InlineData("1969-12-31T23:59:59Z", -1000L)]
    [InlineData("2026-01-01t00:00:00z", 1767225600000L)]
    [InlineData("2026-01-01 00:00:00Z", 1767225600000L)]
    [InlineData("2025-12-31T19:00:00-05:00", 1767225600000L)]
    [InlineData("2024-02-29T00:00:00Z", 1709164800000L)]
    [InlineData("2026-01-01T00:00:00+23:59", 1767139260000L)]
    [InlineData("0001-01-01T00:00:00Z", -62135596800000L)]
    [InlineData("9999-12-31T23:59:59.999Z", 253402300799999L)]
    public void Parses(string s, long expected)
    {
        Assert.Equal(expected, Dates.ParseRfc3339Millis(s));
    }

    [Theory]
    [InlineData("2026-01-01")]
    [InlineData("2026-02-30T00:00:00Z")]
    [InlineData("2025-02-29T00:00:00Z")]
    [InlineData("1900-02-29T00:00:00Z")]
    [InlineData("2026-04-31T00:00:00Z")]
    [InlineData("2026-13-01T00:00:00Z")]
    [InlineData("2026-00-01T00:00:00Z")]
    [InlineData("2026-01-00T00:00:00Z")]
    [InlineData("2026-01-01T24:00:00Z")]
    [InlineData("2026-01-01T00:60:00Z")]
    [InlineData("2026-01-01T00:00:60Z")]
    [InlineData("2026-01-01T00:00:00")]
    [InlineData("2026-01-01T00:00:00+24:00")]
    [InlineData("2026-01-01T00:00:00+0100")]
    [InlineData("0000-01-01T00:00:00Z")]
    [InlineData("2026-01-01T00:00:00.Z")]
    [InlineData("2026-01-01T00:00:00Z\n")]
    [InlineData(" 2026-01-01T00:00:00Z")]
    [InlineData("2026-01-01X00:00:00Z")]
    [InlineData("\u0662026-01-01T00:00:00Z")]
    [InlineData("2026-01-01T00:00:00.\u0661Z")]
    [InlineData("26-01-01T00:00:00Z")]
    [InlineData("")]
    public void Invalid(string s)
    {
        Assert.Null(Dates.ParseRfc3339Millis(s));
    }

    [Fact]
    public void ToMillis()
    {
        Assert.Equal(1500L, Dates.ToMillis(1500.9));
        Assert.Equal(-1L, Dates.ToMillis(-0.5));
        Assert.Equal(1500L, Dates.ToMillis(1500L));
        Assert.Equal(0L, Dates.ToMillis("1970-01-01T00:00:00Z"));
        Assert.Null(Dates.ToMillis("nope"));
        Assert.Null(Dates.ToMillis(double.NaN));
        Assert.Null(Dates.ToMillis(double.PositiveInfinity));
        Assert.Null(Dates.ToMillis(double.NegativeInfinity));
        Assert.Null(Dates.ToMillis(true));
        Assert.Null(Dates.ToMillis(null));
        Assert.Null(Dates.ToMillis(new List<object?>()));
    }
}
