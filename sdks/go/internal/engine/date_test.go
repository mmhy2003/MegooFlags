package engine

import (
	"encoding/json"
	"math"
	"testing"
)

func TestParseRFC3339Millis(t *testing.T) {
	tests := []struct {
		in   string
		want int64
		ok   bool
	}{
		{"2026-01-01T00:00:00Z", 1767225600000, true},
		{"2026-01-01T00:00:00.000Z", 1767225600000, true},
		{"2026-01-01T00:00:00.001Z", 1767225600001, true},
		{"2026-01-01T00:00:00.0009Z", 1767225600000, true},
		{"2026-01-01T00:00:00.0019Z", 1767225600001, true},
		{"2026-01-01T00:00:00.123456789Z", 1767225600123, true},
		{"2026-01-01T00:00:00.5+01:00", 1767222000500, true},
		{"2026-01-01t00:00:00.001z", 1767225600001, true},
		{"2026-01-01 00:00:00Z", 1767225600000, true},
		{"2026-01-01T02:00:00+02:00", 1767225600000, true},
		{"2026-01-01T01:00:00+02:00", 1767222000000, true},
		{"2025-12-31T23:30:00-01:00", 1767227400000, true},
		{"2026-01-01T00:00:00-00:00", 1767225600000, true},
		// Offsets are taken the way Python's datetime.fromisoformat takes them:
		// minutes are not range-checked, but the whole offset must be under 24 h.
		{"2026-01-01T00:00:00+05:75", 1767203100000, true},
		{"2026-01-01T00:00:00+00:60", 1767222000000, true},
		{"2026-01-01T00:00:00+00:99", 1767219660000, true},
		{"2026-01-01T00:00:00+23:59", 1767139260000, true},
		{"2026-01-01T00:00:00+24:00", 0, false},
		{"2026-01-01T00:00:00-24:00", 0, false},
		{"2026-01-01T00:00:00+23:60", 0, false},
		{"0001-01-01T00:00:00+23:59", -62135683140000, true},
		{"9999-12-31T23:59:59-23:59", 253402387139000, true},
		{"2024-02-29T00:00:00Z", 1709164800000, true},
		{"2000-02-29T00:00:00Z", 951782400000, true},
		{"0000-01-01T00:00:00Z", 0, false},
		{"2025-02-29T00:00:00Z", 0, false},
		{"1900-02-29T00:00:00Z", 0, false},
		{"2026-04-31T00:00:00Z", 0, false},
		{"2026-00-01T00:00:00Z", 0, false},
		{"2026-01-00T00:00:00Z", 0, false},
		{"2025-13-01T00:00:00Z", 0, false},
		{"2026-01-01T24:00:00Z", 0, false},
		{"2026-01-01T00:60:00Z", 0, false},
		{"2026-01-01T00:00:60Z", 0, false},
		{"2025-01-01", 0, false},
		{"yesterday", 0, false},
		{"", 0, false},
		{"2026-01-01T00:00:00Z\n", 0, false},
		{"2026-01-01T00:00:00", 0, false},
		{"2026-01-01T00:00:00.Z", 0, false},
		{"2026-01-01T00:00:00+0200", 0, false},
		{"2026-01-01T00:00:00+2:00", 0, false},
		{"2026-01-01X00:00:00Z", 0, false},
		{"+2026-01-01T00:00:00Z", 0, false},
		{"2026-01-01T00:00:0٣Z", 0, false},
		{"２026-01-01T00:00:00Z", 0, false},
	}
	for _, tt := range tests {
		got, ok := ParseRFC3339Millis(tt.in)
		if got != tt.want || ok != tt.ok {
			t.Errorf("ParseRFC3339Millis(%q) = %d, %v, want %d, %v", tt.in, got, ok, tt.want, tt.ok)
		}
	}
}

func TestToMillis(t *testing.T) {
	tests := []struct {
		name string
		in   any
		want int64
		ok   bool
	}{
		{"int", 1767225600000, 1767225600000, true},
		{"int64", int64(-5), -5, true},
		{"json int", json.Number("1767225599999"), 1767225599999, true},
		{"float floored", 1767225600000.9, 1767225600000, true},
		{"json float floored", json.Number("1767225600000.9"), 1767225600000, true},
		{"negative fraction floored", -0.5, -1, true},
		{"integral float", 3.0, 3, true},
		{"float above int64", 1e300, math.MaxInt64, true},
		{"float below int64", -1e300, math.MinInt64, true},
		{"float at 2^63", 9223372036854775808.0, math.MaxInt64, true},
		{"float at -2^63", -9223372036854775808.0, math.MinInt64, true},
		{"uint64 above int64", uint64(math.MaxUint64), math.MaxInt64, true},
		{"infinity", math.Inf(1), 0, false},
		{"negative infinity", math.Inf(-1), 0, false},
		{"NaN", math.NaN(), 0, false},
		{"string", "2026-01-01T00:00:00Z", 1767225600000, true},
		{"invalid string", "yesterday", 0, false},
		{"bool", true, 0, false},
		{"nil", nil, 0, false},
		{"slice", []any{1}, 0, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := toMillis(tt.in)
			if got != tt.want || ok != tt.ok {
				t.Errorf("toMillis(%#v) = %d, %v, want %d, %v", tt.in, got, ok, tt.want, tt.ok)
			}
		})
	}
}
