package engine

import (
	"math"
	"time"
)

// ParseRFC3339Millis parses an RFC 3339 date-time to integer milliseconds
// since the Unix epoch, truncating the fraction. It accepts exactly
// \d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2}) with
// ASCII digits, and only real date-times as Python's datetime.fromisoformat
// takes them: year >= 1, a valid day for the month, no leap second, and an
// offset under 24 h (its minutes are not range-checked on their own).
func ParseRFC3339Millis(s string) (int64, bool) {
	if len(s) < 20 || s[4] != '-' || s[7] != '-' || s[13] != ':' || s[16] != ':' {
		return 0, false
	}
	if sep := s[10]; sep != 'T' && sep != 't' && sep != ' ' {
		return 0, false
	}
	year, ok1 := digits(s[0:4])
	month, ok2 := digits(s[5:7])
	day, ok3 := digits(s[8:10])
	hour, ok4 := digits(s[11:13])
	minute, ok5 := digits(s[14:16])
	second, ok6 := digits(s[17:19])
	if !ok1 || !ok2 || !ok3 || !ok4 || !ok5 || !ok6 {
		return 0, false
	}
	rest := s[19:]
	millis := 0
	if rest[0] == '.' {
		n := 1
		for n < len(rest) && rest[n] >= '0' && rest[n] <= '9' {
			if n <= 3 {
				millis = millis*10 + int(rest[n]-'0')
			}
			n++
		}
		if n == 1 {
			return 0, false
		}
		for i := n; i <= 3; i++ {
			millis *= 10
		}
		rest = rest[n:]
	}
	offset := 0 // minutes east of UTC
	switch {
	case rest == "Z" || rest == "z":
	case len(rest) == 6 && (rest[0] == '+' || rest[0] == '-') && rest[3] == ':':
		h, okH := digits(rest[1:3])
		m, okM := digits(rest[4:6])
		if !okH || !okM || h*60+m >= 24*60 {
			return 0, false
		}
		offset = h*60 + m
		if rest[0] == '-' {
			offset = -offset
		}
	default:
		return 0, false
	}
	if year < 1 || month < 1 || month > 12 || day < 1 || day > daysIn(year, month) ||
		hour > 23 || minute > 59 || second > 59 {
		return 0, false
	}
	t := time.Date(year, time.Month(month), day, hour, minute, second, millis*int(time.Millisecond), time.UTC)
	return t.Add(-time.Duration(offset) * time.Minute).UnixMilli(), true
}

// toMillis is v as integer epoch milliseconds: a number is floored (clamped
// to the int64 range), a string is parsed as RFC 3339. Anything else, and a
// non-finite number, is not a date.
func toMillis(v any) (int64, bool) {
	if s, ok := v.(string); ok {
		return ParseRFC3339Millis(s)
	}
	n, ok := toNumber(v)
	if !ok {
		return 0, false
	}
	if n.isInt {
		return n.i, true
	}
	if math.IsNaN(n.f) || math.IsInf(n.f, 0) {
		return 0, false
	}
	f := math.Floor(n.f)
	switch {
	case f >= math.MaxInt64: // float64(MaxInt64) is 2^63
		return math.MaxInt64, true
	case f <= math.MinInt64:
		return math.MinInt64, true
	}
	return int64(f), true
}

// digits parses a run of ASCII digits; false if any byte is not one.
func digits(s string) (int, bool) {
	n := 0
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return 0, false
		}
		n = n*10 + int(s[i]-'0')
	}
	return n, true
}

// daysIn is the number of days in month of the proleptic Gregorian year.
func daysIn(year, month int) int {
	switch month {
	case 2:
		if year%4 == 0 && (year%100 != 0 || year%400 == 0) {
			return 29
		}
		return 28
	case 4, 6, 9, 11:
		return 30
	}
	return 31
}
