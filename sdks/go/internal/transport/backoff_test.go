package transport

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestBackoff(t *testing.T) {
	b := &Backoff{Base: 3 * time.Second, Max: 60 * time.Second, Rand: func() float64 { return 1 }}
	want := []time.Duration{3, 6, 12, 24, 48, 60, 60}
	for i, w := range want {
		if got := b.Next(); got != w*time.Second {
			t.Errorf("wait %d = %v, want %v", i+1, got, w*time.Second)
		}
	}
	b.Reset()
	if got := b.Next(); got != 3*time.Second {
		t.Errorf("after Reset = %v, want 3s", got)
	}
}

func TestBackoffJitter(t *testing.T) {
	b := &Backoff{Base: 3 * time.Second, Max: 60 * time.Second, Rand: func() float64 { return 0 }}
	b.Next()
	if got := b.Next(); got != 4800*time.Millisecond {
		t.Errorf("2nd wait = %v, want 4.8s", got)
	}
}

func TestBackoffNeverAboveMax(t *testing.T) {
	b := &Backoff{Base: 3 * time.Second, Max: 60 * time.Second}
	for i := range 200 {
		if got := b.Next(); got > b.Max || got <= 0 {
			t.Fatalf("wait %d = %v, outside (0, %v]", i+1, got, b.Max)
		}
	}
}

func TestSleep(t *testing.T) {
	if err := Sleep(context.Background(), time.Millisecond); err != nil {
		t.Fatalf("Sleep: %v", err)
	}
}

func TestSleepCancel(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(10 * time.Millisecond)
		cancel()
	}()
	start := time.Now()
	err := Sleep(ctx, time.Hour)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Sleep = %v, want context.Canceled", err)
	}
	if time.Since(start) > 5*time.Second {
		t.Fatal("Sleep did not return when the context ended")
	}
}
