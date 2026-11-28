package engine

import (
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"slices"
	"testing"
	"time"
)

func TestEvaluateP99(t *testing.T) {
	if raceEnabled || testing.Short() {
		t.Skip("timing is not meaningful under -race or -short")
	}
	const n = 10000
	rs, err := Parse(genRuleset(n))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	keys := flagKeys(n)
	ctxs := benchContexts()
	rng := rand.New(rand.NewPCG(1, 2))
	for range 10000 {
		Evaluate(rs, keys[rng.IntN(n)], ctxs[rng.IntN(len(ctxs))], nil, "")
	}
	samples := make([]time.Duration, 200000)
	for i := range samples {
		key, ctx := keys[rng.IntN(n)], ctxs[rng.IntN(len(ctxs))]
		start := time.Now()
		Evaluate(rs, key, ctx, nil, "")
		samples[i] = time.Since(start)
	}
	slices.Sort(samples)
	p50 := samples[len(samples)/2]
	p99 := samples[len(samples)*99/100]
	t.Logf("p50 %v, p99 %v, max %v", p50, p99, samples[len(samples)-1])
	if p99 >= time.Millisecond {
		t.Errorf("p99 %v, want under 1ms", p99)
	}
}

const benchSegments = 50

// genRuleset is a deterministic ruleset of n flags and benchSegments
// segments. Even flags are booleans with three rules (in, matches,
// semver_gt), a 100-key target list and a three-way fallthrough rollout; odd
// flags are strings with an in_segment rule and a before rule. Every 10th
// flag has a prerequisite on the flag before it.
func genRuleset(n int) []byte {
	plans := make([]any, 20)
	for i := range plans {
		plans[i] = fmt.Sprintf("plan-%d", i)
	}
	flags := make(map[string]any, n)
	for i, key := range flagKeys(n) {
		var f map[string]any
		if i%2 == 0 {
			targets := make([]any, 100)
			for k := range targets {
				targets[k] = fmt.Sprintf("user-%d", (i+k*37)%1000)
			}
			f = flagJSON(key, map[string]any{
				"type": "boolean",
				"variations": []any{
					map[string]any{"key": "on", "value": true},
					map[string]any{"key": "off", "value": false},
					map[string]any{"key": "alt", "value": false},
				},
				"targets": []any{map[string]any{"variation": "alt", "values": targets}},
				"rules": []any{
					benchRule("on", cl("plan", "in", false, plans...)),
					benchRule("off", cl("email", "matches", false, "^user-[0-9]+$")),
					benchRule("alt", cl("app_version", "semver_gt", false, "1.5.0")),
				},
				"fallthrough": map[string]any{"rollout": map[string]any{
					"bucket_by": "key",
					"weights": []any{
						map[string]any{"variation": "on", "weight": 33334},
						map[string]any{"variation": "off", "weight": 33333},
						map[string]any{"variation": "alt", "weight": 33333},
					},
				}},
			})
		} else {
			f = flagJSON(key, map[string]any{
				"variations": []any{
					map[string]any{"key": "on", "value": "on"},
					map[string]any{"key": "off", "value": "off"},
					map[string]any{"key": "alt", "value": "alt"},
				},
				"rules": []any{
					benchRule("alt", cl("key", "in_segment", false, fmt.Sprintf("segment-%d", i%benchSegments))),
					benchRule("off", cl("signed_up", "before", false, "2025-01-01T00:00:00Z")),
				},
			})
		}
		if i > 0 && i%10 == 0 {
			f["prerequisites"] = []any{map[string]any{"flag": fmt.Sprintf("flag-%d", i-1), "variation": "on"}}
		}
		flags[key] = f
	}
	segments := make(map[string]any, benchSegments)
	for s := range benchSegments {
		included := make([]any, 1000)
		for k := range included {
			included[k] = fmt.Sprintf("user-%d", s*1000+k)
		}
		segments[fmt.Sprintf("segment-%d", s)] = map[string]any{
			"included": included,
			"excluded": []any{},
			"rules":    []any{map[string]any{"clauses": []any{cl("org", "in", false, fmt.Sprintf("org-%d", s))}}},
		}
	}
	data, err := json.Marshal(map[string]any{
		"schema_version": 1, "project": "bench", "environment": "bench",
		"flags": flags, "segments": segments,
	})
	if err != nil {
		panic(err)
	}
	return data
}

func benchRule(variation string, clauses ...map[string]any) map[string]any {
	cs := make([]any, len(clauses))
	for i, c := range clauses {
		cs[i] = c
	}
	return map[string]any{"description": "", "clauses": cs, "serve": map[string]any{"variation": variation}}
}

func flagKeys(n int) []string {
	keys := make([]string, n)
	for i := range keys {
		keys[i] = fmt.Sprintf("flag-%d", i)
	}
	return keys
}

// benchContexts are 100 contexts that hit every rule shape of genRuleset.
func benchContexts() []map[string]any {
	ctxs := make([]map[string]any, 100)
	for j := range ctxs {
		email := fmt.Sprintf("user%d@example.com", j)
		if j%3 == 0 {
			email = fmt.Sprintf("user-%d", j)
		}
		ctxs[j] = map[string]any{
			"key":         fmt.Sprintf("user-%d", j*503),
			"plan":        fmt.Sprintf("plan-%d", j%40),
			"email":       email,
			"app_version": fmt.Sprintf("1.%d.0", j%10),
			"org":         fmt.Sprintf("org-%d", j%(2*benchSegments)),
		}
	}
	return ctxs
}

func BenchmarkEvaluate(b *testing.B) {
	const n = 10000
	rs, err := Parse(genRuleset(n))
	if err != nil {
		b.Fatalf("Parse: %v", err)
	}
	keys := flagKeys(n)
	ctxs := benchContexts()
	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; b.Loop(); i++ {
		Evaluate(rs, keys[i%n], ctxs[i%len(ctxs)], nil, "")
	}
}

func TestParse10000Flags(t *testing.T) {
	data := genRuleset(10000)
	start := time.Now()
	if _, err := Parse(data); err != nil {
		t.Fatalf("Parse: %v", err)
	}
	t.Logf("parsed %d bytes in %v", len(data), time.Since(start))
}
