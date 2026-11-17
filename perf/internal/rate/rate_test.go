package rate

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

func TestRunIsOpenLoop(t *testing.T) {
	var calls atomic.Int64
	results := Run(context.Background(), 100, time.Second, 2, func(ctx context.Context) error {
		calls.Add(1)
		time.Sleep(50 * time.Millisecond)
		return nil
	})
	if n := len(results); n < 99 || n > 101 {
		t.Fatalf("got %d results, want 100±1", n)
	}
	if int(calls.Load()) != len(results) {
		t.Errorf("do ran %d times for %d results", calls.Load(), len(results))
	}
	// Two workers serve 40 rps against 100 due per second, so the queue grows:
	// the last requests wait well over a second past their due time.
	var max time.Duration
	for _, r := range results {
		if r.Err != nil {
			t.Fatalf("unexpected error %v", r.Err)
		}
		if r.Latency < 50*time.Millisecond {
			t.Errorf("latency %v below the handler's 50ms", r.Latency)
		}
		if r.Latency > max {
			max = r.Latency
		}
	}
	if max < time.Second {
		t.Errorf("max latency %v, want queueing past 1s", max)
	}
}

func TestRunCountsErrors(t *testing.T) {
	boom := errors.New("boom")
	var n atomic.Int64
	results := Run(context.Background(), 200, 250*time.Millisecond, 4, func(ctx context.Context) error {
		if n.Add(1)%2 == 0 {
			return boom
		}
		return nil
	})
	failed := 0
	for _, r := range results {
		if r.Err != nil {
			if !errors.Is(r.Err, boom) {
				t.Errorf("err = %v, want boom", r.Err)
			}
			failed++
		}
	}
	if len(results) < 49 || len(results) > 51 {
		t.Fatalf("got %d results, want 50±1", len(results))
	}
	if want := len(results) / 2; failed != want {
		t.Errorf("%d errors, want %d", failed, want)
	}
}

func TestRunStopsOnCancel(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	start := time.Now()
	results := Run(ctx, 100, 10*time.Second, 4, func(context.Context) error { return nil })
	if el := time.Since(start); el > 2*time.Second {
		t.Errorf("Run took %v after cancel", el)
	}
	if len(results) > 20 {
		t.Errorf("got %d results, want about 10", len(results))
	}
}
