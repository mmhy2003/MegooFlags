# frozen_string_literal: true

require_relative "../test_helper"
require_relative "../support/fake_server"
require "net/http"
require "stringio"
require "megooflags/internal/delivery/sse"

class SseTest < Minitest::Test
  Sse = MegooFlags::Internal::Delivery::Sse
  Event = Sse::Event

  Seen = Struct.new(:lines, :retries, :events)

  def read(*chunks)
    seen = Seen.new(0, [], [])
    Sse.read(chunks.map(&:b), on_line: -> { seen.lines += 1 }, on_retry: ->(ms) { seen.retries << ms },
                              on_event: ->(e) { seen.events << e })
    seen
  end

  def ev(name, data, id = "") = Event.new(name: name, data: data, id: id)

  CASES = {
    "named event with id" => ["event: put\nid: 42\ndata: {\"flags\":{}}\n\n", 4, [["put", '{"flags":{}}', "42"]]],
    "default name" => ["data: x\n\n", 2, [["message", "x", ""]]],
    "multi-line data" => ["data: a\ndata: b\ndata:c\n\n", 4, [["message", "a\nb\nc", ""]]],
    "CRLF endings" => ["event: put\r\nid: 7\r\ndata: x\r\n\r\n", 4, [%w[put x 7]]],
    "unknown event name passed through" => ["event: mystery\ndata: 1\n\n", 3, [["mystery", "1", ""]]],
    "unterminated last event discarded" => ["data: a\n\nevent: put\ndata: b\n", 4, [["message", "a", ""]]],
    "unterminated last line discarded" => ["data: a\n\ndata: b", 2, [["message", "a", ""]]],
    "state resets after dispatch" => ["event: put\nid: 1\ndata: a\n\ndata: b\n\n", 6,
                                      [["put", "a", "1"], ["message", "b", ""]]],
    "empty line without data dispatches nothing" => ["event: put\n\ndata: a\n\n", 4, [["message", "a", ""]]],
    "id with NUL ignored" => ["id: a\0b\ndata: x\n\n", 3, [["message", "x", ""]]],
    "field without colon has an empty value" => ["data\ndata\n\n", 3, [["message", "\n", ""]]],
    "only one leading space removed, unknown field ignored" => ["foo: bar\ndata:  two\n\n", 3,
                                                                [["message", " two", ""]]],
    "colon inside the value kept" => ["data: a: b\n\n", 2, [["message", "a: b", ""]]]
  }.freeze

  def test_cases
    CASES.each do |label, (text, lines, events)|
      seen = read(text)

      assert_equal events.map { |e| ev(*e) }, seen.events, label
      assert_equal lines, seen.lines, label
      assert_empty seen.retries, label
    end
  end

  def test_comments_call_on_line_only
    seen = read(": ping\n:\n\n")

    assert_equal 3, seen.lines
    assert_empty seen.events
  end

  def test_a_valid_retry_calls_on_retry_in_milliseconds
    assert_equal [3000], read("retry: 3000\n\n").retries
  end

  def test_an_invalid_retry_is_ignored
    ["retry: x\n", "retry: -1\n", "retry: 1.5\n", "retry:\n", "retry: ３\n", "retry: 1 \n"].each do |text|
      assert_empty read(text).retries, text
    end
  end

  def test_reads_a_1_mib_data_line
    big = "x" * (1 << 20)
    seen = read("event: put\ndata: ", *big.scan(/.{1,16384}/m), "\n\n")

    assert_equal 1, seen.events.size
    assert_equal big, seen.events.first.data
  end

  def test_decodes_a_multi_byte_character_split_across_two_chunks
    data = "data: é€😀\n\n".b
    at = data.index("\xE2".b) + 1
    seen = read(data[0, at], data[at..])

    assert_equal [ev("message", "é€😀")], seen.events
    assert_equal Encoding::UTF_8, seen.events.first.data.encoding
  end

  def test_invalid_utf8_is_replaced
    data = read("data: a\xFFb\n\n".b).events.first.data

    assert_predicate data, :valid_encoding?
    assert_equal "a\uFFFDb", data
  end

  def test_splits_lines_that_arrive_in_one_chunk_or_byte_by_byte
    text = "event: put\r\ndata: a\r\n\r\nevent: put\ndata: b\n\n"
    one = read(text)
    split = read(*text.b.chars)

    assert_equal one, split
    assert_equal %w[a b], one.events.map(&:data)
  end

  def test_a_cr_split_from_its_lf_is_one_line_ending
    assert_equal [ev("message", "a")], read("data: a\r", "\n\r", "\n").events
  end

  def test_reads_from_an_io
    seen = Seen.new(0, [], [])
    Sse.read(StringIO.new("event: put\ndata: x\n\n".b), on_line: -> { seen.lines += 1 }, on_retry: ->(_) {},
                                                        on_event: ->(e) { seen.events << e })

    assert_equal [ev("put", "x")], seen.events
    assert_equal 3, seen.lines
  end

  def test_an_error_raised_by_on_event_stops_reading
    names = []
    stop = Class.new(StandardError)

    assert_raises(stop) do
      Sse.read(["event: revoked\ndata: {}\n\nevent: put\ndata: x\n\n"], on_line: -> {}, on_retry: ->(_) {},
                                                                        on_event: lambda { |e|
                                                                          names << e.name
                                                                          raise stop
                                                                        })
    end
    assert_equal ["revoked"], names
  end

  def test_a_read_error_is_passed_through
    chunks = Enumerator.new do |y|
      y << "data: a\n\n"
      raise Errno::ECONNRESET
    end
    events = []

    assert_raises(Errno::ECONNRESET) do
      Sse.read(chunks, on_line: -> {}, on_retry: ->(_) {}, on_event: ->(e) { events << e })
    end
    assert_equal [ev("message", "a")], events
  end

  def test_reads_a_chunked_stream_from_the_fake_server
    server = FakeServer.new
    server.reply("/sdk/v1/stream", FakeServer::Stream.new)
    events = Queue.new
    reader = Thread.new { read_stream("#{server.url}/sdk/v1/stream", events) }
    conn = server.next_stream
    data = "event: put\nid: 9\ndata: é€\n\n".b
    conn.write(data[0, data.index("\xE2".b) + 1])
    conn.write(data[(data.index("\xE2".b) + 1)..])

    assert_equal ev("put", "é€", "9"), events.pop(timeout: 5)
    conn.finish
    reader.join(5)
  ensure
    server&.stop
  end

  private

  def read_stream(url, events)
    uri = URI(url)
    Net::HTTP.start(uri.host, uri.port) do |http|
      http.request(Net::HTTP::Get.new(uri)) do |resp|
        chunks = Enumerator.new { |y| resp.read_body { |chunk| y << chunk } }
        Sse.read(chunks, on_line: -> {}, on_retry: ->(_) {}, on_event: ->(e) { events << e })
      end
    end
  end
end
