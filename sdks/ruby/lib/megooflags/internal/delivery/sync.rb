# frozen_string_literal: true

require "monitor"
require_relative "../engine/parser"
require_relative "../log"
require_relative "../transport/backoff"
require_relative "../transport/transport"
require_relative "cache"
require_relative "poller"
require_relative "store"
require_relative "streamer"

module MegooFlags
  module Internal
    module Delivery
      # Tuning holds shorter timings; only tests set it. poll_interval replaces the poll interval
      # without any clamp (nil keeps the configured one); poll_after_failures is the stream
      # failures in a row before polling.
      Tuning = Data.define(:dead_timeout, :stable_after, :retry_base, :retry_max, :poll_after_failures,
                           :poll_interval) do
        def initialize(dead_timeout: 45.0, stable_after: 60.0, retry_base: 3.0, retry_max: 60.0,
                       poll_after_failures: 2, poll_interval: nil)
          super
        end
      end

      # Sync keeps the Store current: the stream, with polling while the stream keeps failing (or
      # polling only, with disable_stream).
      #
      # It runs the stream and the poll loop on threads. Callbacks run on those threads (or on
      # the caller's, for apply) one at a time. on_change receives the old snapshot (nil at
      # first) and the new one; on_status receives :ready (the stream is connected, or with
      # disable_stream the last poll succeeded), :stale (the stream or the poll failed; the last
      # ruleset is still served) or :fatal (the key was refused and Sync has stopped), with the
      # error. No callback runs after stop has returned or after :fatal.
      class Sync # rubocop:disable Metrics/ClassLength
        # How long stop waits for each worker thread, in seconds.
        JOIN_TIMEOUT = 2.0
        NOOP = proc {}
        LIVE = %w[stream poll].freeze

        def initialize(base_url:, sdk_key:, store:, cache: nil, logger: Log::Stderr.new, disable_stream: false, # rubocop:disable Metrics/ParameterLists
                       poll_interval: 30.0, on_change: NOOP, on_status: NOOP, tuning: Tuning.new)
          @logger = logger
          @disable_stream = disable_stream
          @poll_interval = tuning.poll_interval || poll_interval
          @poll_after_failures = tuning.poll_after_failures
          @store = store
          @cache = cache
          @on_change = on_change
          @on_status = on_status

          # @lock serialises apply, the status callbacks and the worker bookkeeping. It is
          # reentrant, so a callback may call apply or stop.
          @lock = Monitor.new
          @logged = {}
          @started = false
          @stopped = false
          @fatal = false
          @stream_cancel = Transport::Cancel.new
          @poll_cancel = nil
          @threads = []
          # The earliest next poll (monotonic seconds); kept across restarts of the poll loop.
          @next_poll = -Float::INFINITY

          @poller = Poller.new(url: Transport.sdk_url(base_url, "ruleset"), sdk_key: sdk_key, etag: method(:etag),
                               on_ruleset: ->(raw, etag) { apply(raw, etag, "poll").nil? })
          @streamer = Streamer.new(
            url: Transport.sdk_url(base_url, "stream"), sdk_key: sdk_key, logger: logger,
            last_event_id: method(:etag), on_put: ->(raw, id) { apply(raw, id, "stream").nil? },
            on_connected: method(:connected), on_disconnected: method(:disconnected),
            dead_timeout: tuning.dead_timeout, stable_after: tuning.stable_after,
            backoff: Transport::Backoff.new(tuning.retry_base, tuning.retry_max)
          )
        end

        # apply parses raw and installs it, returning nil, or the error for an invalid ruleset,
        # which is logged while the store keeps the previous one. A ruleset from the stream or a
        # poll is also saved to the cache (a save error is only logged), and is ignored once Sync
        # has stopped.
        def apply(raw, etag, source)
          live = LIVE.include?(source)
          @lock.synchronize do
            return nil if live && halted?

            begin
              ruleset = Engine::Parser.parse(raw)
            rescue Engine::ParseError => e
              @logger.warn("megooflags: invalid ruleset from #{source}; keeping the previous one: #{e.message}")
              return e
            end
            new = Snapshot.new(ruleset: ruleset, etag: etag, source: source)
            old = @store.swap(new)
            save(raw, etag) if live
            log_warnings(ruleset)
            call(@on_change, old, new)
            nil
          end
        end

        # start runs the stream, or only the poll loop with disable_stream, and returns at once.
        # Only the first call does anything, and none after stop.
        def start
          @lock.synchronize do
            return if @started || halted?

            @started = true
            if @disable_stream
              start_polling
            else
              spawn("megooflags-stream") { stream_loop }
            end
          end
          nil
        end

        # stop ends the stream and the poll loop, closing a connection in flight, and waits
        # briefly for their threads. Idempotent and safe before start.
        def stop
          threads = @lock.synchronize do
            @stopped = true
            stop_polling
            @threads.dup
          end
          @stream_cancel.cancel
          threads.each { |t| t.join(JOIN_TIMEOUT) unless t == Thread.current }
          nil
        end

        private

        def halted? = @fatal || @stopped

        def etag = @store.load&.etag.to_s

        def save(raw, etag)
          @cache&.save(raw, etag)
        rescue CacheError, SystemCallError => e
          @logger.warn("megooflags: could not save the ruleset cache: #{e.message}")
        end

        def log_warnings(ruleset)
          ruleset.warnings.each do |w|
            next if @logged.key?(w)

            @logged[w] = true
            @logger.warn("megooflags: ruleset warning: #{w}")
          end
        end

        # spawn starts a worker thread; the caller holds @lock.
        def spawn(name)
          @threads.select!(&:alive?)
          @threads << Thread.new do
            Thread.current.report_on_exception = false
            Thread.current.name = name
            yield
          end
        end

        def call(callback, *)
          callback.call(*)
        rescue StandardError => e
          @logger.error("megooflags: sync callback failed: #{e.class}: #{e.message}")
        end

        # status reports status unless Sync is stopped or has failed.
        def status(status, err = nil)
          @lock.synchronize { call(@on_status, status, err) unless halted? }
        end

        # fatal! reports a refused key once and stops everything.
        def fatal!(err)
          @lock.synchronize do
            return if halted?

            @fatal = true
            stop_polling
            @stream_cancel.cancel
            call(@on_status, :fatal, err)
          end
        end

        def stream_loop
          fatal = @streamer.run(@stream_cancel)
          fatal!(fatal) if fatal
        rescue StandardError => e
          @logger.error("megooflags: stream loop failed: #{e.class}: #{e.message}")
        end

        def connected
          @lock.synchronize { stop_polling }
          status(:ready)
        end

        def disconnected(err, failures)
          status(:stale, err)
          @lock.synchronize { start_polling } if failures >= @poll_after_failures
        end

        # start_polling starts the poll loop unless it runs; the caller holds @lock.
        def start_polling
          return if @poll_cancel || halted?

          cancel = Transport::Cancel.new
          @poll_cancel = cancel
          spawn("megooflags-poll") { poll_loop(cancel) }
        end

        # stop_polling ends the poll loop and its poll in flight; the caller holds @lock.
        def stop_polling
          @poll_cancel&.cancel
          @poll_cancel = nil
        end

        def poll_loop(cancel)
          poll_once(cancel) until cancel.cancelled?
        rescue StandardError => e
          @logger.error("megooflags: poll loop failed: #{e.class}: #{e.message}")
        end

        def poll_once(cancel)
          now = Process.clock_gettime(Process::CLOCK_MONOTONIC)
          wait = @lock.synchronize do
            left = @next_poll - now
            @next_poll = now + @poll_interval if left <= 0
            left
          end
          return cancel.wait(wait) if wait.positive?

          err = begin
            @poller.poll(cancel)
            nil
          rescue StandardError => e
            e
          end
          return if cancel.cancelled?

          handle_poll(err, now, cancel)
        end

        def handle_poll(err, started, cancel)
          if err.is_a?(Transport::FatalError)
            fatal!(err)
            return cancel.cancel
          end
          if err.is_a?(Transport::RetryAfterError)
            @lock.synchronize { @next_poll = started + [err.after, @poll_interval].max }
          end
          @logger.debug("megooflags: poll failed: #{err.message}") if err
          return unless @disable_stream

          err ? status(:stale, err) : status(:ready)
        end
      end
    end
  end
end
