// Package result writes scenario results in the format perf.sh merges.
package result

import (
	"encoding/json"
	"os"
	"path/filepath"
)

// Metrics are a scenario's measurements, keyed as in perf/results/<version>.json.
type Metrics map[string]float64

// Scenario is what one mfload command writes.
type Scenario struct {
	Metrics Metrics `json:"metrics"`
	Errors  int     `json:"errors"`
}

// Write writes s as indented JSON to path, creating its directory.
func Write(path string, s Scenario) error {
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}
