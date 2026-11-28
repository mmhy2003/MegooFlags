# frozen_string_literal: true

require "digest"

module MegooFlags
  module Internal
    module Engine
      # Rollout bucketing: sha256 of "<flag key>.<text>" into 100 000 buckets.
      module Bucketing
        # BUCKETS is the number of rollout buckets; weights add up to it.
        BUCKETS = 100_000
        # MAX_SAFE is 2^53 - 1, the largest integer every SDK buckets the same way.
        MAX_SAFE = 9_007_199_254_740_991

        module_function

        # bucket_text is the text hashed for value, or nil when value can't be
        # bucketed (which counts as a missing attribute). A string is used as
        # is, an integral number with abs <= 2^53-1 as its base-10 text.
        def bucket_text(value)
          case value
          when String then value
          when Integer then value.abs <= MAX_SAFE ? value.to_s : nil
          when Float
            return nil unless value.finite? && value == value.floor && value.abs <= MAX_SAFE

            value.to_i.to_s # -0.0.to_i is 0
          end
        end

        # bucket is uint64_be(sha256(flag_key + "." + text)[0, 8]) mod BUCKETS,
        # reduced byte by byte.
        def bucket(flag_key, text)
          sum = Digest::SHA256.digest("#{flag_key}.#{text}".encode(Encoding::UTF_8))
          sum.byteslice(0, 8).each_byte.reduce(0) { |r, byte| ((r * 256) + byte) % BUCKETS }
        end

        # pick_variation returns the first variation whose running total of
        # weights is greater than bucket. weights is an enumerable of
        # [variation, weight] pairs; the parser makes them add up to BUCKETS.
        def pick_variation(weights, bucket)
          total = 0
          weights.each do |variation, weight|
            total += weight
            return variation if total > bucket
          end
          nil
        end
      end
    end
  end
end
