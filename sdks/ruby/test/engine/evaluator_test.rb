# frozen_string_literal: true

require_relative "../test_helper"
require_relative "../support/ruleset_gen"
require "megooflags/internal/engine/parser"
require "megooflags/internal/engine/evaluator"

class EvaluatorTest < Minitest::Test
  Engine = MegooFlags::Internal::Engine
  G = RulesetGen

  def ruleset(flags, segments = {}) = Engine::Parser.parse(G.ruleset_json(flags, segments))

  def evaluate(ruleset, key, context = {}, default = "dflt", expected_type = nil)
    Engine::Evaluator.evaluate(ruleset, key, context, default, expected_type)
  end

  def assert_result(result, value, variant, reason, error_code = nil)
    value.nil? ? assert_nil(result.value) : assert_equal(value, result.value)
    variant.nil? ? assert_nil(result.variant) : assert_equal(variant, result.variant)
    assert_equal reason, result.reason
    error_code.nil? ? assert_nil(result.error_code) : assert_equal(error_code, result.error_code)
  end

  # one_rule is a flag f serving on when the clause matches, else off.
  def one_rule(clause, segments = {})
    ruleset([G.flag_json("f", rules: [G.rule("on", clause)], fallthrough: { "variation" => "off" })], segments)
  end

  def matches?(clause, context, segments = {})
    evaluate(one_rule(clause, segments), "f", context).variant == "on"
  end

  def test_disabled_serves_the_off_variation
    rs = ruleset([G.flag_json("f", enabled: false), G.flag_json("g", enabled: false, off_variation: nil)])
    assert_result evaluate(rs, "f"), "off", "off", "DISABLED"
    assert_result evaluate(rs, "g"), "dflt", nil, "DISABLED"
  end

  def test_flag_not_found
    result = evaluate(ruleset([]), "missing")
    assert_result result, "dflt", nil, "ERROR", "FLAG_NOT_FOUND"
    assert_match(/missing/, result.error_message)
  end

  def test_invalid_context
    rs = ruleset([G.flag_json("f")])
    [[], "ctx", nil, 1, { key: "a" }].each do |context|
      assert_result evaluate(rs, "f", context), "dflt", nil, "ERROR", "INVALID_CONTEXT"
    end
  end

  def test_targets
    rs = ruleset([G.flag_json("f", targets: [{ "variation" => "off", "values" => %w[alice 1] }])])
    assert_result evaluate(rs, "f", { "key" => "alice" }), "off", "off", "TARGETING_MATCH"
    assert_result evaluate(rs, "f", { "key" => 1 }), "on", "on", "DEFAULT"
    assert_result evaluate(rs, "f", { "key" => "bob" }), "on", "on", "DEFAULT"
  end

  def test_first_matching_rule_and_its_index
    rs = ruleset([G.flag_json("f", rules: [G.rule("on", G.clause("plan", "in", "free")),
                                           G.rule("off", G.clause("plan", "in", "pro"))])])
    result = evaluate(rs, "f", { "plan" => "pro" })
    assert_result result, "off", "off", "TARGETING_MATCH"
    assert_equal 1, result.rule_index
    assert_equal({ "rule_index" => 1 }, result.metadata)
    assert_empty evaluate(rs, "f", { "plan" => "x" }).metadata
  end

  def test_all_clauses_of_a_rule_must_match
    rs = ruleset([G.flag_json("f", rules: [G.rule("off", G.clause("a", "in", 1), G.clause("b", "in", 2))])])
    assert_equal "off", evaluate(rs, "f", { "a" => 1, "b" => 2 }).variant
    assert_equal "on", evaluate(rs, "f", { "a" => 1, "b" => 3 }).variant
  end

  def test_clause_negate
    negated = G.clause("plan", "in", "pro", negate: true)
    assert matches?(negated, { "plan" => "free" })
    refute matches?(negated, { "plan" => "pro" })
    refute matches?(negated, {}), "a missing attribute is false without negate"
    refute matches?(negated, { "plan" => nil }), "a null attribute is false without negate"
    assert matches?(negated, { "plan" => { "a" => 1 } }), "an object never matches, then negate"
    assert matches?(negated, { "plan" => [] }), "an empty array matches nothing, then negate"
  end

  def test_arrays_match_any_element
    assert matches?(G.clause("tags", "in", "b"), { "tags" => %w[a b] })
    refute matches?(G.clause("tags", "in", "c"), { "tags" => %w[a b] })
    refute matches?(G.clause("tags", "in", "b"), { "tags" => [["b"]] })
  end

  def test_in_and_not_in
    assert matches?(G.clause("n", "in", 1), { "n" => 1.0 })
    refute matches?(G.clause("n", "in", 1), { "n" => true })
    refute matches?(G.clause("n", "in", "1"), { "n" => 1 })
    assert matches?(G.clause("n", "not_in", "a"), { "n" => "b" })
    refute matches?(G.clause("n", "not_in", "a"), { "n" => "a" })
    refute matches?(G.clause("n", "not_in", "a"), { "n" => { "x" => 1 } })
  end

  def test_string_operators
    assert matches?(G.clause("s", "contains", "ell"), { "s" => "hello" })
    refute matches?(G.clause("s", "contains", "1"), { "s" => 1 })
    assert matches?(G.clause("s", "starts_with", "he"), { "s" => "hello" })
    refute matches?(G.clause("s", "starts_with", "lo"), { "s" => "hello" })
    assert matches?(G.clause("s", "ends_with", "lo"), { "s" => "hello" })
    assert matches?(G.clause("s", "ends_with", "é"), { "s" => "café" })
    refute matches?(G.clause("s", "ends_with", "he"), { "s" => "hello" })
  end

  def test_matches
    assert matches?(G.clause("s", "matches", "^a.c$"), { "s" => "abc" })
    refute matches?(G.clause("s", "matches", "^a.c$"), { "s" => "abcd" })
    refute matches?(G.clause("s", "matches", "a"), { "s" => 1 })
    refute matches?(G.clause("s", "matches", "(?=a)"), { "s" => "a" })
  end

  def test_numeric_operators
    assert matches?(G.clause("n", "lt", 2), { "n" => 1 })
    refute matches?(G.clause("n", "lt", 2), { "n" => 2 })
    assert matches?(G.clause("n", "lte", 2), { "n" => 2.0 })
    assert matches?(G.clause("n", "gt", 1.5), { "n" => 2 })
    assert matches?(G.clause("n", "gte", 2), { "n" => 2 })
    refute matches?(G.clause("n", "gt", 1), { "n" => "2" })
    refute matches?(G.clause("n", "gt", 0), { "n" => true })
    assert matches?(G.clause("n", "gt", 9_007_199_254_740_992), { "n" => 9_007_199_254_740_993 })
  end

  def test_semver_operators
    assert matches?(G.clause("v", "semver_eq", "1.2.3"), { "v" => "1.2.3+build" })
    assert matches?(G.clause("v", "semver_lt", "1.0.0"), { "v" => "1.0.0-rc.1" })
    assert matches?(G.clause("v", "semver_gt", "1.5.0"), { "v" => "1.10.0" })
    refute matches?(G.clause("v", "semver_gt", "1.5.0"), { "v" => "v2.0.0" })
    refute matches?(G.clause("v", "semver_eq", "1.0.0"), { "v" => 1 })
  end

  def test_date_operators
    assert matches?(G.clause("t", "before", "2025-01-01T00:00:00Z"), { "t" => "2024-12-31T23:59:59Z" })
    assert matches?(G.clause("t", "before", "1970-01-01T00:00:01Z"), { "t" => 999.9 })
    refute matches?(G.clause("t", "before", "1970-01-01T00:00:01Z"), { "t" => 1000 })
    assert matches?(G.clause("t", "after", "1970-01-01T00:00:00Z"), { "t" => "1970-01-01T01:00:00+00:30" })
    refute matches?(G.clause("t", "after", 0), { "t" => 5 }), "a number clause value never matches"
    refute matches?(G.clause("t", "after", "1970-01-01T00:00:00Z"), { "t" => "garbage" })
  end

  INVALID_UTF8 = (+"\xFF").force_encoding(Encoding::UTF_8)

  def test_invalid_utf8_strings_never_match
    [
      G.clause("a", "semver_eq", "1.2.3"), G.clause("a", "semver_lt", "1.2.3"), G.clause("a", "semver_gt", "1.2.3"),
      G.clause("a", "before", "2025-01-01T00:00:00Z"), G.clause("a", "after", "1970-01-01T00:00:00Z"),
      G.clause("a", "matches", ""), G.clause("a", "contains", "x"), G.clause("a", "starts_with", "x"),
      G.clause("a", "ends_with", "x")
    ].each do |clause|
      refute matches?(clause, { "a" => INVALID_UTF8 }), clause["operator"]
      refute matches?(clause, { "a" => (+"1.2.3\xFF").force_encoding(Encoding::UTF_8) }), clause["operator"]
    end
  end

  def test_invalid_utf8_element_is_skipped
    assert matches?(G.clause("a", "semver_eq", "1.2.3"), { "a" => [INVALID_UTF8, "1.2.3"] })
    assert matches?(G.clause("a", "before", "2025-01-01T00:00:00Z"), { "a" => [INVALID_UTF8, "2024-01-01T00:00:00Z"] })
    assert matches?(G.clause("a", "after", "1970-01-01T00:00:00Z"), { "a" => [INVALID_UTF8, "2024-01-01T00:00:00Z"] })
    assert matches?(G.clause("a", "matches", "^b$"), { "a" => [INVALID_UTF8, "b"] })
    assert matches?(G.clause("a", "semver_eq", "1.2.3", negate: true), { "a" => INVALID_UTF8 })
  end

  def segments
    { "s" => { "included" => %w[in both], "excluded" => %w[out both],
               "rules" => [{ "clauses" => [G.clause("org", "in", "acme")] }] } }
  end

  def test_segments
    clause = G.clause("ignored", "in_segment", "s")
    assert matches?(clause, { "key" => "in" }, segments)
    refute matches?(clause, { "key" => "both", "org" => "acme" }, segments), "excluded wins"
    refute matches?(clause, { "key" => "out", "org" => "acme" }, segments)
    assert matches?(clause, { "key" => "other", "org" => "acme" }, segments)
    assert matches?(clause, { "org" => "acme" }, segments), "no key: the rules decide"
    refute matches?(clause, { "key" => "other" }, segments)
    refute matches?(G.clause("k", "in_segment", "missing"), { "key" => "in" }, segments)
    assert matches?(G.clause("k", "in_segment", "missing", "s"), { "key" => "in" }, segments)
    assert matches?(G.clause("k", "in_segment", "s", negate: true), { "key" => "out" }, segments)
  end

  def rollout(bucket_by)
    { "rollout" => { "bucket_by" => bucket_by, "weights" => [{ "variation" => "on", "weight" => 50_000 },
                                                             { "variation" => "off", "weight" => 50_000 }] } }
  end

  def test_rollout
    rs = ruleset([G.flag_json("f", fallthrough: rollout("org"))])
    assert_equal "SPLIT", evaluate(rs, "f", {}).reason
    assert_equal "on", evaluate(rs, "f", {}).variant, "a missing value is bucket 0"
    assert_equal "on", evaluate(rs, "f", { "org" => 1.5 }).variant, "a value that can't be bucketed is bucket 0"
    expected = Engine::Bucketing.bucket("f", "acme") < 50_000 ? "on" : "off"
    assert_equal expected, evaluate(rs, "f", { "key" => "x", "org" => "acme" }).variant
    expected = Engine::Bucketing.bucket("f", "42") < 50_000 ? "on" : "off"
    assert_equal expected, evaluate(rs, "f", { "org" => 42.0 }).variant
  end

  def test_rollout_in_a_rule_is_targeting_match
    rule = { "clauses" => [G.clause("a", "in", 1)], "serve" => rollout("key") }
    rs = ruleset([G.flag_json("f", rules: [rule])])
    assert_equal "TARGETING_MATCH", evaluate(rs, "f", { "a" => 1 }).reason
  end

  def test_prerequisite_failed
    rs = ruleset([G.flag_json("p", fallthrough: { "variation" => "off" }),
                  G.flag_json("f", prerequisites: [{ "flag" => "p", "variation" => "on" }])])
    result = evaluate(rs, "f")
    assert_result result, "off", "off", "PREREQUISITE_FAILED"
    assert_equal "p", result.prerequisite
    assert_equal({ "prerequisite" => "p" }, result.metadata)
  end

  def test_prerequisite_missing_or_disabled_fails
    rs = ruleset([G.flag_json("d", enabled: false, off_variation: "on"),
                  G.flag_json("f", prerequisites: [{ "flag" => "missing", "variation" => "on" }]),
                  G.flag_json("g", prerequisites: [{ "flag" => "d", "variation" => "on" }])])
    assert_equal "PREREQUISITE_FAILED", evaluate(rs, "f").reason
    assert_equal "PREREQUISITE_FAILED", evaluate(rs, "g").reason
  end

  def test_prerequisite_met
    rs = ruleset([G.flag_json("p"), G.flag_json("f", prerequisites: [{ "flag" => "p", "variation" => "on" }])])
    assert_result evaluate(rs, "f"), "on", "on", "DEFAULT"
  end

  def test_prerequisite_serving_its_off_variation_can_pass
    # p's prerequisite fails, so p serves its off variation "on", which f requires.
    rs = ruleset([G.flag_json("q", fallthrough: { "variation" => "off" }),
                  G.flag_json("p", off_variation: "on", prerequisites: [{ "flag" => "q", "variation" => "on" }]),
                  G.flag_json("f", prerequisites: [{ "flag" => "p", "variation" => "on" }])])
    assert_equal "PREREQUISITE_FAILED", evaluate(rs, "p").reason
    assert_result evaluate(rs, "f"), "on", "on", "DEFAULT"
  end

  # chain is flags c0..c<n> where each c<i> requires c<i+1> to serve on.
  def chain(length)
    flags = Array.new(length) do |i|
      G.flag_json("c#{i}", prerequisites: [{ "flag" => "c#{i + 1}", "variation" => "on" }])
    end
    ruleset(flags + [G.flag_json("c#{length}")])
  end

  def test_prerequisite_depth_guard
    assert_result evaluate(chain(32), "c0"), "on", "on", "DEFAULT"
    assert_result evaluate(chain(33), "c0"), "dflt", nil, "ERROR", "GENERAL"
  end

  def test_prerequisite_cycle_is_general
    rs = ruleset([G.flag_json("a", prerequisites: [{ "flag" => "b", "variation" => "on" }]),
                  G.flag_json("b", prerequisites: [{ "flag" => "a", "variation" => "on" }])])
    assert_result evaluate(rs, "a"), "dflt", nil, "ERROR", "GENERAL"
  end

  def test_static_and_default_reasons
    rs = ruleset([G.flag_json("s"), G.flag_json("t", targets: [{ "variation" => "off", "values" => ["x"] }])])
    assert_result evaluate(rs, "s"), "on", "on", "STATIC"
    assert_result evaluate(rs, "t"), "on", "on", "DEFAULT"
  end

  def test_type_mismatch
    rs = ruleset([G.flag_json("s"), G.flag_json("b", type: "boolean", variations: [{ "key" => "on", "value" => true }],
                                                     off_variation: nil),
                  G.flag_json("n", type: "number", variations: [{ "key" => "on", "value" => 1.5 }], off_variation: nil),
                  G.flag_json("d", enabled: false, off_variation: nil)])
    assert_result evaluate(rs, "s", {}, "dflt", "boolean"), "dflt", nil, "ERROR", "TYPE_MISMATCH"
    assert_result evaluate(rs, "s", {}, "dflt", "string"), "on", "on", "STATIC"
    assert_result evaluate(rs, "b", {}, "dflt", "boolean"), true, "on", "STATIC"
    assert_result evaluate(rs, "b", {}, "dflt", "number"), "dflt", nil, "ERROR", "TYPE_MISMATCH"
    assert_result evaluate(rs, "n", {}, "dflt", "number"), 1.5, "on", "STATIC"
    assert_result evaluate(rs, "n", {}, "dflt", "json"), 1.5, "on", "STATIC"
    assert_result evaluate(rs, "d", {}, "dflt", "boolean"), "dflt", nil, "DISABLED"
  end

  def test_null_variation_value_served_for_json
    rs = ruleset([G.flag_json("j", type: "json", variations: [{ "key" => "nothing", "value" => nil }],
                                   off_variation: nil, fallthrough: { "variation" => "nothing" })])
    assert_result evaluate(rs, "j", {}, "dflt", "json"), nil, "nothing", "STATIC"
  end

  def test_unknown_expected_type_raises
    rs = ruleset([G.flag_json("s")])
    assert_raises(ArgumentError) { evaluate(rs, "s", {}, "dflt", "integer") }
  end

  def test_result_is_frozen
    assert_predicate evaluate(ruleset([G.flag_json("s")]), "s"), :frozen?
  end
end
