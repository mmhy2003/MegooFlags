# frozen_string_literal: true

# Runs the provider against a running MegooFlags stack. Set MEGOOFLAGS_E2E_URL (for example
# http://localhost:8080) and MEGOOFLAGS_API_TOKEN (scripts/sdk_e2e_token.sh prints one) to run it:
# `bundle exec rake e2e`. Without both, the test is skipped.

require "fileutils"
require "json"
require "net/http"
require "securerandom"
require "socket"
require "tmpdir"
require "uri"
require_relative "../test/test_helper"
require "megooflags"

# The four end-to-end checks: the downloaded ruleset is evaluated, a toggle arrives over SSE,
# evaluation counts are accepted, and a provider with the API unreachable serves the cache.
class EndToEndTest < Minitest::Test
  SDK = OpenFeature::SDK
  Event = OpenFeature::SDK::ProviderEvent

  BASE = ENV.fetch("MEGOOFLAGS_E2E_URL", "").sub(%r{/+\z}, "")
  TOKEN = ENV.fetch("MEGOOFLAGS_API_TOKEN", "")
  REQUEST_TIMEOUT = 10

  def setup
    skip "MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN are not set" if BASE.empty? || TOKEN.empty?

    @project = "sdkruby-#{SecureRandom.hex(4)}"
    @cache_dir = Dir.mktmpdir("megooflags-e2e")
    must(201, :post, "/api/projects",
         { key: @project, name: @project, environments: [{ key: "e2e", name: "E2E" }] })
  end

  def teardown
    return if @project.nil?

    SDK.shutdown
    call(:delete, "/api/projects/#{@project}")
    FileUtils.remove_entry(@cache_dir)
  end

  def test_end_to_end
    flag_path = "/api/projects/#{@project}/flags/e2e-flag/environments/e2e"
    sdk_key = create_flag_and_key(flag_path)
    logger = TestLogger.new
    provider = MegooFlags::Provider.new(sdk_key: sdk_key, base_url: BASE, cache_dir: @cache_dir,
                                        flush_interval: 10, logger: logger)
    client = SDK.build_client(domain: @project)
    changes = Queue.new
    client.add_handler(Event::PROVIDER_CONFIGURATION_CHANGED) { |details| changes << details }
    SDK.set_provider_and_wait(provider, domain: @project)
    ctx = SDK::EvaluationContext.new(targeting_key: "user-1")
    value = -> { client.fetch_boolean_value(flag_key: "e2e-flag", default_value: false, evaluation_context: ctx) }

    # Check 1: the downloaded ruleset is evaluated.
    assert_equal true, value.call

    # Check 2: a toggle arrives over SSE within 5 s, with a configuration-changed event.
    must(200, :post, "#{flag_path}/toggle", { enabled: false })
    poll_until("e2e-flag false after the toggle", 5) { value.call == false }
    refute_nil changes.pop(timeout: 5), "no configuration-changed event within 5 s"

    # Check 3: evaluation counts are sent at shutdown and accepted.
    20.times { value.call }
    provider.shutdown
    poll_until("evaluations total >= 21", 10, 0.5) do
      total = must(200, :get, "#{flag_path}/evaluations?range=24h")["total"]
      total.is_a?(Integer) && total >= 21
    end

    # Check 4: with the API unreachable, a new provider serves the cache.
    offline = MegooFlags::Provider.new(sdk_key: sdk_key, base_url: "http://127.0.0.1:#{closed_port}",
                                       cache_dir: @cache_dir, init_timeout: 1, logger: logger)
    SDK.set_provider_and_wait(offline, domain: "#{@project}-offline")
    offline_client = SDK.build_client(domain: "#{@project}-offline")
    assert_equal false,
                 offline_client.fetch_boolean_value(flag_key: "e2e-flag", default_value: true, evaluation_context: ctx)
  end

  private

  # create_flag_and_key creates e2e-flag, turns it on and returns a new server key.
  def create_flag_and_key(flag_path)
    must(201, :post, "/api/projects/#{@project}/flags", { key: "e2e-flag", name: "E2E", type: "boolean" })
    must(200, :post, "#{flag_path}/toggle", { enabled: true })
    created = must(201, :post, "/api/projects/#{@project}/environments/e2e/sdk-keys",
                   { name: "ruby-e2e", kind: "server" })
    assert_kind_of String, created["key"], "POST sdk-keys returned no key"
    created["key"]
  end

  # call calls the management API and returns the status and the JSON body.
  def call(method, path, body = nil)
    uri = URI("#{BASE}#{path}")
    request = Net::HTTP.const_get(method.to_s.capitalize).new(uri)
    request["Authorization"] = "Bearer #{TOKEN}"
    unless body.nil?
      request["Content-Type"] = "application/json"
      request.body = JSON.generate(body)
    end
    response = Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == "https", open_timeout: REQUEST_TIMEOUT,
                                                   read_timeout: REQUEST_TIMEOUT) { |http| http.request(request) }
    text = response.body.to_s
    [response.code.to_i, text.empty? ? {} : JSON.parse(text)]
  end

  # must calls the management API and fails unless the status is want.
  def must(want, method, path, body = nil)
    status, out = call(method, path, body)
    assert_equal want, status, "#{method.upcase} #{path}: #{out}"
    out
  end

  # poll_until checks the block every interval seconds until it holds or timeout passes.
  def poll_until(what, timeout, interval = 0.05)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + timeout
    until yield
      flunk "#{what}: not within #{timeout} s" if Process.clock_gettime(Process::CLOCK_MONOTONIC) > deadline
      sleep interval
    end
  end

  # closed_port is a local port nothing listens on.
  def closed_port
    server = TCPServer.new("127.0.0.1", 0)
    server.addr[1]
  ensure
    server&.close
  end
end
