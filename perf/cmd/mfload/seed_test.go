package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/mmhy2003/MegooFlags/perf/internal/result"
	"github.com/mmhy2003/MegooFlags/perf/internal/seed"
)

func TestSeedFileHasKeysAndScenario(t *testing.T) {
	path := filepath.Join(t.TempDir(), "out", "seed.json")
	output := seed.Output{Project: "perf", Env: "development", ServerKey: "mf_server_a", EvalProject: "perf-eval", EvalKey: "mf_server_b", Flag: "flag-00000"}
	if err := writeSeedFile(path, output, result.Metrics{"ruleset_bytes": 42}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}
	var keys []string
	for k := range got {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	want := []string{"env", "errors", "eval_key", "eval_project", "flag", "metrics", "project", "server_key"}
	if !slices.Equal(keys, want) {
		t.Errorf("keys %v, want %v", keys, want)
	}
	if got["server_key"] != "mf_server_a" || got["eval_key"] != "mf_server_b" || got["errors"] != float64(0) {
		t.Errorf("file %v", got)
	}
	if m := got["metrics"].(map[string]any); m["ruleset_bytes"] != float64(42) {
		t.Errorf("metrics %v", m)
	}
}
