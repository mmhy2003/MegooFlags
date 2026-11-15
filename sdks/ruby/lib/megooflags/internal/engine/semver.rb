# frozen_string_literal: true

require_relative "values"

module MegooFlags
  module Internal
    module Engine
      # Strict SemVer 2.0.0 parsing and precedence.
      module Semver
        # Version is a parsed version. Build metadata is dropped, as it has no
        # precedence. core holds three digit strings without leading zeros, so
        # they compare by (length, text) without overflow; pre is nil when the
        # version has no prerelease.
        Version = Data.define(:core, :pre)
        # PreId is one prerelease identifier.
        PreId = Data.define(:numeric, :text)

        DIGITS = /\A[0-9]+\z/
        IDENT = /\A[0-9A-Za-z-]+\z/

        module_function

        # parse_semver parses a strict SemVer 2.0.0 string: ASCII only, no "v"
        # prefix, no surrounding whitespace, no leading zeros in numeric parts.
        # Returns nil for anything else, invalid UTF-8 included.
        def parse_semver(str)
          str = Values.utf8(str)
          return nil if str.nil?

          rest, build = str.split("+", 2)
          return nil if build && !identifiers(build)

          rest, pre_text = rest.to_s.split("-", 2)
          pre = nil
          if pre_text
            pre = parse_pre(pre_text)
            return nil unless pre
          end
          core = rest.to_s.split(".", -1)
          return nil unless core.size == 3 && core.all? { |part| numeric_part?(part) }

          Version.new(core: core.freeze, pre: pre)
        end

        # compare_semver returns -1, 0 or 1 by SemVer 2.0.0 precedence.
        def compare_semver(left, right)
          3.times do |i|
            c = compare_digits(left.core[i], right.core[i])
            return c unless c.zero?
          end
          return right.pre.nil? ? 0 : 1 if left.pre.nil?
          return -1 if right.pre.nil?

          left.pre.zip(right.pre) do |x, y|
            break if y.nil?

            c = compare_ids(x, y)
            return c unless c.zero?
          end
          left.pre.size <=> right.pre.size
        end

        # identifiers splits text on "." and returns the parts when each is a
        # non-empty [0-9A-Za-z-] run, else nil.
        def identifiers(text)
          parts = text.split(".", -1)
          return nil if parts.empty? || !parts.all? { |id| IDENT.match?(id) }

          parts
        end

        def parse_pre(text)
          ids = identifiers(text)
          return nil unless ids

          pre = ids.map do |id|
            numeric = DIGITS.match?(id)
            return nil if numeric && !numeric_part?(id)

            PreId.new(numeric: numeric, text: id)
          end
          pre.freeze
        end

        def compare_ids(left, right)
          if left.numeric && right.numeric then compare_digits(left.text, right.text)
          elsif left.numeric then -1
          elsif right.numeric then 1
          else left.text <=> right.text
          end
        end

        # compare_digits compares two digit strings without leading zeros.
        def compare_digits(left, right)
          left.size == right.size ? left <=> right : left.size <=> right.size
        end

        # numeric_part? reports whether text is "0" or [1-9][0-9]*.
        def numeric_part?(text)
          DIGITS.match?(text) && (text == "0" || !text.start_with?("0"))
        end
      end
    end
  end
end
