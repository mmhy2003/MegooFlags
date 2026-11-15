# frozen_string_literal: true

require_relative "../test_helper"
require_relative "../support/fake_server"
require_relative "../support/ruleset_gen"
require "json"
require "tmpdir"
require "megooflags/internal/delivery/sync"

class SyncTest < Minitest::Test
  include ThreadTestHelpers
  include RulesetGen

  Transport = MegooFlags::Internal::Transport
  Delivery = MegooFlags::Internal::Delivery

  KEY = "mf_server_sync-key-value"
  STREAM = "/sdk/v1/stream"
  RULESET = "/sdk/v1/ruleset"

  def setup
    @threads = live_threads
    @server = FakeServer.new
    @dir = Dir.mktmpdir("megooflags-sync")
    @store = Delivery::Store.new
    @cache = Delivery::Cache.new(@dir, KEY)
    @logger = TestLogger.new
    @changes = Queue.new
    @statuses = Queue.new
    @syncs = []
  end

  def teardown
    @syncs.each(&:stop)
    @server.stop
    FileUtils.remove_entry(@dir)
    assert_no_thread_leak(@threads)
  end

  def new_sync(**opts)
    args = {
      base_url: @server.url, sdk_key: KEY, logger: @logger, store: @store, cache: @cache,
      on_change: ->(old, new) { @changes << [old, new] },
      on_status: ->(status, err) { @statuses << [status, err] },
      tuning: Delivery::Tuning.new(retry_base: 0.01, retry_max: 0.05, poll_interval: 0.02)
    }.merge(opts)
    sync = Delivery::Sync.new(**args)
    @syncs << sync
    sync
  end

  def changes = drain(@changes)

  def statuses = drain(@statuses)

  def status_names = statuses.map(&:first)

  def drain(queue)
    all = []
    all << queue.pop until queue.empty?
    all.each { |item| queue << item }
    all
  end

  def raw(env) = JSON.generate(ruleset_json([]).merge("environment" => env))

  def put(id, data) = "event: put\nid: #{id}\ndata: #{data}\n\n"

  def stream = FakeServer::Stream.new

  def scripted(status, headers = {}, body = "") = FakeServer::Scripted.new(status: status, headers: headers, body: body)

  # apply

  def test_a_bootstrap_apply_does_not_write_the_cache
    assert_nil new_sync.apply(raw("boot"), "", "bootstrap")
    snap = @store.load

    assert_equal "bootstrap", snap.source
    assert_equal "boot", snap.ruleset.environment
    assert_empty Dir.children(@dir)
    assert_equal 1, changes.size
    assert_nil changes.first.first
  end

  def test_an_invalid_ruleset_keeps_the_previous_one
    sync = new_sync
    sync.apply(raw("one"), '"1"', "poll")
    err = sync.apply('{"schema_version":99}', '"2"', "poll")

    assert_kind_of MegooFlags::Internal::Engine::ParseError, err
    assert_equal '"1"', @store.load.etag
    assert_equal 1, changes.size
    assert(@logger.messages(:warn).any? { |m| m.include?("invalid ruleset") })
  end

  def test_each_ruleset_warning_is_logged_once
    bad = JSON.generate(ruleset_json([flag_json("re", "rules" => [rule("on", clause("email", "matches", "("))])]))
    sync = new_sync
    sync.apply(bad, '"1"', "stream")
    sync.apply(bad, '"2"', "stream")
    regex = @logger.messages(:warn).grep(/regex/)

    assert_equal 1, regex.size
    assert_includes regex.first, "flags.re"
  end

  # Stream and poll loops

  def test_a_stream_put_updates_the_store_and_the_cache
    @server.reply(STREAM, stream)
    new_sync.start
    @server.next_stream.write(put('"e1"', raw("streamed")))
    wait_for { changes.size == 1 }
    snap = @store.load

    assert_equal ['"e1"', "stream"], [snap.etag, snap.source]
    cached = @cache.load

    assert_equal '"e1"', cached.etag
    assert_equal JSON.parse(raw("streamed")), cached.ruleset
    assert_equal [:ready], status_names
  end

  def test_the_stores_etag_is_sent_as_last_event_id
    @server.reply(STREAM, stream)
    sync = new_sync
    sync.apply(raw("boot"), '"boot-etag"', "bootstrap")
    sync.start

    assert_equal '"boot-etag"', @server.next_stream.request.headers["last-event-id"]
  end

  def test_an_invalid_put_keeps_the_previous_ruleset_and_the_stream
    @server.reply(STREAM, stream)
    new_sync.start
    conn = @server.next_stream
    conn.write(put('"1"', raw("one")))
    conn.write(put('"2"', "{nope"))
    conn.write(put('"3"', raw("three")))
    wait_for { changes.size == 2 }

    assert_equal '"3"', @store.load.etag
    assert_equal 1, @server.requests(STREAM).size
  end

  def test_polls_after_two_stream_failures_and_stops_polling_on_reconnect
    @server.reply(STREAM, scripted(500), scripted(500), stream)
    @server.reply(RULESET, scripted(200, { "ETag" => '"p1"' }, raw("polled")))
    new_sync(tuning: Delivery::Tuning.new(retry_base: 0.06, retry_max: 0.06, poll_interval: 0.01)).start
    @server.next_stream
    wait_for { status_names.include?(:ready) }
    polls = @server.requests(RULESET)

    refute_empty polls
    # The first poll came after the second stream failure.
    assert_operator polls.first.at, :>=, @server.requests(STREAM)[1].at
    assert_equal "poll", @store.load.source
    assert_equal '"p1"', @cache.load.etag
    assert_equal %i[stale stale ready], status_names
    count = @server.requests(RULESET).size
    sleep 0.1

    assert_equal count, @server.requests(RULESET).size
  end

  def test_a_single_stream_failure_does_not_poll
    @server.reply(STREAM, scripted(500), stream)
    @server.reply(RULESET, scripted(200, {}, raw("polled")))
    new_sync.start
    @server.next_stream
    sleep 0.05

    assert_empty @server.requests(RULESET)
  end

  def test_polls_are_at_least_the_poll_interval_apart
    @server.reply(RULESET, scripted(304))
    new_sync(disable_stream: true, tuning: Delivery::Tuning.new(poll_interval: 0.1)).start
    sleep 0.35
    at = @server.requests(RULESET).map(&:at)

    assert_includes 3..4, at.size
    at.each_cons(2) { |a, b| assert_operator b - a, :>=, 0.095 }
  end

  def test_a_poll_429_waits_its_retry_after
    @server.reply(RULESET, scripted(429, "Retry-After" => "1"), scripted(304))
    new_sync(disable_stream: true).start
    wait_for(3) { @server.requests(RULESET).size == 2 }
    first, second = @server.requests(RULESET)

    assert_operator second.at - first.at, :>=, 0.95
  end

  def test_disable_stream_polls_only_and_reports_stale_then_ready
    @server.reply(RULESET, scripted(500), scripted(200, { "ETag" => '"v1"' }, raw("polled")), scripted(304))
    new_sync(disable_stream: true).start
    wait_for { status_names.first(3) == %i[stale ready ready] }

    assert_empty @server.requests(STREAM)
    assert_equal '"v1"', @store.load.etag
  end

  def test_revoked_is_fatal_once_and_stops
    @server.reply(STREAM, stream)
    @server.reply(RULESET, scripted(200, {}, raw("polled")))
    new_sync.start
    @server.next_stream.write("event: revoked\ndata: {}\n\n")
    wait_for { status_names.include?(:fatal) }
    sleep 0.08

    assert_equal %i[ready fatal], status_names
    fatal = statuses[1][1]

    assert_kind_of Transport::FatalError, fatal
    assert_equal "revoked", fatal.reason
    assert_equal 1, @server.requests(STREAM).size
    assert_empty @server.requests(RULESET)
  end

  def test_a_403_on_a_poll_is_fatal_and_stops_polling
    @server.reply(RULESET, scripted(403))
    new_sync(disable_stream: true).start
    wait_for { status_names.include?(:fatal) }
    sleep 0.08

    assert_equal [:fatal], status_names
    err = statuses.first.last

    assert_kind_of Transport::FatalError, err
    assert_equal 403, err.status
    assert_equal 1, @server.requests(RULESET).size
  end

  def test_a_fatal_poll_stops_the_stream
    @server.reply(STREAM, scripted(500))
    @server.reply(RULESET, scripted(401))
    new_sync(tuning: Delivery::Tuning.new(retry_base: 0.03, retry_max: 0.03, poll_interval: 0.01)).start
    wait_for { status_names.include?(:fatal) }
    count = @server.requests(STREAM).size
    sleep 0.1

    assert_equal count, @server.requests(STREAM).size
    assert_equal 1, status_names.count(:fatal)
  end

  def test_stop_is_safe_before_start_and_idempotent
    sync = new_sync
    sync.stop
    sync.stop
    sync.start
    sleep 0.02

    assert_empty @server.requests
  end

  def test_stop_ends_the_loops_and_silences_callbacks
    @server.reply(STREAM, stream)
    sync = new_sync
    sync.start
    conn = @server.next_stream
    wait_for { status_names == [:ready] }
    started = monotonic
    Array.new(2) { Thread.new { sync.stop } }.each { |t| t.join(5) }

    assert_operator monotonic - started, :<, 0.5
    conn.write(put('"late"', raw("late")))
    sync.start
    sleep 0.05

    assert_equal [:ready], status_names
    assert_empty changes
    assert_equal 1, @server.requests(STREAM).size
  end

  def test_stop_ends_a_poll_in_flight
    @server.reply(RULESET, FakeServer::Stream.new(headers: { "Content-Length" => "10" }))
    sync = new_sync(disable_stream: true)
    sync.start
    @server.next_stream
    started = monotonic
    sync.stop

    assert_operator monotonic - started, :<, 0.5
    assert_empty statuses
  end
end
