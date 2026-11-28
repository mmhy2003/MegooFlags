# frozen_string_literal: true

require "socket"

# A scripted HTTP/1.1 server on 127.0.0.1 for the transport, delivery, events and provider
# tests. Each connection gets its own thread and one response, then is closed.
class FakeServer
  # A canned response: status, headers and body.
  Scripted = Data.define(:status, :headers, :body) do
    def initialize(status:, headers: {}, body: "")
      super
    end
  end

  # A response held open as a chunked text/event-stream; the test feeds it through the
  # SseConnection that next_stream returns.
  Stream = Data.define(:status, :headers) do
    def initialize(status: 200, headers: {})
      super
    end
  end

  # A request the server received, with its whole body. Header names are lower case; at is
  # Process.clock_gettime(Process::CLOCK_MONOTONIC) on arrival.
  Received = Data.define(:verb, :path, :url, :headers, :body, :at)

  # An open event-stream response, written from the test through a queue.
  class SseConnection
    attr_reader :request

    def initialize(request)
      @request = request
      @queue = Queue.new
      @closed = Queue.new
    end

    # write sends data as one chunk.
    def write(data) = @queue.push(data.b)

    # finish ends the chunked response and closes the connection.
    def finish = @queue.push(:finish)

    # drop closes the connection without ending the response.
    def drop = @queue.push(:drop)

    # closed? waits up to timeout seconds for the connection to close; true when it did.
    def closed?(timeout = 5) = !@closed.pop(timeout: timeout).nil?

    def next_item = @queue.pop

    def closed! = @closed.push(true)
  end

  attr_reader :port

  def initialize
    @server = TCPServer.new("127.0.0.1", 0)
    @port = @server.addr[1]
    @lock = Mutex.new
    @stream_ready = ConditionVariable.new
    @routes = {}
    @requests = []
    @streams = []
    @taken = 0
    @sockets = []
    @threads = []
    @accept = Thread.new { accept_loop }
    @accept.report_on_exception = false
  end

  # url is the base URL, for example http://127.0.0.1:41234.
  def url = "http://127.0.0.1:#{@port}"

  # reply adds replies to the queue of path. A reply is a Scripted, a Stream or a proc taking
  # the Received request and returning a Scripted (it runs on the connection's thread). The
  # last reply of a queue repeats; a path without replies gets 404.
  def reply(path, *replies)
    @lock.synchronize { (@routes[path] ||= []).concat(replies) }
  end

  # requests lists every request received, or those for path.
  def requests(path = nil)
    @lock.synchronize { path ? @requests.select { |r| r.path == path } : @requests.dup }
  end

  # next_stream returns the first event-stream connection not yet handed out, waiting for it up
  # to timeout seconds.
  def next_stream(timeout = 5)
    deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + timeout
    @lock.synchronize do
      while @streams.size <= @taken
        left = deadline - Process.clock_gettime(Process::CLOCK_MONOTONIC)
        raise "no stream connection" if left <= 0

        @stream_ready.wait(@lock, left)
      end
      @taken += 1
      @streams[@taken - 1]
    end
  end

  # stop drops every open stream, closes the listener and every connection, and waits for the
  # threads. Idempotent.
  def stop
    streams, sockets, threads = @lock.synchronize { [@streams.dup, @sockets.dup, @threads.dup] }
    streams.each(&:drop)
    @server.close unless @server.closed?
    sockets.each { |s| s.close unless s.closed? }
    ([@accept] + threads).each { |t| t.join(5) || t.kill }
  end

  private

  def accept_loop
    loop do
      sock = @server.accept
      thread = Thread.new { serve(sock) }
      thread.report_on_exception = false
      @lock.synchronize do
        @sockets << sock
        @threads << thread
      end
    end
  rescue IOError, SystemCallError
    nil
  end

  def serve(sock)
    req = read_request(sock)
    return unless req

    @lock.synchronize { @requests << req }
    case (reply = next_reply(req.path))
    when nil then send_scripted(sock, Scripted.new(status: 404))
    when Stream then stream(sock, req, reply)
    when Scripted then send_scripted(sock, reply)
    else send_scripted(sock, reply.call(req))
    end
  rescue IOError, SystemCallError
    nil
  ensure
    sock.close unless sock.closed?
  end

  def read_request(sock)
    line = sock.gets("\r\n")
    return nil unless line

    at = Process.clock_gettime(Process::CLOCK_MONOTONIC)
    method, url, = line.split(" ", 3)
    headers = {}
    while (h = sock.gets("\r\n")) && h != "\r\n"
      name, value = h.split(":", 2)
      headers[name.strip.downcase] = value.to_s.strip
    end
    length = headers.fetch("content-length", "0").to_i
    body = length.positive? ? sock.read(length) : +""
    Received.new(verb: method, path: url.split("?", 2).first, url: url, headers: headers,
                 body: body.b, at: at)
  end

  def next_reply(path)
    @lock.synchronize do
      replies = @routes[path]
      next nil if replies.nil? || replies.empty?

      replies.size > 1 ? replies.shift : replies.first
    end
  end

  def send_scripted(sock, reply)
    body = reply.body.b
    sock.write(status_line(reply.status), header_lines(reply.headers),
               "Content-Length: #{body.bytesize}\r\nConnection: close\r\n\r\n", body)
  end

  def stream(sock, req, reply)
    conn = SseConnection.new(req)
    sock.write(status_line(reply.status), "Content-Type: text/event-stream\r\nCache-Control: no-cache\r\n",
               header_lines(reply.headers), "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
    @lock.synchronize do
      @streams << conn
      @stream_ready.broadcast
    end
    pump(sock, conn)
  ensure
    conn&.closed!
  end

  def pump(sock, conn)
    loop do
      item = conn.next_item
      break if item == :drop

      if item == :finish
        sock.write("0\r\n\r\n")
        break
      end
      sock.write(item.bytesize.to_s(16), "\r\n", item, "\r\n")
    end
  end

  def status_line(status) = "HTTP/1.1 #{status} Status\r\n"

  def header_lines(headers) = headers.map { |k, v| "#{k}: #{v}\r\n" }.join
end
