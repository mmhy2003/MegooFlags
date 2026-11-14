package engine

import (
	"encoding/json"
	"errors"
	"math"
	"reflect"
	"strconv"
)

// maxSafeInteger is 2^53-1, the largest integer every JSON implementation
// represents exactly.
const maxSafeInteger = 1<<53 - 1

// number is a normalised JSON number. Integers that fit int64 keep their
// exact value in i (isInt true), so they compare exactly beyond 2^53.
type number struct {
	f     float64
	i     int64
	isInt bool
}

// toNumber normalises a Go or JSON number. A bool is not a number.
func toNumber(v any) (number, bool) {
	switch n := v.(type) {
	case int:
		return intNumber(int64(n)), true
	case int8:
		return intNumber(int64(n)), true
	case int16:
		return intNumber(int64(n)), true
	case int32:
		return intNumber(int64(n)), true
	case int64:
		return intNumber(n), true
	case uint:
		return uintNumber(uint64(n)), true
	case uint8:
		return intNumber(int64(n)), true
	case uint16:
		return intNumber(int64(n)), true
	case uint32:
		return intNumber(int64(n)), true
	case uint64:
		return uintNumber(n), true
	case uintptr:
		return uintNumber(uint64(n)), true
	case float32:
		return number{f: float64(n)}, true
	case float64:
		return number{f: n}, true
	case json.Number:
		// A literal with '.', 'e' or 'E' fails ParseInt, so it is a float even when integral.
		if i, err := strconv.ParseInt(string(n), 10, 64); err == nil {
			return intNumber(i), true
		}
		f, ok := parseFloat(string(n))
		if !ok {
			return number{}, false
		}
		return number{f: f}, true
	}
	return number{}, false
}

func intNumber(i int64) number { return number{f: float64(i), i: i, isInt: true} }

func uintNumber(u uint64) number {
	if u > math.MaxInt64 {
		return number{f: float64(u)}
	}
	return intNumber(int64(u))
}

// parseFloat accepts out-of-range literals as ±Inf or 0, as Python's json does.
func parseFloat(s string) (float64, bool) {
	f, err := strconv.ParseFloat(s, 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return 0, false
	}
	return f, true
}

// cmp returns -1, 0 or 1. Two integers compare exactly; otherwise as float64.
func (a number) cmp(b number) int {
	if a.isInt && b.isInt {
		switch {
		case a.i < b.i:
			return -1
		case a.i > b.i:
			return 1
		}
		return 0
	}
	switch {
	case a.f < b.f:
		return -1
	case a.f > b.f:
		return 1
	}
	return 0
}

// jsonEqual is JSON scalar equality: a bool only equals a bool, numbers
// compare numerically, a string equals a string. Anything else, nil
// included, is never equal.
func jsonEqual(a, b any) bool {
	ab, aIsBool := a.(bool)
	bb, bIsBool := b.(bool)
	if aIsBool || bIsBool {
		return aIsBool && bIsBool && ab == bb
	}
	if an, ok := toNumber(a); ok {
		bn, ok := toNumber(b)
		if !ok {
			return false
		}
		if an.isInt && bn.isInt {
			return an.i == bn.i
		}
		return an.f == bn.f
	}
	as, aIsString := a.(string)
	bs, bIsString := b.(string)
	return aIsString && bIsString && as == bs
}

// isScalar reports whether v is a string, a bool or a number.
func isScalar(v any) bool {
	switch v.(type) {
	case string, bool:
		return true
	}
	_, ok := toNumber(v)
	return ok
}

// elements returns the elements of a slice or array as []any.
func elements(v any) (elems []any, isArray bool) {
	switch s := v.(type) {
	case []any:
		return s, true
	case []string:
		return convert(s), true
	case []int:
		return convert(s), true
	case []int64:
		return convert(s), true
	case []float64:
		return convert(s), true
	case []bool:
		return convert(s), true
	case nil:
		return nil, false
	}
	rv := reflect.ValueOf(v)
	if rv.Kind() != reflect.Slice && rv.Kind() != reflect.Array {
		return nil, false
	}
	out := make([]any, rv.Len())
	for i := range out {
		out[i] = rv.Index(i).Interface()
	}
	return out, true
}

func convert[T any](s []T) []any {
	out := make([]any, len(s))
	for i, e := range s {
		out[i] = e
	}
	return out
}

// plain converts json.Number to float64, recursively through []any and
// map[string]any, so a value decoded with UseNumber looks like one decoded
// without it.
func plain(v any) any {
	switch x := v.(type) {
	case json.Number:
		f, _ := parseFloat(string(x))
		return f
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = plain(e)
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(x))
		for k, e := range x {
			out[k] = plain(e)
		}
		return out
	}
	return v
}
