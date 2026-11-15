# frozen_string_literal: true

require "json"
require_relative "ruleset_gen"

# The provider, lifecycle and fork tests' rulesets: one flag per result shape.
module ProviderRulesets
  include RulesetGen

  BOOL_VARIATIONS = [{ "key" => "on", "value" => true }, { "key" => "off", "value" => false }].freeze

  JSON_VARIATIONS = [{ "key" => "cfg", "value" => { "limit" => 10, "tags" => %w[a b] } },
                     { "key" => "empty", "value" => {} }].freeze

  # provider_flags are the provider tests' flags: one per result shape.
  def provider_flags
    [
      flag_json("bool-flag", type: "boolean", variations: BOOL_VARIATIONS,
                             rules: [rule("on", clause("plan", "in", "pro")).merge("description" => "pro plan")],
                             fallthrough: { "variation" => "off" }),
      flag_json("targeted", variations: [{ "key" => "alice", "value" => "hello alice" },
                                         { "key" => "other", "value" => "hello" }],
                            off_variation: "other", targets: [{ "variation" => "alice", "values" => ["alice"] }],
                            fallthrough: { "variation" => "other" }),
      flag_json("string-flag", variations: [{ "key" => "blue", "value" => "blue" },
                                            { "key" => "red", "value" => "red" }],
                               off_variation: "red", fallthrough: { "variation" => "blue" }),
      flag_json("number-flag", type: "number", variations: [{ "key" => "half", "value" => 2.5 },
                                                            { "key" => "zero", "value" => 0 }],
                               off_variation: "zero", fallthrough: { "variation" => "half" }),
      flag_json("int-flag", type: "number", variations: [{ "key" => "seven", "value" => 7 },
                                                         { "key" => "zero", "value" => 0 }],
                            off_variation: "zero", fallthrough: { "variation" => "seven" }),
      flag_json("json-flag", type: "json", variations: JSON_VARIATIONS,
                             off_variation: "empty", fallthrough: { "variation" => "cfg" }),
      flag_json("json-text", type: "json", variations: [{ "key" => "text", "value" => "plain" }],
                             off_variation: "text", fallthrough: { "variation" => "text" }),
      flag_json("prereq-off", type: "boolean", enabled: false, variations: BOOL_VARIATIONS),
      flag_json("needs-prereq", type: "boolean", variations: BOOL_VARIATIONS,
                                prerequisites: [{ "flag" => "prereq-off", "variation" => "on" }])
    ]
  end

  # provider_ruleset is the provider tests' ruleset as JSON text.
  def provider_ruleset = JSON.generate(ruleset_json(provider_flags))

  # changed_provider_ruleset serves red for string-flag (version 2), removes targeted and adds
  # new-flag.
  def changed_provider_ruleset
    flags = provider_flags.reject { |f| f["key"] == "targeted" }
    string = flags.find { |f| f["key"] == "string-flag" }
    string.merge!("version" => 2, "fallthrough" => { "variation" => "red" })
    flags << string.merge("key" => "new-flag")
    JSON.generate(ruleset_json(flags))
  end
end
