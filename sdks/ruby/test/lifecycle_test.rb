# frozen_string_literal: true

require_relative "test_helper"
require_relative "support/fake_server"
require_relative "support/provider_rulesets"
require "json"
require "tmpdir"
require "megooflags"

# The provider's start-up, provider events and shutdown, through the real OpenFeature API and the
# fake server.
class LifecycleTest < Minitest::Test
  include ThreadTestHelpers
  include ProviderRulesets

  SDK = OpenFeature::SDK
  Event = OpenFeature::SDK::ProviderEvent
  ErrorCode = OpenFeature::SDK::Provider::ErrorCode
  Delivery = MegooFlags::Internal::Delivery

  KEY = "mf_server_lifecycle-secret-value"
  STREAM = "/sdk/v1/stream"
  RULESET = "/sdk/v1/ruleset"
  EVENTS = "/sdk/v1/events"

  @domains = 0
  class << self
    def next_domain = "lifecycle-#{@domains += 1}"
  end

  # Registration is a provider registered under its own domain on a background thread, like
  # set_provider_and_wait, with the events of that domain.
  class Registration
    include ThreadTestHelpers

    attr_reader :client, :provider

    def initialize(provider)
      domain = LifecycleTest.next_domain
      @provider = provider
      @client = SDK.build_client(domain: domain)
      @lock = Mutex.new
      @events = []
      Event::ALL_EVENTS.each do |type|
        @client.add_handler(type) { |details| @lock.synchronize { @events << [type, details] } }
      end
      @done = Queue.new
      @thread = Thread.new do
        SDK.set_provider_and_wait(provider, domain: domain)
        @done << :ok
      rescue StandardError => e
        @done << e
      end
    end

    # ready waits for set_provider_and_wait to return: its error, or nil.
    def ready(timeout = 5)
      @result = @done.pop(timeout: timeout) if @result.nil?
      raise Minitest::Assertion, "initialisation did not return" if @result.nil?

      @result == :ok ? nil : @result
    end

    # settled? is true when initialisation returned within timeout seconds.
    def settled?(timeout) = !(@result ||= @done.pop(timeout: timeout)).nil?

    def join = @thread.join(5)

    def types = @lock.synchronize { @events.map(&:first) }

    # next waits for the n-th (1-based) event of type and returns its details.
    def next(type, count = 1)
      wait_for { types.count(type) >= count }
      @lock.synchronize { @events.select { |t, _| t == type }.map(&:last) }[count - 1]
    end
  end

  def setup
    @threads = live_threads
    @server = new_server
    @dir = Dir.mktmpdir("megooflags-lifecycle")
    @logger = TestLogger.new
    @registrations = []
    MegooFlags::Internal.sync_tuning = Delivery::Tuning.new(retry_base: 0.02, retry_max: 0.2, stable_after: 0.1,
                                                            dead_timeout: 5.0)
  end

  def teardown
    SDK.shutdown
    @registrations.each(&:join)
    @servers.each(&:stop)
    MegooFlags::Internal.sync_tuning = nil
    FileUtils.remove_entry(@dir)
    assert_no_thread_leak(@threads)
  end

  def new_server(stream: FakeServer::Stream.new, events: FakeServer::Scripted.new(status: 202))
    server = FakeServer.new
    server.reply(STREAM, stream)
    server.reply(EVENTS, events)
    (@servers ||= []) << server
    server
  end

  def provider(server = @server, **)
    MegooFlags::Provider.new(sdk_key: KEY, base_url: server.url, logger: @logger, **)
  end

  def register(provider)
    r = Registration.new(provider)
    @registrations << r
    r
  end

  def put(id, data) = "event: put\nid: #{id}\ndata: #{data}\n\n"

  def ctx(key = nil, **fields) = SDK::EvaluationContext.new(targeting_key: key, **fields)

  def value(reg, flag = "string-flag", context = nil)
    reg.client.fetch_string_value(flag_key: flag, default_value: "x", evaluation_context: context)
  end

  def write_bootstrap(text = provider_ruleset)
    path = File.join(@dir, "ruleset.json")
    File.write(path, text)
    path
  end

  def save_cache(text, etag, dir = @dir) = Delivery::Cache.new(dir, KEY).save(text, etag)

  # init

  def test_waits_for_the_first_put
    r = register(provider)
    conn = @server.next_stream

    refute r.settled?(0.1)
    conn.write(put('"e1"', provider_ruleset))

    assert_nil r.ready
    assert_equal "blue", value(r)
    r.next(Event::PROVIDER_READY)
    sleep 0.05

    assert_equal [Event::PROVIDER_READY], r.types
  end

  def test_times_out_keeps_retrying_and_emits_ready_with_the_ruleset
    r = register(provider(init_timeout: 0.2))
    conn = @server.next_stream
    err = r.ready

    assert_kind_of SDK::ProviderInitializationError, err
    assert_equal ErrorCode::GENERAL, err.error_code
    assert_includes err.message, "no ruleset within 0.2s"
    d = r.client.fetch_string_details(flag_key: "string-flag", default_value: "x")

    assert_equal ["x", ErrorCode::PROVIDER_NOT_READY], [d.value, d.error_code]

    conn.write(put('"e1"', provider_ruleset))
    r.next(Event::PROVIDER_READY)

    assert_equal "blue", value(r)
    assert_equal [Event::PROVIDER_ERROR, Event::PROVIDER_READY], r.types
    assert_equal ErrorCode::GENERAL, r.next(Event::PROVIDER_ERROR)[:error_code]
  end

  def test_is_ready_from_the_bootstrap_file_then_refreshed_by_the_stream
    cache_dir = File.join(@dir, "cache")
    r = register(provider(bootstrap_file: write_bootstrap, cache_dir: cache_dir))

    assert_nil r.ready
    assert_equal "blue", value(r)

    conn = @server.next_stream

    refute conn.request.headers.key?("last-event-id")
    conn.write(put('"e2"', changed_provider_ruleset))
    changed = r.next(Event::PROVIDER_CONFIGURATION_CHANGED)

    assert_equal %w[new-flag string-flag targeted], changed[:flags_changed]
    assert_equal "red", value(r)
    assert_equal '"e2"', Delivery::Cache.new(cache_dir, KEY).load.etag
  end

  def test_is_ready_from_the_cache_offline_without_any_request
    save_cache(provider_ruleset, '"c1"')
    r = register(provider(offline: true, cache_dir: @dir))

    assert_nil r.ready
    assert_equal "blue", value(r)
    r.provider.shutdown

    assert_empty @server.requests
  end

  def test_sends_the_cached_etag_as_last_event_id
    save_cache(provider_ruleset, '"c1"')
    r = register(provider(cache_dir: @dir))

    assert_nil r.ready
    assert_equal '"c1"', @server.next_stream.request.headers["last-event-id"]
  end

  def test_prefers_the_bootstrap_file_to_the_cache
    save_cache(changed_provider_ruleset, '"c1"')
    r = register(provider(offline: true, bootstrap_file: write_bootstrap, cache_dir: @dir))

    assert_nil r.ready
    assert_equal "blue", value(r)
  end

  def test_falls_back_to_the_cache_after_a_bad_bootstrap_file
    save_cache(changed_provider_ruleset, '"c1"')
    r = register(provider(offline: true, bootstrap_file: write_bootstrap("{"), cache_dir: @dir))

    assert_nil r.ready
    assert_equal "red", value(r)
  end

  def test_warns_about_a_corrupt_cache
    save_cache(provider_ruleset, '"c1"')
    File.write(Delivery::Cache.new(@dir, KEY).path, "{")
    r = register(provider(offline: true, cache_dir: @dir))

    assert_kind_of SDK::ProviderInitializationError, r.ready
    assert(@logger.messages(:warn).any? { |m| m.include?("could not read the ruleset cache") })
  end

  def test_fails_offline_without_a_ruleset
    r = register(provider(offline: true, cache_dir: @dir))
    err = r.ready

    assert_kind_of SDK::ProviderInitializationError, err
    assert_equal ErrorCode::GENERAL, err.error_code
    assert_includes err.message, "offline"
    assert_equal [Event::PROVIDER_ERROR], r.types
    assert_empty @server.requests
  end

  def test_is_fatal_for_a_client_key
    r = register(provider(sdk_key: "mf_client_abc"))
    err = r.ready

    assert_equal ErrorCode::PROVIDER_FATAL, err.error_code
    d = r.client.fetch_string_details(flag_key: "string-flag", default_value: "x")

    assert_equal ["x", ErrorCode::PROVIDER_FATAL], [d.value, d.error_code]
    assert_equal ErrorCode::PROVIDER_FATAL, r.next(Event::PROVIDER_ERROR)[:error_code]
    assert_empty @server.requests
  end

  def test_is_fatal_when_the_server_answers_unauthorized
    refused(401)
  end

  def test_is_fatal_when_the_server_answers_forbidden
    refused(403)
  end

  def refused(status)
    server = new_server(stream: FakeServer::Scripted.new(status: status))
    r = register(provider(server))
    err = r.ready

    assert_equal ErrorCode::PROVIDER_FATAL, err.error_code
    refute_includes err.message, KEY
    d = r.client.fetch_string_details(flag_key: "string-flag", default_value: "x")

    assert_equal ["x", ErrorCode::PROVIDER_FATAL], [d.value, d.error_code]
    assert_equal ErrorCode::PROVIDER_FATAL, r.provider.fetch_string_value(flag_key: "string-flag",
                                                                          default_value: "x").error_code
    sleep 0.1

    assert_equal 1, server.requests(STREAM).size
    assert_equal [Event::PROVIDER_ERROR], r.types
  end

  def test_fails_after_shutdown
    p = provider(bootstrap_file: write_bootstrap)
    p.shutdown
    err = assert_raises(SDK::EvaluationError) { p.init }

    assert_equal ErrorCode::GENERAL, err.error_code
    assert_includes err.message, "shut down"
  end

  def test_shutdown_ends_a_waiting_init
    r = register(provider(init_timeout: 10))
    @server.next_stream
    r.provider.shutdown

    assert_kind_of SDK::ProviderInitializationError, r.ready(1)
  end

  # events

  def test_emits_the_fatal_error_on_revoked_and_keeps_serving
    r = register(provider)
    conn = @server.next_stream
    conn.write(put('"e2"', changed_provider_ruleset))

    assert_nil r.ready
    value(r)

    conn.write("event: revoked\ndata: {}\n\n")
    ev = r.next(Event::PROVIDER_ERROR)

    assert_equal ErrorCode::PROVIDER_FATAL, ev[:error_code]
    refute_includes ev[:message], KEY
    # The provider keeps serving the last ruleset; the OpenFeature SDK puts a provider that
    # reported PROVIDER_FATAL in the FATAL state and returns the default itself.
    direct = r.provider.fetch_string_value(flag_key: "string-flag", default_value: "x")

    assert_equal ["red", "red", nil], [direct.value, direct.variant, direct.error_code]
    d = r.client.fetch_string_details(flag_key: "string-flag", default_value: "x")

    assert_equal ["x", ErrorCode::PROVIDER_FATAL], [d.value, d.error_code]

    sleep 0.2

    assert_equal 1, @server.requests(STREAM).size
    SDK.shutdown

    assert_empty @server.requests(EVENTS)
    assert_equal 1, r.types.count(Event::PROVIDER_ERROR)
  end

  def test_a_refused_events_post_is_fatal
    server = new_server(events: FakeServer::Scripted.new(status: 401))
    r = register(provider(server))
    server.next_stream.write(put('"e1"', provider_ruleset))

    assert_nil r.ready
    1000.times { |i| value(r, "string-flag", ctx("user-#{i}")) }
    ev = r.next(Event::PROVIDER_ERROR)

    assert_equal ErrorCode::PROVIDER_FATAL, ev[:error_code]
    wait_for { Thread.list.none? { |t| t.name == "megooflags-stream" && t.alive? } }

    assert_equal 1, server.requests(EVENTS).size
    assert_equal "blue", r.provider.fetch_string_value(flag_key: "string-flag", default_value: "x").value
  end

  def test_emits_stale_on_a_disconnect_and_ready_on_reconnect
    r = register(provider)
    conn = @server.next_stream
    conn.write(put('"e1"', provider_ruleset))

    assert_nil r.ready

    conn.drop
    r.next(Event::PROVIDER_STALE)

    assert_equal "blue", value(r)
    r.next(Event::PROVIDER_READY, 2)
    sleep 0.1

    assert_equal [Event::PROVIDER_READY, Event::PROVIDER_STALE, Event::PROVIDER_READY], r.types
  end

  def test_emits_nothing_after_shutdown
    r = register(provider)
    conn = @server.next_stream
    conn.write(put('"e1"', provider_ruleset))

    assert_nil r.ready
    before = r.types
    r.provider.shutdown
    conn.write(put('"e2"', changed_provider_ruleset))
    sleep 0.1

    assert_equal before, r.types
    assert_equal "blue", r.provider.fetch_string_value(flag_key: "string-flag", default_value: "x").value
  end

  # shutdown

  def test_shutdown_sends_the_events
    shutdown_sends_the_events(send_context: false)
  end

  def test_shutdown_sends_the_events_with_the_context
    shutdown_sends_the_events(send_context: true)
  end

  def shutdown_sends_the_events(send_context:)
    r = register(provider(send_context: send_context))
    @server.next_stream.write(put('"e1"', provider_ruleset))

    assert_nil r.ready
    alice = ctx("alice", plan: "pro")
    3.times { value(r, "string-flag", alice) }
    value(r, "string-flag", ctx("bob"))
    r.client.fetch_boolean_value(flag_key: "bool-flag", default_value: false, evaluation_context: alice)
    value(r, "missing", alice)
    SDK.shutdown

    batches = @server.requests(EVENTS).map { |req| JSON.parse(req.body) }
    counts = Hash.new(0)
    batches.each { |b| b["counts"].each { |c| counts["#{c["flag_key"]}/#{c["variant"].inspect}"] += c["count"] } }
    exposures = batches.flat_map { |b| b["exposures"] }

    assert_equal({ 'string-flag/"blue"' => 4, 'bool-flag/"on"' => 1, "missing/nil" => 1 }, counts)
    assert_equal([%w[string-flag blue], %w[string-flag blue], %w[bool-flag on]],
                 exposures.map { |e| [e["flag_key"], e["variant"]] })
    if send_context
      assert_equal({ "key" => "alice", "plan" => "pro" }, exposures[0]["context"])
      assert_equal({ "key" => "bob" }, exposures[1]["context"])
    else
      assert(exposures.none? { |e| e.key?("context") })
    end
    assert_equal "Bearer #{KEY}", @server.requests(EVENTS)[0].headers["authorization"]
  end

  def test_sends_a_time_attribute_as_iso_8601_text
    r = register(provider(send_context: true))
    @server.next_stream.write(put('"e1"', provider_ruleset))

    assert_nil r.ready
    value(r, "string-flag", ctx("alice", signed_up: Time.utc(2020, 1, 2, 3, 4, 5)))
    SDK.shutdown

    exposure = JSON.parse(@server.requests(EVENTS)[0].body)["exposures"][0]

    assert_equal({ "key" => "alice", "signed_up" => "2020-01-02T03:04:05Z" }, exposure["context"])
  end

  def test_shutdown_is_idempotent_and_keeps_serving_the_last_ruleset
    r = register(provider)
    @server.next_stream.write(put('"e1"', provider_ruleset))

    assert_nil r.ready
    value(r)
    r.provider.shutdown
    r.provider.shutdown

    assert_equal 1, @server.requests(EVENTS).size
    d = r.provider.fetch_string_value(flag_key: "string-flag", default_value: "x", evaluation_context: ctx("later"))

    assert_equal "blue", d.value
    r.provider.shutdown
    sleep 0.05

    assert_equal 1, @server.requests(EVENTS).size
  end

  def test_offline_records_nothing
    save_cache(provider_ruleset, '"c1"')
    r = register(provider(offline: true, cache_dir: @dir))

    assert_nil r.ready
    value(r)
    SDK.shutdown

    assert_empty @server.requests
  end

  def test_polls_only_with_disable_stream
    @server.reply(RULESET, FakeServer::Scripted.new(status: 200, headers: { "ETag" => '"p1"' }, body: provider_ruleset))
    r = register(provider(disable_stream: true))

    assert_nil r.ready
    assert_equal "blue", value(r)
    assert_empty @server.requests(STREAM)
  end
end
