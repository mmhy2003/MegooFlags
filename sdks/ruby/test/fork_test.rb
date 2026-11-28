# frozen_string_literal: true

require_relative "test_helper"
require_relative "support/fake_server"
require_relative "support/provider_rulesets"
require "json"
require "megooflags"

# A forked child (Puma, Unicorn, Passenger) restarts the provider's threads with empty event
# buffers.
class ForkTest < Minitest::Test
  include ThreadTestHelpers
  include ProviderRulesets

  SDK = OpenFeature::SDK

  KEY = "mf_server_fork-secret-value"
  STREAM = "/sdk/v1/stream"
  EVENTS = "/sdk/v1/events"

  def setup
    skip "Process.fork is not available" unless Process.respond_to?(:fork)

    @server = FakeServer.new
    @server.reply(STREAM, FakeServer::Stream.new)
    @server.reply(EVENTS, FakeServer::Scripted.new(status: 202))
    @logger = TestLogger.new
  end

  def teardown
    return unless @server

    SDK.shutdown
    @server.stop
  end

  # in_child forks, runs the block in the child and returns what it reported through a pipe. The
  # child ends with exit!, so it never runs the parent's at_exit hooks (Minitest's included).
  def in_child
    reader, writer = IO.pipe
    pid = fork do
      reader.close
      report = begin
        { "ok" => true }.merge(yield)
      rescue Exception => e # rubocop:disable Lint/RescueException
        { "ok" => false, "error" => "#{e.class}: #{e.message}\n#{e.backtrace.join("\n")}" }
      end
      writer.write(JSON.generate(report))
      writer.close
      exit!(report["ok"] ? 0 : 1)
    end
    writer.close
    data = reader.read
    reader.close
    _, status = Process.wait2(pid)
    report = JSON.parse(data)

    assert report["ok"], report["error"]
    assert_predicate status, :success?
    report
  end

  def sdk_threads = Thread.list.select { |t| t.alive? && t.name.to_s.start_with?("megooflags-") }.map(&:name).sort

  def exposure_keys
    @server.requests(EVENTS).flat_map { |r| JSON.parse(r.body)["exposures"].map { |e| e["context"]["key"] } }.sort
  end

  def start(domain, **)
    p = MegooFlags::Provider.new(sdk_key: KEY, base_url: @server.url, logger: @logger, **)
    t = Thread.new { SDK.set_provider_and_wait(p, domain: domain) }
    @server.next_stream.write("event: put\nid: e1\ndata: #{provider_ruleset}\n\n")
    t.join(5)
    [p, SDK.build_client(domain: domain)]
  end

  def ctx(key) = SDK::EvaluationContext.new(targeting_key: key)

  def test_a_forked_child_restarts_its_threads_with_empty_buffers
    p, client = start("fork", send_context: true)

    assert_equal "blue", client.fetch_string_value(flag_key: "string-flag", default_value: "x",
                                                   evaluation_context: ctx("parent"))
    assert_equal %w[megooflags-events megooflags-stream], sdk_threads

    report = in_child do
      wait_for { sdk_threads.size == 2 }
      threads = sdk_threads
      value = p.fetch_string_value(flag_key: "string-flag", default_value: "x", evaluation_context: ctx("child")).value
      p.shutdown
      { "threads" => threads, "value" => value, "after" => sdk_threads }
    end

    assert_equal %w[megooflags-events megooflags-stream], report["threads"]
    assert_equal "blue", report["value"]
    assert_empty report["after"]
    # The child sent only its own evaluation, not the one the parent had recorded.
    assert_equal ["child"], exposure_keys

    # The parent keeps working and sends its own evaluation on shutdown.
    assert_equal %w[megooflags-events megooflags-stream], sdk_threads
    assert_equal "blue", client.fetch_string_value(flag_key: "string-flag", default_value: "x",
                                                   evaluation_context: ctx("parent"))
    SDK.shutdown

    assert_equal %w[child parent], exposure_keys
    assert_empty sdk_threads
  end

  def test_a_shut_down_provider_stays_down_in_the_child
    p, = start("fork-down")
    p.shutdown

    report = in_child { { "threads" => sdk_threads } }

    assert_empty report["threads"]
    assert_equal 1, @server.requests(STREAM).size
  end
end
