// Package stats computes latency percentiles and summaries.
package stats

import (
	"math"
	"slices"
	"time"
)

// Percentile returns the nearest-rank percentile: the ceil(q*n)-th smallest
// sample, the same rule as scripts/latency.py. It panics on no samples.
func Percentile(samples []time.Duration, q float64) time.Duration {
	if len(samples) == 0 {
		panic("stats: percentile of no samples")
	}
	ordered := slices.Clone(samples)
	slices.Sort(ordered)
	return nearestRank(ordered, q)
}

func nearestRank(ordered []time.Duration, q float64) time.Duration {
	rank := max(1, int(math.Ceil(q*float64(len(ordered)))))
	return ordered[min(rank, len(ordered))-1]
}

// Summary is the latency summary of one scenario.
type Summary struct {
	Count, Errors      int
	P50, P95, P99, Max time.Duration
}

// Summarize summarizes the latencies of the successful requests and the
// number of failed ones. With no samples, only Errors is set.
func Summarize(samples []time.Duration, errors int) Summary {
	s := Summary{Count: len(samples), Errors: errors}
	if len(samples) == 0 {
		return s
	}
	ordered := slices.Clone(samples)
	slices.Sort(ordered)
	s.P50 = nearestRank(ordered, 0.50)
	s.P95 = nearestRank(ordered, 0.95)
	s.P99 = nearestRank(ordered, 0.99)
	s.Max = ordered[len(ordered)-1]
	return s
}

// Metrics returns the summary with the metric keys of the result format:
// count, errors, p50_ms, p95_ms, p99_ms and max_ms. It converts to
// result.Metrics without this package importing result.
func (s Summary) Metrics() map[string]float64 {
	return map[string]float64{
		"count":  float64(s.Count),
		"errors": float64(s.Errors),
		"p50_ms": ms(s.P50),
		"p95_ms": ms(s.P95),
		"p99_ms": ms(s.P99),
		"max_ms": ms(s.Max),
	}
}

func ms(d time.Duration) float64 { return float64(d) / float64(time.Millisecond) }
