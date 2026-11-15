using System;

namespace MegooFlags.Internal.Engine;

/// <summary>
/// RFC 3339 timestamps as integer epoch milliseconds, computed by hand rather than with System.DateTime so every
/// SDK accepts and refuses the same strings.
/// </summary>
internal static class Dates
{
    /// <summary>
    /// ParseRfc3339Millis parses YYYY-MM-DD, a T, t or space, hh:mm:ss, an optional fraction (truncated to
    /// milliseconds) and a Z, z or ±hh:mm zone, ASCII digits only. Returns null for anything else or an out-of-range
    /// field. The offset's minutes are not range-checked on their own; only the whole offset must be under 24 h.
    /// </summary>
    public static long? ParseRfc3339Millis(string s)
    {
        var n = s.Length;
        if (n < 20 || s[4] != '-' || s[7] != '-' || "Tt ".IndexOf(s[10]) < 0 || s[13] != ':' || s[16] != ':')
        {
            return null;
        }
        var year = Digits(s, 0, 4);
        var month = Digits(s, 5, 2);
        var day = Digits(s, 8, 2);
        var hour = Digits(s, 11, 2);
        var minute = Digits(s, 14, 2);
        var second = Digits(s, 17, 2);
        if (year < 0 || month < 0 || day < 0 || hour < 0 || minute < 0 || second < 0)
        {
            return null;
        }
        var i = 19;
        var millis = 0;
        if (s[i] == '.')
        {
            i++;
            var start = i;
            while (i < n && char.IsAsciiDigit(s[i]))
            {
                if (i - start < 3)
                {
                    millis = millis * 10 + (s[i] - '0');
                }
                i++;
            }
            var count = i - start;
            if (count == 0)
            {
                return null;
            }
            for (var k = count; k < 3; k++)
            {
                millis *= 10;
            }
        }
        if (i >= n)
        {
            return null;
        }
        var offset = 0; // minutes east of UTC
        var z = s[i];
        if (z is 'Z' or 'z')
        {
            if (i + 1 != n)
            {
                return null;
            }
        }
        else if (z is '+' or '-')
        {
            if (i + 6 != n || s[i + 3] != ':')
            {
                return null;
            }
            var oh = Digits(s, i + 1, 2);
            var om = Digits(s, i + 4, 2);
            if (oh < 0 || om < 0)
            {
                return null;
            }
            offset = oh * 60 + om;
            if (offset >= 24 * 60)
            {
                return null;
            }
            if (z == '-')
            {
                offset = -offset;
            }
        }
        else
        {
            return null;
        }
        if (year < 1 || month < 1 || month > 12 || day < 1 || day > DaysIn(year, month)
            || hour > 23 || minute > 59 || second > 59)
        {
            return null;
        }
        var secs = DaysFromCivil(year, month, day) * 86400L + hour * 3600L + minute * 60L + second - offset * 60L;
        return secs * 1000 + millis;
    }

    /// <summary>
    /// ToMillis is v as integer epoch milliseconds: a number is floored, a string is parsed as RFC 3339. Anything
    /// else, and a non-finite number, is not a date (null).
    /// </summary>
    public static long? ToMillis(object? v)
    {
        if (v is string s)
        {
            return ParseRfc3339Millis(s);
        }
        if (Values.IsInteger(v))
        {
            return Values.ToLong(v!);
        }
        if (v is double or float)
        {
            var d = Values.ToDouble(v);
            if (!double.IsFinite(d))
            {
                return null;
            }
            return (long)Math.Floor(d);
        }
        return null;
    }

    /// <summary>Digits reads count ASCII digits at from, or returns -1.</summary>
    private static int Digits(string s, int from, int count)
    {
        var v = 0;
        for (var i = from; i < from + count; i++)
        {
            var c = s[i];
            if (!char.IsAsciiDigit(c))
            {
                return -1;
            }
            v = v * 10 + (c - '0');
        }
        return v;
    }

    /// <summary>DaysIn is the number of days in month of the proleptic Gregorian year.</summary>
    private static int DaysIn(int year, int month) => month switch
    {
        2 => year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) ? 29 : 28,
        4 or 6 or 9 or 11 => 30,
        _ => 31,
    };

    /// <summary>DaysFromCivil is the days since 1970-01-01 of a proleptic Gregorian date (year &gt;= 1).</summary>
    private static long DaysFromCivil(int year, int month, int day)
    {
        long y = month <= 2 ? year - 1 : year;
        var era = (y >= 0 ? y : y - 399) / 400;
        var yoe = y - era * 400;
        var doy = (153L * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1;
        var doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        return era * 146097 + doe - 719468;
    }
}
