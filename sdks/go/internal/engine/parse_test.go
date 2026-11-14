package engine

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestParseValid(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "spec", "conformance", "cases", "reasons.json"))
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Ruleset json.RawMessage `json:"ruleset"`
	}
	if err := json.Unmarshal(data, &file); err != nil {
		t.Fatal(err)
	}
	rs, err := Parse(file.Ruleset)
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if rs.Project != "conformance" || rs.Environment != "test" {
		t.Errorf("project, environment = %q, %q", rs.Project, rs.Environment)
	}
	if len(rs.Flags) != 7 || len(rs.Segments) != 0 {
		t.Fatalf("got %d flags and %d segments", len(rs.Flags), len(rs.Segments))
	}

	f := rs.Flags["with-rule"]
	if f.Key != "with-rule" || f.Type != "string" || !f.Enabled || f.Version != 1 {
		t.Errorf("with-rule = %+v", f)
	}
	if !f.HasOffVariation || f.OffVariation != "off" {
		t.Errorf("off variation = %q, %v", f.OffVariation, f.HasOffVariation)
	}
	if v := f.Variations["on"]; v == nil || v.Key != "on" || v.Value != "on" {
		t.Errorf("variation on = %+v", v)
	}
	if len(f.Rules) != 1 || f.Rules[0].Description != "pro plan" || f.Rules[0].Serve.Variation != "on" {
		t.Fatalf("rules = %+v", f.Rules)
	}
	c := f.Rules[0].Clauses[0]
	if c.Attribute != "plan" || c.Operator != "in" || c.Negate || !reflect.DeepEqual(c.Values, []any{"pro"}) {
		t.Errorf("clause = %+v", c)
	}
	if !reflect.DeepEqual(c.equal, []any{"pro"}) {
		t.Errorf("prepared equal = %#v", c.equal)
	}
	if f.Fallthrough.Variation != "off" || f.Fallthrough.Rollout != nil {
		t.Errorf("fallthrough = %+v", f.Fallthrough)
	}

	target := rs.Flags["with-target"].Targets
	if len(target) != 1 || target[0].Variation != "on" {
		t.Fatalf("targets = %+v", target)
	}
	if _, ok := target[0].Values["alice"]; !ok || len(target[0].Values) != 1 {
		t.Errorf("target values = %v", target[0].Values)
	}

	if f := rs.Flags["disabled-no-off"]; f.HasOffVariation || f.OffVariation != "" || f.Enabled {
		t.Errorf("disabled-no-off = %+v", f)
	}
	if p := rs.Flags["prerequisite-no-off"].Prerequisites; !reflect.DeepEqual(p, []Prerequisite{{Flag: "ghost", Variation: "on"}}) {
		t.Errorf("prerequisites = %+v", p)
	}
	null := rs.Flags["null-value"]
	if v := null.Variations["none"]; v == nil || v.Value != nil {
		t.Errorf("null variation = %+v", v)
	}
	if v := null.Variations["some"]; v == nil || !reflect.DeepEqual(v.Value, map[string]any{"a": 1.0}) {
		t.Errorf("object variation = %+v", v)
	}
}

func TestParseStructures(t *testing.T) {
	rs, err := Parse(mustJSON(t, baseRuleset()))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	f := rs.Flags["f"]
	ro := f.Fallthrough.Rollout
	if ro == nil || ro.BucketBy != "key" || !reflect.DeepEqual(ro.Weights, []Weight{{"on", 50000}, {"off", 50000}}) {
		t.Errorf("rollout = %+v", ro)
	}
	if f.Rules[0].Description != "" {
		t.Errorf("default description = %q", f.Rules[0].Description)
	}
	s := rs.Segments["s"]
	if _, ok := s.Included["a"]; !ok || len(s.Included) != 1 {
		t.Errorf("included = %v", s.Included)
	}
	if _, ok := s.Excluded["b"]; !ok || len(s.Excluded) != 1 {
		t.Errorf("excluded = %v", s.Excluded)
	}
	if len(s.Rules) != 1 || len(s.Rules[0]) != 1 || s.Rules[0][0].Attribute != "country" {
		t.Errorf("segment rules = %+v", s.Rules)
	}
}

func TestParseVariationInt(t *testing.T) {
	tests := []struct {
		value json.RawMessage
		want  any
		i     int64
		isInt bool
	}{
		{json.RawMessage(`42`), 42.0, 42, true},
		{json.RawMessage(`9007199254740993`), 9007199254740993.0, 9007199254740993, true},
		{json.RawMessage(`3.0`), 3.0, 3, true},
		{json.RawMessage(`-2e3`), -2000.0, -2000, true},
		{json.RawMessage(`1.5`), 1.5, 0, false},
		{json.RawMessage(`1e300`), 1e300, 0, false},
		{json.RawMessage(`"7"`), "7", 0, false},
		{json.RawMessage(`true`), true, 0, false},
	}
	for _, tt := range tests {
		t.Run(string(tt.value), func(t *testing.T) {
			root := baseRuleset()
			flag(root)["variations"] = []any{map[string]any{"key": "on", "value": tt.value}, map[string]any{"key": "off", "value": 0}}
			rs, err := Parse(mustJSON(t, root))
			if err != nil {
				t.Fatalf("Parse: %v", err)
			}
			v := rs.Flags["f"].Variations["on"]
			if !reflect.DeepEqual(v.Value, tt.want) || v.Int != tt.i || v.IsInt != tt.isInt {
				t.Errorf("variation = %#v", v)
			}
		})
	}
}

func TestParseErrors(t *testing.T) {
	tests := []struct {
		name   string
		raw    string
		mutate func(root map[string]any)
		path   string
	}{
		{name: "invalid JSON", raw: `{"schema_version": 1,`, path: "ruleset"},
		{name: "trailing data", raw: `{} {}`, path: "ruleset"},
		{name: "non-object root", raw: `[]`, path: "ruleset"},
		{name: "schema_version 2", mutate: func(r map[string]any) { r["schema_version"] = 2 }, path: "schema_version"},
		{name: "schema_version 1.0", mutate: func(r map[string]any) { r["schema_version"] = json.Number("1.0") }, path: "schema_version"},
		{name: "schema_version string", mutate: func(r map[string]any) { r["schema_version"] = "1" }, path: "schema_version"},
		{name: "missing schema_version", mutate: func(r map[string]any) { delete(r, "schema_version") }, path: "schema_version"},
		{name: "missing project", mutate: func(r map[string]any) { delete(r, "project") }, path: "project"},
		{name: "environment not a string", mutate: func(r map[string]any) { r["environment"] = 1 }, path: "environment"},
		{name: "flags not an object", mutate: func(r map[string]any) { r["flags"] = []any{} }, path: "flags"},
		{name: "segments missing", mutate: func(r map[string]any) { delete(r, "segments") }, path: "segments"},
		{name: "flag not an object", mutate: func(r map[string]any) { r["flags"].(map[string]any)["f"] = 1 }, path: "flags.f"},
		{name: "flag key mismatch", mutate: func(r map[string]any) { flag(r)["key"] = "g" }, path: "flags.f.key"},
		{name: "missing flag key", mutate: func(r map[string]any) { delete(flag(r), "key") }, path: "flags.f.key"},
		{name: "bad type", mutate: func(r map[string]any) { flag(r)["type"] = "int" }, path: "flags.f.type"},
		{name: "enabled yes", mutate: func(r map[string]any) { flag(r)["enabled"] = "yes" }, path: "flags.f.enabled"},
		{name: "version 1.5", mutate: func(r map[string]any) { flag(r)["version"] = 1.5 }, path: "flags.f.version"},
		{name: "version true", mutate: func(r map[string]any) { flag(r)["version"] = true }, path: "flags.f.version"},
		{name: "empty variations", mutate: func(r map[string]any) { flag(r)["variations"] = []any{} }, path: "flags.f.variations"},
		{name: "variation without value", mutate: func(r map[string]any) {
			flag(r)["variations"] = []any{map[string]any{"key": "on"}}
		}, path: "flags.f.variations[0].value"},
		{name: "variation key not a string", mutate: func(r map[string]any) {
			flag(r)["variations"] = []any{map[string]any{"key": 1, "value": true}}
		}, path: "flags.f.variations[0].key"},
		{name: "duplicate variation", mutate: func(r map[string]any) {
			flag(r)["variations"] = []any{map[string]any{"key": "on", "value": true}, map[string]any{"key": "on", "value": false}}
		}, path: "flags.f.variations[1].key"},
		{name: "unknown off_variation", mutate: func(r map[string]any) { flag(r)["off_variation"] = "nope" }, path: "flags.f.off_variation"},
		{name: "missing off_variation", mutate: func(r map[string]any) { delete(flag(r), "off_variation") }, path: "flags.f.off_variation"},
		{name: "prerequisite flag not a string", mutate: func(r map[string]any) {
			flag(r)["prerequisites"] = []any{map[string]any{"flag": 1, "variation": "on"}}
		}, path: "flags.f.prerequisites[0].flag"},
		{name: "unknown target variation", mutate: func(r map[string]any) {
			flag(r)["targets"] = []any{map[string]any{"variation": "nope", "values": []any{"a"}}}
		}, path: "flags.f.targets[0].variation"},
		{name: "target value not a string", mutate: func(r map[string]any) {
			flag(r)["targets"] = []any{map[string]any{"variation": "on", "values": []any{"a", 2}}}
		}, path: "flags.f.targets[0].values[1]"},
		{name: "missing rules", mutate: func(r map[string]any) { delete(flag(r), "rules") }, path: "flags.f.rules"},
		{name: "missing fallthrough", mutate: func(r map[string]any) { delete(flag(r), "fallthrough") }, path: "flags.f.fallthrough"},
		{name: "description not a string", mutate: func(r map[string]any) { rule(r)["description"] = nil }, path: "flags.f.rules[0].description"},
		{name: "serve with both", mutate: func(r map[string]any) {
			flag(r)["fallthrough"] = map[string]any{"variation": "on", "rollout": map[string]any{}}
		}, path: "flags.f.fallthrough"},
		{name: "serve with neither", mutate: func(r map[string]any) { rule(r)["serve"] = map[string]any{} }, path: "flags.f.rules[0].serve"},
		{name: "unknown serve variation", mutate: func(r map[string]any) {
			rule(r)["serve"] = map[string]any{"variation": "nope"}
		}, path: "flags.f.rules[0].serve.variation"},
		{name: "bucket_by not a string", mutate: func(r map[string]any) { rollout(r)["bucket_by"] = nil }, path: "flags.f.fallthrough.rollout.bucket_by"},
		{name: "empty weights", mutate: func(r map[string]any) { rollout(r)["weights"] = []any{} }, path: "flags.f.fallthrough.rollout.weights"},
		{name: "weights summing to 99999", mutate: func(r map[string]any) {
			rollout(r)["weights"] = []any{map[string]any{"variation": "on", "weight": 50000}, map[string]any{"variation": "off", "weight": 49999}}
		}, path: "flags.f.fallthrough.rollout.weights"},
		{name: "weight of 100001", mutate: func(r map[string]any) {
			rollout(r)["weights"] = []any{map[string]any{"variation": "on", "weight": 100001}}
		}, path: "flags.f.fallthrough.rollout.weights[0].weight"},
		{name: "negative weight", mutate: func(r map[string]any) {
			rollout(r)["weights"] = []any{map[string]any{"variation": "on", "weight": -1}}
		}, path: "flags.f.fallthrough.rollout.weights[0].weight"},
		{name: "float weight", mutate: func(r map[string]any) {
			rollout(r)["weights"] = []any{map[string]any{"variation": "on", "weight": json.Number("100000.0")}}
		}, path: "flags.f.fallthrough.rollout.weights[0].weight"},
		{name: "unknown weight variation", mutate: func(r map[string]any) {
			rollout(r)["weights"] = []any{map[string]any{"variation": "nope", "weight": 100000}}
		}, path: "flags.f.fallthrough.rollout.weights[0].variation"},
		{name: "empty clauses", mutate: func(r map[string]any) { rule(r)["clauses"] = []any{} }, path: "flags.f.rules[0].clauses"},
		{name: "attribute not a string", mutate: func(r map[string]any) { clause(r)["attribute"] = 1 }, path: "flags.f.rules[0].clauses[0].attribute"},
		{name: "unknown operator", mutate: func(r map[string]any) { clause(r)["operator"] = "like" }, path: "flags.f.rules[0].clauses[0].operator"},
		{name: "empty clause values", mutate: func(r map[string]any) { clause(r)["values"] = []any{} }, path: "flags.f.rules[0].clauses[0].values"},
		{name: "null clause value", mutate: func(r map[string]any) { clause(r)["values"] = []any{"a", nil} }, path: "flags.f.rules[0].clauses[0].values[1]"},
		{name: "array clause value", mutate: func(r map[string]any) { clause(r)["values"] = []any{[]any{}} }, path: "flags.f.rules[0].clauses[0].values[0]"},
		{name: "object clause value", mutate: func(r map[string]any) { clause(r)["values"] = []any{map[string]any{}} }, path: "flags.f.rules[0].clauses[0].values[0]"},
		{name: "negate not a bool", mutate: func(r map[string]any) { clause(r)["negate"] = "no" }, path: "flags.f.rules[0].clauses[0].negate"},
		{name: "in_segment in a segment rule", mutate: func(r map[string]any) {
			segment(r)["rules"] = []any{map[string]any{"clauses": []any{map[string]any{"attribute": "key", "operator": "in_segment", "values": []any{"s"}}}}}
		}, path: "segments.s.rules[0].clauses[0].operator"},
		{name: "segment included [1]", mutate: func(r map[string]any) { segment(r)["included"] = []any{1} }, path: "segments.s.included[0]"},
		{name: "segment excluded missing", mutate: func(r map[string]any) { delete(segment(r), "excluded") }, path: "segments.s.excluded"},
		{name: "segment rule not an object", mutate: func(r map[string]any) { segment(r)["rules"] = []any{1} }, path: "segments.s.rules[0]"},
		{name: "segment rule empty clauses", mutate: func(r map[string]any) {
			segment(r)["rules"] = []any{map[string]any{"clauses": []any{}}}
		}, path: "segments.s.rules[0].clauses"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			data := []byte(tt.raw)
			if tt.mutate != nil {
				root := baseRuleset()
				tt.mutate(root)
				data = mustJSON(t, root)
			}
			rs, err := Parse(data)
			if rs != nil {
				t.Errorf("Parse returned a ruleset with error %v", err)
			}
			var pe *ParseError
			if !errors.As(err, &pe) {
				t.Fatalf("Parse error = %v, want *ParseError", err)
			}
			if pe.Path != tt.path {
				t.Errorf("Path = %q, want %q (%s)", pe.Path, tt.path, pe.Msg)
			}
			if want := "PARSE_ERROR: " + pe.Path + ": " + pe.Msg; err.Error() != want {
				t.Errorf("Error() = %q, want %q", err.Error(), want)
			}
		})
	}
}

func TestParsePrepared(t *testing.T) {
	tests := []struct {
		operator string
		values   []any
		check    func(t *testing.T, c Clause)
	}{
		{"matches", []any{"(", "^a"}, func(t *testing.T, c Clause) {
			if len(c.regexes) != 1 || !c.regexes[0].MatchString("abc") {
				t.Errorf("regexes = %v", c.regexes)
			}
		}},
		{"matches", []any{1, true}, func(t *testing.T, c Clause) {
			if len(c.regexes) != 0 {
				t.Errorf("regexes = %v", c.regexes)
			}
		}},
		{"semver_eq", []any{"x", "1.0.0"}, func(t *testing.T, c Clause) {
			want, _ := ParseSemver("1.0.0")
			if len(c.semvers) != 1 || CompareSemver(c.semvers[0], want) != 0 {
				t.Errorf("semvers = %v", c.semvers)
			}
		}},
		{"before", []any{123, "2026-01-01T00:00:00Z"}, func(t *testing.T, c Clause) {
			if !reflect.DeepEqual(c.times, []int64{1767225600000}) {
				t.Errorf("times = %v", c.times)
			}
		}},
		{"after", []any{"not a date"}, func(t *testing.T, c Clause) {
			if len(c.times) != 0 {
				t.Errorf("times = %v", c.times)
			}
		}},
		{"contains", []any{"a", 1, true}, func(t *testing.T, c Clause) {
			if !reflect.DeepEqual(c.strs, []string{"a"}) {
				t.Errorf("strs = %v", c.strs)
			}
		}},
		{"starts_with", []any{"b"}, func(t *testing.T, c Clause) {
			if !reflect.DeepEqual(c.strs, []string{"b"}) {
				t.Errorf("strs = %v", c.strs)
			}
		}},
		{"lt", []any{"5", 5, json.Number("2.5"), false}, func(t *testing.T, c Clause) {
			if !reflect.DeepEqual(c.nums, []number{intNumber(5), {f: 2.5}}) {
				t.Errorf("nums = %v", c.nums)
			}
		}},
		{"in", []any{"a", json.Number("9007199254740993"), json.Number("1.5"), true}, func(t *testing.T, c Clause) {
			want := []any{"a", int64(9007199254740993), 1.5, true}
			if !reflect.DeepEqual(c.equal, want) {
				t.Errorf("equal = %#v, want %#v", c.equal, want)
			}
		}},
		{"in_segment", []any{"s", 1}, func(t *testing.T, c Clause) {
			if !reflect.DeepEqual(c.segments, []string{"s"}) {
				t.Errorf("segments = %v", c.segments)
			}
		}},
	}
	for _, tt := range tests {
		t.Run(tt.operator, func(t *testing.T) {
			root := baseRuleset()
			clause(root)["operator"] = tt.operator
			clause(root)["values"] = tt.values
			rs, err := Parse(mustJSON(t, root))
			if err != nil {
				t.Fatalf("Parse: %v", err)
			}
			tt.check(t, rs.Flags["f"].Rules[0].Clauses[0])
		})
	}
}

func TestParseIgnoresUnknownFields(t *testing.T) {
	root := baseRuleset()
	root["extra"] = map[string]any{"x": 1}
	flag(root)["owner"] = "team"
	rule(root)["id"] = 7
	clause(root)["comment"] = nil
	rollout(root)["seed"] = 3
	segment(root)["name"] = "S"
	rs, err := Parse(mustJSON(t, root))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if rs.Flags["f"] == nil || rs.Segments["s"] == nil {
		t.Fatalf("ruleset = %+v", rs)
	}
}

func TestParseNegate(t *testing.T) {
	root := baseRuleset()
	clause(root)["negate"] = true
	rs, err := Parse(mustJSON(t, root))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if !rs.Flags["f"].Rules[0].Clauses[0].Negate {
		t.Error("negate = false, want true")
	}
}

// baseRuleset is a valid ruleset with one flag "f" and one segment "s".
func baseRuleset() map[string]any {
	return map[string]any{
		"schema_version": 1,
		"project":        "p",
		"environment":    "e",
		"flags": map[string]any{
			"f": map[string]any{
				"key": "f", "type": "boolean", "enabled": true, "version": 3,
				"variations": []any{
					map[string]any{"key": "on", "value": true},
					map[string]any{"key": "off", "value": false},
				},
				"off_variation": "off",
				"prerequisites": []any{},
				"targets":       []any{map[string]any{"variation": "on", "values": []any{"alice"}}},
				"rules": []any{map[string]any{
					"clauses": []any{map[string]any{"attribute": "plan", "operator": "in", "values": []any{"pro"}}},
					"serve":   map[string]any{"variation": "on"},
				}},
				"fallthrough": map[string]any{"rollout": map[string]any{
					"bucket_by": "key",
					"weights": []any{
						map[string]any{"variation": "on", "weight": 50000},
						map[string]any{"variation": "off", "weight": 50000},
					},
				}},
			},
		},
		"segments": map[string]any{
			"s": map[string]any{
				"included": []any{"a"},
				"excluded": []any{"b"},
				"rules": []any{map[string]any{
					"clauses": []any{map[string]any{"attribute": "country", "operator": "in", "values": []any{"US"}}},
				}},
			},
		},
	}
}

func flag(root map[string]any) map[string]any {
	return root["flags"].(map[string]any)["f"].(map[string]any)
}

func rule(root map[string]any) map[string]any {
	return flag(root)["rules"].([]any)[0].(map[string]any)
}

func clause(root map[string]any) map[string]any {
	return rule(root)["clauses"].([]any)[0].(map[string]any)
}

func rollout(root map[string]any) map[string]any {
	return flag(root)["fallthrough"].(map[string]any)["rollout"].(map[string]any)
}

func segment(root map[string]any) map[string]any {
	return root["segments"].(map[string]any)["s"].(map[string]any)
}

func mustJSON(t *testing.T, v any) []byte {
	t.Helper()
	data, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return data
}
