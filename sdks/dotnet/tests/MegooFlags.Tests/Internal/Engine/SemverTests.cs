using MegooFlags.Internal.Engine;
using Xunit;

namespace MegooFlags.Tests.Internal.Engine;

public class SemverTests
{
    [Theory]
    [InlineData("1.2.3")]
    [InlineData("1.0.0-alpha.1")]
    [InlineData("1.0.0+build.5")]
    [InlineData("1.0.0-0.3.7")]
    [InlineData("1.0.0-x-y-z.--")]
    [InlineData("1.0.0-alpha+b.1")]
    [InlineData("0.0.0")]
    public void Valid(string s)
    {
        Assert.NotNull(Semver.Parse(s));
    }

    [Theory]
    [InlineData("v1.2.3")]
    [InlineData("1.2")]
    [InlineData("1.2.3.4")]
    [InlineData("01.2.3")]
    [InlineData("1.2.3-01")]
    [InlineData("1.2.3-")]
    [InlineData("1.2.3+")]
    [InlineData("1.2.3-a..b")]
    [InlineData("1.2.3\n")]
    [InlineData(" 1.2.3")]
    [InlineData("1.\u0662.3")]
    [InlineData("1.2.3-\u03b1")]
    [InlineData("1.2.3+a..b")]
    [InlineData("")]
    public void Invalid(string s)
    {
        Assert.Null(Semver.Parse(s));
    }

    [Fact]
    public void Precedence()
    {
        string[] ordered =
        [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
        ];
        for (var i = 0; i < ordered.Length; i++)
        {
            for (var j = 0; j < ordered.Length; j++)
            {
                Assert.True(
                    i.CompareTo(j) == Semver.Compare(Semver.Parse(ordered[i])!, Semver.Parse(ordered[j])!),
                    ordered[i] + " vs " + ordered[j]);
            }
        }
    }

    [Fact]
    public void BuildIsIgnored()
    {
        Assert.Equal(0, Semver.Compare(Semver.Parse("1.0.0+b1")!, Semver.Parse("1.0.0+b2")!));
    }

    [Fact]
    public void CoreComparesWithoutOverflow()
    {
        Assert.Equal(1, Semver.Compare(Semver.Parse("99999999999999999999.0.0")!, Semver.Parse("9.0.0")!));
        Assert.Equal(-1, Semver.Compare(Semver.Parse("1.9.0")!, Semver.Parse("1.10.0")!));
    }
}
