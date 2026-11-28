# frozen_string_literal: true

require_relative "../test_helper"
require_relative "../support/fake_server"
require "megooflags/internal/delivery/poller"

class PollerTest < Minitest::Test
  include ThreadTestHelpers

  Transport = MegooFlags::Internal::Transport
  Delivery = MegooFlags::Internal::Delivery

  KEY = "mf_server_poller-key-value"
  RULESET = "/sdk/v1/ruleset"

  def setup
    @threads = live_threads
    @server = FakeServer.new
    @etag = ""
    @got = []
  end

  def teardown
    @server.stop
    assert_no_thread_leak(@threads)
  end

  def poller(timeout: 10)
    on_ruleset = lambda do |raw, etag|
      @got << [raw, etag]
      next false if raw == "invalid"

      @etag = etag
      true
    end
    Delivery::Poller.new(url: Transport.sdk_url(@server.url, "ruleset"), sdk_key: KEY, etag: -> { @etag },
                         on_ruleset: on_ruleset, timeout: timeout)
  end

  def scripted(status, headers = {}, body = "") = FakeServer::Scripted.new(status: status, headers: headers, body: body)

  # A held-open response that never sends its body.
  def hanging = FakeServer::Stream.new(headers: { "Content-Length" => "10" })

  def test_polls_without_if_none_match_first_then_with_the_etag
    @server.reply(RULESET, scripted(200, { "ETag" => '"v1"' }, "{}"), scripted(304))
    p = poller
    p.poll(Transport::Cancel.new)
    p.poll(Transport::Cancel.new)
    first, second = @server.requests(RULESET)

    refute first.headers.key?("if-none-match")
    assert_equal "Bearer #{KEY}", first.headers["authorization"]
    assert_equal "application/json", first.headers["accept"]
    assert_equal Transport::USER_AGENT, first.headers["user-agent"]
    assert_equal RULESET, first.url
    assert_equal '"v1"', second.headers["if-none-match"]
  end

  def test_a_200_body_and_its_etag_go_to_on_ruleset
    @server.reply(RULESET, scripted(200, { "ETag" => '"v2"' }, '{"flags":{}}'))
    poller.poll(Transport::Cancel.new)

    assert_equal [['{"flags":{}}', '"v2"']], @got
  end

  def test_a_200_without_etag_gives_an_empty_one
    @server.reply(RULESET, scripted(200, {}, "{}"))
    poller.poll(Transport::Cancel.new)

    assert_equal [["{}", ""]], @got
  end

  def test_304_is_success_without_on_ruleset
    @etag = '"v1"'
    @server.reply(RULESET, scripted(304))
    poller.poll(Transport::Cancel.new)

    assert_empty @got
  end

  def test_an_invalid_ruleset_fails
    @server.reply(RULESET, scripted(200, {}, "invalid"))
    err = assert_raises(Delivery::InvalidRulesetError) { poller.poll(Transport::Cancel.new) }

    assert_includes err.message, "invalid ruleset"
  end

  def test_a_refused_key_is_fatal
    [401, 403].each do |status|
      @server.reply("/s#{status}#{RULESET}", scripted(status))
      p = Delivery::Poller.new(url: Transport.sdk_url("#{@server.url}/s#{status}", "ruleset"), sdk_key: KEY)
      err = assert_raises(Transport::FatalError) { p.poll(Transport::Cancel.new) }

      assert_equal status, err.status
    end
  end

  def test_a_429_reports_its_retry_after
    @server.reply(RULESET, scripted(429, "Retry-After" => "7"))
    err = assert_raises(Transport::RetryAfterError) { poller.poll(Transport::Cancel.new) }

    assert_in_delta 7.0, err.after
  end

  def test_a_500_is_an_http_error
    @server.reply(RULESET, scripted(500, {}, "oops"))
    err = assert_raises(Transport::HttpError) { poller.poll(Transport::Cancel.new) }

    assert_equal 500, err.status
  end

  def test_times_out
    @server.reply(RULESET, hanging)
    started = monotonic
    err = assert_raises(Delivery::PollTimeoutError) { poller(timeout: 0.1).poll(Transport::Cancel.new) }

    assert_includes err.message, "timed out"
    assert_operator monotonic - started, :<, 2
    assert_empty @got
  end

  def test_cancel_ends_a_poll_in_flight
    @server.reply(RULESET, hanging)
    cancel = Transport::Cancel.new
    thread = Thread.new do
      poller.poll(cancel)
      nil
    rescue StandardError => e
      e
    end
    @server.next_stream
    started = monotonic
    cancel.cancel

    assert thread.join(5)
    assert_operator monotonic - started, :<, 1
    assert_kind_of Transport::CancelledError, thread.value
    assert_empty @got
  end
end
