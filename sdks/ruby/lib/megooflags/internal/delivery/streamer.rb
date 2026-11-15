# frozen_string_literal: true

require_relative "../log"
require_relative "../transport/backoff"
require_relative "../transport/transport"
require_relative "sse"

module MegooFlags
  module Internal
    module Delivery
      # DeadConnectionError: the stream sent nothing for the dead timeout.
      class DeadConnectionError < StandardError
        def initialize(seconds)
          super("megooflags: stream sent nothing for #{seconds}s (dead connection)")
        end
      end

      # StreamEndedError: the server closed the stream.
      class StreamEndedError < StandardError
        def initialize(message = "megooflags: stream ended") = super
      end

      # Streamer follows GET /sdk/v1/stream on the calling thread (see run), reconnecting with
      # backoff until it is cancelled or the server refuses the key.
      #
      # on_put receives each put event's data and id and returns false for an invalid ruleset;
      # on_connected runs on each 200; on_disconnected receives the error and the failures in a
      # row before each wait. The stream's read timeout is dead_timeout (45 s), so a silent
      # connection fails with Net::ReadTimeout; any bytes received, pings included, restart it.
      # A connection that lasted stable_after (60 s) resets the backoff and the failure count.
      # The backoff is 3 s to 60 s by default; the server's retry field replaces its base.
      class Streamer
        NOOP = proc {}

        def initialize(url:, sdk_key:, logger: Log::Stderr.new, last_event_id: -> { "" }, # rubocop:disable Metrics/ParameterLists
                       on_put: ->(_data, _id) { true }, on_connected: NOOP, on_disconnected: NOOP,
                       dead_timeout: 45.0, stable_after: 60.0, backoff: Transport::Backoff.new(3.0, 60.0))
          @url = url
          @sdk_key = sdk_key
          @logger = logger
          @last_event_id = last_event_id
          @on_put = on_put
          @on_connected = on_connected
          @on_disconnected = on_disconnected
          @dead_timeout = dead_timeout
          @stable_after = stable_after
          @backoff = backoff
        end

        # run streams until cancel is set, returning nil, or until the server refuses the key,
        # returning the FatalError. It raises nothing: other errors reconnect.
        def run(cancel)
          failures = 0
          loop do
            connected_at, err = connect(cancel)
            return nil if cancel.cancelled?
            return err if err.is_a?(Transport::FatalError)

            if connected_at && monotonic - connected_at >= @stable_after
              @backoff.reset
              failures = 0
            end
            failures += 1
            wait = @backoff.next
            wait = [err.after, wait].max if err.is_a?(Transport::RetryAfterError)
            @logger.debug("megooflags: stream disconnected (#{err.message}), failures #{failures}, " \
                          "retry in #{format("%.3f", wait)}s")
            call(@on_disconnected, err, failures)
            return nil if cancel.wait(wait)
          end
        end

        private

        def monotonic = Process.clock_gettime(Process::CLOCK_MONOTONIC)

        # connect opens one stream and reads it until it ends. It returns the monotonic time of
        # the 200 (nil when the server did not answer 200) and the error that ended it.
        def connect(cancel)
          connected_at = nil
          headers = Transport.headers(@sdk_key, stream: true)
          id = @last_event_id.call.to_s
          headers["Last-Event-ID"] = id unless id.empty?
          Transport.get(@url, headers: headers, timeout: @dead_timeout, cancel: cancel) do |resp|
            status = resp.code.to_i
            bad = Transport.status_error(status, resp, Time.now)
            bad ||= Transport::HttpError.new(status) unless status == 200
            next [nil, bad] if bad

            connected_at = monotonic
            @on_connected.call
            read(resp)
            [connected_at, StreamEndedError.new]
          end
        rescue Net::ReadTimeout
          [connected_at, DeadConnectionError.new(@dead_timeout)]
        rescue StandardError => e
          [connected_at, e]
        end

        def read(resp)
          reader = Sse::Reader.new(on_line: NOOP, on_retry: ->(ms) { @backoff.base = ms / 1000.0 },
                                   on_event: method(:event))
          resp.read_body { |chunk| reader.feed(chunk) }
        end

        def event(event)
          case event.name
          when "put"
            unless @on_put.call(event.data, event.id)
              @logger.warn("megooflags: stream sent an invalid ruleset (event id #{event.id.inspect}); " \
                           "keeping the previous one")
            end
          when "revoked" then raise Transport::FatalError.new(reason: "revoked")
          end
        end

        def call(callback, *)
          callback.call(*)
        rescue StandardError => e
          @logger.error("megooflags: stream callback failed: #{e.class}: #{e.message}")
        end
      end
    end
  end
end
