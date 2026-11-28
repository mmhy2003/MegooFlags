using System.Collections.Generic;
using MegooFlags.Internal.Json;
using Xunit;

namespace MegooFlags.Tests.Internal.Json;

public class JsonTreeTests
{
    [Theory]
    [InlineData("0", 0L)]
    [InlineData("42", 42L)]
    [InlineData("-7", -7L)]
    [InlineData("9223372036854775807", long.MaxValue)]
    [InlineData("-9223372036854775808", long.MinValue)]
    public void IntegerLiteralsBecomeLong(string text, long expected)
    {
        var v = JsonTree.Parse(text);
        Assert.IsType<long>(v);
        Assert.Equal(expected, v);
    }

    [Theory]
    [InlineData("1.0", 1.0)]
    [InlineData("1.5", 1.5)]
    [InlineData("1e2", 100.0)]
    [InlineData("1E-2", 0.01)]
    [InlineData("-2.5e+1", -25.0)]
    public void FractionsAndExponentsBecomeDouble(string text, double expected)
    {
        var v = JsonTree.Parse(text);
        Assert.IsType<double>(v);
        Assert.Equal(expected, v);
    }

    [Fact]
    public void IntegerBeyondLongBecomesDouble()
    {
        var v = JsonTree.Parse("9223372036854775808");
        Assert.IsType<double>(v);
        Assert.Equal(9223372036854775808.0, v);
        Assert.Equal(1e20, JsonTree.Parse("100000000000000000000"));
    }

    [Fact]
    public void ScalarsAndNull()
    {
        Assert.Null(JsonTree.Parse("null"));
        Assert.Equal(true, JsonTree.Parse("true"));
        Assert.Equal(false, JsonTree.Parse("false"));
        Assert.Equal("a\"b\u00e9\U0001F600", JsonTree.Parse("\"a\\\"b\u00e9\\ud83d\\ude00\""));
    }

    [Fact]
    public void Objects()
    {
        var v = Assert.IsType<Dictionary<string, object?>>(JsonTree.Parse("{\"a\":1,\"b\":{\"c\":null},\"a\":2}"));
        Assert.Equal(2, v.Count);
        Assert.Equal(2L, v["a"]); // duplicate keys: the last one wins
        var b = Assert.IsType<Dictionary<string, object?>>(v["b"]);
        Assert.True(b.ContainsKey("c"));
        Assert.Null(b["c"]);
    }

    [Fact]
    public void Arrays()
    {
        var v = Assert.IsType<List<object?>>(JsonTree.Parse("[1, 2.5, \"x\", null, [], {}]"));
        Assert.Equal(6, v.Count);
        Assert.Equal(1L, v[0]);
        Assert.Equal(2.5, v[1]);
        Assert.Equal("x", v[2]);
        Assert.Null(v[3]);
        Assert.Empty(Assert.IsType<List<object?>>(v[4]));
        Assert.Empty(Assert.IsType<Dictionary<string, object?>>(v[5]));
    }

    [Fact]
    public void DeepNestingUpTo512()
    {
        var ok = new string('[', 512) + new string(']', 512);
        Assert.IsType<List<object?>>(JsonTree.Parse(ok));
        var tooDeep = new string('[', 513) + new string(']', 513);
        Assert.ThrowsAny<System.Text.Json.JsonException>(() => JsonTree.Parse(tooDeep));
    }

    [Theory]
    [InlineData("")]
    [InlineData("{} x")]
    [InlineData("[1,]")]
    [InlineData("{\"a\":1,}")]
    [InlineData("01")]
    [InlineData("NaN")]
    [InlineData("-")]
    [InlineData("\"\\x\"")]
    [InlineData("\"a\tb\"")]
    [InlineData("// c\n1")]
    [InlineData("1e400")]
    [InlineData("\"\\ud800\"")]
    [InlineData("{\"\\udc00\":1}")]
    public void RefusesInvalidJson(string text)
    {
        Assert.ThrowsAny<System.Text.Json.JsonException>(() => JsonTree.Parse(text));
    }

    [Fact]
    public void WriteEscapesAndKeepsNonAscii()
    {
        Assert.Equal("\"q\\\"b\\\\n\\n\\u0001é\"", JsonTree.Write("q\"b\\n\n\u0001é"));
        Assert.Equal("null", JsonTree.Write(null));
        Assert.Equal("[true,false,1,1.5,1.0]", JsonTree.Write(new List<object?> { true, false, 1L, 1.5, 1.0 }));
    }

    [Fact]
    public void WriteRefusesNonFiniteNumbers()
    {
        Assert.ThrowsAny<System.Text.Json.JsonException>(() => JsonTree.Write(double.NaN));
        Assert.ThrowsAny<System.Text.Json.JsonException>(() => JsonTree.Write(double.PositiveInfinity));
    }

    [Fact]
    public void RoundTripThroughWrite()
    {
        const string text =
            "{\"s\":\"h\u00e9\\\"\",\"i\":-3,\"big\":9223372036854775807,\"d\":2.0,\"e\":1E+20,"
            + "\"f\":0.1,\"t\":true,\"n\":null,\"a\":[1,[2,{\"k\":[]}]],\"o\":{}}";
        var tree = JsonTree.Parse(text);
        var written = JsonTree.Write(tree);
        var again = JsonTree.Parse(written);
        Assert.Equal(written, JsonTree.Write(again));
        var o = Assert.IsType<Dictionary<string, object?>>(again);
        Assert.Equal("h\u00e9\"", o["s"]);
        Assert.Equal(-3L, o["i"]);
        Assert.Equal(long.MaxValue, o["big"]);
        Assert.IsType<double>(o["d"]);
        Assert.Equal(2.0, o["d"]);
        Assert.Equal(1e20, o["e"]);
        Assert.Equal(0.1, o["f"]);
        Assert.Equal(true, o["t"]);
        Assert.Null(o["n"]);
        Assert.Equal("[1,[2,{\"k\":[]}]]", JsonTree.Write(o["a"]));
        Assert.Equal("{}", JsonTree.Write(o["o"]));
    }
}
