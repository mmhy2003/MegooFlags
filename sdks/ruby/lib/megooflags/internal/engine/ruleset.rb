# frozen_string_literal: true

module MegooFlags
  module Internal
    module Engine
      # The parsed ruleset. Every object is frozen, so one ruleset can be read by
      # many threads without a lock. Hashes and Sets are keyed by strings.

      # Ruleset: flags and segments are Hashes by key; warnings are
      # "<path>: regex does not compile: <reason>", in ruleset order.
      Ruleset = Data.define(:project, :environment, :flags, :segments, :warnings)

      # Flag: variations is a Hash by key in ruleset order; off_variation is a
      # key or nil; fallthrough is a Serve.
      Flag = Data.define(:key, :type, :enabled, :version, :variations, :off_variation,
                         :prerequisites, :targets, :rules, :fallthrough)

      # Variation: is_int is true when value is an integral number that fits 64
      # bits, and int_value is that number (0 otherwise).
      Variation = Data.define(:key, :value, :is_int, :int_value)

      Prerequisite = Data.define(:flag, :variation)

      # Target: values is a Set of context keys.
      Target = Data.define(:variation, :values)

      Rule = Data.define(:description, :clauses, :serve)

      # Serve: exactly one of variation (a key) and rollout is set.
      Serve = Data.define(:variation, :rollout)

      # Rollout: weights is an Array of [variation, weight] pairs adding up to
      # Bucketing::BUCKETS.
      Rollout = Data.define(:bucket_by, :weights)

      # Segment: included and excluded are Sets of context keys; rules is an
      # Array of clause Arrays.
      Segment = Data.define(:key, :included, :excluded, :rules)

      # Clause: prepared holds the values the operator can use, in the form it
      # needs (see Parser): matchers, parsed versions, milliseconds and so on.
      Clause = Data.define(:attribute, :operator, :negate, :values, :prepared)
    end
  end
end
