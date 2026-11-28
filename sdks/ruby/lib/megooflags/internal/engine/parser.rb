# frozen_string_literal: true

require "json"
require_relative "bucketing"
require_relative "dates"
require_relative "regex"
require_relative "ruleset"
require_relative "semver"
require_relative "values"

module MegooFlags
  module Internal
    module Engine
      # ParseError is a ruleset that does not follow spec/ruleset.schema.json.
      # The message is "<json path>: <reason>".
      class ParseError < StandardError; end

      # Parser validates a ruleset (evaluation.md §11) and builds the frozen
      # model. Unknown fields are ignored. Prerequisite flag keys and segment
      # keys are not checked: a missing flag fails the prerequisite and a
      # missing segment is not matched. Cycles are not checked.
      class Parser # rubocop:disable Metrics/ClassLength
        SCHEMA_VERSION = 1
        FLAG_TYPES = Set["boolean", "string", "number", "json"].freeze
        OPERATORS = Set[
          "in", "not_in", "contains", "starts_with", "ends_with", "matches", "lt", "lte", "gt", "gte",
          "semver_eq", "semver_lt", "semver_gt", "before", "after", "in_segment"
        ].freeze
        INT64 = 2**63

        # parse takes JSON text or the decoded tree (Hashes with string keys)
        # and returns a Ruleset. It raises ParseError naming the bad JSON path.
        # A matches value that does not compile is not an error: it never
        # matches and is named in Ruleset#warnings.
        def self.parse(text_or_tree)
          new.ruleset(text_or_tree.is_a?(String) ? load(text_or_tree) : text_or_tree)
        end

        def self.load(text)
          JSON.parse(text)
        rescue JSON::ParserError, EncodingError, ArgumentError => e
          raise ParseError, "ruleset: invalid JSON: #{e.message}"
        end

        def initialize
          @warnings = []
        end

        def ruleset(tree)
          root = hash(tree, "ruleset")
          version = integer(fetch(root, "schema_version", ""), "schema_version")
          fail!("schema_version", "must be #{SCHEMA_VERSION}") unless version == SCHEMA_VERSION
          project = string(fetch(root, "project", ""), "project")
          environment = string(fetch(root, "environment", ""), "environment")
          flags = hash(fetch(root, "flags", ""), "flags")
          segments = hash(fetch(root, "segments", ""), "segments")
          Ruleset.new(
            project: project, environment: environment,
            flags: flags.to_h { |key, value| [-key, flag(key, value, "flags.#{key}")] }.freeze,
            segments: segments.to_h { |key, value| [-key, segment(key, value, "segments.#{key}")] }.freeze,
            warnings: @warnings.freeze
          )
        end

        private

        def fail!(path, reason)
          raise ParseError, "#{path}: #{reason}"
        end

        # fetch is obj[name], which must be present (null counts as present).
        def fetch(obj, name, path)
          fail!(path.empty? ? name : "#{path}.#{name}", "is required") unless obj.key?(name)
          obj[name]
        end

        def hash(value, path)
          fail!(path, "must be an object") unless value.is_a?(Hash)
          value
        end

        def array(value, path, non_empty: false)
          fail!(path, "must be an array") unless value.is_a?(Array)
          fail!(path, "must not be empty") if non_empty && value.empty?
          value
        end

        def string(value, path)
          fail!(path, "must be a string") unless value.is_a?(String)
          -value
        end

        def boolean(value, path)
          fail!(path, "must be a boolean") unless Values.boolean?(value)
          value
        end

        # integer refuses true, false and floats such as 1.0.
        def integer(value, path)
          fail!(path, "must be an integer") unless value.is_a?(Integer)
          value
        end

        def strings(value, path)
          array(value, path).each_with_index.to_set { |item, i| string(item, "#{path}[#{i}]") }.freeze
        end

        def known(value, variations, path)
          key = string(value, path)
          fail!(path, "unknown variation '#{key}'") unless variations.key?(key)
          key
        end

        def list(value, path, non_empty: false, &)
          array(value, path, non_empty: non_empty).each_with_index.map { |item, i| yield item, "#{path}[#{i}]" }.freeze
        end

        def flag(key, value, path)
          obj = hash(value, path)
          fail!("#{path}.key", "must equal '#{key}'") unless fetch(obj, "key", path) == key
          type = string(fetch(obj, "type", path), "#{path}.type")
          fail!("#{path}.type", "must be one of #{FLAG_TYPES.to_a.sort.join(", ")}") unless FLAG_TYPES.include?(type)
          variations = variations(fetch(obj, "variations", path), "#{path}.variations")
          off = fetch(obj, "off_variation", path)
          Flag.new(
            key: -key, type: type,
            off_variation: off.nil? ? nil : known(off, variations, "#{path}.off_variation"),
            prerequisites: list(fetch(obj, "prerequisites", path), "#{path}.prerequisites") do |p, at|
              prerequisite(p, at)
            end,
            targets: list(fetch(obj, "targets", path), "#{path}.targets") { |t, at| target(t, variations, at) },
            rules: list(fetch(obj, "rules", path), "#{path}.rules") { |r, at| rule(r, variations, at) },
            enabled: boolean(fetch(obj, "enabled", path), "#{path}.enabled"),
            version: integer(fetch(obj, "version", path), "#{path}.version"),
            fallthrough: serve(fetch(obj, "fallthrough", path), variations, "#{path}.fallthrough"),
            variations: variations
          )
        end

        def variations(value, path)
          variations = {}
          array(value, path, non_empty: true).each_with_index do |item, i|
            at = "#{path}[#{i}]"
            obj = hash(item, at)
            key = string(fetch(obj, "key", at), "#{at}.key")
            fail!("#{at}.key", "duplicate variation '#{key}'") if variations.key?(key)
            raw = fetch(obj, "value", at)
            int = integral(raw)
            variations[key] = Variation.new(key: key, value: frozen_copy(raw), is_int: !int.nil?, int_value: int || 0)
          end
          variations.freeze
        end

        # integral is value as an Integer when it is an integral number that
        # fits 64 bits, else nil.
        def integral(value)
          value = value.to_i if value.is_a?(Float) && value.finite? && value == value.floor
          value if value.is_a?(Integer) && value >= -INT64 && value < INT64
        end

        # frozen_copy is a deeply frozen copy of a JSON value, so the model
        # shares nothing with the caller's tree.
        def frozen_copy(value)
          case value
          when Hash then value.to_h { |k, v| [-k, frozen_copy(v)] }.freeze
          when Array then value.map { |v| frozen_copy(v) }.freeze
          when String then -value
          else value
          end
        end

        def prerequisite(value, path)
          obj = hash(value, path)
          Prerequisite.new(flag: string(fetch(obj, "flag", path), "#{path}.flag"),
                           variation: string(fetch(obj, "variation", path), "#{path}.variation"))
        end

        def target(value, variations, path)
          obj = hash(value, path)
          Target.new(variation: known(fetch(obj, "variation", path), variations, "#{path}.variation"),
                     values: strings(fetch(obj, "values", path), "#{path}.values"))
        end

        def rule(value, variations, path)
          obj = hash(value, path)
          Rule.new(description: string(obj.fetch("description", ""), "#{path}.description"),
                   clauses: clauses(fetch(obj, "clauses", path), "#{path}.clauses", segment: false),
                   serve: serve(fetch(obj, "serve", path), variations, "#{path}.serve"))
        end

        def serve(value, variations, path)
          obj = hash(value, path)
          fail!(path, "must have exactly one of variation or rollout") if obj.key?("variation") == obj.key?("rollout")
          if obj.key?("variation")
            return Serve.new(variation: known(obj["variation"], variations, "#{path}.variation"), rollout: nil)
          end

          Serve.new(variation: nil, rollout: rollout(obj["rollout"], variations, "#{path}.rollout"))
        end

        def rollout(value, variations, path)
          obj = hash(value, path)
          bucket_by = string(fetch(obj, "bucket_by", path), "#{path}.bucket_by")
          weights = list(fetch(obj, "weights", path), "#{path}.weights", non_empty: true) do |item, at|
            weight_obj = hash(item, at)
            variation = known(fetch(weight_obj, "variation", at), variations, "#{at}.variation")
            weight = integer(fetch(weight_obj, "weight", at), "#{at}.weight")
            unless weight.between?(0, Bucketing::BUCKETS)
              fail!("#{at}.weight", "must be between 0 and #{Bucketing::BUCKETS}")
            end
            [variation, weight].freeze
          end
          unless weights.sum(&:last) == Bucketing::BUCKETS
            fail!("#{path}.weights", "must add up to #{Bucketing::BUCKETS}")
          end
          Rollout.new(bucket_by: bucket_by, weights: weights)
        end

        def clauses(value, path, segment:)
          list(value, path, non_empty: true) { |item, at| clause(item, at, segment: segment) }
        end

        def clause(value, path, segment:)
          obj = hash(value, path)
          attribute = string(fetch(obj, "attribute", path), "#{path}.attribute")
          operator = string(fetch(obj, "operator", path), "#{path}.operator")
          fail!("#{path}.operator", "unknown operator '#{operator}'") unless OPERATORS.include?(operator)
          if segment && operator == "in_segment"
            fail!("#{path}.operator", "in_segment is not allowed in a segment rule")
          end
          values = list(fetch(obj, "values", path), "#{path}.values", non_empty: true) do |item, at|
            fail!(at, "must be a string, number or boolean") unless Values.scalar?(item)
            item.is_a?(String) ? -item : item
          end
          Clause.new(attribute: attribute, operator: operator, values: values,
                     negate: boolean(obj.fetch("negate", false), "#{path}.negate"),
                     prepared: prepare(operator, values, "#{path}.values"))
        end

        # prepare is the clause values the operator can use, in the form it
        # needs. A value it can't use is left out, so it never matches.
        def prepare(operator, values, path)
          prepared =
            case operator
            when "in", "not_in" then values
            when "contains", "starts_with", "ends_with", "in_segment" then values.grep(String)
            when "lt", "lte", "gt", "gte" then values.select { |v| Values.number?(v) }
            when "semver_eq", "semver_lt", "semver_gt" then values.filter_map do |v|
              v.is_a?(String) && Semver.parse_semver(v)
            end
            when "before", "after" then values.filter_map { |v| v.is_a?(String) && Dates.parse_rfc3339_millis(v) }
            when "matches" then matchers(values, path)
            end
          prepared.freeze
        end

        def matchers(values, path)
          values.each_with_index.filter_map do |value, i|
            next unless value.is_a?(String)

            matcher, reason = Regex.compile(value)
            @warnings << -"#{path}[#{i}]: regex does not compile: #{reason}" unless matcher
            matcher&.freeze
          end
        end

        def segment(key, value, path)
          obj = hash(value, path)
          Segment.new(
            key: -key,
            included: strings(fetch(obj, "included", path), "#{path}.included"),
            excluded: strings(fetch(obj, "excluded", path), "#{path}.excluded"),
            rules: list(fetch(obj, "rules", path), "#{path}.rules") do |item, at|
              clauses(fetch(hash(item, at), "clauses", at), "#{at}.clauses", segment: true)
            end
          )
        end
      end
    end
  end
end
