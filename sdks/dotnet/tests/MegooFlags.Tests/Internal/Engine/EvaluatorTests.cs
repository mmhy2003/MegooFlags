using System.Collections.Generic;
using MegooFlags.Internal.Engine;
using Xunit;
using static MegooFlags.Tests.RulesetGen;

namespace MegooFlags.Tests.Internal.Engine;

public class EvaluatorTests
{
    private static Ruleset Build(params Dictionary<string, object?>[] flags) => BuildWithSegments(Obj(), flags);

    private static Ruleset BuildWithSegments(
        Dictionary<string, object?> segments,
        params Dictionary<string, object?>[] flags) =>
        // From the tree, not JSON text: System.Text.Json cannot write the lone surrogates of the operator cases.
        RulesetParser.Parse((object)RulesetJson(flags, segments));

    private static Result Eval(Ruleset rs, string flag, object? ctx, object? def) =>
        Evaluator.Evaluate(rs, flag, ctx, def, null);

    private static void AssertError(Result r, object? def, string code)
    {
        Assert.Equal(def, r.Value);
        Assert.Null(r.Variant);
        Assert.Equal(Reasons.Error, r.Reason);
        Assert.Equal(code, r.ErrorCode);
        Assert.Null(r.RuleIndex);
        Assert.Null(r.Prerequisite);
        Assert.NotEmpty(r.ErrorMessage);
    }

    private static void AssertServed(Result r, string variant, string reason)
    {
        Assert.Equal(variant, r.Variant);
        Assert.Equal(reason, r.Reason);
        Assert.Null(r.ErrorCode);
        Assert.Equal("", r.ErrorMessage);
    }

    // Errors

    public static TheoryData<string> InvalidContexts() => new("array", "string", "null", "number", "read-only map");

    private static object? InvalidContext(string name) => name switch
    {
        "array" => List(),
        "string" => "user",
        "number" => 1L,
        "read-only map" => new System.Collections.ObjectModel.ReadOnlyDictionary<string, object?>(Obj()),
        _ => null,
    };

    [Theory]
    [MemberData(nameof(InvalidContexts))]
    public void ANonObjectContextIsInvalidContext(string name)
    {
        AssertError(Eval(Build(FlagJson("f")), "f", InvalidContext(name), "d"), "d", ErrorCodes.InvalidContext);
    }

    [Fact]
    public void AMissingFlagIsFlagNotFound()
    {
        AssertError(Eval(Build(FlagJson("f")), "nope", Obj(), "d"), "d", ErrorCodes.FlagNotFound);
    }

    // Reasons

    [Fact]
    public void ServesStaticForAFlagWithNothingButAFallthrough()
    {
        var r = Eval(Build(FlagJson("f")), "f", Obj(), "d");
        Assert.Equal("on", r.Value);
        AssertServed(r, "on", Reasons.Static);
        Assert.Null(r.RuleIndex);
        Assert.Null(r.Prerequisite);
        Assert.Empty(r.Metadata());
    }

    [Fact]
    public void ServesDefaultWhenAFlagHasTargetsOrRulesThatDoNotMatch()
    {
        var rs = Build(
            FlagJson("t", "targets", List(Obj("variation", "off", "values", List("x")))),
            RuleFlag("r", Cl("a", "in", false, "x")));
        AssertServed(Eval(rs, "t", Obj("key", "u"), "d"), "on", Reasons.Default);
        AssertServed(Eval(rs, "r", Obj(), "d"), "off", Reasons.Default);
    }

    [Fact]
    public void ServesTheOffVariationWhenDisabled()
    {
        var r = Eval(Build(FlagJson("f", "enabled", false)), "f", Obj(), "d");
        Assert.Equal("off", r.Value);
        AssertServed(r, "off", Reasons.Disabled);
    }

    [Fact]
    public void ServesTheDefaultWithoutAVariantWhenDisabledWithoutAnOffVariation()
    {
        var r = Eval(Build(FlagJson("f", "enabled", false, "off_variation", null)), "f", Obj(), "d");
        Assert.Equal("d", r.Value);
        Assert.Null(r.Variant);
        Assert.Equal(Reasons.Disabled, r.Reason);
        Assert.Null(r.ErrorCode);
        Assert.Equal("", r.ErrorMessage);
        Assert.Null(r.RuleIndex);
        Assert.Null(r.Prerequisite);
    }

    // Type check

    private static Ruleset Typed() =>
        Build(
            FlagJson("s"),
            FlagJson(
                "b",
                "type", "boolean",
                "variations", List(Obj("key", "on", "value", true), Obj("key", "off", "value", false))),
            FlagJson(
                "n",
                "type", "number",
                "variations", List(Obj("key", "on", "value", 1.5), Obj("key", "off", "value", 0L))),
            FlagJson(
                "i",
                "type", "number",
                "variations", List(Obj("key", "on", "value", 2L), Obj("key", "off", "value", 0L))),
            FlagJson(
                "j",
                "type", "json",
                "variations", List(Obj("key", "on", "value", null), Obj("key", "off", "value", Obj()))),
            FlagJson("x", "enabled", false, "off_variation", null));

    [Theory]
    [InlineData("s", "string")]
    [InlineData("b", "boolean")]
    [InlineData("n", "number")]
    [InlineData("i", "number")]
    [InlineData("j", "json")]
    [InlineData("s", "json")]
    [InlineData("s", null)]
    public void TheTypeCheckPasses(string flag, string? type)
    {
        Assert.Null(Evaluator.Evaluate(Typed(), flag, Obj(), "d", type).ErrorCode);
    }

    [Theory]
    [InlineData("s", "boolean")]
    [InlineData("s", "number")]
    [InlineData("b", "number")]
    [InlineData("b", "string")]
    [InlineData("n", "string")]
    [InlineData("j", "string")]
    public void TheTypeCheckFailsWithTypeMismatch(string flag, string type)
    {
        AssertError(Evaluator.Evaluate(Typed(), flag, Obj(), "d", type), "d", ErrorCodes.TypeMismatch);
    }

    [Fact]
    public void ANullVariationValueServedForJson()
    {
        var r = Evaluator.Evaluate(Typed(), "j", Obj(), "d", "json");
        Assert.Null(r.Value);
        AssertServed(r, "on", Reasons.Static);
    }

    [Fact]
    public void TheTypeIsNotCheckedWhenNoVariationWasServed()
    {
        var r = Evaluator.Evaluate(Typed(), "x", Obj(), 7L, "boolean");
        Assert.Equal(7L, r.Value);
        Assert.Equal(Reasons.Disabled, r.Reason);
        Assert.Null(r.ErrorCode);
    }

    // Prerequisites

    [Fact]
    public void FailsWithTheFirstUnmetPrerequisite()
    {
        var rs = Build(
            FlagJson("ok"),
            FlagJson("off", "enabled", false),
            FlagJson(
                "f",
                "prerequisites", List(
                    Obj("flag", "ok", "variation", "on"),
                    Obj("flag", "off", "variation", "on"),
                    Obj("flag", "missing", "variation", "on"))));
        var r = Eval(rs, "f", Obj(), "d");
        Assert.Equal("off", r.Value);
        AssertServed(r, "off", Reasons.PrerequisiteFailed);
        Assert.Equal("off", r.Prerequisite);
        Assert.Equal(new Dictionary<string, object?> { ["prerequisite"] = "off" }, r.Metadata());
    }

    [Fact]
    public void FailsOnAMissingPrerequisiteFlagOrADifferentVariant()
    {
        var rs = Build(
            FlagJson("p"),
            FlagJson("a", "prerequisites", List(Obj("flag", "missing", "variation", "on"))),
            FlagJson("b", "prerequisites", List(Obj("flag", "p", "variation", "off"))));
        Assert.Equal("missing", Eval(rs, "a", Obj(), "d").Prerequisite);
        Assert.Equal(Reasons.PrerequisiteFailed, Eval(rs, "b", Obj(), "d").Reason);
    }

    [Fact]
    public void PassesWhenThePrerequisiteServesItsOffVariationAndThatIsTheOneRequired()
    {
        var rs = Build(
            FlagJson("p", "prerequisites", List(Obj("flag", "missing", "variation", "on"))),
            FlagJson("f", "prerequisites", List(Obj("flag", "p", "variation", "off"))));
        AssertServed(Eval(rs, "f", Obj(), "d"), "on", Reasons.Default);
    }

    [Fact]
    public void FailsWhenThePrerequisiteServesNoVariation()
    {
        var rs = Build(
            FlagJson("p", "off_variation", null, "prerequisites", List(Obj("flag", "missing", "variation", "on"))),
            FlagJson("f", "prerequisites", List(Obj("flag", "p", "variation", "off"))));
        Assert.Equal(Reasons.PrerequisiteFailed, Eval(rs, "f", Obj(), "d").Reason);
    }

    /// <summary>Chain is flag-0 requiring flag-1, ..., flag-(n-1) requiring flag-n.</summary>
    private static Ruleset Chain(int n)
    {
        var flags = new List<Dictionary<string, object?>>();
        for (var i = 0; i <= n; i++)
        {
            flags.Add(FlagJson(
                "flag-" + i,
                "prerequisites", i < n ? List(Obj("flag", "flag-" + (i + 1), "variation", "on")) : List()));
        }
        return BuildWithSegments(Obj(), flags.ToArray());
    }

    [Fact]
    public void FollowsAChainOf32Prerequisites()
    {
        AssertServed(Eval(Chain(32), "flag-0", Obj(), "d"), "on", Reasons.Default);
    }

    [Fact]
    public void RefusesAChainOf33Prerequisites()
    {
        AssertError(Eval(Chain(33), "flag-0", Obj(), "d"), "d", ErrorCodes.General);
    }

    [Fact]
    public void RefusesACycle()
    {
        var rs = Build(
            FlagJson("a", "prerequisites", List(Obj("flag", "b", "variation", "on"))),
            FlagJson("b", "prerequisites", List(Obj("flag", "a", "variation", "on"))));
        AssertError(Eval(rs, "a", Obj(), "d"), "d", ErrorCodes.General);
    }

    // Targets and rules

    private static Ruleset Targeted() =>
        Build(FlagJson(
            "f",
            "targets", List(
                Obj("variation", "off", "values", List("u1", "42")),
                Obj("variation", "on", "values", List("u1", "u2"))),
            "rules", List(
                Obj("clauses", List(Cl("a", "in", false, 1L)), "serve", Obj("variation", "off")),
                Obj("clauses", List(Cl("a", "in", false, 2L)), "serve", Obj("variation", "on"))),
            "fallthrough", Obj("variation", "off")));

    [Fact]
    public void ServesTheFirstTargetContainingTheKey()
    {
        var rs = Targeted();
        AssertServed(Eval(rs, "f", Obj("key", "u1"), "d"), "off", Reasons.TargetingMatch);
        var r = Eval(rs, "f", Obj("key", "u2"), "d");
        AssertServed(r, "on", Reasons.TargetingMatch);
        Assert.Null(r.RuleIndex);
    }

    [Fact]
    public void MatchesTargetsOnlyWithAStringKey()
    {
        AssertServed(Eval(Targeted(), "f", Obj("key", 42L), "d"), "off", Reasons.Default);
    }

    [Fact]
    public void ReportsTheIndexOfTheMatchingRule()
    {
        var rs = Targeted();
        var r = Eval(rs, "f", Obj("a", 2L), "d");
        AssertServed(r, "on", Reasons.TargetingMatch);
        Assert.Equal(1, r.RuleIndex);
        Assert.Equal(new Dictionary<string, object?> { ["rule_index"] = 1 }, r.Metadata());
        Assert.Equal(0, Eval(rs, "f", Obj("a", 1L), "d").RuleIndex);
    }

    [Fact]
    public void NeedsEveryClauseOfARule()
    {
        var rs = Build(RuleFlag("f", Cl("a", "in", false, 1L), Cl("b", "in", false, 2L)));
        Assert.Equal("on", Eval(rs, "f", Obj("a", 1L, "b", 2L), "d").Variant);
        Assert.Equal("off", Eval(rs, "f", Obj("a", 1L, "b", 3L), "d").Variant);
    }

    // Clauses

    private static bool Matches(Dictionary<string, object?> clause, Dictionary<string, object?> ctx) =>
        Eval(Build(RuleFlag("f", clause)), "f", ctx, "d").Variant == "on";

    private static readonly Dictionary<string, (bool Negate, Dictionary<string, object?> Ctx, bool Want)> _shapes =
        new()
        {
            ["missing attribute"] = (false, Obj(), false),
            ["missing attribute, negated"] = (true, Obj(), false),
            ["null attribute, negated"] = (true, Obj("a", null), false),
            ["wrong type"] = (false, Obj("a", 1L), false),
            ["wrong type, negated"] = (true, Obj("a", 1L), true),
            ["empty array"] = (false, Obj("a", List()), false),
            ["empty array, negated"] = (true, Obj("a", List()), true),
            ["array with a match"] = (false, Obj("a", List(1L, "x")), true),
            ["array with a match, negated"] = (true, Obj("a", List(1L, "x")), false),
            ["object"] = (false, Obj("a", Obj("x", "x")), false),
            ["nested array"] = (false, Obj("a", List(List("x"))), false),
        };

    public static TheoryData<string> ClauseShapes() => new(_shapes.Keys);

    [Theory]
    [MemberData(nameof(ClauseShapes))]
    public void AppliesAClauseToTheShapeOfTheAttribute(string name)
    {
        var (negate, ctx, want) = _shapes[name];
        Assert.Equal(want, Matches(Cl("a", "contains", negate, "x"), ctx));
    }

    private static readonly (string Op, object?[] Values, object? Attribute, bool Want)[] _operators =
    [
        ("in", ["a", 1L], "a", true),
        ("in", ["a", 1L], 1.0, true),
        ("in", [1L], true, false),
        ("in", [true], true, true),
        ("in", ["1"], 1L, false),
        ("in", [9007199254740993L], 9007199254740992L, false),
        ("not_in", ["a"], "b", true),
        ("not_in", ["a"], "a", false),
        ("not_in", ["a"], List(Obj()), false),
        ("contains", ["ell"], "hello", true),
        ("contains", ["x"], "hello", false),
        ("contains", ["\uDE00"], "\U0001F600", false),
        ("contains", ["\uDE00"], "a\uDE00", true),
        ("starts_with", ["he"], "hello", true),
        ("starts_with", ["lo"], "hello", false),
        ("starts_with", ["\uD83D"], "\U0001F600", false),
        ("ends_with", ["lo"], "hello", true),
        ("ends_with", ["he"], "hello", false),
        ("ends_with", ["\uDE00"], "\U0001F600", false),
        ("matches", ["^h.l"], "hello", true),
        ("matches", [@"^\d+$"], "12a", false),
        ("matches", [@"^\d+$"], 12L, false),
        ("lt", [5L], 4L, true),
        ("lt", [5L], 5L, false),
        ("lte", [5L], 5L, true),
        ("gt", [5L], 6L, true),
        ("gt", [5L], "6", false),
        ("gte", [5L], 5L, true),
        ("gte", [5L], 4.9, false),
        ("lt", [5L], true, false),
        ("gt", [9007199254740992L], 9007199254740993L, true),
        ("semver_eq", ["1.0.0"], "1.0.0+build", true),
        ("semver_lt", ["1.0.0"], "1.0.0-rc.1", true),
        ("semver_gt", ["1.0.0"], "1.0.1", true),
        ("semver_gt", ["1.0.0"], "v1.0.1", false),
        ("before", ["2026-01-01T00:00:00Z"], "2025-12-31T23:59:59Z", true),
        ("before", ["2026-01-01T00:00:00Z"], 1767225599999L, true),
        ("before", ["2026-01-01T00:00:00Z"], 1767225600000L, false),
        ("after", ["2026-01-01T00:00:00Z"], "2026-01-01T00:00:00.001Z", true),
        ("after", ["2026-01-01T00:00:00Z"], "not a date", false),
        ("after", [0L], 1L, false),
    ];

    // The index keeps lone surrogates out of the theory data, which xunit would replace.
    public static TheoryData<int> Operators()
    {
        var data = new TheoryData<int>();
        for (var i = 0; i < _operators.Length; i++)
        {
            data.Add(i);
        }
        return data;
    }

    [Theory]
    [MemberData(nameof(Operators))]
    public void AppliesTheOperator(int index)
    {
        var (op, values, attribute, want) = _operators[index];
        Assert.Equal(want, Matches(Cl("a", op, false, values), Obj("a", attribute)));
    }

    // Segments

    private static readonly Dictionary<string, (Dictionary<string, object?> Ctx, bool Want)> _segmentCases = new()
    {
        ["included"] = (Obj("key", "in"), true),
        ["excluded before included"] = (Obj("key", "both"), false),
        ["excluded even when a rule matches"] = (Obj("key", "out", "org", "acme"), false),
        ["by rule"] = (Obj("key", "other", "org", "acme"), true),
        ["by rule without a key"] = (Obj("org", "acme"), true),
        ["a number key is not looked up"] = (Obj("key", 1L, "org", "x"), false),
        ["no match"] = (Obj("key", "other"), false),
    };

    public static TheoryData<string> SegmentCases() => new(_segmentCases.Keys);

    [Theory]
    [MemberData(nameof(SegmentCases))]
    public void MatchesSegments(string name)
    {
        var rs = BuildWithSegments(
            Obj("s", Obj(
                "included", List("in", "both"),
                "excluded", List("out", "both"),
                "rules", List(Obj("clauses", List(Cl("org", "in", false, "acme")))))),
            RuleFlag("f", Cl("ignored", "in_segment", false, "s", "unknown")),
            RuleFlag("n", Cl("ignored", "in_segment", true, "s")));
        var (ctx, want) = _segmentCases[name];
        Assert.Equal(want ? "on" : "off", Eval(rs, "f", ctx, "d").Variant);
        Assert.Equal(want ? "off" : "on", Eval(rs, "n", ctx, "d").Variant);
    }

    [Fact]
    public void AnUnknownSegmentNeverMatches()
    {
        var rs = Build(RuleFlag("u", Cl("ignored", "in_segment", false, "unknown")));
        Assert.Equal("off", Eval(rs, "u", Obj("key", "in"), "d").Variant);
    }

    // Rollouts

    private static Ruleset Rollouts()
    {
        var weights = List(Obj("variation", "on", "weight", 30000L), Obj("variation", "off", "weight", 70000L));
        return Build(
            FlagJson("f", "fallthrough", Obj("rollout", Obj("bucket_by", "key", "weights", weights))),
            FlagJson("g", "fallthrough", Obj("rollout", Obj("bucket_by", "org", "weights", weights))));
    }

    [Fact]
    public void SplitsByTheBucketOfTheAttribute()
    {
        // Bucket("f", "u") is 68258: past "on" (30000), so "off".
        AssertServed(Eval(Rollouts(), "f", Obj("key", "u"), "d"), "off", Reasons.Split);
    }

    [Fact]
    public void BucketsByAnotherAttribute()
    {
        var rs = Rollouts();
        var byOrg = new HashSet<string?>();
        for (var i = 0; i < 50; i++)
        {
            byOrg.Add(Eval(rs, "g", Obj("key", "same", "org", "o" + i), "d").Variant);
        }
        Assert.Equal(new HashSet<string?> { "on", "off" }, byOrg);
    }

    [Fact]
    public void UsesBucket0WhenTheValueIsMissingOrCannotBeBucketed()
    {
        var rs = Rollouts();
        foreach (var ctx in new[] { Obj(), Obj("org", null), Obj("org", 1.5), Obj("org", true) })
        {
            AssertServed(Eval(rs, "g", ctx, "d"), "on", Reasons.Split);
        }
    }
}
