# frozen_string_literal: true

require "json"
require "time"

module MegooFlags
  module Internal
    # Hour-bucketed evaluation counts and exposures deduplicated per context key, flag, variant and
    # UTC hour, and the background sender of POST /sdk/v1/events.
    module Events
      # The largest count the server accepts in one count item.
      MAX_COUNT = 1_000_000_000

      HOUR_MS = 3_600_000

      # Evaluation is one flag evaluation as recorded by the provider. served is false when the
      # default was returned (error, not ready); context_key is "" when the context key is not a
      # string; context is only sent with send_context; at is Unix time in milliseconds.
      Evaluation = Data.define(:flag_key, :variant, :served, :reason, :context_key, :at, :context) do
        def initialize(flag_key:, variant:, served:, reason:, context_key:, at:, context: nil)
          super
        end
      end

      # Batch is the body of POST /sdk/v1/events: exposures and counts are Hashes with string
      # keys, as sent.
      Batch = Data.define(:exposures, :counts) do
        def initialize(exposures: [], counts: [])
          super
        end

        def size = exposures.size + counts.size

        # body is the JSON body, UTF-8 bytes. Raises JSON::GeneratorError for a context that is
        # not JSON (NaN, invalid UTF-8).
        def body = JSON.generate({ "exposures" => exposures, "counts" => counts }).b
      end

      # Lru is a fixed-size set that forgets the least recently seen key. A Hash keeps insertion
      # order, so its first key is the oldest.
      class Lru
        def initialize(capacity)
          @cap = [capacity, 1].max
          @items = {}
        end

        def size = @items.size

        # seen? tells whether key is present and marks it as the most recent. A new key is
        # inserted, evicting the oldest one above the capacity.
        def seen?(key)
          if @items.key?(key)
            @items.delete(key)
            @items[key] = true
            return true
          end
          @items[key] = true
          @items.shift if @items.size > @cap
          false
        end
      end

      # Aggregator buffers exposures and counts. An exposure beyond max_pending is dropped and
      # counted in dropped; counts are never dropped. It is not thread-safe: the batcher's thread
      # alone owns it.
      class Aggregator
        attr_reader :dropped

        def initialize(dedup_size: 10_000, max_pending: 10_000, send_context: false)
          @dedup = Lru.new(dedup_size)
          @max_pending = max_pending
          @send_context = send_context
          # [flag, variant or nil when not served, Unix hour] => count, in first-seen order.
          @counts = {}
          @exposures = []
          @dropped = 0
        end

        # add counts evaluation and, the first time a served variant is seen for its context key,
        # flag and hour, queues an exposure.
        def add(evaluation)
          flag = evaluation.flag_key
          variant = evaluation.variant
          hour = evaluation.at.div(HOUR_MS)
          key = [flag, evaluation.served ? variant : nil, hour]
          @counts[key] = @counts.fetch(key, 0) + 1

          return if !evaluation.served || @dedup.seen?([evaluation.context_key, flag, variant, hour])

          if @exposures.size >= @max_pending
            @dropped += 1
            return
          end
          e = { "flag_key" => flag, "variant" => variant, "reason" => evaluation.reason,
                "timestamp" => Events.timestamp(evaluation.at) }
          e["context"] = evaluation.context if @send_context && !evaluation.context.nil?
          @exposures << e
          nil
        end

        # pending is [pending exposures, pending count buckets].
        def pending = [@exposures.size, @counts.size]

        # take removes and returns up to limit exposures (oldest first) and up to limit count
        # items (in the order the buckets were first seen). A bucket above MAX_COUNT becomes
        # several items; what does not fit stays for the next batch.
        def take(limit)
          exposures = @exposures.shift(limit)
          counts = []
          @counts.first(limit).each do |key, left|
            flag, variant, hour = key
            stamp = Events.hour(hour)
            while left.positive? && counts.size < limit
              n = [left, MAX_COUNT].min
              left -= n
              counts << { "flag_key" => flag, "variant" => variant, "hour" => stamp, "count" => n }
            end
            if left.positive?
              @counts[key] = left
              break
            end
            @counts.delete(key)
          end
          Batch.new(exposures, counts)
        end

        # restore puts an unsent batch back in front of what is pending, so a retry sends it
        # first. Its counts add to any newer count for the same bucket.
        def restore(batch)
          @exposures.unshift(*batch.exposures)
          counts = {}
          batch.counts.each do |c|
            key = [c["flag_key"], c["variant"], Events.parse_hour(c["hour"])]
            counts[key] = counts.fetch(key, 0) + c["count"]
          end
          @counts.each { |key, n| counts[key] = counts.fetch(key, 0) + n }
          @counts = counts
          nil
        end
      end

      module_function

      # timestamp formats Unix milliseconds as 2026-11-10T09:15:02.000Z.
      def timestamp(at_ms)
        seconds, ms = at_ms.divmod(1000)
        Time.at(seconds, ms, :millisecond, in: "UTC").strftime("%Y-%m-%dT%H:%M:%S.%LZ")
      end

      # hour formats a Unix hour as 2026-11-10T09:00:00Z.
      def hour(hour) = Time.at(hour * 3600, in: "UTC").strftime("%Y-%m-%dT%H:00:00Z")

      # parse_hour is the Unix hour of an hour string.
      def parse_hour(value) = Time.iso8601(value).to_i.div(3600)
    end
  end
end
