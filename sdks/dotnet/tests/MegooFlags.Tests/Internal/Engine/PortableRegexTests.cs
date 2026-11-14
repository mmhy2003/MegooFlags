using System;
using System.Text.RegularExpressions;
using MegooFlags.Internal.Engine;
using Xunit;

namespace MegooFlags.Tests.Internal.Engine;

public class PortableRegexTests
{
    private const string W = @"[\u0030-\u0039\u0041-\u005A\u005F\u0061-\u007A]";

    private static PortableRegex Compile(string pattern)
    {
        Assert.True(PortableRegex.TryCompile(pattern, out var regex, out var error), error);
        return regex!;
    }

    // Python re with re.ASCII semantics.
    [Theory]
    [InlineData("b", "abc", true)]
    [InlineData("b", "xyz", false)]
    [InlineData("^ab$", "ab", true)]
    [InlineData("^ab$", "abc", false)]
    [InlineData("^ab$", "xab", false)]
    [InlineData("^ab$", "ab\n", true)]
    [InlineData("^ab$", "ab\n\n", false)]
    [InlineData("^ab$", "ab\r", false)]
    [InlineData(@"\d", "a3", true)]
    [InlineData(@"\d", "\u0663", false)]
    [InlineData("(?i)k", "K", true)]
    [InlineData("(?i)k", "\u212a", false)]
    [InlineData("(?i)^[s]$", "S", true)]
    [InlineData("(?i)^[s]$", "\u017f", false)]
    [InlineData("(?i)^[j-l]$", "K", true)]
    [InlineData("(?i)^[j-l]$", "\u212a", false)]
    [InlineData("(?i)\u00e9", "\u00e9", true)]
    [InlineData("(?i)\u00e9", "\u00c9", false)]
    [InlineData("(?i)[^a]", "A", false)]
    [InlineData("(?i)[^a]", "b", true)]
    [InlineData("^(?i:ab)c$", "ABc", true)]
    [InlineData("^(?i:ab)c$", "ABC", false)]
    [InlineData("(?im)^B$", "a\nb", true)]
    [InlineData(@"^\s$", "\u000b", true)]
    [InlineData(@"^\s$", " ", true)]
    [InlineData(@"^\s$", "\u00a0", false)]
    [InlineData(@"^\s$", "\u0085", false)]
    [InlineData(@"^\s$", "\u2028", false)]
    [InlineData(@"^\S$", "\u000b", false)]
    [InlineData(@"^\S$", "\u00a0", true)]
    [InlineData(@"^[\s]$", "\u000b", true)]
    [InlineData(@"^[^\d]$", "x", true)]
    [InlineData(@"^[^\d]$", "5", false)]
    [InlineData(@"^[\w-]$", "-", true)]
    [InlineData(@"^[\w-]$", "a", true)]
    [InlineData(@"^[\w-]$", "\u00e9", false)]
    [InlineData(@"^\w+$", "a_1", true)]
    [InlineData(@"^\w+$", "\u00e9", false)]
    [InlineData("a\\b\u00e9", "a\u00e9", true)]
    [InlineData("a\\B\u00e9", "a\u00e9", false)]
    [InlineData(@"\bfoo\b", "a foo b", true)]
    [InlineData(@"\bfoo\b", "afoo", false)]
    [InlineData("^.$", "\r", true)]
    [InlineData("^.$", "\n", false)]
    [InlineData("^.$", "\u2028", true)]
    [InlineData("^.$", "\U0001F600", true)]
    [InlineData("^..$", "\U0001F600", false)]
    [InlineData("^[\U0001F600a]$", "\U0001F600", true)]
    [InlineData("^[\U0001F600a]$", "a", true)]
    [InlineData("^a.b$", "a\nb", false)]
    [InlineData("(?s)^a.b$", "a\nb", true)]
    [InlineData("^b$", "a\nb\nc", false)]
    [InlineData("(?m)^b$", "a\nb\nc", true)]
    [InlineData("(?m)a$", "a\nb", true)]
    [InlineData("(?m)a$", "a\r\nb", false)]
    [InlineData("(?m)^$", "a\n\nb", true)]
    [InlineData(@"(?m)\Ab", "b\na", true)]
    [InlineData(@"(?m)\Ab", "a\nb", false)]
    [InlineData("^$", "", true)]
    [InlineData("^$", "\n", true)]
    [InlineData("", "", true)]
    [InlineData("", "x", true)]
    [InlineData("^(?P<x>ab)+c$", "ababc", true)]
    [InlineData("^(?:a|b|)$", "", true)]
    [InlineData("^(?:a|b|)$", "c", false)]
    [InlineData("^a{2,3}$", "aa", true)]
    [InlineData("^a{2,3}$", "aaaa", false)]
    [InlineData("^a{2,}$", "aaaaa", true)]
    [InlineData("^a*?$", "aaa", true)]
    [InlineData("x{", "x{", true)]
    [InlineData("x{a}", "x{a}", true)]
    [InlineData(@"^\x41\t\.$", "A\t.", true)]
    [InlineData("^[a[]$", "[", true)]
    [InlineData("^[a[]$", "a", true)]
    [InlineData("^[a[]$", "b", false)]
    [InlineData("^[a&&b]$", "&", true)]
    [InlineData(@"^[a\-z]$", "-", true)]
    [InlineData(@"^[a\-z]$", "b", false)]
    [InlineData("^[a-]$", "-", true)]
    [InlineData("^[]a]$", "]", true)]
    [InlineData("^[^]a]$", "b", true)]
    [InlineData("^[^]a]$", "]", false)]
    [InlineData("^[a-c-e]$", "-", true)]
    [InlineData("^[a-c-e]$", "d", false)]
    [InlineData(@"^a\|b$", "a|b", true)]
    public void MatchesLikePythonAscii(string pattern, string subject, bool match)
    {
        Assert.Equal(match, Compile(pattern).Test(subject));
    }

    [Theory]
    [InlineData("(?=a)")]
    [InlineData("(?!a)")]
    [InlineData("(?<=a)b")]
    [InlineData("(?<!a)b")]
    [InlineData(@"(a)\1")]
    [InlineData("(?P<n>a)(?P=n)")]
    [InlineData("(?P<n>a)(?P<n>b)")]
    [InlineData("(?>a)")]
    [InlineData("(?(1)a|b)")]
    [InlineData("(?<n>a)")]
    [InlineData("(?#c)a")]
    [InlineData("a*+")]
    [InlineData("a++")]
    [InlineData("a?+")]
    [InlineData("a**")]
    [InlineData("a{2}{3}")]
    [InlineData("*a")]
    [InlineData("(*a)")]
    [InlineData("a|*")]
    [InlineData("^*")]
    [InlineData(@"\b+")]
    [InlineData("a{,2}")]
    [InlineData("a{3,2}")]
    [InlineData("a{1001}")]
    [InlineData(@"\z")]
    [InlineData(@"\Z")]
    [InlineData(@"\pL")]
    [InlineData(@"\x{41}")]
    [InlineData(@"\N{LATIN CAPITAL LETTER A}")]
    [InlineData(@"\e")]
    [InlineData(@"\q")]
    [InlineData(@"\0")]
    [InlineData("\\")]
    [InlineData("\\\u00e9")]
    [InlineData("(?x)a")]
    [InlineData("(?U)a")]
    [InlineData("(?a)a")]
    [InlineData("a(?i)b")]
    [InlineData("(?-i:a)")]
    [InlineData("[[:alpha:]]")]
    [InlineData(@"[a-\d]")]
    [InlineData(@"[\w-a]")]
    [InlineData("[z-a]")]
    [InlineData(@"[\b]")]
    [InlineData("[]")]
    [InlineData("[a")]
    [InlineData("(")]
    [InlineData(")")]
    [InlineData("(a")]
    public void Refuses(string pattern)
    {
        Assert.False(PortableRegex.TryCompile(pattern, out var regex, out var error));
        Assert.Null(regex);
        Assert.False(string.IsNullOrEmpty(error));
    }

    [Theory]
    [InlineData("^a$", @"\A\u0061(?=\u000A?\z)")]
    [InlineData("(?m)^a$", @"(?:\A|(?<=\u000A))\u0061(?=\u000A|\z)")]
    [InlineData(@"\bx", "(?:(?<=" + W + ")(?!" + W + ")|(?<!" + W + ")(?=" + W + "))" + @"\u0078")]
    [InlineData("(?i)[a-c]", @"[\u0041-\u0043\u0061-\u0063]")]
    public void EmitsTheDotNetColumn(string pattern, string source)
    {
        var regex = Compile(pattern);
        Assert.Equal(source, regex.Source);
        Assert.Equal(RegexOptions.CultureInvariant, regex.Options);
    }

    [Fact]
    public void WritesAnAstralCodePointAsOneSurrogatePair()
    {
        Assert.Equal(@"(?:\uD83D\uDE00)", Compile("\U0001F600").Source);
    }

    [Theory]
    // U+10000 to U+1F64F: a partial last high surrogate (D83D) and full ones in between (D800 to D83C).
    [InlineData("a", true)]
    [InlineData("c", true)]
    [InlineData("d", false)]
    [InlineData("\U00010000", true)]
    [InlineData("\U00010400", true)]
    [InlineData("\U0001F600", true)]
    [InlineData("\U0001F64F", true)]
    [InlineData("\U0001F680", false)]
    public void MatchesASetMixingBmpAndAstralRanges(string subject, bool match)
    {
        Assert.Equal(match, Compile("^[a-c\U00010000-\U0001F64F]$").Test(subject));
    }

    // Lone surrogates are passed as UTF-16 code units: xunit does not keep them in string theory data.
    [Theory]
    [InlineData("^.$", new[] { 0xD800 }, true)]
    [InlineData("^.$", new[] { 0xDC00 }, true)]
    [InlineData("^..$", new[] { 0xDC00, 0xD800 }, true)]
    [InlineData("^.$", new[] { 0xD83D, 0xDE00 }, true)]
    [InlineData("^..$", new[] { 0xD83D, 0xDE00 }, false)]
    [InlineData("^[^a]$", new[] { 0xDE00 }, true)]
    [InlineData("^[^a]{2}$", new[] { 0xD83D, 0xDE00 }, false)]
    [InlineData("^[a-c\U00010000-\U0001F64F]$", new[] { 0xD83D }, false)]
    [InlineData("^[a-c\U00010000-\U0001F64F]$", new[] { 0xDE00 }, false)]
    public void TreatsALoneSurrogateAsOneCharacter(string pattern, int[] units, bool match)
    {
        var subject = new string(Array.ConvertAll(units, u => (char)u));
        Assert.Equal(match, Compile(pattern).Test(subject));
    }

    [Fact]
    public void RepeatsAnEmptySetWithoutAHostError()
    {
        var regex = Compile(@"^[^\s\S]*$");
        Assert.True(regex.Test(""));
        Assert.False(regex.Test("a"));
    }
}
