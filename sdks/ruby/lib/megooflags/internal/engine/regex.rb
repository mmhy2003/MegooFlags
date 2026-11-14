# frozen_string_literal: true

module MegooFlags
  module Internal
    module Engine
      # Portable regex translator: the subset common to RE2 and Python re, with
      # Python re.ASCII semantics, rewritten into explicit Onigmo constructs so
      # that no host default matters. Three stages: parse (recursive descent
      # over code points), an AST whose sets are sorted merged code point
      # ranges, and the emitter. Regexp.new gets no options, and the emitted
      # source never holds ^ or $, which Ruby treats as line anchors.
      module Regex
        MAX_CP = 0x10FFFF
        MAX_REPEAT = 1000

        # Matcher is a compiled pattern; test is an unanchored search.
        class Matcher
          attr_reader :source

          def initialize(source, regexp)
            @source = source
            @regexp = regexp
          end

          # test reports whether the pattern matches somewhere in subject. A
          # subject that is not valid UTF-8 (after transcoding, when it is in
          # another encoding) never matches.
          def test(subject)
            text = subject.encoding == Encoding::UTF_8 ? subject : subject.encode(Encoding::UTF_8)
            return false unless text.valid_encoding?

            @regexp.match?(text)
          rescue EncodingError, ArgumentError
            false
          end
        end

        # ---- Sets: sorted, merged, inclusive code point ranges ----

        # Sets holds the range list operations.
        module Sets
          module_function

          def normalize(ranges)
            out = []
            ranges.sort_by(&:first).each do |lo, hi|
              last = out.last
              if last && lo <= last[1] + 1
                last[1] = hi if hi > last[1]
              else
                out << [lo, hi]
              end
            end
            out
          end

          def complement(ranges)
            out = []
            nxt = 0
            ranges.each do |lo, hi|
              out << [nxt, lo - 1] if lo > nxt
              nxt = hi + 1
            end
            out << [nxt, MAX_CP] if nxt <= MAX_CP
            out
          end

          # fold_ascii adds the other case of every ASCII letter in the set.
          def fold_ascii(ranges)
            extra = []
            ranges.each do |lo, hi|
              [[0x41, 0x5A, 0x20], [0x61, 0x7A, -0x20]].each do |a, z, shift|
                l = [lo, a].max
                h = [hi, z].min
                extra << [l + shift, h + shift] if l <= h
              end
            end
            normalize(ranges + extra)
          end

          # without_surrogates removes U+D800 to U+DFFF: Ruby strings cannot
          # hold them, and Onigmo refuses them in a UTF-8 pattern.
          def without_surrogates(ranges)
            ranges.flat_map do |lo, hi|
              next [[lo, hi]] if hi < 0xD800 || lo > 0xDFFF

              [([lo, 0xD7FF] if lo < 0xD800), ([0xE000, hi] if hi > 0xDFFF)].compact
            end
          end
        end

        DIGIT = [[0x30, 0x39]].freeze
        WORD = [[0x30, 0x39], [0x41, 0x5A], [0x5F, 0x5F], [0x61, 0x7A]].freeze
        SPACE = [[0x09, 0x0D], [0x20, 0x20]].freeze

        SHORTHANDS = {
          "d" => DIGIT, "D" => Sets.complement(DIGIT),
          "w" => WORD, "W" => Sets.complement(WORD),
          "s" => SPACE, "S" => Sets.complement(SPACE)
        }.freeze

        CONTROL_ESCAPES = { "t" => 0x09, "n" => 0x0A, "r" => 0x0D, "f" => 0x0C, "v" => 0x0B, "a" => 0x07 }.freeze

        # ---- AST ----

        SetNode = Data.define(:ranges)
        ConcatNode = Data.define(:items)
        AltNode = Data.define(:alternatives)
        GroupNode = Data.define(:child)
        RepeatNode = Data.define(:child, :min, :max, :lazy)
        AssertNode = Data.define(:kind)
        Flags = Data.define(:i, :m, :s)
        FlagGroup = Data.define(:flags, :terminator, :end)
        Quantifier = Data.define(:min, :max, :end)

        # ---- Stage 1: parse ----

        # ParseError is a pattern outside the portable subset.
        class ParseError < StandardError; end

        ASSERT_ESCAPES = { "A" => :start, "b" => :word_b, "B" => :not_word_b }.freeze

        # Parser is a recursive descent parser over the code points of a pattern.
        # It is kept as one class, like the parser of the other ports.
        class Parser # rubocop:disable Metrics/ClassLength
          NAME = /\A[A-Za-z_][A-Za-z0-9_]*\z/

          def initialize(pattern)
            @cps = pattern.codepoints
            @pos = 0
            @names = {}
          end

          def parse
            flags = Flags.new(i: false, m: false, s: false)
            loop do
              global = flag_group_at(@pos)
              break if global.nil? || global.terminator != ")"

              flags = merge(flags, global.flags)
              @pos = global.end
            end
            node = alternation(flags)
            # Only an unmatched ")" stops the top-level alternation.
            fail!("unbalanced parenthesis") if @pos < @cps.size
            node
          end

          private

          def fail!(reason)
            raise ParseError, "#{reason} at offset #{@pos}"
          end

          def peek(offset = 0)
            @cps[@pos + offset]
          end

          def peek_char(offset = 0)
            c = peek(offset)
            c&.chr(Encoding::UTF_8)
          end

          def merge(flags, set)
            flags.with(**set)
          end

          # flag_group_at reads "(?" [ims]+ followed by ")" or ":" at index at,
          # without consuming it.
          def flag_group_at(at)
            return nil unless @cps[at] == 0x28 && @cps[at + 1] == 0x3F

            flags = {}
            j = at + 2
            while (name = { 0x69 => :i, 0x6D => :m, 0x73 => :s }[@cps[j]])
              flags[name] = true
              j += 1
            end
            return nil if j == at + 2
            return nil unless [0x29, 0x3A].include?(@cps[j])

            FlagGroup.new(flags: flags, terminator: @cps[j].chr, end: j + 1)
          end

          def alternation(flags)
            alternatives = [concat(flags)]
            while peek_char == "|"
              @pos += 1
              alternatives << concat(flags)
            end
            alternatives.size == 1 ? alternatives.first : AltNode.new(alternatives: alternatives)
          end

          def concat(flags)
            items = []
            loop do
              ch = peek_char
              break if ch.nil? || ch == "|" || ch == ")"

              fail!("nothing to repeat") if quantifier_at
              atom = atom(flags)
              q = quantifier_at
              unless q
                items << atom
                next
              end
              fail!("nothing to repeat") if atom.is_a?(AssertNode)
              @pos = q.end
              lazy = peek_char == "?"
              @pos += 1 if lazy
              fail!("multiple repeat") if quantifier_at
              items << RepeatNode.new(child: atom, min: q.min, max: q.max, lazy: lazy)
            end
            items.size == 1 ? items.first : ConcatNode.new(items: items)
          end

          # quantifier_at reads a quantifier at the current position without
          # consuming it. A "{" that is not {n}, {n,} or {n,m} is a literal,
          # except {,...} which is refused.
          def quantifier_at
            case peek_char
            when "*" then return Quantifier.new(min: 0, max: nil, end: @pos + 1)
            when "+" then return Quantifier.new(min: 1, max: nil, end: @pos + 1)
            when "?" then return Quantifier.new(min: 0, max: 1, end: @pos + 1)
            when "{" then nil
            else return nil
            end
            braced_quantifier
          end

          def braced_quantifier
            j = @pos + 1
            n, j = digits_at(j)
            if n.empty?
              if @cps[j] == 0x2C
                _, j = digits_at(j + 1)
                fail!("missing repeat minimum") if @cps[j] == 0x7D
              end
              return nil
            end
            m = n
            if @cps[j] == 0x2C
              d, j = digits_at(j + 1)
              m = d.empty? ? nil : d
            end
            return nil unless @cps[j] == 0x7D

            min = n.to_i
            max = m&.to_i
            fail!("repeat count over #{MAX_REPEAT}") if min > MAX_REPEAT || (max && max > MAX_REPEAT)
            fail!("repeat minimum over maximum") if max && min > max
            Quantifier.new(min: min, max: max, end: j + 1)
          end

          def digits_at(index)
            digits = +""
            while (c = @cps[index]) && c.between?(0x30, 0x39)
              digits << c
              index += 1
            end
            [digits, index]
          end

          def atom(flags)
            c = peek
            case c.chr(Encoding::UTF_8)
            when "(" then group(flags)
            when "[" then char_class(flags)
            when "."
              @pos += 1
              SetNode.new(ranges: flags.s ? [[0, MAX_CP]] : Sets.complement([[0x0A, 0x0A]]))
            when "^"
              @pos += 1
              AssertNode.new(kind: flags.m ? :line_start : :start)
            when "$"
              @pos += 1
              AssertNode.new(kind: flags.m ? :line_end : :end_dollar)
            when "\\" then escape(flags)
            else
              @pos += 1
              Regex.literal(c, flags)
            end
          end

          def group(flags)
            if peek_char(1) != "?"
              @pos += 1
              return group_body(flags)
            end
            scoped = flag_group_at(@pos)
            if scoped
              fail!("global flags not at the start") if scoped.terminator == ")"
              @pos = scoped.end
              return group_body(merge(flags, scoped.flags))
            end
            if peek_char(2) == ":"
              @pos += 3
              return group_body(flags)
            end
            return named_group(flags) if peek_char(2) == "P" && peek_char(3) == "<"

            fail!("unsupported group syntax")
          end

          def named_group(flags)
            @pos += 4
            name = +""
            loop do
              n = peek_char
              fail!("unterminated group name") if n.nil?
              break if n == ">"

              name << n
              @pos += 1
            end
            fail!("bad group name") unless NAME.match?(name)
            fail!("duplicate group name") if @names.key?(name)
            @names[name] = true
            @pos += 1
            group_body(flags)
          end

          def group_body(flags)
            child = alternation(flags)
            fail!("missing )") if peek_char != ")"
            @pos += 1
            GroupNode.new(child: child)
          end

          def escape(flags)
            e = peek_char(1)
            fail!("trailing backslash") if e.nil?
            if (shorthand = SHORTHANDS[e])
              @pos += 2
              return SetNode.new(ranges: shorthand)
            end
            if (kind = ASSERT_ESCAPES[e])
              @pos += 2
              return AssertNode.new(kind: kind)
            end
            Regex.literal(single_escape, flags)
          end

          # single_escape reads an escape that stands for one code point:
          # \t \n \r \f \v \a, \xHH, or a backslash before ASCII punctuation.
          def single_escape
            e = peek(1)
            fail!("trailing backslash") if e.nil?
            ch = e.chr(Encoding::UTF_8)
            if (control = CONTROL_ESCAPES[ch])
              @pos += 2
              return control
            end
            if ch == "x"
              h = hex_value(peek(2))
              l = hex_value(peek(3))
              fail!("\\x needs two hex digits") if h.nil? || l.nil?
              @pos += 4
              return (h * 16) + l
            end
            if e < 0x80 && !ascii_alnum?(e)
              @pos += 2
              return e
            end
            fail!("unsupported escape \\#{ch}")
          end

          def char_class(flags)
            @pos += 1
            negated = peek_char == "^"
            @pos += 1 if negated
            members = []
            first = true
            loop do
              ch = peek_char
              fail!("unterminated character class") if ch.nil?
              break if ch == "]" && !first

              first = false
              class_member(members)
            end
            @pos += 1
            ranges = Sets.normalize(members)
            ranges = Sets.fold_ascii(ranges) if flags.i
            ranges = Sets.complement(ranges) if negated
            SetNode.new(ranges: ranges)
          end

          # class_member reads one member (a code point, a range or a shorthand)
          # and appends its ranges to members.
          def class_member(members)
            kind, value = class_item
            range = peek_char == "-" && range_dash?
            if kind == :shorthand
              fail!("bad character range") if range
              members.concat(value)
              return
            end
            unless range
              members << [value, value]
              return
            end
            @pos += 1
            end_kind, end_value = class_item
            fail!("bad character range") if end_kind == :shorthand || value > end_value
            members << [value, end_value]
          end

          # range_dash? reports whether the "-" at @pos is followed by a range end.
          def range_dash?
            nxt = peek_char(1)
            !nxt.nil? && nxt != "]"
          end

          def class_item
            c = peek
            fail!("POSIX class") if c == 0x5B && peek(1) == 0x3A
            unless c == 0x5C
              @pos += 1
              return [:cp, c]
            end
            e = peek_char(1)
            if e && (shorthand = SHORTHANDS[e])
              @pos += 2
              return [:shorthand, shorthand]
            end
            [:cp, single_escape]
          end

          def ascii_alnum?(code)
            code.between?(0x30, 0x39) || code.between?(0x41, 0x5A) || code.between?(0x61, 0x7A)
          end

          def hex_value(code)
            return nil if code.nil?
            return code - 0x30 if code.between?(0x30, 0x39)
            return code - 0x41 + 10 if code.between?(0x41, 0x46)
            return code - 0x61 + 10 if code.between?(0x61, 0x66)

            nil
          end
        end

        # ---- Stage 3: emit (Ruby column) ----

        # Emitter writes the AST as an Onigmo source of hex escapes, \A, \z,
        # lookarounds, groups and quantifiers only.
        module Emitter
          module_function

          def cp(code)
            "\\u{#{code.to_s(16).upcase}}"
          end

          def set(ranges)
            ranges = Sets.without_surrogates(ranges)
            return "(?!)" if ranges.empty?
            return cp(ranges[0][0]) if ranges.size == 1 && ranges[0][0] == ranges[0][1]

            body = ranges.map { |lo, hi| lo == hi ? cp(lo) : "#{cp(lo)}-#{cp(hi)}" }.join
            "[#{body}]"
          end

          def emit(node)
            case node
            when SetNode then set(node.ranges)
            when ConcatNode then node.items.map { |n| emit(n) }.join
            when AltNode then node.alternatives.map { |n| emit(n) }.join("|")
            when GroupNode then "(?:#{emit(node.child)})"
            when AssertNode then ASSERTS.fetch(node.kind)
            when RepeatNode then repeat(node)
            end
          end

          def repeat(node)
            operand = emit(node.child)
            # A bare lookahead is not a repeat target.
            operand = "(?:#{operand})" if operand == "(?!)"
            q =
              if node.max.nil? then { 0 => "*", 1 => "+" }.fetch(node.min, "{#{node.min},}")
              elsif node.min.zero? && node.max == 1 then "?"
              elsif node.min == node.max then "{#{node.min}}"
              else "{#{node.min},#{node.max}}"
              end
            operand + q + (node.lazy ? "?" : "")
          end

          START = "\\A"
          FINISH = "\\z"
          LF = cp(0x0A)
          W = set(WORD)

          ASSERTS = {
            start: START,
            end_dollar: "(?=#{LF}?#{FINISH})",
            line_start: "(?:#{START}|(?<=#{LF}))",
            line_end: "(?=#{LF}|#{FINISH})",
            word_b: "(?:(?<=#{W})(?!#{W})|(?<!#{W})(?=#{W}))",
            not_word_b: "(?:(?<=#{W})(?=#{W})|(?<!#{W})(?!#{W}))"
          }.freeze
        end

        module_function

        # literal is the set for code point c: under i an ASCII letter also
        # matches its other case; non-ASCII letters never fold.
        def literal(code, flags)
          ranges = [[code, code]]
          SetNode.new(ranges: flags.i ? Sets.fold_ascii(ranges) : ranges)
        end

        # compile compiles pattern with the portable semantics of evaluation.md
        # (Python re with re.ASCII). Returns [matcher, nil], or [nil, reason]
        # for a pattern outside the portable subset or one the host refused.
        def compile(pattern)
          text = pattern.encoding == Encoding::UTF_8 ? pattern : pattern.encode(Encoding::UTF_8)
          return [nil, "pattern is not valid UTF-8"] unless text.valid_encoding?

          source = Emitter.emit(Parser.new(text).parse)
          [Matcher.new(source, Regexp.new(source)), nil]
        rescue ParseError => e
          [nil, e.message]
        rescue EncodingError
          [nil, "pattern is not valid UTF-8"]
        rescue RegexpError => e
          [nil, "host regex error: #{e.message}"]
        end
      end
    end
  end
end
