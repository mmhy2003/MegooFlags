package result

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/mmhy2003/MegooFlags/perf/internal/stats"
)

func TestScenarioJSON(t *testing.T) {
	s := stats.Summary{Count: 120000, Errors: 3, P50: 3100 * time.Microsecond, P95: 9 * time.Millisecond,
		P99: 14200 * time.Microsecond, Max: 80 * time.Millisecond}
	m := Metrics(s.Metrics())
	m["rps"] = 2000
	path := filepath.Join(t.TempDir(), "sub", "remote_ofrep.json")
	if err := Write(path, Scenario{Metrics: m, Errors: 3}); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatal(err)
	}
	want := map[string]any{
		"errors": 3.0,
		"metrics": map[string]any{"rps": 2000.0, "count": 120000.0, "errors": 3.0,
			"p50_ms": 3.1, "p95_ms": 9.0, "p99_ms": 14.2, "max_ms": 80.0},
	}
	if !reflect.DeepEqual(raw, want) {
		t.Errorf("JSON = %v, want %v", raw, want)
	}
	var back Scenario
	if err := json.Unmarshal(data, &back); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(back, Scenario{Metrics: m, Errors: 3}) {
		t.Errorf("round trip = %+v", back)
	}
}
