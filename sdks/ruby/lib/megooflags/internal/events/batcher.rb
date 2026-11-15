# frozen_string_literal: true

require "json"
require "net/http"
require_relative "../log"
require_relative "../transport/backoff"
require_relative "../transport/transport"
require_relative "aggregator"

module MegooFlags
  module Internal
    module Events
      # Batcher posts evaluation counts and exposures to POST /sdk/v1/events from one thread,
      # which alone owns the Aggregator.
      #
      # record never blocks: it queues the evaluation, and a full queue, or a Batcher that was
      # stopped or shut down, drops it and counts the drop. The thread flushes every
      # flush_interval, when either list reaches max_items, and on shutdown. clock (seconds,
      # monotonic by default) times the retries; only tests replace it.
      #
      # on_fatal runs once, on the batcher's thread, when the server refuses the key (401 or
      # 403); sending has stopped for good by then. It may call stop and shutdown.
      class Batcher # rubocop:disable Metrics/ClassLength
        # The largest events body the server accepts.
        MAX_BODY_BYTES = 1_000_000
        # The open, write and read timeout of one POST, in seconds.
        SEND_TIMEOUT = 10.0
        # How long stop waits for the thread, in seconds.
        JOIN_TIMEOUT = 2.0
        # Put on the queue to wake the thread for stop or shutdown.
        WAKE = Object.new.freeze
        MONOTONIC = -> { Process.clock_gettime(Process::CLOCK_MONOTONIC) }

        def initialize(url:, sdk_key:, logger: Log::Stderr.new, flush_interval: 30.0, queue_size: 10_000, # rubocop:disable Metrics/ParameterLists
                       dedup_size: 10_000, max_pending: 10_000, max_items: 1000, send_context: false,
                       on_fatal: ->(_err) {}, clock: MONOTONIC, retry_base: 2.0, retry_max: 60.0)
          @url = url
          @sdk_key = sdk_key
          @logger = logger
          @flush_interval = flush_interval
          @max_items = max_items
          @on_fatal = on_fatal
          @clock = clock
          @queue = SizedQueue.new(queue_size)
          @agg = Aggregator.new(dedup_size: dedup_size, max_pending: max_pending, send_context: send_context)
          @backoff = Transport::Backoff.new(retry_base, retry_max)
          # Ends a POST in flight on stop or past the shutdown deadline.
          @cancel = Transport::Cancel.new

          @lock = Mutex.new
          @dropped = 0
          @closed = false # record drops from now on
          @stopping = false
          @shutdown_at = nil # monotonic deadline, once requested
          @thread = nil
          @result = true # what shutdown returns once the thread has ended

          # Owned by the thread.
          @next_attempt = -Float::INFINITY
          @fatal = false
        end

        # dropped counts the evaluations and exposures that were dropped.
        def dropped = @lock.synchronize { @dropped } + @agg.dropped

        # start runs the thread. Only the first call does anything, and none after stop or
        # shutdown.
        def start
          @lock.synchronize do
            return if @thread || @closed

            @thread = Thread.new { run }
            @thread.name = "megooflags-events"
            @thread.report_on_exception = false
          end
          nil
        end

        # record queues evaluation without blocking.
        def record(evaluation)
          unless @closed
            begin
              @queue.push(evaluation, true)
              return
            rescue ThreadError
              nil # full
            end
          end
          @lock.synchronize { @dropped += 1 }
          nil
        end

        # shutdown stops accepting, then sends what is buffered until it is empty or deadline
        # seconds have passed, waiting out a retry delay when it fits. true when everything
        # buffered was delivered (or refused by the server); false after the deadline, a stop or a
        # refused key. Idempotent.
        def shutdown(deadline = 5.0)
          thread, at = @lock.synchronize do
            @closed = true
            @shutdown_at = MONOTONIC.call + deadline if @thread && @shutdown_at.nil?
            [@thread, @shutdown_at]
          end
          return @result if thread.nil?

          wake
          return false if thread == Thread.current
          return @result if thread.join([at - MONOTONIC.call, 0].max)

          @cancel.cancel
          thread.join(JOIN_TIMEOUT)
          false
        end

        # stop stops without sending what is buffered, ending a POST in flight, and waits briefly
        # for the thread. Idempotent and safe before start.
        def stop
          thread = @lock.synchronize do
            @closed = true
            @stopping = true
            @result = false if @thread.nil?
            @thread
          end
          @cancel.cancel
          wake
          thread.join(JOIN_TIMEOUT) if thread && thread != Thread.current
          nil
        end

        private

        # wake needs no room in a full queue: the thread is busy with it and checks the flags.
        def wake
          @queue.push(WAKE, true)
        rescue ThreadError
          nil
        end

        def halted? = @stopping || @fatal

        def run
          loop_until_done
        rescue StandardError => e
          @result = false
          @logger.error("megooflags: the events thread failed: #{e.class}: #{e.message}")
        end

        def loop_until_done
          next_flush = MONOTONIC.call + @flush_interval
          until halted?
            return drain unless @shutdown_at.nil?

            item = @queue.pop(timeout: [next_flush - MONOTONIC.call, 0].max)
            if item.is_a?(Evaluation)
              @agg.add(item)
              flush if @agg.pending.max >= @max_items
            end
            next if MONOTONIC.call < next_flush

            flush
            next_flush = MONOTONIC.call + @flush_interval
          end
          @result = false
        end

        # drain empties the queue, then sends everything until the shutdown deadline, waiting
        # out a retry delay when it fits.
        def drain
          until @queue.empty?
            item = @queue.pop(true)
            @agg.add(item) if item.is_a?(Evaluation)
          end
          until halted?
            return if @agg.pending == [0, 0]

            left = @shutdown_at - MONOTONIC.call
            wait = @next_attempt - @clock.call
            if left <= 0 || wait > left || (wait.positive? && @cancel.wait(wait))
              @result = false
              return
            end
            flush
          end
          @result = false
        end

        # flush sends what is pending in batches of at most max_items per list. It does nothing
        # before the next attempt and stops at the first failure.
        def flush
          return if @clock.call < @next_attempt

          until halted? || @agg.pending == [0, 0]
            unsent = deliver(@agg.take(@max_items))
            next if unsent.nil?

            @agg.restore(unsent)
            return
          end
        end

        # deliver posts batch, splitting it when it is too large. It returns the part that was
        # not delivered and must be retried, or nil.
        def deliver(batch)
          begin
            body = batch.body
          rescue JSON::GeneratorError => e
            @logger.error("megooflags: cannot encode events; dropping the batch: #{e.message}")
            return nil
          end
          return split(batch, "the body is over 1 MB") if body.bytesize > MAX_BODY_BYTES

          begin
            status, headers = post(body)
          rescue Transport::CancelledError
            return batch
          rescue StandardError => e
            return batch if @cancel.cancelled?

            retry_in(@backoff.next)
            @logger.debug("megooflags: sending events failed; will retry: #{e.class}: #{e.message}")
            return batch
          end
          answered(batch, Transport.status_error(status, headers, Time.now))
        end

        # answered handles the server's answer to batch (err is nil for a 2xx) and returns the
        # part to retry, or nil.
        def answered(batch, err)
          case err
          when nil
            @backoff.reset
            nil
          when Transport::FatalError then fatal(err)
          when Transport::RetryAfterError
            retry_in([err.after, @backoff.next].max)
            @logger.debug("megooflags: events rate limited; retry after #{err.after}s")
            batch
          else
            return split(batch, "the server refused the body as too large") if err.status == 413

            if err.status >= 500
              retry_in(@backoff.next)
              @logger.debug("megooflags: sending events failed; will retry: #{err.message}")
              return batch
            end
            @logger.warn("megooflags: the server refused an events batch; dropping it (#{err.message}, " \
                         "#{batch.exposures.size} exposures, #{batch.counts.size} counts)")
            nil
          end
        end

        def fatal(err)
          @fatal = true
          @lock.synchronize do
            @closed = true
            @result = false
          end
          begin
            @on_fatal.call(err)
          rescue StandardError => e
            @logger.error("megooflags: events on_fatal callback failed: #{e.class}: #{e.message}")
          end
          nil
        end

        # split sends batch as two halves, exposures first. A single item cannot be split and is
        # dropped. When the first half must be retried, the second is kept unsent.
        def split(batch, why)
          if batch.size <= 1
            @logger.warn("megooflags: an events item is too large; dropping it (#{why})")
            return nil
          end
          half = batch.size / 2
          ne = [half, batch.exposures.size].min
          nc = half - ne
          first = Batch.new(batch.exposures[0, ne], batch.counts[0, nc])
          second = Batch.new(batch.exposures[ne..], batch.counts[nc..])
          unsent = deliver(first)
          return nil if halted?
          return Batch.new(unsent.exposures + second.exposures, unsent.counts + second.counts) if unsent

          deliver(second)
        end

        # post sends body and returns the status and the response. cancel closes the connection
        # at once; once it is set, any error becomes CancelledError.
        def post(body)
          http, path = Transport.connection(@url, timeout: SEND_TIMEOUT)
          @cancel.guard(-> { http.finish if http.started? }) do
            http.start do
              raise Transport::CancelledError if @cancel.cancelled?

              resp = http.request(Net::HTTP::Post.new(path, Transport.headers(@sdk_key, body: true)), body)
              [resp.code.to_i, resp]
            end
          end
        rescue StandardError
          raise Transport::CancelledError if @cancel.cancelled?

          raise
        end

        def retry_in(seconds)
          @next_attempt = @clock.call + seconds
        end
      end
    end
  end
end
