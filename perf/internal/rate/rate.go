// Package rate runs requests at a fixed rate in an open loop.
package rate

import (
	"context"
	"math"
	"sync"
	"sync/atomic"
	"time"
)

// Result is the outcome of one request.
type Result struct {
	Latency time.Duration
	Err     error
}

// Run calls do ceil(rps*d) times. Request i is due at start + i/rps whether or
// not earlier requests have finished (open loop), and its latency is measured
// from the due time, so time spent waiting for one of the concurrency workers
// counts. Run stops starting requests when ctx is done and returns the results
// of the requests it ran, in due order.
func Run(ctx context.Context, rps float64, d time.Duration, concurrency int, do func(context.Context) error) []Result {
	n := int(math.Ceil(rps * d.Seconds()))
	results := make([]Result, n)
	ran := make([]bool, n)
	start := time.Now()
	var next atomic.Int64
	var wg sync.WaitGroup
	for range max(1, concurrency) {
		wg.Go(func() {
			timer := time.NewTimer(0)
			defer timer.Stop()
			for {
				i := int(next.Add(1) - 1)
				if i >= n || ctx.Err() != nil {
					return
				}
				due := start.Add(time.Duration(float64(i) * float64(time.Second) / rps))
				if wait := time.Until(due); wait > 0 {
					timer.Reset(wait)
					select {
					case <-ctx.Done():
						return
					case <-timer.C:
					}
				}
				err := do(ctx)
				results[i] = Result{Latency: time.Since(due), Err: err}
				ran[i] = true
			}
		})
	}
	wg.Wait()
	out := results[:0]
	for i, r := range results {
		if ran[i] {
			out = append(out, r)
		}
	}
	return out
}
