package engine

import (
	"encoding/json"
	"fmt"
	"reflect"
	"testing"
)

// flagJSON is a minimal enabled string flag with variations "on" and "off",
// off_variation "off" and fallthrough "on". fields override or add fields.
func flagJSON(key string, fields map[string]any) map[string]any {
	f := map[string]any{
		"key":     key,
		"type":    "string",
		"enabled": true,
		"version": 1,
		"variations": []any{
			map[string]any{"key": "on", "value": "on"},
			map[string]any{"key": "off", "value": "off"},
		},
		"off_variation": "off",
		"prerequisites": []any{},
		"targets":       []any{},
		"rules":         []any{},
		"fallthrough":   map[string]any{"variation": "on"},
	}
	for k, v := range fields {
		f[k] = v
	}
	return f
}

// buildRuleset runs the flags and segments through Parse.
func buildRuleset(t testing.TB, flags []map[string]any, segments map[string]any) *Ruleset {
	t.Helper()
	fm := map[string]any{}
	for _, f := range flags {
		fm[f["key"].(string)] = f
	}
	if segments == nil {
		segments = map[string]any{}
	}
	data, err := json.Marshal(map[string]any{
		"schema_version": 1, "project": "p", "environment": "e",
		"flags": fm, "segments": segments,
	})
	if err != nil {
		t.Fatal(err)
	}
	rs, err := Parse(data)
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	return rs
}

// ruleFlag is a flag with one rule made of clauses that serves "on", and
// fallthrough "off".
func ruleFlag(key string, clauses ...map[string]any) map[string]any {
	cs := make([]any, len(clauses))
	for i, c := range clauses {
		cs[i] = c
	}
	return flagJSON(key, map[string]any{
		"rules":       []any{map[string]any{"clauses": cs, "serve": map[string]any{"variation": "on"}}},
		"fallthrough": map[string]any{"variation": "off"},
	})
}

func cl(attr, op string, negate bool, values ...any) map[string]any {
	return map[string]any{"attribute": attr, "operator": op, "negate": negate, "values": values}
}

func wantError(t *testing.T, r Result, def any, code ErrorCode) {
	t.Helper()
	if r.Reason != ReasonError || r.ErrorCode != code || r.Value != def || r.Served ||
		r.Variant != "" || r.RuleIndex != -1 || r.ErrorMessage == "" {
		t.Errorf("got %+v, want ERROR %s with default %v", r, code, def)
	}
}

func wantServed(t *testing.T, r Result, variant string, reason Reason) {
	t.Helper()
	if !r.Served || r.Variant != variant || r.Value != variant || r.Reason != reason || r.ErrorCode != ErrNone {
		t.Errorf("got %+v, want %q with %s", r, variant, reason)
	}
}

func TestEvaluateInvalidContext(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{flagJSON("f", nil)}, nil)
	for _, ctx := range []any{[]any{}, "x", nil} {
		wantError(t, Evaluate(rs, "f", ctx, "d", ""), "d", ErrInvalidContext)
	}
}

func TestEvaluateFlagNotFound(t *testing.T) {
	rs := buildRuleset(t, nil, nil)
	wantError(t, Evaluate(rs, "missing", map[string]any{}, "d", ""), "d", ErrFlagNotFound)
}

func TestEvaluateStaticAndDefault(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{
		flagJSON("static", nil),
		ruleFlag("default", cl("plan", "in", false, "pro")),
	}, nil)
	r := Evaluate(rs, "static", map[string]any{}, "d", "string")
	wantServed(t, r, "on", ReasonStatic)
	if r.RuleIndex != -1 || r.Prerequisite != "" {
		t.Errorf("metadata fields = %d, %q", r.RuleIndex, r.Prerequisite)
	}
	wantServed(t, Evaluate(rs, "default", map[string]any{}, "d", ""), "off", ReasonDefault)
}

func TestEvaluateDisabled(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{
		flagJSON("with-off", map[string]any{"enabled": false}),
		flagJSON("no-off", map[string]any{"enabled": false, "off_variation": nil}),
	}, nil)
	wantServed(t, Evaluate(rs, "with-off", map[string]any{}, "d", ""), "off", ReasonDisabled)
	r := Evaluate(rs, "no-off", map[string]any{}, "d", "")
	if r.Served || r.Value != "d" || r.Variant != "" || r.Reason != ReasonDisabled || r.ErrorCode != ErrNone {
		t.Errorf("got %+v, want the default with DISABLED", r)
	}
}

func TestEvaluateTypeMismatch(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{
		flagJSON("s", nil),
		flagJSON("no-off", map[string]any{"enabled": false, "off_variation": nil}),
		flagJSON("n", map[string]any{
			"type":        "number",
			"variations":  []any{map[string]any{"key": "on", "value": 3}, map[string]any{"key": "off", "value": true}},
			"fallthrough": map[string]any{"variation": "on"},
		}),
	}, nil)
	ctx := map[string]any{}
	wantError(t, Evaluate(rs, "s", ctx, false, "boolean"), false, ErrTypeMismatch)
	wantError(t, Evaluate(rs, "s", ctx, 1.0, "number"), 1.0, ErrTypeMismatch)
	if r := Evaluate(rs, "s", ctx, "d", "json"); r.Reason != ReasonStatic {
		t.Errorf("json accepts anything: %+v", r)
	}
	// Not served: the default is returned as is, with no type check.
	if r := Evaluate(rs, "no-off", ctx, "d", "boolean"); r.Reason != ReasonDisabled || r.Value != "d" {
		t.Errorf("got %+v, want DISABLED with the default", r)
	}
	r := Evaluate(rs, "n", ctx, 0.0, "number")
	if r.Reason != ReasonStatic || r.Value != 3.0 || !r.IsInt || r.Int != 3 {
		t.Errorf("number = %+v", r)
	}
	wantError(t, Evaluate(rs, "n", ctx, "d", "string"), "d", ErrTypeMismatch)
}

func TestEvaluatePrerequisiteFailed(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{
		flagJSON("child", map[string]any{"prerequisites": []any{
			map[string]any{"flag": "ok", "variation": "on"},
			map[string]any{"flag": "parent", "variation": "on"},
		}}),
		flagJSON("ok", nil),
		flagJSON("parent", map[string]any{"fallthrough": map[string]any{"variation": "off"}}),
		flagJSON("missing-prereq", map[string]any{"prerequisites": []any{map[string]any{"flag": "nope", "variation": "on"}}}),
		flagJSON("disabled-prereq", map[string]any{"prerequisites": []any{map[string]any{"flag": "dis", "variation": "off"}}}),
		flagJSON("dis", map[string]any{"enabled": false}),
	}, nil)
	ctx := map[string]any{}
	r := Evaluate(rs, "child", ctx, "d", "")
	wantServed(t, r, "off", ReasonPrerequisiteFailed)
	if r.Prerequisite != "parent" || !reflect.DeepEqual(r.Metadata(), map[string]any{"prerequisite": "parent"}) {
		t.Errorf("prerequisite = %q, metadata = %v", r.Prerequisite, r.Metadata())
	}
	if r := Evaluate(rs, "missing-prereq", ctx, "d", ""); r.Reason != ReasonPrerequisiteFailed || r.Prerequisite != "nope" {
		t.Errorf("missing prerequisite flag: %+v", r)
	}
	// A disabled prerequisite fails even when its off variation is the one required.
	if r := Evaluate(rs, "disabled-prereq", ctx, "d", ""); r.Reason != ReasonPrerequisiteFailed || r.Prerequisite != "dis" {
		t.Errorf("disabled prerequisite: %+v", r)
	}
}

func TestEvaluatePrerequisiteServingOffVariation(t *testing.T) {
	// "mid" is enabled but its own prerequisite fails, so it serves its off
	// variation with PREREQUISITE_FAILED; that is not ERROR, so "top" passes.
	rs := buildRuleset(t, []map[string]any{
		flagJSON("top", map[string]any{"prerequisites": []any{map[string]any{"flag": "mid", "variation": "off"}}}),
		flagJSON("mid", map[string]any{"prerequisites": []any{map[string]any{"flag": "nope", "variation": "on"}}}),
	}, nil)
	wantServed(t, Evaluate(rs, "top", map[string]any{}, "d", ""), "on", ReasonDefault)
}

func chain(n int) []map[string]any {
	flags := make([]map[string]any, n)
	for i := range n {
		fields := map[string]any{}
		if i < n-1 {
			fields["prerequisites"] = []any{map[string]any{"flag": fmt.Sprintf("f%d", i+1), "variation": "on"}}
		}
		flags[i] = flagJSON(fmt.Sprintf("f%d", i), fields)
	}
	return flags
}

func TestEvaluatePrerequisiteDepthGuard(t *testing.T) {
	// 33 flags reach depth 32, which is allowed; 34 flags reach depth 33.
	wantServed(t, Evaluate(buildRuleset(t, chain(33), nil), "f0", map[string]any{}, "d", ""), "on", ReasonDefault)
	wantError(t, Evaluate(buildRuleset(t, chain(34), nil), "f0", map[string]any{}, "d", ""), "d", ErrGeneral)
}

func TestEvaluatePrerequisiteCycle(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{
		flagJSON("a", map[string]any{"prerequisites": []any{map[string]any{"flag": "b", "variation": "on"}}}),
		flagJSON("b", map[string]any{"prerequisites": []any{map[string]any{"flag": "a", "variation": "on"}}}),
	}, nil)
	wantError(t, Evaluate(rs, "a", map[string]any{}, "d", ""), "d", ErrGeneral)
}

func TestEvaluateTargets(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{flagJSON("f", map[string]any{
		"targets":     []any{map[string]any{"variation": "on", "values": []any{"42", "alice"}}},
		"fallthrough": map[string]any{"variation": "off"},
	})}, nil)
	wantServed(t, Evaluate(rs, "f", map[string]any{"key": "alice"}, "d", ""), "on", ReasonTargetingMatch)
	// Only a string key is looked up in the targets.
	wantServed(t, Evaluate(rs, "f", map[string]any{"key": 42}, "d", ""), "off", ReasonDefault)
	wantServed(t, Evaluate(rs, "f", map[string]any{"key": int64(42)}, "d", ""), "off", ReasonDefault)
}

func TestEvaluateRuleIndex(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{flagJSON("f", map[string]any{
		"rules": []any{
			map[string]any{"clauses": []any{cl("plan", "in", false, "free")}, "serve": map[string]any{"variation": "off"}},
			map[string]any{"clauses": []any{cl("plan", "in", false, "pro"), cl("age", "gte", false, 18)}, "serve": map[string]any{"variation": "on"}},
		},
		"fallthrough": map[string]any{"variation": "off"},
	})}, nil)
	r := Evaluate(rs, "f", map[string]any{"plan": "pro", "age": 18}, "d", "")
	wantServed(t, r, "on", ReasonTargetingMatch)
	if r.RuleIndex != 1 || !reflect.DeepEqual(r.Metadata(), map[string]any{"rule_index": 1}) {
		t.Errorf("rule index = %d, metadata = %v", r.RuleIndex, r.Metadata())
	}
	// Not every clause matches: the fallthrough.
	wantServed(t, Evaluate(rs, "f", map[string]any{"plan": "pro", "age": 17}, "d", ""), "off", ReasonDefault)
}

func TestEvaluateClauseNegate(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{
		ruleFlag("lt", cl("age", "lt", true, 10)),
		ruleFlag("in", cl("tags", "in", true, "a")),
	}, nil)
	cases := []struct {
		name, flag string
		ctx        map[string]any
		want       string
	}{
		{"missing attribute stays false", "lt", map[string]any{}, "off"},
		{"null attribute stays false", "lt", map[string]any{"age": nil}, "off"},
		{"wrong type is true", "lt", map[string]any{"age": "x"}, "on"},
		{"object is the wrong type", "lt", map[string]any{"age": map[string]any{"a": 1}}, "on"},
		{"matching value is false", "lt", map[string]any{"age": 5}, "off"},
		{"empty array is true", "in", map[string]any{"tags": []any{}}, "on"},
		{"array with a match is false", "in", map[string]any{"tags": []string{"b", "a"}}, "off"},
		{"array without a match is true", "in", map[string]any{"tags": []any{"b", nil}}, "on"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if r := Evaluate(rs, c.flag, c.ctx, "d", ""); r.Variant != c.want {
				t.Errorf("got %+v, want %q", r, c.want)
			}
		})
	}
}

func TestEvaluateOperators(t *testing.T) {
	cases := []struct {
		name  string
		cl    map[string]any
		value any
		want  bool
	}{
		{"in number", cl("a", "in", false, 1, "x"), 1.0, true},
		{"in bool is not a number", cl("a", "in", false, 1), true, false},
		{"not_in scalar", cl("a", "not_in", false, "x"), "y", true},
		{"not_in equal", cl("a", "not_in", false, "x"), "x", false},
		{"not_in object", cl("a", "not_in", false, "x"), map[string]any{}, false},
		{"contains", cl("a", "contains", false, 1, "ell"), "hello", true},
		{"contains non-string", cl("a", "contains", false, "1"), 1, false},
		{"starts_with", cl("a", "starts_with", false, "he"), "hello", true},
		{"ends_with", cl("a", "ends_with", false, "lo"), "hello", true},
		{"ends_with miss", cl("a", "ends_with", false, "he"), "hello", false},
		{"matches", cl("a", "matches", false, "(", "^h.l"), "hello", true},
		{"matches non-string", cl("a", "matches", false, "1"), 1, false},
		{"lt", cl("a", "lt", false, 10), 9.5, true},
		{"lte", cl("a", "lte", false, 10), int64(10), true},
		{"gt", cl("a", "gt", false, 10), 10, false},
		{"gte", cl("a", "gte", false, "x", 10), uint8(10), true},
		{"lt bool", cl("a", "lt", false, 10), false, false},
		{"semver_eq build ignored", cl("a", "semver_eq", false, "1.2.3"), "1.2.3+b", true},
		{"semver_lt", cl("a", "semver_lt", false, "1.2.3"), "1.2.3-rc.1", true},
		{"semver_gt", cl("a", "semver_gt", false, "1.2.3"), "1.10.0", true},
		{"semver invalid element", cl("a", "semver_eq", false, "1.2.3"), "1.2", false},
		{"before string", cl("a", "before", false, "2025-01-01T00:00:00Z"), "2024-12-31T23:59:59Z", true},
		{"before number", cl("a", "before", false, "1970-01-01T00:00:01Z"), 999.9, true},
		{"after", cl("a", "after", false, "1970-01-01T00:00:01Z"), 1000, false},
		{"after number value never matches", cl("a", "after", false, 0), 1000, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			rs := buildRuleset(t, []map[string]any{ruleFlag("f", c.cl)}, nil)
			r := Evaluate(rs, "f", map[string]any{"a": c.value}, "d", "")
			if got := r.Variant == "on"; got != c.want {
				t.Errorf("got %+v, want match %v", r, c.want)
			}
		})
	}
}

func TestEvaluateSegments(t *testing.T) {
	segments := map[string]any{
		"s": map[string]any{
			"included": []any{"alice", "bob"},
			"excluded": []any{"bob"},
			"rules":    []any{map[string]any{"clauses": []any{cl("plan", "in", false, "pro")}}},
		},
	}
	rs := buildRuleset(t, []map[string]any{
		ruleFlag("f", cl("", "in_segment", false, "nope", "s")),
		ruleFlag("unknown", cl("", "in_segment", false, "nope")),
		ruleFlag("not-unknown", cl("", "in_segment", true, "nope")),
	}, segments)
	cases := []struct {
		name, flag string
		ctx        map[string]any
		want       string
	}{
		{"included", "f", map[string]any{"key": "alice"}, "on"},
		{"excluded wins over included", "f", map[string]any{"key": "bob", "plan": "pro"}, "off"},
		{"rule", "f", map[string]any{"key": "carol", "plan": "pro"}, "on"},
		{"rule without a key", "f", map[string]any{"plan": "pro"}, "on"},
		{"a numeric key is not looked up", "f", map[string]any{"key": 1, "plan": "pro"}, "on"},
		{"no match", "f", map[string]any{"key": "carol"}, "off"},
		{"unknown segment", "unknown", map[string]any{"key": "alice"}, "off"},
		{"negated unknown segment", "not-unknown", map[string]any{"key": "alice"}, "on"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if r := Evaluate(rs, c.flag, c.ctx, "d", ""); r.Variant != c.want {
				t.Errorf("got %+v, want %q", r, c.want)
			}
		})
	}
}

func TestEvaluateRollout(t *testing.T) {
	weights := []any{
		map[string]any{"variation": "on", "weight": 50000},
		map[string]any{"variation": "off", "weight": 50000},
	}
	rs := buildRuleset(t, []map[string]any{
		flagJSON("split", map[string]any{"fallthrough": map[string]any{
			"rollout": map[string]any{"bucket_by": "id", "weights": weights},
		}}),
		flagJSON("zero-first", map[string]any{"fallthrough": map[string]any{
			"rollout": map[string]any{"bucket_by": "id", "weights": []any{
				map[string]any{"variation": "on", "weight": 0},
				map[string]any{"variation": "off", "weight": 100000},
			}},
		}}),
	}, nil)
	want := pickVariation(rs.Flags["split"].Fallthrough.Rollout.Weights, Bucket("split", "42"))
	for _, id := range []any{"42", 42, int64(42), 42.0, json.Number("42")} {
		r := Evaluate(rs, "split", map[string]any{"id": id}, "d", "")
		if r.Variant != want || r.Reason != ReasonSplit || !r.Served {
			t.Errorf("id %#v: got %+v, want %q with SPLIT", id, r, want)
		}
	}
	// A missing or unbucketable attribute is bucket 0: the first non-zero weight.
	for _, ctx := range []map[string]any{{}, {"id": 1.5}, {"id": true}} {
		wantServed(t, Evaluate(rs, "zero-first", ctx, "d", ""), "off", ReasonSplit)
	}
}

func TestResultMetadata(t *testing.T) {
	cases := []struct {
		r    Result
		want map[string]any
	}{
		{Result{RuleIndex: -1}, map[string]any{}},
		{Result{RuleIndex: 0}, map[string]any{"rule_index": 0}},
		{Result{RuleIndex: -1, Prerequisite: "p"}, map[string]any{"prerequisite": "p"}},
	}
	for _, c := range cases {
		if got := c.r.Metadata(); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%+v: got %v, want %v", c.r, got, c.want)
		}
	}
}

func TestEvaluateNoAllocs(t *testing.T) {
	rs := buildRuleset(t, []map[string]any{ruleFlag("f", cl("plan", "in", false, "free", "pro"))}, nil)
	ctx := map[string]any{"key": "alice", "plan": "pro"}
	var r Result
	allocs := testing.AllocsPerRun(100, func() { r = Evaluate(rs, "f", ctx, "d", "string") })
	if r.Variant != "on" {
		t.Fatalf("got %+v", r)
	}
	if allocs != 0 {
		t.Errorf("Evaluate allocated %v times per run, want 0", allocs)
	}
}
