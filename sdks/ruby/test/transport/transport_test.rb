# frozen_string_literal: true

require_relative "../test_helper"
require_relative "../support/fake_server"
require "megooflags/internal/transport/transport"

class TransportTest < Minitest::Test
  Transport = MegooFlags::Internal::Transport

  KEY = "srv-secret-key"
  NOW = Time.utc(2026, 11, 10, 9, 0, 0)

  def test_headers_send_the_key_as_a_bearer_token_with_the_user_agent
    assert_equal(
      { "Authorization" => "Bearer #{KEY}", "User-Agent" => "megooflags-ruby/#{MegooFlags::VERSION}",
        "Accept" => "application/json" },
      Transport.headers(KEY)
    )
  end

  def test_headers_add_content_type_only_with_a_body
    assert_equal "application/json", Transport.headers(KEY, body: true)["Content-Type"]
    refute Transport.headers(KEY).key?("Content-Type")
  end

  def test_stream_headers_ask_for_an_event_stream_without_caching
    h = Transport.headers(KEY, stream: true)

    assert_equal "text/event-stream", h["Accept"]
    assert_equal "no-cache", h["Cache-Control"]
    refute Transport.headers(KEY).key?("Cache-Control")
  end

  def test_sdk_url_drops_trailing_slashes_and_never_carries_the_key
    assert_equal "http://h:1/sdk/v1/ruleset", Transport.sdk_url("http://h:1", "ruleset")
    assert_equal "http://h:1/base/sdk/v1/stream", Transport.sdk_url("http://h:1/base//", "stream")
    assert_equal "https://h/sdk/v1/events", Transport.sdk_url("https://h/", "events")
  end

  def test_success_statuses_are_not_errors
    [200, 204, 304].each { |s| assert_nil Transport.status_error(s, {}, NOW), s }
  end

  def test_refused_key_is_fatal
    [401, 403].each do |s|
      err = Transport.status_error(s, {}, NOW)

      assert_instance_of Transport::FatalError, err
      assert_equal s, err.status
      assert_equal "megooflags: SDK key refused (#{s})", err.message
    end
  end

  def test_revoked_is_fatal
    err = Transport::FatalError.new(reason: "revoked")

    assert_nil err.status
    assert_equal "revoked", err.reason
    assert_equal "megooflags: SDK key refused (revoked)", err.message
  end

  def test_429_carries_retry_after
    err = Transport.status_error(429, { "Retry-After" => "7" }, NOW)

    assert_instance_of Transport::RetryAfterError, err
    assert_in_delta 7.0, err.after
  end

  def test_429_reads_the_header_case_insensitively
    assert_in_delta 7.0, Transport.status_error(429, { "retry-after" => "7" }, NOW).after
  end

  def test_429_without_retry_after_waits_zero
    assert_in_delta 0.0, Transport.status_error(429, {}, NOW).after
  end

  def test_other_statuses_are_http_errors
    [404, 500, 302].each do |s|
      err = Transport.status_error(s, {}, NOW)

      assert_instance_of Transport::HttpError, err
      assert_equal s, err.status
      assert_equal "megooflags: HTTP #{s}", err.message
    end
  end

  def test_parse_retry_after
    {
      "5" => 5.0, " 5 " => 5.0, "-1" => 0.0, "0" => 0.0,
      "Tue, 10 Nov 2026 09:00:30 GMT" => 30.0,
      "Tue, 10 Nov 2026 08:59:00 GMT" => 0.0,
      "Tuesday, 10-Nov-26 09:00:10 GMT" => 10.0,
      "Tue Nov 10 09:00:20 2026" => 20.0
    }.each { |value, want| assert_in_delta want, Transport.parse_retry_after(value, NOW), 1e-9, value }
  end

  def test_parse_retry_after_invalid_is_nil
    [nil, "", "abc", "1.5", "Tue, 99 Nov 2026 09:00:00 GMT"].each do |value|
      assert_nil Transport.parse_retry_after(value, NOW), value.inspect
    end
  end

  def test_cancel_wait_returns_false_after_the_wait
    refute Transport::Cancel.new.wait(0.01)
  end

  def test_cancel_wait_ends_early_when_cancelled
    cancel = Transport::Cancel.new
    started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
    Thread.new do
      sleep 0.05
      cancel.cancel
    end

    assert cancel.wait(10)
    assert_operator Process.clock_gettime(Process::CLOCK_MONOTONIC) - started, :<, 2
    assert_predicate cancel, :cancelled?
  end

  def test_cancel_wait_returns_true_at_once_when_already_cancelled
    cancel = Transport::Cancel.new
    cancel.cancel
    cancel.cancel

    assert cancel.wait(10)
  end

  def test_request_sends_headers_and_body_and_returns_the_response
    with_server do |server|
      server.reply("/sdk/v1/events", FakeServer::Scripted.new(status: 202, headers: { "ETag" => '"e1"' },
                                                              body: "ok"))
      resp = Transport.request("POST", Transport.sdk_url(server.url, "events"),
                               headers: Transport.headers(KEY, body: true), body: '{"a":1}', timeout: 5)

      assert_equal 202, resp.status
      assert_equal '"e1"', resp.header("etag")
      assert_equal '"e1"', resp.header("ETag")
      assert_equal "ok", resp.body
      got = server.requests("/sdk/v1/events").first

      assert_equal "POST", got.verb
      assert_equal "Bearer #{KEY}", got.headers["authorization"]
      assert_equal "application/json", got.headers["content-type"]
      assert_equal '{"a":1}', got.body
      refute_includes got.url, KEY
    end
  end

  def test_request_keeps_the_query_string
    with_server do |server|
      server.reply("/sdk/v1/ruleset", FakeServer::Scripted.new(status: 200))
      Transport.request("GET", "#{server.url}/sdk/v1/ruleset?x=1", headers: {}, timeout: 5)

      assert_equal "/sdk/v1/ruleset?x=1", server.requests.first.url
    end
  end

  def test_request_status_feeds_status_error
    with_server do |server|
      server.reply("/r", FakeServer::Scripted.new(status: 429, headers: { "Retry-After" => "3" }))
      resp = Transport.request("GET", "#{server.url}/r", headers: {}, timeout: 5)

      assert_in_delta 3.0, Transport.status_error(resp.status, resp.headers, NOW).after
    end
  end

  def test_request_times_out
    with_server do |server|
      server.reply("/slow", lambda { |_|
        sleep 1
        FakeServer::Scripted.new(status: 200)
      })

      assert_raises(Net::ReadTimeout) do
        Transport.request("GET", "#{server.url}/slow", headers: {}, timeout: 0.1)
      end
    end
  end

  def test_connection_is_chosen_by_scheme
    http, path = Transport.connection("https://example.com/sdk/v1/stream?a=b", timeout: 3)

    assert_predicate http, :use_ssl?
    assert_equal 443, http.port
    assert_equal "/sdk/v1/stream?a=b", path
    assert_in_delta 3, http.open_timeout
    assert_in_delta 3, http.read_timeout
    refute_predicate Transport.connection("http://example.com", timeout: nil).first, :use_ssl?
    assert_equal "/", Transport.connection("http://example.com", timeout: nil).last
  end

  def test_connection_refuses_other_schemes
    assert_raises(ArgumentError) { Transport.connection("ftp://example.com/x", timeout: 1) }
    assert_raises(ArgumentError) { Transport.connection("http:///x", timeout: 1) }
  end

  private

  def with_server
    server = FakeServer.new
    yield server
  ensure
    server&.stop
  end
end
