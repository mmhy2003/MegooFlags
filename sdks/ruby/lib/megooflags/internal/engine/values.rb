# frozen_string_literal: true

module MegooFlags
  module Internal
    module Engine
      # JSON values as the engine sees them: nil, true, false, Integer, Float,
      # String, Array and Hash. true and false are never numbers.
      module Values
        module_function

        # number? reports whether v is an Integer or a Float.
        def number?(value)
          value.is_a?(Integer) || value.is_a?(Float)
        end

        # scalar? reports whether v is a string, a boolean or a number.
        def scalar?(value)
          value.is_a?(String) || boolean?(value) || number?(value)
        end

        # json_equal? is JSON scalar equality: a boolean only equals a boolean,
        # two Integers compare exactly, other numbers as floats, a string equals
        # a string. Anything else, nil included, is never equal.
        def json_equal?(left, right)
          return left.equal?(right) if boolean?(left) || boolean?(right)
          return left == right if left.is_a?(String) && right.is_a?(String)
          return false unless number?(left) && number?(right)
          return left == right if left.is_a?(Integer) && right.is_a?(Integer)

          left.to_f == right.to_f # rubocop:disable Lint/FloatComparison
        end

        # utf8 is str as valid UTF-8 (transcoded from another encoding), or nil
        # when it is not valid text. A string op on invalid UTF-8 raises, so
        # such a string matches as no version, date or pattern.
        def utf8(str)
          text = str.encoding == Encoding::UTF_8 ? str : str.encode(Encoding::UTF_8)
          text.valid_encoding? ? text : nil
        rescue EncodingError
          nil
        end

        def boolean?(value)
          [true, false].include?(value)
        end
      end
    end
  end
end
