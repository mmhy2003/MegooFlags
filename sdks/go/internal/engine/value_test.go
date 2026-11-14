package engine

import (
	"encoding/json"
	"math"
	"reflect"
	"testing"
)

func TestJSONEqual(t *testing.T) {
	tests := []struct {
		name string
		a, b any
		want bool
	}{
		{"int and float", 1, 1.0, true},
		{"int and json float", int64(1), json.Number("1.0"), true},
		{"json ints", json.Number("2"), json.Number("2"), true},
		{"different numbers", 1, 2, false},
		{"big ints exact", int64(9007199254740993), int64(9007199254740992), false},
		{"bool and number", true, 1, false},
		{"number and bool", 0, false, false},
		{"bools", true, true, true},
		{"different bools", true, false, false},
		{"string and number", "1", 1, false},
		{"strings", "a", "a", true},
		{"different strings", "a", "b", false},
		{"nils", nil, nil, false},
		{"slices", []any{1}, []any{1}, false},
		{"maps", map[string]any{}, map[string]any{}, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := jsonEqual(tt.a, tt.b); got != tt.want {
				t.Errorf("jsonEqual(%#v, %#v) = %v, want %v", tt.a, tt.b, got, tt.want)
			}
		})
	}
}

func TestToNumber(t *testing.T) {
	tests := []struct {
		name string
		in   any
		want number
		ok   bool
	}{
		{"int", int(-3), number{f: -3, i: -3, isInt: true}, true},
		{"int8", int8(-8), number{f: -8, i: -8, isInt: true}, true},
		{"int16", int16(16), number{f: 16, i: 16, isInt: true}, true},
		{"int32", int32(32), number{f: 32, i: 32, isInt: true}, true},
		{"int64", int64(math.MaxInt64), number{f: math.MaxInt64, i: math.MaxInt64, isInt: true}, true},
		{"uint", uint(1), number{f: 1, i: 1, isInt: true}, true},
		{"uint8", uint8(8), number{f: 8, i: 8, isInt: true}, true},
		{"uint16", uint16(16), number{f: 16, i: 16, isInt: true}, true},
		{"uint32", uint32(32), number{f: 32, i: 32, isInt: true}, true},
		{"uint64", uint64(64), number{f: 64, i: 64, isInt: true}, true},
		{"uint64 max int64", uint64(math.MaxInt64), number{f: math.MaxInt64, i: math.MaxInt64, isInt: true}, true},
		{"uint64 above max int64", uint64(math.MaxUint64), number{f: math.MaxUint64}, true},
		{"uintptr", uintptr(7), number{f: 7, i: 7, isInt: true}, true},
		{"float32", float32(1.5), number{f: 1.5}, true},
		{"float64", 2.5, number{f: 2.5}, true},
		{"float64 integral", 2.0, number{f: 2}, true},
		{"json int", json.Number("9007199254740993"), number{f: 9007199254740993, i: 9007199254740993, isInt: true}, true},
		{"json negative int", json.Number("-12"), number{f: -12, i: -12, isInt: true}, true},
		{"json float", json.Number("1.25"), number{f: 1.25}, true},
		{"json integral float", json.Number("42.0"), number{f: 42}, true},
		{"json exponent", json.Number("1e2"), number{f: 100}, true},
		{"json big int", json.Number("18446744073709551616"), number{f: 18446744073709551616}, true},
		{"json invalid", json.Number("x"), number{}, false},
		{"bool", true, number{}, false},
		{"string", "1", number{}, false},
		{"nil", nil, number{}, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, ok := toNumber(tt.in)
			if ok != tt.ok || got != tt.want {
				t.Errorf("toNumber(%#v) = %+v, %v, want %+v, %v", tt.in, got, ok, tt.want, tt.ok)
			}
		})
	}
}

func TestNumberCmp(t *testing.T) {
	n := func(v any) number {
		t.Helper()
		x, ok := toNumber(v)
		if !ok {
			t.Fatalf("toNumber(%#v) failed", v)
		}
		return x
	}
	tests := []struct {
		name string
		a, b any
		want int
	}{
		{"ints beyond 2^53", int64(9007199254740993), int64(9007199254740992), 1},
		{"ints beyond 2^53 reversed", int64(9007199254740992), int64(9007199254740993), -1},
		{"json ints beyond 2^53", json.Number("9007199254740993"), json.Number("9007199254740992"), 1},
		{"equal ints", 5, int64(5), 0},
		{"int and float equal", 1, 1.0, 0},
		{"int and float less", 1, 1.5, -1},
		{"floats greater", 2.5, 1.5, 1},
		{"negative", -1, 0, -1},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := n(tt.a).cmp(n(tt.b)); got != tt.want {
				t.Errorf("cmp(%#v, %#v) = %d, want %d", tt.a, tt.b, got, tt.want)
			}
		})
	}
}

func TestIsScalar(t *testing.T) {
	tests := []struct {
		in   any
		want bool
	}{
		{"s", true},
		{false, true},
		{1, true},
		{2.5, true},
		{json.Number("3"), true},
		{nil, false},
		{[]any{1}, false},
		{map[string]any{}, false},
	}
	for _, tt := range tests {
		if got := isScalar(tt.in); got != tt.want {
			t.Errorf("isScalar(%#v) = %v, want %v", tt.in, got, tt.want)
		}
	}
}

func TestElements(t *testing.T) {
	tests := []struct {
		name    string
		in      any
		want    []any
		isArray bool
	}{
		{"any slice", []any{"a", 1}, []any{"a", 1}, true},
		{"empty any slice", []any{}, []any{}, true},
		{"strings", []string{"a", "b"}, []any{"a", "b"}, true},
		{"ints", []int{1, 2}, []any{1, 2}, true},
		{"int64s", []int64{3}, []any{int64(3)}, true},
		{"float64s", []float64{1.5}, []any{1.5}, true},
		{"bools", []bool{true}, []any{true}, true},
		{"array via reflect", [3]int{1, 2, 3}, []any{1, 2, 3}, true},
		{"uint8 slice via reflect", []uint8{7}, []any{uint8(7)}, true},
		{"string", "abc", nil, false},
		{"number", 1, nil, false},
		{"map", map[string]any{"a": 1}, nil, false},
		{"nil", nil, nil, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, isArray := elements(tt.in)
			if isArray != tt.isArray || !reflect.DeepEqual(got, tt.want) {
				t.Errorf("elements(%#v) = %#v, %v, want %#v, %v", tt.in, got, isArray, tt.want, tt.isArray)
			}
		})
	}
}

func TestPlain(t *testing.T) {
	tests := []struct {
		name string
		in   any
		want any
	}{
		{"json int", json.Number("3"), 3.0},
		{"json float", json.Number("1.5"), 1.5},
		{"string", "s", "s"},
		{"bool", true, true},
		{"nil", nil, nil},
		{"nested", map[string]any{
			"a": []any{json.Number("1"), map[string]any{"b": json.Number("2.5")}},
			"c": "x",
		}, map[string]any{
			"a": []any{1.0, map[string]any{"b": 2.5}},
			"c": "x",
		}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := plain(tt.in); !reflect.DeepEqual(got, tt.want) {
				t.Errorf("plain(%#v) = %#v, want %#v", tt.in, got, tt.want)
			}
		})
	}
}
