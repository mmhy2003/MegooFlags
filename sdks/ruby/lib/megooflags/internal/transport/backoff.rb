# frozen_string_literal: true

module MegooFlags
  module Internal
    module Transport
      # Backoff computes jittered exponential waits in seconds. The first wait is min(base, max);
      # the n-th is min(max, base * 2^n) scaled by a factor in [0.8, 1.0]. base can be replaced,
      # for the stream's retry field.
      class Backoff
        attr_accessor :base, :max

        # rand returns a Float in [0, 1); tests inject a fixed one.
        def initialize(base, max, rand: -> { Random.rand })
          @base = base
          @max = max
          @rand = rand
          @n = 0
        end

        # next returns the next wait and advances the attempt counter.
        def next
          if @n.zero?
            @n = 1
            return [@base, @max].min
          end
          wait = [@max, @base * (2**@n)].min
          @n += 1 if @n < 64
          wait * (0.8 + (0.2 * @rand.call))
        end

        # reset makes the next wait base again.
        def reset
          @n = 0
        end
      end
    end
  end
end
