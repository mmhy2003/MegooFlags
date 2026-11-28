# frozen_string_literal: true

require "json"
require_relative "../test_helper"
require_relative "../support/ruleset_gen"
require "megooflags/internal/engine/parser"

class ParserTest < Minitest::Test
  Engine = MegooFlags::Internal::Engine
  Parser = Engine::Parser
  G = RulesetGen

  def valid_tree
    flag = G.flag_json(
      "f",
      targets: [{ "variation" => "on", "values" => %w[alice bob] }],
      prerequisites: [{ "flag" => "g", "variation" => "on" }],
      rules: [{ "description" => "pro", "clauses" => [G.clause("plan", "in", "pro")],
                "serve" => { "variation" => "on" } }],
      fallthrough: { "rollout" => { "bucket_by" => "key", "weights" => [
        { "variation" => "on", "weight" => 40_000 }, { "variation" => "off", "weight" => 60_000 }
      ] } }
    )
    segments = { "s" => { "included" => ["a"], "excluded" => ["b"],
                          "rules" => [{ "clauses" => [G.clause("org", "in", "o")] }] } }
    G.ruleset_json([flag, G.flag_json("g")], segments)
  end

  # error_for parses tree after the block changed it and returns the message.
  def error_for(tree = valid_tree)
    yield tree
    error = assert_raises(Engine::ParseError) { Parser.parse(tree) }
    error.message
  end

  def flag_f(tree) = tree["flags"]["f"]

  def test_valid_ruleset
    rs = Parser.parse(JSON.generate(valid_tree))
    assert_equal "p", rs.project
    assert_equal "e", rs.environment
    assert_empty rs.warnings
    flag = rs.flags["f"]
    assert_equal "string", flag.type
    assert flag.enabled
    assert_equal 1, flag.version
    assert_equal %w[on off], flag.variations.keys
    assert_equal "on", flag.variations["on"].value
    assert_equal "off", flag.off_variation
    assert_equal "g", flag.prerequisites[0].flag
    assert flag.targets[0].values.include?("alice")
    assert_equal "pro", flag.rules[0].description
    assert_equal [["on", 40_000], ["off", 60_000]], flag.fallthrough.rollout.weights
    assert_equal "key", flag.fallthrough.rollout.bucket_by
    segment = rs.segments["s"]
    assert segment.included.include?("a")
    assert segment.excluded.include?("b")
    assert_equal "org", segment.rules[0][0].attribute
  end

  def test_tree_and_text_give_the_same_model
    assert_equal Parser.parse(valid_tree), Parser.parse(JSON.generate(valid_tree))
  end

  def test_model_is_frozen
    rs = Parser.parse(valid_tree)
    flag = rs.flags["f"]
    [rs, rs.flags, rs.segments, rs.warnings, flag, flag.variations, flag.targets, flag.targets[0].values,
     flag.rules, flag.rules[0].clauses, flag.rules[0].clauses[0].prepared, rs.segments["s"].included].each do |obj|
      assert_predicate obj, :frozen?, obj.class.name
    end
  end

  def test_variation_values_are_frozen_deeply
    tree = G.ruleset_json([G.flag_json("j", type: "json", variations: [{ "key" => "on", "value" => { "a" => [1] } }],
                                            off_variation: nil)])
    value = Parser.parse(tree).flags["j"].variations["on"].value
    assert_predicate value, :frozen?
    assert_predicate value["a"], :frozen?
  end

  def test_integral_variations
    tree = G.ruleset_json([G.flag_json("n", type: "number", off_variation: nil, fallthrough: { "variation" => "i" },
                                            variations: [
                                              { "key" => "i", "value" => 7 }, { "key" => "f", "value" => 7.0 },
                                              { "key" => "h", "value" => 2.5 }, { "key" => "b", "value" => true },
                                              { "key" => "big", "value" => 2**63 },
                                              { "key" => "min", "value" => -2**63 }
                                            ])])
    v = Parser.parse(tree).flags["n"].variations
    assert v["i"].is_int
    assert_equal 7, v["i"].int_value
    assert v["f"].is_int
    assert_equal 7, v["f"].int_value
    refute v["h"].is_int
    refute v["b"].is_int
    refute v["big"].is_int
    assert v["min"].is_int
  end

  def test_invalid_json
    assert_match(/\Aruleset: /, assert_raises(Engine::ParseError) { Parser.parse("{") }.message)
    assert_match(/\Aruleset: /, assert_raises(Engine::ParseError) { Parser.parse("NaN") }.message)
  end

  def test_root_errors
    assert_match(/\Aruleset: /, assert_raises(Engine::ParseError) { Parser.parse([]) }.message)
    assert_match(/\Aschema_version: /, error_for { |t| t.delete("schema_version") })
    assert_match(/\Aschema_version: /, error_for { |t| t["schema_version"] = 2 })
    assert_match(/\Aschema_version: /, error_for { |t| t["schema_version"] = 1.0 })
    assert_match(/\Aschema_version: /, error_for { |t| t["schema_version"] = true })
    assert_match(/\Aproject: /, error_for { |t| t["project"] = 1 })
    assert_match(/\Aenvironment: /, error_for { |t| t.delete("environment") })
    assert_match(/\Aflags: /, error_for { |t| t["flags"] = [] })
    assert_match(/\Asegments: /, error_for { |t| t["segments"] = nil })
  end

  def test_schema_version_1_0_in_text_is_refused
    text = JSON.generate(valid_tree).sub('"schema_version":1', '"schema_version":1.0')
    assert_match(/\Aschema_version: /, assert_raises(Engine::ParseError) { Parser.parse(text) }.message)
  end

  def test_flag_errors
    assert_match(/\Aflags\.f: /, error_for { |t| t["flags"]["f"] = [] })
    assert_match(/\Aflags\.f\.key: /, error_for { |t| flag_f(t)["key"] = "x" })
    assert_match(/\Aflags\.f\.type: /, error_for { |t| flag_f(t)["type"] = "int" })
    assert_match(/\Aflags\.f\.variations: /, error_for { |t| flag_f(t)["variations"] = [] })
    assert_match(/\Aflags\.f\.variations\[0\]: /, error_for { |t| flag_f(t)["variations"][0] = "on" })
    assert_match(/\Aflags\.f\.variations\[0\]\.key: /, error_for { |t| flag_f(t)["variations"][0]["key"] = 1 })
    assert_match(/\Aflags\.f\.variations\[1\]\.key: /, error_for { |t| flag_f(t)["variations"][1]["key"] = "on" })
    assert_match(/\Aflags\.f\.variations\[0\]\.value: /, error_for { |t| flag_f(t)["variations"][0].delete("value") })
    assert_match(/\Aflags\.f\.off_variation: /, error_for { |t| flag_f(t).delete("off_variation") })
    assert_match(/\Aflags\.f\.off_variation: /, error_for { |t| flag_f(t)["off_variation"] = "nope" })
    assert_match(/\Aflags\.f\.prerequisites: /, error_for { |t| flag_f(t)["prerequisites"] = {} })
    assert_match(/\Aflags\.f\.targets: /, error_for { |t| flag_f(t).delete("targets") })
    assert_match(/\Aflags\.f\.rules: /, error_for { |t| flag_f(t)["rules"] = nil })
    assert_match(/\Aflags\.f\.enabled: /, error_for { |t| flag_f(t)["enabled"] = 1 })
    assert_match(/\Aflags\.f\.version: /, error_for { |t| flag_f(t)["version"] = true })
    assert_match(/\Aflags\.f\.version: /, error_for { |t| flag_f(t)["version"] = 1.0 })
    assert_match(/\Aflags\.f\.fallthrough: /, error_for { |t| flag_f(t).delete("fallthrough") })
  end

  def test_null_variation_value_is_allowed
    tree = valid_tree
    flag_f(tree)["variations"][0]["value"] = nil
    assert_nil Parser.parse(tree).flags["f"].variations["on"].value
  end

  def test_prerequisite_target_and_rule_errors
    assert_match(/\Aflags\.f\.prerequisites\[0\]\.flag: /,
                 error_for { |t| flag_f(t)["prerequisites"][0]["flag"] = 1 })
    assert_match(/\Aflags\.f\.prerequisites\[0\]\.variation: /,
                 error_for { |t| flag_f(t)["prerequisites"][0].delete("variation") })
    assert_match(/\Aflags\.f\.targets\[0\]\.variation: /,
                 error_for { |t| flag_f(t)["targets"][0]["variation"] = "nope" })
    assert_match(/\Aflags\.f\.targets\[0\]\.values\[1\]: /,
                 error_for { |t| flag_f(t)["targets"][0]["values"][1] = 2 })
    assert_match(/\Aflags\.f\.rules\[0\]\.description: /,
                 error_for { |t| flag_f(t)["rules"][0]["description"] = 1 })
    assert_match(/\Aflags\.f\.rules\[0\]\.clauses: /, error_for { |t| flag_f(t)["rules"][0]["clauses"] = [] })
    assert_match(/\Aflags\.f\.rules\[0\]\.serve: /, error_for { |t| flag_f(t)["rules"][0].delete("serve") })
  end

  def test_description_defaults_to_empty
    tree = valid_tree
    flag_f(tree)["rules"][0].delete("description")
    assert_equal "", Parser.parse(tree).flags["f"].rules[0].description
  end

  def test_serve_errors
    assert_match(/\Aflags\.f\.fallthrough: /, error_for { |t| flag_f(t)["fallthrough"] = {} })
    assert_match(/\Aflags\.f\.fallthrough: /,
                 error_for { |t| flag_f(t)["fallthrough"]["variation"] = "on" })
    assert_match(/\Aflags\.f\.rules\[0\]\.serve\.variation: /,
                 error_for { |t| flag_f(t)["rules"][0]["serve"]["variation"] = "nope" })
    assert_match(/\Aflags\.f\.fallthrough\.rollout\.bucket_by: /,
                 error_for { |t| flag_f(t)["fallthrough"]["rollout"]["bucket_by"] = nil })
    assert_match(/\Aflags\.f\.fallthrough\.rollout\.weights: /,
                 error_for { |t| flag_f(t)["fallthrough"]["rollout"]["weights"] = [] })
  end

  def weights(tree) = flag_f(tree)["fallthrough"]["rollout"]["weights"]

  def test_rollout_weight_errors
    assert_match(/\Aflags\.f\.fallthrough\.rollout\.weights\[0\]\.variation: /,
                 error_for { |t| weights(t)[0]["variation"] = "nope" })
    assert_match(/\Aflags\.f\.fallthrough\.rollout\.weights\[0\]\.weight: /,
                 error_for { |t| weights(t)[0]["weight"] = 40_000.0 })
    assert_match(/\Aflags\.f\.fallthrough\.rollout\.weights\[0\]\.weight: /,
                 error_for { |t| weights(t)[0]["weight"] = true })
    assert_match(/\Aflags\.f\.fallthrough\.rollout\.weights\[0\]\.weight: /,
                 error_for { |t| weights(t)[0]["weight"] = -1 })
    assert_match(/\Aflags\.f\.fallthrough\.rollout\.weights\[1\]\.weight: /,
                 error_for { |t| weights(t)[1]["weight"] = 100_001 })
    assert_match(/\Aflags\.f\.fallthrough\.rollout\.weights: .*100000/,
                 error_for { |t| weights(t)[1]["weight"] = 50_000 })
  end

  def clause_of(tree) = flag_f(tree)["rules"][0]["clauses"][0]

  def test_clause_errors
    prefix = "flags\\.f\\.rules\\[0\\]\\.clauses\\[0\\]"
    assert_match(/\A#{prefix}: /, error_for { |t| flag_f(t)["rules"][0]["clauses"][0] = 1 })
    assert_match(/\A#{prefix}\.attribute: /, error_for { |t| clause_of(t)["attribute"] = 1 })
    assert_match(/\A#{prefix}\.operator: unknown operator/, error_for { |t| clause_of(t)["operator"] = "eq" })
    assert_match(/\A#{prefix}\.values: /, error_for { |t| clause_of(t)["values"] = [] })
    assert_match(/\A#{prefix}\.values: /, error_for { |t| clause_of(t)["values"] = "pro" })
    assert_match(/\A#{prefix}\.values\[0\]: /, error_for { |t| clause_of(t)["values"] = [nil] })
    assert_match(/\A#{prefix}\.values\[1\]: /, error_for { |t| clause_of(t)["values"] = ["a", ["b"]] })
    assert_match(/\A#{prefix}\.negate: /, error_for { |t| clause_of(t)["negate"] = "yes" })
  end

  def test_in_segment_is_refused_in_a_segment_rule
    message = error_for { |t| t["segments"]["s"]["rules"][0]["clauses"][0] = G.clause("key", "in_segment", "s") }
    assert_match(/\Asegments\.s\.rules\[0\]\.clauses\[0\]\.operator: /, message)
  end

  def test_segment_errors
    assert_match(/\Asegments\.s: /, error_for { |t| t["segments"]["s"] = [] })
    assert_match(/\Asegments\.s\.included: /, error_for { |t| t["segments"]["s"].delete("included") })
    assert_match(/\Asegments\.s\.excluded\[0\]: /, error_for { |t| t["segments"]["s"]["excluded"] = [1] })
    assert_match(/\Asegments\.s\.rules: /, error_for { |t| t["segments"]["s"]["rules"] = {} })
    assert_match(/\Asegments\.s\.rules\[0\]: /, error_for { |t| t["segments"]["s"]["rules"][0] = [] })
    assert_match(/\Asegments\.s\.rules\[0\]\.clauses: /,
                 error_for { |t| t["segments"]["s"]["rules"][0].delete("clauses") })
  end

  def test_unknown_fields_are_ignored
    tree = valid_tree
    tree["extra"] = 1
    flag_f(tree)["extra"] = { "x" => 1 }
    clause_of(tree)["extra"] = []
    tree["segments"]["s"]["extra"] = nil
    assert_equal Parser.parse(valid_tree), Parser.parse(tree)
  end

  def test_negate_defaults_to_false
    tree = valid_tree
    clause_of(tree).delete("negate")
    refute Parser.parse(tree).flags["f"].rules[0].clauses[0].negate
  end

  def test_cross_references_are_not_checked
    tree = valid_tree
    flag_f(tree)["prerequisites"] = [{ "flag" => "missing", "variation" => "any" }]
    clause_of(tree).replace(G.clause("key", "in_segment", "missing"))
    assert Parser.parse(tree)
  end

  def prepared(operator, *values)
    tree = valid_tree
    clause_of(tree).replace(G.clause("a", operator, *values))
    Parser.parse(tree).flags["f"].rules[0].clauses[0].prepared
  end

  def test_prepared_values
    assert_equal ["a", 1, true], prepared("in", "a", 1, true)
    assert_equal ["a", 1, true], prepared("not_in", "a", 1, true)
    %w[contains starts_with ends_with in_segment].each do |op|
      assert_equal ["a"], prepared(op, "a", 1, true), op
    end
    assert_equal [1, 2.5], prepared("lt", "1", 1, 2.5, true)
    assert_equal [1], prepared("gte", 1)
    assert_equal [Engine::Semver.parse_semver("1.2.3")], prepared("semver_eq", "1.2.3", "bad", 1)
    assert_equal [0], prepared("before", "1970-01-01T00:00:00Z", "bad", 5)
    assert_equal [1000], prepared("after", "1970-01-01T00:00:01Z")
    matchers = prepared("matches", "^a$", 1, "(")
    assert_equal 1, matchers.size
    assert matchers[0].test("a")
  end

  def test_regex_that_does_not_compile_is_a_warning
    tree = valid_tree
    clause_of(tree).replace(G.clause("a", "matches", "ok", "(?=x)"))
    rs = Parser.parse(tree)
    assert_equal 1, rs.warnings.size
    assert_match(/\Aflags\.f\.rules\[0\]\.clauses\[0\]\.values\[1\]: regex does not compile: /, rs.warnings[0])
  end
end
