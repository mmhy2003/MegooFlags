# frozen_string_literal: true

require_relative "test_helper"
require "megooflags"
require "pathname"

class ConfigTest < Minitest::Test
  Config = MegooFlags::Config

  KEY = "mf_server_config-secret-value"

  def config(**) = Config.new(sdk_key: KEY, base_url: "https://flags.example", logger: TestLogger.new, **)

  def test_fills_in_the_defaults
    c = nil
    capture_io { c = Config.new(sdk_key: KEY, base_url: "https://flags.example") }

    assert_equal "https://flags.example", c.base_url
    assert_nil c.bootstrap_file
    assert_nil c.cache_dir
    refute c.offline
    assert_in_delta 5.0, c.init_timeout
    refute c.disable_stream
    assert_in_delta 30.0, c.poll_interval
    assert_in_delta 30.0, c.flush_interval
    refute c.send_context
    assert_respond_to c.logger, :warn
    assert_predicate c, :frozen?
    assert_nil c.error
  end

  def test_clamps_the_intervals
    assert_in_delta 15.0, config(poll_interval: 1).poll_interval
    assert_in_delta 120.0, config(poll_interval: 120).poll_interval
    assert_in_delta 10.0, config(flush_interval: 2).flush_interval
    assert_in_delta 60.0, config(flush_interval: 600).flush_interval
    assert_in_delta 20.0, config(flush_interval: 20).flush_interval
  end

  def test_zero_negative_and_nan_durations_mean_the_default
    [0, -1, Float::NAN, nil].each do |bad|
      c = config(init_timeout: bad, poll_interval: bad, flush_interval: bad)

      assert_in_delta 5.0, c.init_timeout
      assert_in_delta 30.0, c.poll_interval
      assert_in_delta 30.0, c.flush_interval
    end
    assert_in_delta 0.25, config(init_timeout: 0.25).init_timeout
  end

  def test_normalises_the_base_url_and_the_paths
    c = config(base_url: "https://flags.example//", bootstrap_file: Pathname("/tmp/r.json"), cache_dir: "")

    assert_equal "https://flags.example", c.base_url
    assert_equal "/tmp/r.json", c.bootstrap_file
    assert_nil c.cache_dir
  end

  def test_refuses_a_client_key
    c = Config.new(sdk_key: "mf_client_secret-value", base_url: "https://flags.example", logger: TestLogger.new)

    assert_equal "megooflags: a client SDK key cannot be used by a server SDK; use a server key (mf_server_…)",
                 c.error
  end

  def test_refuses_a_key_that_is_not_a_server_key
    ["sdk-secret-value", "", nil, 42].each do |key|
      c = Config.new(sdk_key: key, base_url: "https://flags.example", logger: TestLogger.new)

      assert_equal "megooflags: invalid SDK key; use a server key (mf_server_…)", c.error
    end
  end

  def test_validates_the_base_url
    ["", "flags.example", "ftp://flags.example", "http://", "https:///path", "http://[bad"].each do |url|
      assert_equal "megooflags: base_url must be an http or https URL", config(base_url: url).error, url
    end
    assert_nil config(base_url: "http://127.0.0.1:8080/").error
  end

  def test_offline_needs_a_bootstrap_file_or_a_cache_dir
    assert_equal "megooflags: offline needs a bootstrap_file or a cache_dir",
                 Config.new(sdk_key: KEY, logger: TestLogger.new, offline: true).error
    assert_nil Config.new(sdk_key: KEY, logger: TestLogger.new, offline: true, cache_dir: "/tmp/c").error
    assert_nil Config.new(sdk_key: KEY, logger: TestLogger.new, offline: true, bootstrap_file: "/tmp/r.json").error
  end

  def test_messages_and_inspect_never_contain_the_key
    errors = [
      Config.new(sdk_key: "mf_client_secret-value", logger: TestLogger.new).error,
      Config.new(sdk_key: "bad-secret-value", logger: TestLogger.new).error,
      Config.new(sdk_key: KEY, base_url: "nope", logger: TestLogger.new).error
    ]

    errors.each { |e| refute_includes e, "secret-value" }
    refute_includes config.inspect, "secret-value"
    refute_includes config.to_s, "secret-value"
    assert_equal KEY, config.sdk_key
  end

  def test_keeps_the_given_logger
    logger = TestLogger.new

    assert_same logger, Config.new(sdk_key: KEY, logger: logger).logger
  end
end
