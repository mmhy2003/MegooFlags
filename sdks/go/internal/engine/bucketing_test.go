package engine

import (
	"encoding/json"
	"math"
	"testing"
)

func TestBucketText(t *testing.T) {
	tests := []struct {
		name string
		in   any
		want string
		ok   bool
	}{
		{"string", "user-1", "user-1", true},
		{"empty string", "", "", true},
		{"bool", true, "", false},
		{"false", false, "", false},
		{"int", 42, "42", true},
		{"negative int", int64(-7), "-7", true},
		{"max safe int", int64(9007199254740991), "9007199254740991", true},
		{"min safe int", int64(-9007199254740991), "-9007199254740991", true},
		{"int above max safe", int64(9007199254740992), "", false},
		{"int below min safe", int64(-9007199254740992), "", false},
		{"json int above max safe", json.Number("9007199254740992"), "", false},
		{"uint64 above max int64", uint64(math.MaxUint64), "", false},
		{"integral float", 42.0, "42", true},
		{"json integral float", json.Number("42.0"), "42", true},
		{"negative zero", math.Copysign(0, -1), "0", true},
		{"json negative zero", json.Number("-0.0"), "0", true},
		{"float above max safe", 9007199254740992.0, "", false},
		{"fraction", 1.5, "", false},
		{"NaN", math.NaN(), "", false},
		{"infinity", math.Inf(1), "", false},
		{"nil", nil, "", false},
		{"slice", []any{"a"}, "", false},
		{"map", map[string]any{}, "", false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := BucketText(tt.in)
			if got != tt.want || ok != tt.ok {
				t.Errorf("BucketText(%#v) = %q, %v, want %q, %v", tt.in, got, ok, tt.want, tt.ok)
			}
		})
	}
}

func TestBucket(t *testing.T) {
	tests := []struct {
		flag, text string
		want       int
	}{
		{"f", "u", 68258},
		{"rollout", "user-1", 2613},
		{"f", "日本", 51328},
	}
	for _, tt := range tests {
		if got := Bucket(tt.flag, tt.text); got != tt.want {
			t.Errorf("Bucket(%q, %q) = %d, want %d", tt.flag, tt.text, got, tt.want)
		}
	}
}

func TestPickVariation(t *testing.T) {
	split := []Weight{{"a", 30000}, {"b", 40000}, {"c", 30000}}
	leadingZero := []Weight{{"a", 0}, {"b", 100000}}
	tests := []struct {
		name   string
		ws     []Weight
		bucket int
		want   string
	}{
		{"worked example", split, 68258, "b"},
		{"first bucket", split, 0, "a"},
		{"last in first", split, 29999, "a"},
		{"boundary goes to next", split, 30000, "b"},
		{"last bucket", split, Buckets - 1, "c"},
		{"leading zero weight", leadingZero, 0, "b"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := pickVariation(tt.ws, tt.bucket); got != tt.want {
				t.Errorf("pickVariation(%v, %d) = %q, want %q", tt.ws, tt.bucket, got, tt.want)
			}
		})
	}
}
