# frozen_string_literal: true

module MegooFlags
  module Internal
    module Delivery
      SOURCES = %w[bootstrap cache stream poll].freeze

      # Snapshot is one parsed ruleset with the ETag and the source it came from ("bootstrap",
      # "cache", "stream" or "poll").
      Snapshot = Data.define(:ruleset, :etag, :source) do
        def initialize(ruleset:, etag:, source:)
          raise ArgumentError, "megooflags: unknown ruleset source #{source.inspect}" unless SOURCES.include?(source)

          super(ruleset: ruleset, etag: etag.to_s.dup.freeze, source: source.dup.freeze)
        end
      end

      # Store holds the current snapshot in one instance variable. Readers take no lock: a
      # reference assignment is atomic and the snapshot is frozen. Writers swap under a mutex.
      class Store
        def initialize
          @snapshot = nil
          @lock = Mutex.new
        end

        # load returns the current snapshot, or nil before the first swap.
        def load = @snapshot

        # swap installs snapshot and returns the one it replaced.
        def swap(snapshot)
          @lock.synchronize do
            old = @snapshot
            @snapshot = snapshot
            old
          end
        end
      end
    end
  end
end
