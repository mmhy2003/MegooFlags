package delivery

import (
	"sync"
	"testing"
)

func TestStoreSwap(t *testing.T) {
	var s Store
	if got := s.Load(); got != nil {
		t.Fatalf("Load before Swap = %v, want nil", got)
	}
	a := &Snapshot{ETag: "a", Source: "bootstrap"}
	if old := s.Swap(a); old != nil {
		t.Errorf("first Swap returned %v, want nil", old)
	}
	if got := s.Load(); got != a {
		t.Errorf("Load = %v, want %v", got, a)
	}
	b := &Snapshot{ETag: "b", Source: "stream"}
	if old := s.Swap(b); old != a {
		t.Errorf("second Swap returned %v, want %v", old, a)
	}
	if got := s.Load(); got != b {
		t.Errorf("Load = %v, want %v", got, b)
	}
}

func TestStoreConcurrentLoad(t *testing.T) {
	var s Store
	s.Swap(&Snapshot{ETag: "0"})
	var wg sync.WaitGroup
	stop := make(chan struct{})
	for range 4 {
		wg.Go(func() {
			for {
				select {
				case <-stop:
					return
				default:
				}
				if snap := s.Load(); snap == nil || snap.ETag == "" {
					t.Error("Load returned an empty snapshot during Swap")
					return
				}
			}
		})
	}
	for i := range 1000 {
		s.Swap(&Snapshot{ETag: string(rune('a' + i%26))})
	}
	close(stop)
	wg.Wait()
}
