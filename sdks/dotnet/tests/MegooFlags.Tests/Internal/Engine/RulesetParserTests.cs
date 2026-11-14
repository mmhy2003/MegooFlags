using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using MegooFlags.Internal.Engine;
using MegooFlags.Internal.Json;
using Xunit;
using static MegooFlags.Tests.RulesetGen;

namespace MegooFlags.Tests.Internal.Engine;

public class RulesetParserTests
{
    /// <summary>Base is a valid ruleset: flag f with one rule, rollout fallthrough, segment s.</summary>
    private static Dictionary<string, object?> Base() =>
        RulesetJson(
            [
                FlagJson(
                    "f",
                    "rules", List(Obj(
                        "description", "r",
                        "clauses", List(Cl("country", "in", false, "NL")),
                        "serve", Obj("variation", "on"))),
                    "fallthrough", Obj("rollout", Obj(
                        "bucket_by", "key",
                        "weights", List(
                            Obj("variation", "on", "weight", 50000L),
                            Obj("variation", "off", "weight", 50000L))))),
            ],
            Obj("s", Obj(
                "included", List("a"),
                "excluded", List("b"),
                "rules", List(Obj("clauses", List(Cl("org", "in", false, "acme")))))));

    private static Dictionary<string, object?> M(object? v) => (Dictionary<string, object?>)v!;

    private static List<object?> L(object? v) => (List<object?>)v!;

    private static Dictionary<string, object?> FlagOf(Dictionary<string, object?> r) => M(M(r["flags"])["f"]);

    private static Dictionary<string, object?> RuleOf(Dictionary<string, object?> r) => M(L(FlagOf(r)["rules"])[0]);

    private static Dictionary<string, object?> ClauseOf(Dictionary<string, object?> r) =>
        M(L(RuleOf(r)["clauses"])[0]);

    private static Dictionary<string, object?> RolloutOf(Dictionary<string, object?> r) =>
        M(M(FlagOf(r)["fallthrough"])["rollout"]);

    private static Dictionary<string, object?> SegmentOf(Dictionary<string, object?> r) => M(M(r["segments"])["s"]);

    private static Clause FirstClause(Dictionary<string, object?> root) =>
        RulesetParser.Parse(root).Flags["f"].Rules[0].Clauses[0];

    [Fact]
    public void ParsesAValidRulesetFromTextAndFromATree()
    {
        foreach (var rs in new[] { RulesetParser.Parse(JsonTree.Write(Base())), RulesetParser.Parse((object)Base()) })
        {
            Assert.Equal("p", rs.Project);
            Assert.Equal("e", rs.Environment);
            Assert.Empty(rs.Warnings);
            var f = rs.Flags["f"];
            Assert.Equal("f", f.Key);
            Assert.Equal("string", f.Type);
            Assert.True(f.Enabled);
            Assert.Equal(1L, f.Version);
            Assert.Equal(new[] { "on", "off" }, f.Variations.Select(v => v.Key));
            Assert.True(f.TryGetVariation("on", out var on));
            Assert.Equal("on", on!.Value);
            Assert.False(f.TryGetVariation("nope", out _));
            Assert.Equal("off", f.OffVariation);
            Assert.Empty(f.Prerequisites);
            Assert.Empty(f.Targets);
            Assert.Single(f.Rules);
            Assert.Equal("r", f.Rules[0].Description);
            Assert.Equal("on", f.Rules[0].Serve.Variation);
            Assert.Null(f.Rules[0].Serve.Rollout);
            Assert.Null(f.Fallthrough.Variation);
            Assert.Equal("key", f.Fallthrough.Rollout!.BucketBy);
            Assert.Equal(2, f.Fallthrough.Rollout.Weights.Count);
            Assert.Equal(new WeightedVariation("off", 50000), f.Fallthrough.Rollout.Weights[1]);
            var s = rs.Segments["s"];
            Assert.Equal(new[] { "a" }, s.Included);
            Assert.Equal(new[] { "b" }, s.Excluded);
            Assert.Single(s.Rules);
            Assert.Equal("org", s.Rules[0][0].Attribute);
        }
    }

    [Fact]
    public void ParsesPrerequisitesTargetsAndANullOffVariation()
    {
        var root = Base();
        FlagOf(root)["off_variation"] = null;
        FlagOf(root)["prerequisites"] = List(Obj("flag", "g", "variation", "on"));
        FlagOf(root)["targets"] = List(Obj("variation", "off", "values", List("u1", "u2")));
        var f = RulesetParser.Parse(root).Flags["f"];
        Assert.Null(f.OffVariation);
        Assert.Equal(new[] { new Prerequisite("g", "on") }, f.Prerequisites);
        Assert.Single(f.Targets);
        Assert.Equal("off", f.Targets[0].Variation);
        Assert.Equal(new[] { "u1", "u2" }, f.Targets[0].Values.Order());
    }

    [Fact]
    public void LooksUpKeysOrdinally()
    {
        var root = Base();
        FlagOf(root)["targets"] = List(Obj("variation", "off", "values", List("u1")));
        var rs = RulesetParser.Parse(root);
        Assert.False(rs.Flags.ContainsKey("F"));
        Assert.False(rs.Flags["f"].Targets[0].Values.Contains("U1"));
        Assert.False(rs.Flags["f"].TryGetVariation("ON", out _));
    }

    [Fact]
    public void KeepsVariationValuesAndTheirIntegralForm()
    {
        var root = Base();
        FlagOf(root)["variations"] = List(
            Obj("key", "on", "value", null),
            Obj("key", "off", "value", Obj("a", List(1L))),
            Obj("key", "i", "value", 9007199254740993L),
            Obj("key", "d", "value", 3.0),
            Obj("key", "h", "value", 2.5),
            Obj("key", "b", "value", true));
        var f = RulesetParser.Parse(root).Flags["f"];
        Variation V(string key) => f.Variations.Single(v => v.Key == key);
        Assert.Null(V("on").Value);
        var off = Assert.IsAssignableFrom<IReadOnlyDictionary<string, object?>>(V("off").Value);
        Assert.Equal(new object?[] { 1L }, Assert.IsAssignableFrom<IReadOnlyList<object?>>(off["a"]));
        Assert.True(V("i").IsInt);
        Assert.Equal(9007199254740993L, V("i").IntValue);
        Assert.True(V("d").IsInt);
        Assert.Equal(3L, V("d").IntValue);
        Assert.False(V("h").IsInt);
        Assert.False(V("b").IsInt);
        Assert.False(V("on").IsInt);
    }

    [Fact]
    public void DefaultsTheRuleDescriptionToEmpty()
    {
        var root = Base();
        RuleOf(root).Remove("description");
        Assert.Equal("", RulesetParser.Parse(root).Flags["f"].Rules[0].Description);
    }

    [Fact]
    public void IgnoresUnknownFields()
    {
        var root = Base();
        root["extra"] = Obj("x", 1L);
        FlagOf(root)["extra"] = true;
        ClauseOf(root)["extra"] = List();
        SegmentOf(root)["extra"] = null;
        Assert.True(RulesetParser.Parse(root).Flags.ContainsKey("f"));
    }

    [Fact]
    public void DefaultsNegateToFalseAndReadsItWhenPresent()
    {
        var root = Base();
        ClauseOf(root).Remove("negate");
        Assert.False(FirstClause(root).Negate);
        ClauseOf(root)["negate"] = true;
        Assert.True(FirstClause(root).Negate);
    }

    [Fact]
    public void ReturnsImmutableModelObjects()
    {
        var root = Base();
        FlagOf(root)["variations"] = List(
            Obj("key", "on", "value", Obj("a", List(1L))),
            Obj("key", "off", "value", "off"));
        var rs = RulesetParser.Parse(root);
        Assert.Throws<NotSupportedException>(() => ((IDictionary<string, Flag>)rs.Flags).Remove("f"));
        Assert.Throws<NotSupportedException>(() => ((IDictionary<string, Segment>)rs.Segments).Clear());
        Assert.Throws<NotSupportedException>(() => ((IList<string>)rs.Warnings).Add("x"));
        var f = rs.Flags["f"];
        Assert.Throws<NotSupportedException>(() => ((IList<Variation>)f.Variations).Clear());
        Assert.Throws<NotSupportedException>(() => ((IList<Rule>)f.Rules).Clear());
        Assert.Throws<NotSupportedException>(() => ((IList<Clause>)f.Rules[0].Clauses).Clear());
        Assert.Throws<NotSupportedException>(() => ((ISet<string>)rs.Segments["s"].Included).Clear());
        var value = (IDictionary<string, object?>)f.Variations[0].Value!;
        Assert.Throws<NotSupportedException>(() => value.Clear());
        Assert.Throws<NotSupportedException>(() => ((IList<object?>)value["a"]!).Clear());
    }

    [Fact]
    public void CopiesVariationValuesFromTheTree()
    {
        var root = Base();
        var value = Obj("a", List(1L));
        FlagOf(root)["variations"] = List(Obj("key", "on", "value", value), Obj("key", "off", "value", "off"));
        var rs = RulesetParser.Parse(root);
        value["b"] = 2L;
        L(value["a"]).Add(2L);
        var served = (IReadOnlyDictionary<string, object?>)rs.Flags["f"].Variations[0].Value!;
        Assert.Equal(new[] { "a" }, served.Keys);
        Assert.Single((IReadOnlyList<object?>)served["a"]!);
    }

    private static void AssertParseError(Func<Ruleset> parse, string path)
    {
        var e = Assert.Throws<ParseException>(parse);
        Assert.Equal(path, e.Path);
        Assert.NotEmpty(e.Reason);
        Assert.Equal(path + ": " + e.Reason, e.Message);
    }

    public static TheoryData<string, string, string> TextErrors() => new()
    {
        { "invalid JSON", "{\"schema_version\": 1,", "ruleset" },
        { "trailing data", "{} {}", "ruleset" },
        { "non-object root", "[]", "ruleset" },
        { "null root", "null", "ruleset" },
        {
            "schema_version 1.0",
            "{\"schema_version\": 1.0, \"project\": \"p\", \"environment\": \"e\", \"flags\": {}, \"segments\": {}}",
            "schema_version"
        },
    };

    [Theory]
    [MemberData(nameof(TextErrors))]
    public void RefusesInvalidText(string name, string text, string path)
    {
        Assert.NotEmpty(name);
        AssertParseError(() => RulesetParser.Parse(text), path);
    }

    private static readonly Dictionary<string, (Action<Dictionary<string, object?>> Mutate, string Path)> _errors = new()
    {
        ["schema_version 2"] = (r => r["schema_version"] = 2L, "schema_version"),
        ["schema_version 1.0"] = (r => r["schema_version"] = 1.0, "schema_version"),
        ["schema_version true"] = (r => r["schema_version"] = true, "schema_version"),
        ["schema_version string"] = (r => r["schema_version"] = "1", "schema_version"),
        ["missing schema_version"] = (r => r.Remove("schema_version"), "schema_version"),
        ["missing project"] = (r => r.Remove("project"), "project"),
        ["environment not a string"] = (r => r["environment"] = 1L, "environment"),
        ["flags not an object"] = (r => r["flags"] = List(), "flags"),
        ["flags null"] = (r => r["flags"] = null, "flags"),
        ["segments missing"] = (r => r.Remove("segments"), "segments"),
        ["flag not an object"] = (r => M(r["flags"])["f"] = 1L, "flags.f"),
        ["flag key mismatch"] = (r => FlagOf(r)["key"] = "g", "flags.f.key"),
        ["missing flag key"] = (r => FlagOf(r).Remove("key"), "flags.f.key"),
        ["bad type"] = (r => FlagOf(r)["type"] = "int", "flags.f.type"),
        ["enabled yes"] = (r => FlagOf(r)["enabled"] = "yes", "flags.f.enabled"),
        ["version 1.5"] = (r => FlagOf(r)["version"] = 1.5, "flags.f.version"),
        ["version 1.0"] = (r => FlagOf(r)["version"] = 1.0, "flags.f.version"),
        ["version true"] = (r => FlagOf(r)["version"] = true, "flags.f.version"),
        ["version out of int64"] = (r => FlagOf(r)["version"] = 9.3e18, "flags.f.version"),
        ["empty variations"] = (r => FlagOf(r)["variations"] = List(), "flags.f.variations"),
        ["variations not a list"] = (r => FlagOf(r)["variations"] = Obj(), "flags.f.variations"),
        ["variation not an object"] = (r => FlagOf(r)["variations"] = List(1L), "flags.f.variations[0]"),
        ["variation without value"] =
            (r => FlagOf(r)["variations"] = List(Obj("key", "on")), "flags.f.variations[0].value"),
        ["variation key not a string"] =
            (r => FlagOf(r)["variations"] = List(Obj("key", 1L, "value", true)), "flags.f.variations[0].key"),
        ["duplicate variation"] = (
            r => FlagOf(r)["variations"] = List(Obj("key", "on", "value", true), Obj("key", "on", "value", false)),
            "flags.f.variations[1].key"),
        ["unknown off_variation"] = (r => FlagOf(r)["off_variation"] = "nope", "flags.f.off_variation"),
        ["missing off_variation"] = (r => FlagOf(r).Remove("off_variation"), "flags.f.off_variation"),
        ["missing prerequisites"] = (r => FlagOf(r).Remove("prerequisites"), "flags.f.prerequisites"),
        ["prerequisite not an object"] = (r => FlagOf(r)["prerequisites"] = List("g"), "flags.f.prerequisites[0]"),
        ["prerequisite flag not a string"] = (
            r => FlagOf(r)["prerequisites"] = List(Obj("flag", 1L, "variation", "on")),
            "flags.f.prerequisites[0].flag"),
        ["prerequisite variation missing"] = (
            r => FlagOf(r)["prerequisites"] = List(Obj("flag", "g")), "flags.f.prerequisites[0].variation"),
        ["targets not a list"] = (r => FlagOf(r)["targets"] = Obj(), "flags.f.targets"),
        ["unknown target variation"] = (
            r => FlagOf(r)["targets"] = List(Obj("variation", "nope", "values", List("a"))),
            "flags.f.targets[0].variation"),
        ["target value not a string"] = (
            r => FlagOf(r)["targets"] = List(Obj("variation", "on", "values", List("a", 2L))),
            "flags.f.targets[0].values[1]"),
        ["missing rules"] = (r => FlagOf(r).Remove("rules"), "flags.f.rules"),
        ["rule not an object"] = (r => FlagOf(r)["rules"] = List(1L), "flags.f.rules[0]"),
        ["missing fallthrough"] = (r => FlagOf(r).Remove("fallthrough"), "flags.f.fallthrough"),
        ["description not a string"] = (r => RuleOf(r)["description"] = null, "flags.f.rules[0].description"),
        ["serve with both"] = (
            r => FlagOf(r)["fallthrough"] = Obj("variation", "on", "rollout", Obj()), "flags.f.fallthrough"),
        ["serve with neither"] = (r => RuleOf(r)["serve"] = Obj(), "flags.f.rules[0].serve"),
        ["serve not an object"] = (r => RuleOf(r)["serve"] = "on", "flags.f.rules[0].serve"),
        ["unknown serve variation"] = (
            r => RuleOf(r)["serve"] = Obj("variation", "nope"), "flags.f.rules[0].serve.variation"),
        ["rollout not an object"] = (
            r => FlagOf(r)["fallthrough"] = Obj("rollout", List()), "flags.f.fallthrough.rollout"),
        ["bucket_by not a string"] = (
            r => RolloutOf(r)["bucket_by"] = null, "flags.f.fallthrough.rollout.bucket_by"),
        ["empty weights"] = (r => RolloutOf(r)["weights"] = List(), "flags.f.fallthrough.rollout.weights"),
        ["weights summing to 99999"] = (
            r => RolloutOf(r)["weights"] = List(
                Obj("variation", "on", "weight", 50000L),
                Obj("variation", "off", "weight", 49999L)),
            "flags.f.fallthrough.rollout.weights"),
        ["weight of 100001"] = (
            r => RolloutOf(r)["weights"] = List(Obj("variation", "on", "weight", 100001L)),
            "flags.f.fallthrough.rollout.weights[0].weight"),
        ["negative weight"] = (
            r => RolloutOf(r)["weights"] = List(Obj("variation", "on", "weight", -1L)),
            "flags.f.fallthrough.rollout.weights[0].weight"),
        ["fractional weight"] = (
            r => RolloutOf(r)["weights"] = List(Obj("variation", "on", "weight", 99999.5)),
            "flags.f.fallthrough.rollout.weights[0].weight"),
        ["weight 100000.0"] = (
            r => RolloutOf(r)["weights"] = List(Obj("variation", "on", "weight", 100000.0)),
            "flags.f.fallthrough.rollout.weights[0].weight"),
        ["boolean weight"] = (
            r => RolloutOf(r)["weights"] = List(Obj("variation", "on", "weight", true)),
            "flags.f.fallthrough.rollout.weights[0].weight"),
        ["unknown weight variation"] = (
            r => RolloutOf(r)["weights"] = List(Obj("variation", "nope", "weight", 100000L)),
            "flags.f.fallthrough.rollout.weights[0].variation"),
        ["empty clauses"] = (r => RuleOf(r)["clauses"] = List(), "flags.f.rules[0].clauses"),
        ["attribute not a string"] = (r => ClauseOf(r)["attribute"] = 1L, "flags.f.rules[0].clauses[0].attribute"),
        ["unknown operator"] = (r => ClauseOf(r)["operator"] = "like", "flags.f.rules[0].clauses[0].operator"),
        ["empty clause values"] = (r => ClauseOf(r)["values"] = List(), "flags.f.rules[0].clauses[0].values"),
        ["null clause value"] = (
            r => ClauseOf(r)["values"] = List("a", null), "flags.f.rules[0].clauses[0].values[1]"),
        ["array clause value"] = (
            r => ClauseOf(r)["values"] = List(List()), "flags.f.rules[0].clauses[0].values[0]"),
        ["object clause value"] = (
            r => ClauseOf(r)["values"] = List(Obj()), "flags.f.rules[0].clauses[0].values[0]"),
        ["negate not a boolean"] = (r => ClauseOf(r)["negate"] = "no", "flags.f.rules[0].clauses[0].negate"),
        ["in_segment in a segment rule"] = (
            r => SegmentOf(r)["rules"] = List(Obj("clauses", List(Cl("key", "in_segment", false, "s")))),
            "segments.s.rules[0].clauses[0].operator"),
        ["segment not an object"] = (r => M(r["segments"])["s"] = List(), "segments.s"),
        ["segment included [1]"] = (r => SegmentOf(r)["included"] = List(1L), "segments.s.included[0]"),
        ["segment excluded missing"] = (r => SegmentOf(r).Remove("excluded"), "segments.s.excluded"),
        ["segment rules missing"] = (r => SegmentOf(r).Remove("rules"), "segments.s.rules"),
        ["segment rule not an object"] = (r => SegmentOf(r)["rules"] = List(1L), "segments.s.rules[0]"),
        ["segment rule empty clauses"] = (
            r => SegmentOf(r)["rules"] = List(Obj("clauses", List())), "segments.s.rules[0].clauses"),
    };

    public static TheoryData<string> ErrorNames() => new(_errors.Keys);

    [Theory]
    [MemberData(nameof(ErrorNames))]
    public void RefusesAnInvalidRuleset(string name)
    {
        var (mutate, path) = _errors[name];
        var root = Base();
        mutate(root);
        AssertParseError(() => RulesetParser.Parse(JsonTree.Write(root)), path);
        AssertParseError(() => RulesetParser.Parse((object)root), path);
    }

    [Fact]
    public void NamesTheUnknownOperator()
    {
        var root = Base();
        ClauseOf(root)["operator"] = "like";
        var e = Assert.Throws<ParseException>(() => RulesetParser.Parse(root));
        Assert.Equal("flags.f.rules[0].clauses[0].operator: unknown operator \"like\"", e.Message);
    }

    private static Clause Prepared(string op, params object?[] values)
    {
        var root = Base();
        ClauseOf(root)["operator"] = op;
        ClauseOf(root)["values"] = List(values);
        return FirstClause(root);
    }

    [Theory]
    [InlineData("in")]
    [InlineData("not_in")]
    public void InAndNotInKeepEveryValue(string op)
    {
        var c = Prepared(op, "a", 1.5, true, 2L);
        Assert.Equal(op, c.Operator);
        Assert.Equal(new object[] { "a", 1.5, true, 2L }, c.Prepared);
        Assert.Equal(new object[] { "a", 1.5, true, 2L }, c.Values);
    }

    [Theory]
    [InlineData("contains")]
    [InlineData("starts_with")]
    [InlineData("ends_with")]
    public void StringOperatorsKeepStrings(string op)
    {
        Assert.Equal(new object[] { "a" }, Prepared(op, "a", 1L, true).Prepared);
    }

    [Fact]
    public void MatchesKeepsThePatternsThatCompileAndWarnsAboutTheOthers()
    {
        var root = Base();
        ClauseOf(root)["operator"] = "matches";
        ClauseOf(root)["values"] = List("(", "^a", 1L);
        var rs = RulesetParser.Parse(root);
        var regexes = rs.Flags["f"].Rules[0].Clauses[0].Prepared;
        var regex = Assert.IsType<PortableRegex>(Assert.Single(regexes));
        Assert.True(regex.Test("abc"));
        var warning = Assert.Single(rs.Warnings);
        Assert.Matches(
            new Regex(@"^flags\.f\.rules\[0\]\.clauses\[0\]\.values\[0\]: regex does not compile: .+$"),
            warning);
    }

    [Fact]
    public void WarnsAboutABadPatternInASegmentRule()
    {
        var root = Base();
        SegmentOf(root)["rules"] = List(Obj("clauses", List(Cl("email", "matches", false, "a{,2}"))));
        var warning = Assert.Single(RulesetParser.Parse(root).Warnings);
        Assert.StartsWith("segments.s.rules[0].clauses[0].values[0]: regex does not compile: ", warning);
    }

    [Theory]
    [InlineData("lt")]
    [InlineData("lte")]
    [InlineData("gt")]
    [InlineData("gte")]
    public void NumericOperatorsKeepNumbers(string op)
    {
        Assert.Equal(new object[] { 5L, 2.5 }, Prepared(op, "5", 5L, 2.5, false).Prepared);
    }

    [Theory]
    [InlineData("semver_eq")]
    [InlineData("semver_lt")]
    [InlineData("semver_gt")]
    public void SemverOperatorsKeepValidVersions(string op)
    {
        var version = Assert.IsType<Semver>(Assert.Single(Prepared(op, "x", "1.0.0", 1L).Prepared));
        Assert.Equal(0, Semver.Compare(version, Semver.Parse("1.0.0")!));
    }

    [Fact]
    public void DateOperatorsKeepValidRfc3339StringsNeverNumbers()
    {
        Assert.Equal(new object[] { 1767225600000L }, Prepared("before", 123L, "2026-01-01T00:00:00Z").Prepared);
        Assert.Empty(Prepared("after", "not a date").Prepared);
    }

    [Fact]
    public void InSegmentKeepsStrings()
    {
        Assert.Equal(new object[] { "s" }, Prepared("in_segment", "s", 1L).Prepared);
    }
}
