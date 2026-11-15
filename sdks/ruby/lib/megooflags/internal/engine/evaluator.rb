# frozen_string_literal: true

require_relative "bucketing"
require_relative "dates"
require_relative "ruleset"
require_relative "semver"
require_relative "values"

module MegooFlags
  module Internal
    module Engine
      # Result is one resolution. variant, error_code, error_message,
      # rule_index and prerequisite are nil when not set.
      Result = Data.define(:value, :variant, :reason, :error_code, :error_message, :rule_index, :prerequisite) do
        # metadata is {"rule_index" => i} and/or {"prerequisite" => key} when set.
        def metadata
          meta = {}
          meta["rule_index"] = rule_index unless rule_index.nil?
          meta["prerequisite"] = prerequisite unless prerequisite.nil?
          meta
        end
      end

      # Evaluator resolves a flag for a context as spec/evaluation.md says. It
      # is pure and takes no lock: the ruleset is frozen.
      module Evaluator # rubocop:disable Metrics/ModuleLength
        MAX_PREREQUISITE_DEPTH = 32
        EXPECTED_TYPES = %w[boolean string number json].freeze
        SEMVER_WANT = { "semver_eq" => 0, "semver_lt" => -1, "semver_gt" => 1 }.freeze

        # DepthExceeded is a prerequisite chain deeper than
        # MAX_PREREQUISITE_DEPTH, or a cycle.
        class DepthExceeded < StandardError; end

        module_function

        # evaluate resolves flag_key for context (a Hash with string keys).
        # expected_type is "boolean", "string", "number", "json" or nil, and is
        # checked only when a variation was served. Errors return default with
        # reason ERROR.
        def evaluate(ruleset, flag_key, context, default, expected_type = nil)
          unless expected_type.nil? || EXPECTED_TYPES.include?(expected_type)
            raise ArgumentError, "unknown flag type: #{expected_type.inspect}"
          end
          unless context.is_a?(Hash) && context.each_key.all?(String)
            return error(default, "INVALID_CONTEXT", "context must be a JSON object")
          end

          flag = ruleset.flags[flag_key]
          return error(default, "FLAG_NOT_FOUND", "flag '#{flag_key}' not found") if flag.nil?

          result = evaluate_flag(ruleset, flag, context, default, 0)
          if expected_type && !result.variant.nil? && !type?(result.value, expected_type)
            return error(default, "TYPE_MISMATCH", "flag '#{flag_key}' is not a #{expected_type}")
          end

          result
        rescue DepthExceeded
          error(default, "GENERAL", "prerequisite chain is too deep or has a cycle")
        end

        def evaluate_flag(ruleset, flag, context, default, depth)
          return serve(flag, flag.off_variation, "DISABLED", default) unless flag.enabled

          flag.prerequisites.each do |prereq|
            unless prerequisite_met?(ruleset, prereq, context, depth + 1)
              return serve(flag, flag.off_variation, "PREREQUISITE_FAILED", default, prerequisite: prereq.flag)
            end
          end
          key = context["key"]
          if key.is_a?(String)
            flag.targets.each do |target|
              return serve(flag, target.variation, "TARGETING_MATCH", default) if target.values.include?(key)
            end
          end
          flag.rules.each_with_index do |rule, index|
            if all_match?(ruleset, rule.clauses, context)
              return serve_rule(flag, rule.serve, context, "TARGETING_MATCH", default, rule_index: index)
            end
          end
          serve_rule(flag, flag.fallthrough, context, fallthrough_reason(flag), default)
        end

        def fallthrough_reason(flag)
          if flag.fallthrough.rollout then "SPLIT"
          elsif flag.targets.empty? && flag.rules.empty? && flag.prerequisites.empty? then "STATIC"
          else "DEFAULT"
          end
        end

        def prerequisite_met?(ruleset, prereq, context, depth)
          raise DepthExceeded if depth > MAX_PREREQUISITE_DEPTH

          pflag = ruleset.flags[prereq.flag]
          return false if pflag.nil? || !pflag.enabled

          result = evaluate_flag(ruleset, pflag, context, nil, depth)
          result.reason != "ERROR" && result.variant == prereq.variation
        end

        def all_match?(ruleset, clauses, context)
          clauses.all? { |clause| clause_matches?(ruleset, clause, context) }
        end

        def clause_matches?(ruleset, clause, context)
          if clause.operator == "in_segment"
            hit = clause.prepared.any? { |key| in_segment?(ruleset, ruleset.segments[key], context) }
            return hit != clause.negate
          end
          value = context[clause.attribute]
          return false if value.nil? # missing or null: false, negate not applied

          hit = if value.is_a?(Array)
                  value.any? { |element| match_value?(clause.operator, element, clause.prepared) }
                else
                  match_value?(clause.operator, value, clause.prepared)
                end
          hit != clause.negate
        end

        def in_segment?(ruleset, segment, context)
          return false if segment.nil?

          key = context["key"]
          if key.is_a?(String)
            return false if segment.excluded.include?(key)
            return true if segment.included.include?(key)
          end
          segment.rules.any? { |clauses| all_match?(ruleset, clauses, context) }
        end

        # match_value? reports whether one element of the attribute matches the
        # clause's prepared values. An element of the wrong type never matches.
        def match_value?(operator, element, prepared)
          case operator
          when "in" then prepared.any? { |p| Values.json_equal?(element, p) }
          when "not_in" then Values.scalar?(element) && prepared.none? { |p| Values.json_equal?(element, p) }
          when "contains", "starts_with", "ends_with", "matches" then string_match?(operator, element, prepared)
          when "lt", "lte", "gt", "gte" then Values.number?(element) && numeric_match?(operator, element, prepared)
          when "semver_eq", "semver_lt", "semver_gt" then semver_match?(operator, element, prepared)
          when "before", "after" then date_match?(operator, element, prepared)
          else false
          end
        end

        def string_match?(operator, element, prepared)
          return false unless element.is_a?(String)

          case operator
          when "contains" then prepared.any? { |p| element.include?(p) }
          when "starts_with" then prepared.any? { |p| element.start_with?(p) }
          when "ends_with" then prepared.any? { |p| element.end_with?(p) }
          else prepared.any? { |matcher| matcher.test(element) }
          end
        rescue EncodingError
          false
        end

        def numeric_match?(operator, element, prepared)
          prepared.any? do |p|
            cmp = compare_numbers(element, p)
            case operator
            when "lt" then cmp.negative?
            when "lte" then cmp <= 0
            when "gt" then cmp.positive?
            else cmp >= 0
            end
          end
        end

        # compare_numbers compares two Integers exactly and anything else as
        # floats. NaN can't come from JSON.
        def compare_numbers(left, right)
          return left <=> right if left.is_a?(Integer) && right.is_a?(Integer)

          left.to_f <=> right.to_f
        end

        def semver_match?(operator, element, prepared)
          return false unless element.is_a?(String)

          version = Semver.parse_semver(element)
          return false if version.nil?

          want = SEMVER_WANT[operator]
          prepared.any? { |p| Semver.compare_semver(version, p) == want }
        end

        def date_match?(operator, element, prepared)
          millis = Dates.to_millis(element)
          return false if millis.nil?

          operator == "before" ? prepared.any? { |p| millis < p } : prepared.any? { |p| millis > p }
        end

        def type?(value, expected_type)
          case expected_type
          when "boolean" then Values.boolean?(value)
          when "string" then value.is_a?(String)
          when "number" then Values.number?(value)
          else true
          end
        end

        def serve(flag, variation, reason, default, rule_index: nil, prerequisite: nil)
          value = variation.nil? ? default : flag.variations[variation].value
          Result.new(value: value, variant: variation, reason: reason, error_code: nil, error_message: nil,
                     rule_index: rule_index, prerequisite: prerequisite)
        end

        def serve_rule(flag, how, context, reason, default, rule_index: nil)
          rollout = how.rollout
          return serve(flag, how.variation, reason, default, rule_index: rule_index) if rollout.nil?

          text = Bucketing.bucket_text(context[rollout.bucket_by])
          bucket = text.nil? ? 0 : Bucketing.bucket(flag.key, text)
          serve(flag, Bucketing.pick_variation(rollout.weights, bucket), reason, default, rule_index: rule_index)
        end

        def error(default, code, message)
          Result.new(value: default, variant: nil, reason: "ERROR", error_code: code, error_message: message,
                     rule_index: nil, prerequisite: nil)
        end
      end
    end
  end
end
