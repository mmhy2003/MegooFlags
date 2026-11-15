# frozen_string_literal: true

require_relative "../test_helper"
require_relative "../support/fake_server"
require "megooflags/internal/delivery/streamer"

class StreamerTest < Minitest::Test
  include ThreadTestHelpers

  Transport = MegooFlags::Internal::Transport
  Delivery = MegooFlags::Internal::Delivery

  KEY = "mf_server_streamer-key-value"
  STREAM = "/sdk/v1/stream"

  Seen = Struct.new(:puts, :connected, :disconnects)

  # Running is a Streamer running on a test thread until it returns.
  class Running
    attr_reader :cancel

    def initialize(streamer)
      @cancel = Transport::Cancel.new
      @thread = Thread.new { streamer.run(@cancel) }
    end

    def stop
      @cancel.cancel
      join
    end

    def join(timeout = 5)
      raise "streamer still running" unless @thread.join(timeout)

      @thread.value
    end
  end

  def setup
    @threads = live_threads
    @server = FakeServer.new
    @running = []
    @logger = TestLogger.new
  end

  def teardown
    @running.each(&:stop)
    @server.stop
    assert_no_thread_leak(@threads)
  end

  def start(**opts)
    seen = Seen.new([], 0, [])
    args = {
      url: Transport.sdk_url(@server.url, "stream"), sdk_key: KEY, logger: @logger,
      on_put: lambda { |raw, id|
        seen.puts << [raw, id]
        raw != "invalid"
      },
      on_connected: -> { seen.connected += 1 },
      on_disconnected: ->(err, n) { seen.disconnects << [err, n] },
      backoff: Transport::Backoff.new(0.01, 0.1, rand: -> { 1.0 })
    }.merge(opts)
    running = Running.new(Delivery::Streamer.new(**args))
    @running << running
    [seen, running]
  end

  def stream = FakeServer::Stream.new

  def scripted(status, headers = {}) = FakeServer::Scripted.new(status: status, headers: headers)

  def test_put_events_go_to_on_put_with_the_stream_headers
    @server.reply(STREAM, stream)
    seen, = start
    conn = @server.next_stream
    conn.write(%(event: put\nid: "e1"\ndata: {"a":1}\n\n))
    wait_for { seen.puts.size == 1 }

    assert_equal [['{"a":1}', '"e1"']], seen.puts
    assert_equal 1, seen.connected
    req = conn.request

    assert_equal "GET", req.verb
    assert_equal "Bearer #{KEY}", req.headers["authorization"]
    assert_equal "text/event-stream", req.headers["accept"]
    assert_equal "no-cache", req.headers["cache-control"]
    assert_equal Transport::USER_AGENT, req.headers["user-agent"]
    refute req.headers.key?("last-event-id")
    assert_equal STREAM, req.url
  end

  def test_sends_last_event_id_when_there_is_an_etag
    @server.reply(STREAM, stream)
    start(last_event_id: -> { '"etag-7"' })

    assert_equal '"etag-7"', @server.next_stream.request.headers["last-event-id"]
  end

  def test_pings_keep_the_stream_alive
    @server.reply(STREAM, stream)
    seen, = start(dead_timeout: 0.15)
    conn = @server.next_stream
    8.times do
      sleep 0.05
      conn.write(": ping\n")
    end

    assert_equal 1, @server.requests(STREAM).size
    assert_empty seen.disconnects
  end

  def test_a_silent_connection_is_dead_and_reconnects
    @server.reply(STREAM, stream)
    seen, = start(dead_timeout: 0.05)
    @server.next_stream
    @server.next_stream
    err, failures = seen.disconnects.first

    assert_equal 1, failures
    assert_kind_of Delivery::DeadConnectionError, err
    assert_includes err.message, "dead"
  end

  def test_unknown_events_are_ignored_and_an_invalid_put_does_not_end_the_stream
    @server.reply(STREAM, stream)
    seen, = start
    conn = @server.next_stream
    conn.write("event: mystery\ndata: x\n\n")
    conn.write("data: unnamed\n\n")
    conn.write("event: put\nid: 1\ndata: invalid\n\n")
    conn.write("event: put\nid: 2\ndata: {}\n\n")
    wait_for { seen.puts.size == 2 }

    assert_equal %w[1 2], seen.puts.map(&:last)
    assert_empty seen.disconnects
    assert_equal 1, @server.requests(STREAM).size
    assert_equal(1, @logger.messages(:warn).count { |m| m.include?("invalid ruleset") })
  end

  def test_revoked_stops_with_a_fatal_error
    @server.reply(STREAM, stream)
    seen, running = start
    conn = @server.next_stream
    conn.write("event: revoked\ndata: {}\n\n")
    err = running.join

    assert_kind_of Transport::FatalError, err
    assert_equal "revoked", err.reason
    assert_empty seen.disconnects
    conn.finish

    assert conn.closed?
    assert_equal 1, @server.requests(STREAM).size
  end

  def test_a_refused_key_stops_with_a_fatal_error
    [401, 403].each do |status|
      @server.reply("/s#{status}#{STREAM}", scripted(status))
      seen, running = start(url: Transport.sdk_url("#{@server.url}/s#{status}", "stream"))
      err = running.join

      assert_kind_of Transport::FatalError, err
      assert_equal status, err.status
      assert_equal 0, seen.connected
      assert_empty seen.disconnects
      assert_equal 1, @server.requests("/s#{status}#{STREAM}").size
    end
  end

  def test_a_429_waits_its_retry_after
    @server.reply(STREAM, scripted(429, "Retry-After" => "1"), stream)
    seen, = start
    @server.next_stream
    first, second = @server.requests(STREAM)

    assert_operator second.at - first.at, :>=, 0.95
    assert_kind_of Transport::RetryAfterError, seen.disconnects.first.first
  end

  def test_5xx_backs_off_with_growing_waits
    @server.reply(STREAM, scripted(500), scripted(502), scripted(503), scripted(500), stream)
    seen, = start(backoff: Transport::Backoff.new(0.04, 1.0, rand: -> { 1.0 }))
    @server.next_stream
    at = @server.requests(STREAM).map(&:at)
    gaps = at.each_cons(2).map { |a, b| b - a }

    assert_equal 4, gaps.size
    gaps.zip([0.04, 0.08, 0.16, 0.32]).each { |gap, want| assert_operator gap, :>=, want - 0.005 }
    assert_equal [1, 2, 3, 4], seen.disconnects.map(&:last)
  end

  def test_the_retry_field_replaces_the_backoff_base
    @server.reply(STREAM, stream)
    seen, = start(backoff: Transport::Backoff.new(5.0, 60.0, rand: -> { 1.0 }))
    conn = @server.next_stream
    conn.write("retry: 20\n\n")
    wait_for { seen.connected == 1 }
    sleep 0.05
    conn.finish
    started = monotonic
    @server.next_stream

    assert_operator monotonic - started, :<, 1
  end

  def test_a_stable_connection_resets_the_backoff
    @server.reply(STREAM, scripted(500), scripted(500), stream, scripted(500), stream)
    seen, = start(stable_after: 0.05)
    conn = @server.next_stream
    # The stable clock starts when the client has read the 200, not when the server accepted.
    wait_for { seen.connected == 1 }
    sleep 0.2
    conn.finish
    @server.next_stream

    assert_equal [1, 2, 1, 2], seen.disconnects.map(&:last)
  end

  def test_stop_returns_promptly_from_an_open_stream
    @server.reply(STREAM, stream)
    seen, running = start
    conn = @server.next_stream
    started = monotonic
    running.cancel.cancel

    assert_nil running.join
    assert_operator monotonic - started, :<, 0.5
    conn.finish

    assert conn.closed?
    assert_empty seen.disconnects
  end

  def test_stop_returns_at_once_while_waiting_to_reconnect
    @server.reply(STREAM, scripted(500))
    seen, running = start(backoff: Transport::Backoff.new(60.0, 60.0))
    wait_for { seen.disconnects.size == 1 }
    started = monotonic
    running.cancel.cancel

    assert_nil running.join
    assert_operator monotonic - started, :<, 0.5
  end
end
