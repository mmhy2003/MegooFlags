# frozen_string_literal: true

# Ruleset builders for the engine tests and the p99 guard (the Go SDK's
# genRuleset). Everything is a JSON tree with string keys.
module RulesetGen
  BENCH_SEGMENTS = 50

  module_function

  # flag_json is an enabled string flag with variations on and off,
  # off_variation off and fallthrough on; fields override any of it.
  def flag_json(key, **fields)
    {
      "key" => key, "type" => "string", "enabled" => true, "version" => 1,
      "variations" => [{ "key" => "on", "value" => "on" }, { "key" => "off", "value" => "off" }],
      "off_variation" => "off", "prerequisites" => [], "targets" => [], "rules" => [],
      "fallthrough" => { "variation" => "on" }
    }.merge(fields.transform_keys(&:to_s))
  end

  def clause(attribute, operator, *values, negate: false)
    { "attribute" => attribute, "operator" => operator, "negate" => negate, "values" => values }
  end

  def rule(variation, *clauses)
    { "description" => "", "clauses" => clauses, "serve" => { "variation" => variation } }
  end

  def ruleset_json(flags, segments = {})
    {
      "schema_version" => 1, "project" => "p", "environment" => "e",
      "flags" => flags.to_h { |f| [f["key"], f] }, "segments" => segments
    }
  end

  def flag_keys(count)
    Array.new(count) { |i| "flag-#{i}" }
  end

  # gen_ruleset is a deterministic ruleset of count flags and 50 segments.
  # Even flags are booleans with three rules (in, matches, semver_gt), a
  # 100-key target list and a three-way fallthrough rollout; odd flags are
  # strings with an in_segment rule and a before rule. Every 10th flag has a
  # prerequisite on the flag before it.
  def gen_ruleset(count)
    flags = flag_keys(count).each_with_index.map do |key, i|
      flag = i.even? ? bench_even(key, i) : bench_odd(key, i)
      flag["prerequisites"] = [{ "flag" => "flag-#{i - 1}", "variation" => "on" }] if i.positive? && (i % 10).zero?
      flag
    end
    segments = Array.new(BENCH_SEGMENTS) do |s|
      ["segment-#{s}", {
        "included" => Array.new(1000) { |k| "user-#{(s * 1000) + k}" }, "excluded" => [],
        "rules" => [{ "clauses" => [clause("org", "in", "org-#{s}")] }]
      }]
    end.to_h
    ruleset_json(flags, segments).merge("project" => "bench", "environment" => "bench")
  end

  def bench_even(key, index)
    flag_json(
      key,
      type: "boolean",
      variations: [{ "key" => "on", "value" => true }, { "key" => "off", "value" => false },
                   { "key" => "alt", "value" => false }],
      targets: [{ "variation" => "alt", "values" => Array.new(100) { |k| "user-#{(index + (k * 37)) % 1000}" } }],
      rules: [
        rule("on", clause("plan", "in", *Array.new(20) { |p| "plan-#{p}" })),
        rule("off", clause("email", "matches", "^user-[0-9]+$")),
        rule("alt", clause("app_version", "semver_gt", "1.5.0"))
      ],
      fallthrough: { "rollout" => { "bucket_by" => "key", "weights" => [
        { "variation" => "on", "weight" => 33_334 }, { "variation" => "off", "weight" => 33_333 },
        { "variation" => "alt", "weight" => 33_333 }
      ] } }
    )
  end

  def bench_odd(key, index)
    flag_json(
      key,
      variations: [{ "key" => "on", "value" => "on" }, { "key" => "off", "value" => "off" },
                   { "key" => "alt", "value" => "alt" }],
      rules: [
        rule("alt", clause("key", "in_segment", "segment-#{index % BENCH_SEGMENTS}")),
        rule("off", clause("signed_up", "before", "2025-01-01T00:00:00Z"))
      ]
    )
  end

  # bench_contexts are 100 contexts that hit every rule shape of gen_ruleset.
  def bench_contexts
    Array.new(100) do |j|
      {
        "key" => "user-#{j * 503}", "plan" => "plan-#{j % 40}",
        "email" => (j % 3).zero? ? "user-#{j}" : "user#{j}@example.com",
        "app_version" => "1.#{j % 10}.0", "org" => "org-#{j % 100}"
      }
    end
  end
end
