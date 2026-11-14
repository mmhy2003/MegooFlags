package transport

import (
	"context"
	"math/rand/v2"
	"time"
)

// Backoff computes jittered exponential waits. The first wait is Base; the
// n-th is min(Max, Base<<n) scaled by a factor in [0.8, 1.0].
type Backoff struct {
	Base, Max time.Duration
	Rand      func() float64 // nil → math/rand/v2.Float64
	n         int
}

// Next returns the next wait and advances the attempt counter.
func (b *Backoff) Next() time.Duration {
	if b.n == 0 {
		b.n++
		return min(b.Base, b.Max)
	}
	// Double step by step so a large n cannot overflow the shift.
	d := b.Base
	for i := 0; i < b.n && d < b.Max; i++ {
		d <<= 1
	}
	d = min(d, b.Max)
	if b.n < 64 {
		b.n++
	}
	r := b.Rand
	if r == nil {
		r = rand.Float64
	}
	return time.Duration(float64(d) * (0.8 + 0.2*r()))
}

// Reset makes the next wait Base again.
func (b *Backoff) Reset() { b.n = 0 }

// Sleep waits for d, or returns ctx.Err() if ctx ends first.
func Sleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}
