# frozen_string_literal: true

require_relative "test_helper"
require_relative "support/provider_rulesets"
require "json"
require "tmpdir"
require "megooflags"

# Evaluation through the real OpenFeature client, with an offline provider and a bootstrap file.
class ProviderTest < Minitest::Test
  include ProviderRulesets

  SDK = OpenFeature::SDK
  ErrorCode = OpenFeature::SDK::Provider::ErrorCode
  Reason = OpenFeature::SDK::Provider::Reason

  KEY = "mf_server_provider-secret-value"

  @domains = 0
  class << self
    def next_domain = "provider-#{@domains += 1}"
  end

  def setup
    @dir = Dir.mktmpdir("megooflags-provider")
    @logger = TestLogger.new
    @bootstrap = write("ruleset.json", provider_ruleset)
  end

  def teardown
    SDK.shutdown
    FileUtils.remove_entry(@dir)
  end

  def write(name, text)
    path = File.join(@dir, name)
    File.write(path, text)
    path
  end

  def offline(bootstrap_file, **)
    MegooFlags::Provider.new(sdk_key: KEY, bootstrap_file: bootstrap_file, offline: true, logger: @logger, **)
  end

  def register(provider)
    domain = self.class.next_domain
    SDK.set_provider_and_wait(provider, domain: domain)
    SDK.build_client(domain: domain)
  end

  def client = @client ||= register(offline(@bootstrap))

  def ctx(key = nil, **fields) = SDK::EvaluationContext.new(targeting_key: key, **fields)

  def test_names_itself_and_emits_its_own_lifecycle_events
    p = offline(nil, cache_dir: @dir)

    assert_equal "MegooFlags", p.metadata.name
    assert_predicate p, :emits_lifecycle_events?
    assert_kind_of SDK::Provider::EventEmitter, p
  end

  def test_resolves_a_boolean_flag
    d = client.fetch_boolean_details(flag_key: "bool-flag", default_value: true,
                                     evaluation_context: ctx("u1", plan: "pro"))

    assert_equal [true, "on", "TARGETING_MATCH", nil], [d.value, d.variant, d.reason, d.error_code]
    assert_equal({ "rule_index" => 0 }, d.flag_metadata)

    d = client.fetch_boolean_details(flag_key: "bool-flag", default_value: true, evaluation_context: ctx("u1"))

    assert_equal [false, "off", Reason::DEFAULT], [d.value, d.variant, d.reason]
  end

  def test_resolves_a_string_flag
    d = client.fetch_string_details(flag_key: "string-flag", default_value: "x")

    assert_equal ["blue", "blue", Reason::STATIC, nil], [d.value, d.variant, d.reason, d.error_code]
  end

  def test_resolves_a_number_as_is
    assert_in_delta 2.5, client.fetch_number_value(flag_key: "number-flag", default_value: 1)
    assert_equal 7, client.fetch_number_value(flag_key: "int-flag", default_value: 1)
  end

  def test_resolves_a_number_as_a_float
    d = client.fetch_float_details(flag_key: "number-flag", default_value: 1.0)

    assert_equal [2.5, "half", nil], [d.value, d.variant, d.error_code]
    v = client.fetch_float_value(flag_key: "int-flag", default_value: 1.0)

    assert_instance_of Float, v
    assert_in_delta 7.0, v
  end

  def test_resolves_an_integral_number_as_an_integer
    d = client.fetch_integer_details(flag_key: "int-flag", default_value: 1)

    assert_equal [7, "seven", nil], [d.value, d.variant, d.error_code]
    assert_instance_of Integer, d.value
  end

  def test_refuses_a_number_that_is_not_integral_as_an_integer
    p = offline(@bootstrap)
    d = register(p).fetch_integer_details(flag_key: "number-flag", default_value: 1)

    assert_equal [1, Reason::ERROR, ErrorCode::TYPE_MISMATCH], [d.value, d.reason, d.error_code]
    assert_equal "flag 'number-flag' is not an integer", d.error_message
    direct = p.fetch_integer_value(flag_key: "number-flag", default_value: 1)

    assert_equal [1, ErrorCode::TYPE_MISMATCH], [direct.value, direct.error_code]
  end

  def test_resolves_an_object_flag
    d = client.fetch_object_details(flag_key: "json-flag", default_value: {})

    assert_equal({ "limit" => 10, "tags" => %w[a b] }, d.value)
    assert_equal "cfg", d.variant
  end

  def test_refuses_an_object_flag_that_is_not_a_hash_or_an_array
    p = offline(@bootstrap)
    d = p.fetch_object_value(flag_key: "json-text", default_value: [])

    assert_equal [[], Reason::ERROR, ErrorCode::TYPE_MISMATCH], [d.value, d.reason, d.error_code]
    assert_equal "flag 'json-text' is not an object or an array", d.error_message
  end

  def test_maps_the_targeting_key_to_the_key_attribute
    d = client.fetch_string_details(flag_key: "targeted", default_value: "x", evaluation_context: ctx("alice"))

    assert_equal ["hello alice", "TARGETING_MATCH"], [d.value, d.reason]
    # The targeting key wins over a key attribute.
    assert_equal "hello alice",
                 client.fetch_string_value(flag_key: "targeted", default_value: "x",
                                           evaluation_context: ctx("alice", key: "bob"))
    assert_equal "hello alice",
                 client.fetch_string_value(flag_key: "targeted", default_value: "x",
                                           evaluation_context: ctx(key: "alice"))
  end

  def test_returns_the_default_with_flag_not_found
    d = client.fetch_string_details(flag_key: "missing", default_value: "x")

    assert_equal ["x", Reason::ERROR, ErrorCode::FLAG_NOT_FOUND], [d.value, d.reason, d.error_code]
    assert_equal "flag 'missing' not found", d.error_message
  end

  def test_returns_the_default_with_type_mismatch
    d = client.fetch_boolean_details(flag_key: "string-flag", default_value: false)

    assert_equal [false, Reason::ERROR, ErrorCode::TYPE_MISMATCH], [d.value, d.reason, d.error_code]
    d = client.fetch_float_details(flag_key: "bool-flag", default_value: 1.5)

    assert_equal [1.5, ErrorCode::TYPE_MISMATCH], [d.value, d.error_code]
  end

  def test_errors_carry_the_default_as_value
    p = offline(@bootstrap)
    d = p.fetch_string_value(flag_key: "missing", default_value: "x")

    assert_equal ["x", Reason::ERROR, ErrorCode::FLAG_NOT_FOUND], [d.value, d.reason, d.error_code]
    assert_instance_of SDK::Provider::ResolutionDetails, d
  end

  def test_passes_the_prerequisite_failed_reason_and_metadata_through
    d = client.fetch_boolean_details(flag_key: "needs-prereq", default_value: true)

    assert_equal [false, "off", "PREREQUISITE_FAILED"], [d.value, d.variant, d.reason]
    assert_equal({ "prerequisite" => "prereq-off" }, d.flag_metadata)
  end

  def test_unwraps_nested_attributes
    flag = flag_json("nested", rules: [rule("on", clause("tags", "in", "beta"))], fallthrough: { "variation" => "off" })
    deep = flag_json("deep", rules: [rule("on", clause("org", "in", "acme"))], fallthrough: { "variation" => "off" })
    c = register(offline(write("r.json", JSON.generate(ruleset_json([flag, deep])))))

    assert_equal "on", c.fetch_string_value(flag_key: "nested", default_value: "x",
                                            evaluation_context: ctx("u1", tags: %w[alpha beta]))
    # Nested hashes get string keys, as JSON objects have.
    assert_equal "off", c.fetch_string_value(flag_key: "deep", default_value: "x",
                                             evaluation_context: ctx("u1", org: { name: "acme" }))
  end

  def test_matches_nothing_with_a_time_attribute
    flag = flag_json("dated", rules: [rule("on", clause("signed_up", "before", "2030-01-01T00:00:00Z"))],
                              fallthrough: { "variation" => "off" })
    c = register(offline(write("r.json", JSON.generate(ruleset_json([flag])))))

    assert_equal "off", c.fetch_string_value(flag_key: "dated", default_value: "x",
                                             evaluation_context: ctx("u1", signed_up: Time.utc(2020)))
    assert_equal "on", c.fetch_string_value(flag_key: "dated", default_value: "x",
                                            evaluation_context: ctx("u1", signed_up: "2020-01-01T00:00:00Z"))
  end

  def test_evaluates_with_a_nil_context
    p = offline(@bootstrap)

    assert_equal "blue", p.fetch_string_value(flag_key: "string-flag", default_value: "x").value
  end

  def test_is_not_ready_without_a_ruleset
    p = offline(nil, cache_dir: @dir)
    d = p.fetch_string_value(flag_key: "string-flag", default_value: "x")

    assert_equal ["x", Reason::ERROR, ErrorCode::PROVIDER_NOT_READY], [d.value, d.reason, d.error_code]
    err = assert_raises(SDK::ProviderInitializationError) { register(p) }

    assert_equal ErrorCode::GENERAL, err.error_code
    d = p.fetch_integer_value(flag_key: "int-flag", default_value: 3)

    assert_equal [3, ErrorCode::PROVIDER_NOT_READY], [d.value, d.error_code]
  end

  def test_is_not_ready_after_an_invalid_bootstrap_file
    p = offline(write("bad.json", '{"schema_version": 99}'))

    assert_raises(SDK::ProviderInitializationError) { register(p) }
    assert_equal ErrorCode::PROVIDER_NOT_READY,
                 p.fetch_boolean_value(flag_key: "bool-flag", default_value: true).error_code
    assert(@logger.messages(:warn).any? { |m| m.include?("invalid ruleset") })
  end

  def test_warns_about_a_missing_bootstrap_file
    p = offline(File.join(@dir, "missing.json"))

    assert_equal ErrorCode::PROVIDER_NOT_READY,
                 p.fetch_boolean_value(flag_key: "bool-flag", default_value: true).error_code
    assert(@logger.messages(:warn).any? { |m| m.include?("could not read the bootstrap file") })
  end

  def test_evaluates_the_default_with_provider_fatal_for_an_invalid_config
    p = MegooFlags::Provider.new(sdk_key: "mf_client_secret-value", base_url: "https://x.example", logger: @logger)
    d = p.fetch_boolean_value(flag_key: "bool-flag", default_value: true)

    assert_equal [true, Reason::ERROR, ErrorCode::PROVIDER_FATAL], [d.value, d.reason, d.error_code]
    assert_includes d.error_message, "client SDK key"
    err = assert_raises(SDK::ProviderInitializationError) { register(p) }

    assert_equal ErrorCode::PROVIDER_FATAL, err.error_code
    assert(@logger.messages(:error).any? { |m| m.include?("invalid config") })
    refute(@logger.messages(:error).any? { |m| m.include?("secret-value") })
    refute_includes err.message, "secret-value"
  end

  def test_exposes_the_config
    p = MegooFlags::Provider.new(sdk_key: KEY, base_url: "https://x.example/", poll_interval: 1, logger: @logger)

    assert_instance_of MegooFlags::Config, p.config
    assert_equal "https://x.example", p.config.base_url
    assert_in_delta 15.0, p.config.poll_interval
  end
end
