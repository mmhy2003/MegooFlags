# frozen_string_literal: true

require_relative "../test_helper"
require_relative "../support/fake_server"
require "json"
require "megooflags/internal/events/batcher"

class BatcherTest < Minitest::Test
  include ThreadTestHelpers

  Transport = MegooFlags::Internal::Transport
  Events = MegooFlags::Internal::Events

  KEY = "mf_server_batcher-key-value"
  EVENTS = "/sdk/v1/events"

  # A clock the test moves by hand; it starts at 1000 s.
  class FakeClock
    def initialize
      @lock = Mutex.new
      @now = 1000.0
    end

    def call = @lock.synchronize { @now }

    def advance(seconds) = @lock.synchronize { @now += seconds }
  end

  def setup
    @threads = live_threads
    @server = FakeServer.new
    @logger = TestLogger.new
    @made = []
  end

  def teardown
    @made.each(&:stop)
    @server.stop
    assert_no_thread_leak(@threads)
  end

  def new_batcher(**opts)
    args = { url: Transport.sdk_url(@server.url, "events"), sdk_key: KEY, logger: @logger, flush_interval: 0.05,
             retry_base: 0.01, retry_max: 0.04 }.merge(opts)
    b = Events::Batcher.new(**args)
    @made << b
    b.start
    b
  end

  def ev(flag, ctx_key, context = nil)
    Events::Evaluation.new(flag_key: flag, variant: "on", served: true, reason: "STATIC", context_key: ctx_key,
                           context: context, at: (Time.now.to_f * 1000).to_i)
  end

  def scripted(status, headers = {}) = FakeServer::Scripted.new(status: status, headers: headers)

  def sent = @server.requests(EVENTS)

  def body(req) = JSON.parse(req.body)

  def items(doc) = doc["exposures"].size + doc["counts"].size

  # totals are the exposures and the count per flag in docs.
  def totals(docs)
    counts = Hash.new(0)
    exposures = 0
    docs.each do |d|
      exposures += d["exposures"].size
      d["counts"].each { |c| counts[c["flag_key"]] += c["count"] }
    end
    [exposures, counts]
  end

  # respond answers each events request with the block's status for (body, n), n counting from 1.
  def respond(&status)
    n = 0
    lock = Mutex.new
    @server.reply(EVENTS, lambda { |req|
      i = lock.synchronize { n += 1 }
      scripted(status.call(body(req), i))
    })
  end

  def accept = @server.reply(EVENTS, scripted(202))

  def test_posts_json_with_the_bearer_key_and_the_user_agent
    accept
    b = new_batcher
    b.record(ev("f", "u1"))
    wait_for { sent.size == 1 }
    r = sent[0]
    assert_equal "POST", r.verb
    assert_equal "Bearer #{KEY}", r.headers["authorization"]
    assert_equal Transport::USER_AGENT, r.headers["user-agent"]
    assert_equal "application/json", r.headers["content-type"]
    refute_includes r.url, KEY
    got = body(r)
    assert_equal([%w[f on]], got["exposures"].map { |e| [e["flag_key"], e["variant"]] })
    assert_equal([["f", "on", 1]], got["counts"].map { |c| [c["flag_key"], c["variant"], c["count"]] })
  end

  def test_flushes_on_the_interval
    accept
    b = new_batcher
    b.record(ev("f", "u1"))
    assert_empty sent
    wait_for { sent.size == 1 }
    b.record(ev("g", "u1"))
    wait_for { sent.size == 2 }
    assert_equal [2, { "f" => 1, "g" => 1 }], totals(sent.map { |r| body(r) })
  end

  def test_flushes_when_a_list_reaches_1000_items
    accept
    b = new_batcher(flush_interval: 3600)
    999.times { |i| b.record(ev("f", "u#{i}")) }
    sleep 0.1
    assert_empty sent
    b.record(ev("f", "u999"))
    wait_for { sent.size == 1 }
    assert_equal 1000, body(sent[0])["exposures"].size
  end

  def test_shutdown_sends_everything_once
    accept
    b = new_batcher(flush_interval: 3600)
    b.record(ev("f", "u1"))
    b.record(ev("f", "u2"))
    b.record(ev("g", "u1"))
    assert b.shutdown(5.0)
    assert_equal [3, { "f" => 2, "g" => 1 }], totals(sent.map { |r| body(r) })
    assert b.shutdown(5.0)
    assert_equal 1, sent.size
  end

  def test_shutdown_gives_up_at_the_deadline
    release = Queue.new
    @server.reply(EVENTS, lambda { |_req|
      release.pop(timeout: 10)
      scripted(202)
    })
    b = new_batcher(flush_interval: 3600)
    b.record(ev("f", "u1"))
    start = monotonic
    refute b.shutdown(0.2)
    assert_operator monotonic - start, :<, 1.0
  ensure
    release&.push(true)
  end

  def test_shutdown_does_not_wait_for_a_retry_delay_past_the_deadline
    @server.reply(EVENTS, scripted(429, { "Retry-After" => "30" }))
    b = new_batcher(flush_interval: 3600)
    b.record(ev("f", "u1"))
    start = monotonic
    refute b.shutdown(2.0)
    assert_operator monotonic - start, :<, 1.0
    assert_equal 1, sent.size
  end

  def test_shutdown_waits_out_a_retry_delay_that_fits
    @server.reply(EVENTS, scripted(503), scripted(202))
    b = new_batcher(flush_interval: 3600, retry_base: 0.2, retry_max: 1.0)
    b.record(ev("f", "u1"))
    assert b.shutdown(5.0)
    first, second = sent
    assert_operator second.at - first.at, :>=, 0.2
    assert_equal body(first), body(second)
  end

  def test_stop_sends_nothing
    accept
    b = new_batcher(flush_interval: 3600)
    b.record(ev("f", "u1"))
    b.stop
    b.stop
    refute b.shutdown(5.0)
    assert_empty sent
  end

  def test_stop_and_shutdown_before_start
    b = Events::Batcher.new(url: Transport.sdk_url(@server.url, "events"), sdk_key: KEY)
    b.stop
    refute b.shutdown(5.0)
    b.start
    b.record(ev("f", "u1"))
    assert_equal 1, b.dropped
  end

  def test_a_full_queue_drops_and_counts_without_blocking
    b = Events::Batcher.new(url: "http://127.0.0.1:9/sdk/v1/events", sdk_key: KEY, queue_size: 2)
    start = monotonic
    5.times { |i| b.record(ev("f", "u#{i}")) }
    assert_operator monotonic - start, :<, 0.1
    assert_equal 3, b.dropped
    b.stop
  end

  def test_records_after_shutdown_or_stop_are_dropped
    accept
    b = new_batcher
    assert b.shutdown(5.0)
    b.record(ev("f", "u1"))
    b.stop
    b.record(ev("f", "u2"))
    assert_equal 2, b.dropped
    assert_empty sent
  end

  def test_401_stops_for_good_and_calls_on_fatal_once
    @server.reply(EVENTS, scripted(401))
    fatals = Queue.new
    b = new_batcher(on_fatal: ->(err) { fatals << err })
    b.record(ev("f", "u1"))
    wait_for { fatals.size == 1 }
    b.record(ev("g", "u1"))
    sleep 0.2
    refute b.shutdown(5.0)
    assert_equal 1, sent.size
    assert_equal 1, fatals.size
    err = fatals.pop
    assert_kind_of Transport::FatalError, err
    assert_equal 401, err.status
    assert_equal 1, b.dropped
  end

  def test_on_fatal_may_stop_and_shut_down_the_batcher
    @server.reply(EVENTS, scripted(403))
    results = Queue.new
    b = nil
    b = new_batcher(on_fatal: lambda { |_err|
      b.stop
      results << b.shutdown(5.0)
    })
    b.record(ev("f", "u1"))
    assert_equal false, results.pop(timeout: 5)
  end

  def test_413_splits_the_batch
    respond { |doc, _n| items(doc) > 10 ? 413 : 202 }
    b = new_batcher(flush_interval: 3600)
    25.times { |i| b.record(ev("f", "u#{i}")) }
    assert b.shutdown(5.0)
    accepted = sent.map { |r| body(r) }.select { |d| items(d) <= 10 }
    assert(accepted.all? { |d| items(d).positive? })
    assert_equal [25, { "f" => 25 }], totals(accepted)
  end

  def test_a_body_over_1_mb_is_split_before_sending
    accept
    b = new_batcher(flush_interval: 3600, send_context: true)
    big = { "blob" => "x" * 2000 }
    1000.times { |i| b.record(ev("f", "u#{i}", big)) }
    assert b.shutdown(5.0)
    assert_operator sent.size, :>=, 2
    assert(sent.all? { |r| r.body.bytesize <= 1_000_000 })
    assert_equal [1000, { "f" => 1000 }], totals(sent.map { |r| body(r) })
  end

  def test_a_single_item_that_is_too_large_is_dropped
    accept
    b = new_batcher(flush_interval: 3600, send_context: true)
    b.record(ev("f", "u1", { "blob" => "x" * 1_100_000 }))
    assert b.shutdown(5.0)
    # The exposure is dropped; its count goes on its own.
    assert_equal([[0, 1]], sent.map { |r| body(r) }.map { |d| [d["exposures"].size, d["counts"].size] })
    assert(@logger.messages(:warn).any? { |m| m.include?("too large") })
  end

  def test_a_context_that_is_not_json_drops_the_batch
    accept
    b = new_batcher(flush_interval: 3600, send_context: true)
    b.record(ev("f", "u1", { "x" => Float::NAN }))
    assert b.shutdown(5.0)
    assert_empty sent
    assert(@logger.messages(:error).any? { |m| m.include?("cannot encode events") })
  end

  def test_422_drops_the_batch
    @server.reply(EVENTS, scripted(422))
    b = new_batcher
    b.record(ev("f", "u1"))
    wait_for { sent.size == 1 }
    sleep 0.2 # four intervals
    assert b.shutdown(5.0)
    assert_equal 1, sent.size
    assert(@logger.messages(:warn).any? { |m| m.include?("refused an events batch") })
  end

  def test_429_resends_after_retry_after
    @server.reply(EVENTS, scripted(429, { "Retry-After" => "30" }), scripted(202))
    clock = FakeClock.new
    b = new_batcher(clock: clock, flush_interval: 0.02)
    b.record(ev("f", "u1"))
    wait_for { sent.size == 1 }
    sleep 0.1 # the batcher reads the 429 before the clock moves
    clock.advance(29)
    sleep 0.1
    assert_equal 1, sent.size
    clock.advance(1.5)
    wait_for { sent.size == 2 }
    first, second = sent
    assert_equal body(first), body(second)
  end

  def test_5xx_retries_with_growing_backoff_and_keeps_the_counts
    clock = FakeClock.new
    b = nil
    respond do |_doc, n|
      # Recorded while the first batch is in flight: merged on restore.
      3.times { b.record(ev("f", "u1")) } if n == 1
      n <= 2 ? 503 : 202
    end
    b = new_batcher(clock: clock, flush_interval: 0.02, retry_base: 2.0, retry_max: 60.0)
    5.times { b.record(ev("f", "u1")) }
    wait_for { sent.size == 1 }
    sleep 0.1
    assert_equal 1, sent.size
    clock.advance(2.1) # the first wait is the base, 2 s
    wait_for { sent.size == 2 }
    sleep 0.1 # the batcher reads the 503 before the clock moves
    clock.advance(2.1) # the second wait is at least 3.2 s
    sleep 0.1
    assert_equal 2, sent.size
    clock.advance(2.1)
    wait_for { sent.size == 3 }
    assert b.shutdown(5.0)
    assert_equal [1, { "f" => 8 }], totals([body(sent[2])])
  end

  def test_a_split_stops_after_a_fatal_status
    respond { |doc, _n| items(doc) > 10 ? 413 : 401 }
    b = new_batcher(flush_interval: 3600)
    25.times { |i| b.record(ev("f", "u#{i}")) }
    refute b.shutdown(5.0)
    # 26 items: 413; 13: 413; 6: 401, and no request after it.
    assert_equal([26, 13, 6], sent.map { |r| items(body(r)) })
  end
end
