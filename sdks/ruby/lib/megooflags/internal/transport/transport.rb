# frozen_string_literal: true

require "net/http"
require "time"
require "uri"
require_relative "../../version"

module MegooFlags
  module Internal
    # HTTP helpers shared by the ruleset download, the stream and the event sender: request
    # headers, status mapping, Retry-After parsing, a cancellable wait and a Net::HTTP request
    # helper.
    module Transport
      USER_AGENT = "megooflags-ruby/#{MegooFlags::VERSION}".freeze

      # FatalError reports that the server refused the SDK key (401, 403, or the stream's
      # revoked event). Retrying with the same key cannot succeed.
      class FatalError < StandardError
        attr_reader :status, :reason

        def initialize(status: nil, reason: nil)
          @status = status
          @reason = reason
          super("megooflags: SDK key refused (#{status || reason})")
        end
      end

      # RetryAfterError reports a 429. after is the server's Retry-After in seconds, or 0.
      class RetryAfterError < StandardError
        attr_reader :after

        def initialize(after)
          @after = after
          super("megooflags: rate limited, retry after #{after}s")
        end
      end

      # HttpError reports any other unexpected status.
      class HttpError < StandardError
        attr_reader :status

        def initialize(status)
          @status = status
          super("megooflags: HTTP #{status}")
        end
      end

      # Response is a whole response: the status, the headers (lower-case names) and the body
      # bytes.
      Response = Data.define(:status, :headers, :body) do
        # header returns the value of the header name, in any case, or nil.
        def header(name) = headers[name.downcase]
      end

      # Cancel is the stop signal of one worker thread: cancel ends wait at once. It cannot be
      # reset.
      class Cancel
        def initialize
          @lock = Mutex.new
          @cond = ConditionVariable.new
          @cancelled = false
        end

        def cancelled? = @lock.synchronize { @cancelled }

        # cancel sets the signal. Idempotent; any thread may call it.
        def cancel
          @lock.synchronize do
            @cancelled = true
            @cond.broadcast
          end
        end

        # wait waits up to seconds; true when cancelled (at once if it already was).
        def wait(seconds)
          deadline = Process.clock_gettime(Process::CLOCK_MONOTONIC) + seconds
          @lock.synchronize do
            until @cancelled
              left = deadline - Process.clock_gettime(Process::CLOCK_MONOTONIC)
              break if left <= 0

              @cond.wait(@lock, left)
            end
            @cancelled
          end
        end
      end

      SECONDS = /\A[+-]?[0-9]+\z/

      module_function

      # headers are the headers of an SDK request. The key travels in the Authorization header
      # only, never in the URL. body adds Content-Type; stream asks for text/event-stream
      # without caching.
      def headers(sdk_key, body: false, stream: false)
        h = {
          "Authorization" => "Bearer #{sdk_key}",
          "User-Agent" => USER_AGENT,
          "Accept" => stream ? "text/event-stream" : "application/json"
        }
        h["Cache-Control"] = "no-cache" if stream
        h["Content-Type"] = "application/json" if body
        h
      end

      # sdk_url is the base URL, without its trailing slashes, joined with an SDK endpoint
      # ("ruleset", "stream" or "events").
      def sdk_url(base_url, endpoint) = "#{base_url.sub(%r{/+\z}, "")}/sdk/v1/#{endpoint}"

      # status_error maps a response status to an error: nil for 2xx and 304. headers is a Hash
      # (any case) or anything answering [] like Net::HTTPResponse; now is a Time, for an
      # HTTP-date Retry-After.
      def status_error(status, headers, now)
        return nil if (200..299).cover?(status) || status == 304
        return FatalError.new(status: status) if [401, 403].include?(status)
        return RetryAfterError.new(parse_retry_after(header(headers, "Retry-After"), now) || 0.0) if status == 429

        HttpError.new(status)
      end

      # parse_retry_after reads a Retry-After value as delta-seconds or an HTTP-date, in seconds.
      # A negative number or a date in the past is 0; a missing or invalid value is nil.
      def parse_retry_after(value, now)
        v = value.to_s.strip
        return nil if v.empty?
        return [v.to_i, 0].max.to_f if SECONDS.match?(v)

        [Time.httpdate(v) - now, 0.0].max
      rescue ArgumentError
        nil
      end

      # connection returns an unstarted Net::HTTP for url (TLS by scheme) and the path with its
      # query to request. timeout is the open and read timeout in seconds (nil: none).
      def connection(url, timeout:)
        uri = URI(url)
        unless %w[http https].include?(uri.scheme) && !uri.host.to_s.empty?
          raise ArgumentError, "megooflags: the URL must be http or https with a host"
        end

        http = Net::HTTP.new(uri.host, uri.port)
        http.use_ssl = uri.scheme == "https"
        http.open_timeout = timeout
        http.read_timeout = timeout
        http.write_timeout = timeout
        [http, uri.request_uri]
      end

      # request sends a request, reads the whole response and closes the connection.
      def request(method, url, headers:, timeout:, body: nil)
        http, path = connection(url, timeout: timeout)
        resp = http.start { http.send_request(method, path, body, headers) }
        names = {}
        resp.each_header { |name, value| names[name] = value }
        Response.new(status: resp.code.to_i, headers: names.freeze, body: (resp.body || "").b)
      end

      def header(headers, name)
        return headers[name] unless headers.is_a?(Hash)

        headers.each { |k, v| return v if k.to_s.casecmp?(name) }
        nil
      end
      private_class_method :header
    end
  end
end
