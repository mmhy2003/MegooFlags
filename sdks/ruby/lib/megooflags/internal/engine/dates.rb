# frozen_string_literal: true

module MegooFlags
  module Internal
    module Engine
      # RFC 3339 timestamps as integer epoch milliseconds, computed by hand
      # rather than with Time so every SDK accepts and refuses the same strings.
      module Dates
        # \A and \z anchor the whole string ($ would match before a newline);
        # [0-9] is ASCII only.
        DATE_TIME = /\A([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt\x20]([0-9]{2}):([0-9]{2}):([0-9]{2})
                     (?:\.([0-9]+))?(?:[Zz]|([+-])([0-9]{2}):([0-9]{2}))\z/x

        module_function

        # parse_rfc3339_millis parses YYYY-MM-DD, a T, t or space, hh:mm:ss, an
        # optional fraction (truncated to milliseconds) and a Z, z or +-hh:mm
        # zone. Returns nil for anything else or an out-of-range field. Only the
        # whole offset must be under 24 h.
        def parse_rfc3339_millis(str)
          m = DATE_TIME.match(str)
          return nil unless m

          year, month, day, hour, minute, second = m.captures.first(6).map { |part| Integer(part, 10) }
          return nil unless valid?(year, month, day, hour, minute, second)

          offset = offset_minutes(m[8], m[9], m[10])
          return nil unless offset

          millis = m[7] ? Integer(m[7][0, 3].ljust(3, "0"), 10) : 0
          secs = (days_from_civil(year, month, day) * 86_400) + (hour * 3600) + (minute * 60) + second - (offset * 60)
          (secs * 1000) + millis
        end

        # to_millis is value as integer epoch milliseconds: a number is floored,
        # a string is parsed as RFC 3339. Anything else, and a non-finite
        # number, is nil.
        def to_millis(value)
          case value
          when String then parse_rfc3339_millis(value)
          when Integer then value
          when Float then value.finite? ? value.floor : nil
          end
        end

        def valid?(year, month, day, hour, minute, second)
          year >= 1 && month.between?(1, 12) && day.between?(1, days_in(year, month)) &&
            hour <= 23 && minute <= 59 && second <= 59
        end

        # offset_minutes is the zone in minutes east of UTC (0 for Z), or nil
        # when it is 24 h or more.
        def offset_minutes(sign, hours, minutes)
          return 0 unless sign

          offset = (Integer(hours, 10) * 60) + Integer(minutes, 10)
          return nil if offset >= 24 * 60

          sign == "-" ? -offset : offset
        end

        # days_in is the number of days in month of the proleptic Gregorian year.
        def days_in(year, month)
          case month
          when 2 then (year % 4).zero? && (!(year % 100).zero? || (year % 400).zero?) ? 29 : 28
          when 4, 6, 9, 11 then 30
          else 31
          end
        end

        # days_from_civil is the days since 1970-01-01 of a proleptic Gregorian
        # date. Integer division floors, as the algorithm needs.
        def days_from_civil(year, month, day)
          y = month <= 2 ? year - 1 : year
          era = y / 400
          yoe = y - (era * 400)
          doy = (((153 * (month + (month > 2 ? -3 : 9))) + 2) / 5) + day - 1
          doe = (yoe * 365) + (yoe / 4) - (yoe / 100) + doy
          (era * 146_097) + doe - 719_468
        end
      end
    end
  end
end
