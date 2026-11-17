package stats

import (
	"testing"
	"time"
)

func TestPercentileNearestRank(t *testing.T) {
	samples := make([]time.Duration, 0, 100)
	for i := 100; i >= 1; i-- {
		samples = append(samples, time.Duration(i)*time.Millisecond)
	}
	if got := Percentile(samples, 0.99); got != 99*time.Millisecond {
		t.Errorf("p99 = %v, want 99ms", got)
	}
	if got := Percentile(samples, 0.50); got != 50*time.Millisecond {
		t.Errorf("p50 = %v, want 50ms", got)
	}
	if got := Percentile(samples, 1); got != 100*time.Millisecond {
		t.Errorf("p100 = %v, want 100ms", got)
	}
	if got := Percentile(samples, 0); got != time.Millisecond {
		t.Errorf("p0 = %v, want 1ms", got)
	}
	if samples[0] != 100*time.Millisecond {
		t.Errorf("Percentile reordered its input")
	}
	one := []time.Duration{7 * time.Millisecond}
	for _, q := range []float64{0, 0.5, 0.99, 1} {
		if got := Percentile(one, q); got != 7*time.Millisecond {
			t.Errorf("single sample q=%v = %v, want 7ms", q, got)
		}
	}
}

func TestPercentilePanicsOnEmpty(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Error("Percentile of no samples did not panic")
		}
	}()
	Percentile(nil, 0.5)
}

func TestSummarize(t *testing.T) {
	samples := []time.Duration{4 * time.Millisecond, time.Millisecond, 3 * time.Millisecond, 2 * time.Millisecond}
	got := Summarize(samples, 2)
	want := Summary{Count: 4, Errors: 2, P50: 2 * time.Millisecond, P95: 4 * time.Millisecond,
		P99: 4 * time.Millisecond, Max: 4 * time.Millisecond}
	if got != want {
		t.Errorf("Summarize = %+v, want %+v", got, want)
	}
	if got := Summarize(nil, 3); got != (Summary{Errors: 3}) {
		t.Errorf("Summarize(nil) = %+v, want only errors", got)
	}
}

func TestSummaryMetrics(t *testing.T) {
	s := Summary{Count: 10, Errors: 1, P50: 1500 * time.Microsecond, P95: 9 * time.Millisecond,
		P99: 14200 * time.Microsecond, Max: 80 * time.Millisecond}
	got := s.Metrics()
	want := map[string]float64{"count": 10, "errors": 1, "p50_ms": 1.5, "p95_ms": 9, "p99_ms": 14.2, "max_ms": 80}
	if len(got) != len(want) {
		t.Fatalf("Metrics = %v, want %v", got, want)
	}
	for k, v := range want {
		if got[k] != v {
			t.Errorf("Metrics[%q] = %v, want %v", k, got[k], v)
		}
	}
}
