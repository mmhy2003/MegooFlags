package events

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/transport"
)

const batcherKey = "mf_server_batcher-key-value"

// eventServer is a fake POST /sdk/v1/events. respond picks the status of each
// request (n counts from 1); only 2xx bodies are recorded as accepted.
type eventServer struct {
	*httptest.Server
	respond func(n int, b Batch, w http.ResponseWriter) int

	mu       sync.Mutex
	requests []*http.Request
	at       []time.Time
	bodies   []Batch
	accepted []Batch
	arrived  chan struct{}
}

func newEventServer(t *testing.T, respond func(n int, b Batch, w http.ResponseWriter) int) *eventServer {
	t.Helper()
	s := &eventServer{respond: respond, arrived: make(chan struct{}, 1000)}
	if s.respond == nil {
		s.respond = func(int, Batch, http.ResponseWriter) int { return http.StatusAccepted }
	}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var b Batch
		if err := json.NewDecoder(r.Body).Decode(&b); err != nil {
			t.Errorf("decode body: %v", err)
		}
		s.mu.Lock()
		s.requests = append(s.requests, r.Clone(context.Background()))
		s.at = append(s.at, time.Now())
		s.bodies = append(s.bodies, b)
		n := len(s.requests)
		s.mu.Unlock()
		status := s.respond(n, b, w)
		if status >= 200 && status < 300 {
			s.mu.Lock()
			s.accepted = append(s.accepted, b)
			s.mu.Unlock()
		}
		w.WriteHeader(status)
		s.arrived <- struct{}{}
	}))
	t.Cleanup(s.Close)
	return s
}

func (s *eventServer) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.requests)
}

// waitRequests waits until the server has answered n requests.
func (s *eventServer) waitRequests(t *testing.T, n int, within time.Duration) {
	t.Helper()
	deadline := time.After(within)
	for s.count() < n {
		select {
		case <-s.arrived:
		case <-deadline:
			t.Fatalf("got %d requests within %v, want %d", s.count(), within, n)
		}
	}
}

// totals sums what the server accepted: exposures and the count per flag.
func (s *eventServer) totals() (exposures int, counts map[string]int64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	counts = make(map[string]int64)
	for _, b := range s.accepted {
		exposures += len(b.Exposures)
		for _, c := range b.Counts {
			counts[c.FlagKey] += c.Count
		}
	}
	return exposures, counts
}

func newTestBatcher(t *testing.T, url string, mod func(*Config)) *Batcher {
	t.Helper()
	cfg := Config{
		URL:           url + "/sdk/v1/events",
		Key:           batcherKey,
		UserAgent:     "megooflags-go/test",
		FlushInterval: 50 * time.Millisecond,
		RetryBase:     10 * time.Millisecond,
		RetryMax:      40 * time.Millisecond,
	}
	if mod != nil {
		mod(&cfg)
	}
	b := New(cfg)
	t.Cleanup(b.Stop)
	return b
}

func eval(flag, ctxKey string) Evaluation {
	return Evaluation{FlagKey: flag, Variant: "on", Served: true, Reason: "STATIC", ContextKey: ctxKey, At: time.Now()}
}

func shutdown(t *testing.T, b *Batcher) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := b.Shutdown(ctx); err != nil {
		t.Fatalf("Shutdown: %v", err)
	}
}

func TestBatcherFlushesOnInterval(t *testing.T) {
	srv := newEventServer(t, nil)
	b := newTestBatcher(t, srv.URL, nil)
	b.Start()

	b.Record(eval("f", "u1"))
	srv.waitRequests(t, 1, 2*time.Second)

	exposures, counts := srv.totals()
	if exposures != 1 || counts["f"] != 1 {
		t.Errorf("accepted exposures = %d, counts = %v; want 1 and f:1", exposures, counts)
	}
	shutdown(t, b)
}

func TestBatcherFlushesAt1000(t *testing.T) {
	srv := newEventServer(t, nil)
	b := newTestBatcher(t, srv.URL, func(c *Config) { c.FlushInterval = time.Hour })
	b.Start()

	for i := range 1000 {
		b.Record(eval("f", "u"+strconv.Itoa(i)))
	}
	srv.waitRequests(t, 1, 2*time.Second)

	srv.mu.Lock()
	got := len(srv.bodies[0].Exposures)
	srv.mu.Unlock()
	if got != 1000 {
		t.Errorf("first batch has %d exposures, want 1000", got)
	}
}

func TestBatcherShutdownFlushes(t *testing.T) {
	srv := newEventServer(t, nil)
	b := newTestBatcher(t, srv.URL, func(c *Config) { c.FlushInterval = time.Hour })
	b.Start()

	b.Record(eval("f", "u1"))
	b.Record(eval("f", "u2"))
	b.Record(eval("g", "u1"))
	shutdown(t, b)

	exposures, counts := srv.totals()
	if exposures != 3 || counts["f"] != 2 || counts["g"] != 1 {
		t.Errorf("accepted exposures = %d, counts = %v; want 3, f:2 g:1", exposures, counts)
	}
	if err := b.Shutdown(context.Background()); err != nil {
		t.Errorf("second Shutdown = %v, want nil", err)
	}
}

func TestBatcherShutdownDeadline(t *testing.T) {
	release := make(chan struct{})
	srv := newEventServer(t, func(int, Batch, http.ResponseWriter) int {
		<-release
		return http.StatusAccepted
	})
	defer close(release) // runs before srv.Close, which waits for handlers
	b := newTestBatcher(t, srv.URL, func(c *Config) { c.FlushInterval = time.Hour })
	b.Start()
	b.Record(eval("f", "u1"))

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	start := time.Now()
	err := b.Shutdown(ctx)
	if elapsed := time.Since(start); elapsed > time.Second {
		t.Errorf("Shutdown took %v, want about 200ms", elapsed)
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("Shutdown = %v, want context.DeadlineExceeded", err)
	}
}

func TestBatcher413Splits(t *testing.T) {
	srv := newEventServer(t, func(_ int, b Batch, _ http.ResponseWriter) int {
		if len(b.Exposures)+len(b.Counts) > 10 {
			return http.StatusRequestEntityTooLarge
		}
		return http.StatusAccepted
	})
	b := newTestBatcher(t, srv.URL, func(c *Config) { c.FlushInterval = time.Hour })
	b.Start()
	for i := range 25 {
		b.Record(eval("f", "u"+strconv.Itoa(i)))
	}
	shutdown(t, b)

	exposures, counts := srv.totals()
	if exposures != 25 || counts["f"] != 25 {
		t.Errorf("accepted exposures = %d, counts = %v; want 25 and f:25", exposures, counts)
	}
	srv.mu.Lock()
	defer srv.mu.Unlock()
	for _, a := range srv.accepted {
		if n := len(a.Exposures) + len(a.Counts); n > 10 || n == 0 {
			t.Errorf("accepted a batch of %d items", n)
		}
	}
}

func TestBatcherSplitsBodiesOver1MB(t *testing.T) {
	srv := newEventServer(t, nil)
	b := newTestBatcher(t, srv.URL, func(c *Config) {
		c.FlushInterval = time.Hour
		c.SendContext = true
	})
	b.Start()
	big := map[string]any{"blob": strings.Repeat("x", 2000)}
	for i := range 1000 {
		ev := eval("f", "u"+strconv.Itoa(i))
		ev.Context = big
		b.Record(ev)
	}
	shutdown(t, b)

	srv.mu.Lock()
	defer srv.mu.Unlock()
	if len(srv.requests) < 2 {
		t.Fatalf("got %d requests, want the batch split", len(srv.requests))
	}
	total := 0
	for _, a := range srv.accepted {
		total += len(a.Exposures)
		body, _ := json.Marshal(a)
		if len(body) > maxBodyBytes {
			t.Errorf("sent a body of %d bytes", len(body))
		}
	}
	if total != 1000 {
		t.Errorf("accepted %d exposures, want 1000", total)
	}
}

func TestBatcher422Drops(t *testing.T) {
	srv := newEventServer(t, func(int, Batch, http.ResponseWriter) int { return http.StatusUnprocessableEntity })
	b := newTestBatcher(t, srv.URL, nil)
	b.Start()

	b.Record(eval("f", "u1"))
	srv.waitRequests(t, 1, 2*time.Second)
	time.Sleep(200 * time.Millisecond) // four intervals
	shutdown(t, b)

	if n := srv.count(); n != 1 {
		t.Errorf("got %d requests, want 1 (the batch is dropped, not resent)", n)
	}
}

func TestBatcher429RetriesAfter(t *testing.T) {
	srv := newEventServer(t, func(n int, _ Batch, w http.ResponseWriter) int {
		if n == 1 {
			w.Header().Set("Retry-After", "1")
			return http.StatusTooManyRequests
		}
		return http.StatusAccepted
	})
	b := newTestBatcher(t, srv.URL, nil)
	b.Start()

	b.Record(eval("f", "u1"))
	srv.waitRequests(t, 2, 3*time.Second)

	srv.mu.Lock()
	gap := srv.at[1].Sub(srv.at[0])
	first, second := srv.bodies[0], srv.bodies[1]
	srv.mu.Unlock()
	if gap < time.Second {
		t.Errorf("resent after %v, want at least 1s", gap)
	}
	if len(second.Exposures) != 1 || *second.Exposures[0].Variant != *first.Exposures[0].Variant ||
		len(second.Counts) != 1 || second.Counts[0] != (Count{FlagKey: "f", Variant: second.Counts[0].Variant, Hour: first.Counts[0].Hour, Count: 1}) {
		t.Errorf("resent %+v, want the same items as %+v", second, first)
	}
	shutdown(t, b)
}

func TestBatcher500RetriesWithBackoff(t *testing.T) {
	srv := newEventServer(t, func(n int, _ Batch, _ http.ResponseWriter) int {
		if n <= 2 {
			return http.StatusServiceUnavailable
		}
		return http.StatusAccepted
	})
	b := newTestBatcher(t, srv.URL, nil)
	b.Start()

	for range 5 {
		b.Record(eval("f", "u1"))
	}
	srv.waitRequests(t, 1, 2*time.Second)
	for range 3 {
		b.Record(eval("f", "u1"))
	}
	srv.waitRequests(t, 3, 3*time.Second)
	shutdown(t, b)

	exposures, counts := srv.totals()
	if exposures != 1 || counts["f"] != 8 {
		t.Errorf("accepted exposures = %d, counts = %v; want 1 and f:8", exposures, counts)
	}
}

func TestBatcher401StopsAndCallsOnFatal(t *testing.T) {
	srv := newEventServer(t, func(int, Batch, http.ResponseWriter) int { return http.StatusUnauthorized })
	var fatals atomic.Int32
	var fatalErr atomic.Value
	b := newTestBatcher(t, srv.URL, func(c *Config) {
		c.OnFatal = func(err error) {
			fatals.Add(1)
			fatalErr.Store(err)
		}
	})
	b.Start()

	b.Record(eval("f", "u1"))
	srv.waitRequests(t, 1, 2*time.Second)
	b.Record(eval("g", "u1"))
	time.Sleep(200 * time.Millisecond)
	shutdown(t, b)

	if n := srv.count(); n != 1 {
		t.Errorf("got %d requests, want 1", n)
	}
	if n := fatals.Load(); n != 1 {
		t.Errorf("OnFatal called %d times, want 1", n)
	}
	var fe *transport.FatalError
	if err, _ := fatalErr.Load().(error); !errors.As(err, &fe) || fe.Status != http.StatusUnauthorized {
		t.Errorf("OnFatal error = %v, want a FatalError with status 401", err)
	}
}

func TestBatcherStopDoesNotSend(t *testing.T) {
	srv := newEventServer(t, nil)
	b := newTestBatcher(t, srv.URL, func(c *Config) { c.FlushInterval = time.Hour })
	b.Start()
	b.Record(eval("f", "u1"))
	b.Stop()
	b.Stop()
	shutdown(t, b)

	if n := srv.count(); n != 0 {
		t.Errorf("got %d requests after Stop, want 0", n)
	}
}

func TestRecordNeverBlocks(t *testing.T) {
	b := New(Config{URL: "http://127.0.0.1:1/sdk/v1/events", QueueSize: 1})
	defer b.Stop()

	start := time.Now()
	b.Record(eval("f", "u1"))
	b.Record(eval("f", "u2"))
	if elapsed := time.Since(start); elapsed > 100*time.Millisecond {
		t.Errorf("Record took %v", elapsed)
	}
	if n := b.Dropped(); n != 1 {
		t.Errorf("Dropped() = %d, want 1", n)
	}
}

func TestRecordAfterShutdown(t *testing.T) {
	srv := newEventServer(t, nil)
	b := newTestBatcher(t, srv.URL, nil)
	b.Start()
	shutdown(t, b)

	b.Record(eval("f", "u1"))
	b.Stop()
	b.Record(eval("f", "u2"))
	if n := b.Dropped(); n != 2 {
		t.Errorf("Dropped() = %d, want 2", n)
	}
	if n := srv.count(); n != 0 {
		t.Errorf("got %d requests, want 0", n)
	}
}

func TestBatcherSendsBearerAndUserAgent(t *testing.T) {
	srv := newEventServer(t, nil)
	b := newTestBatcher(t, srv.URL, nil)
	b.Start()
	b.Record(eval("f", "u1"))
	srv.waitRequests(t, 1, 2*time.Second)
	shutdown(t, b)

	srv.mu.Lock()
	r := srv.requests[0]
	srv.mu.Unlock()
	if r.Method != http.MethodPost || r.URL.Path != "/sdk/v1/events" {
		t.Errorf("request = %s %s", r.Method, r.URL.Path)
	}
	if a := r.Header.Get("Authorization"); a != "Bearer "+batcherKey {
		t.Errorf("Authorization = %q", a)
	}
	if ua := r.Header.Get("User-Agent"); ua != "megooflags-go/test" {
		t.Errorf("User-Agent = %q", ua)
	}
	if ct := r.Header.Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type = %q", ct)
	}
}

func TestBatcherOnFatalCanStopAndShutdown(t *testing.T) {
	srv := newEventServer(t, func(int, Batch, http.ResponseWriter) int { return http.StatusForbidden })
	returned := make(chan struct{})
	var b *Batcher
	b = newTestBatcher(t, srv.URL, func(c *Config) {
		c.OnFatal = func(error) {
			b.Stop()
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := b.Shutdown(ctx); err != nil {
				t.Errorf("Shutdown from OnFatal = %v, want nil", err)
			}
			close(returned)
		}
	})
	b.Start()
	b.Record(eval("f", "u1"))

	select {
	case <-returned:
	case <-time.After(2 * time.Second):
		t.Fatal("Stop and Shutdown called from OnFatal did not return")
	}
}

func TestBatcherSplitStopsAfterFatal(t *testing.T) {
	srv := newEventServer(t, func(_ int, b Batch, _ http.ResponseWriter) int {
		if len(b.Exposures)+len(b.Counts) > 10 {
			return http.StatusRequestEntityTooLarge
		}
		return http.StatusUnauthorized
	})
	var attempts atomic.Int32
	b := newTestBatcher(t, srv.URL, func(c *Config) {
		c.FlushInterval = time.Hour
		c.Client = &http.Client{Transport: countingTransport{&attempts, http.DefaultTransport}}
	})
	b.Start()
	for i := range 25 {
		b.Record(eval("f", "u"+strconv.Itoa(i)))
	}
	shutdown(t, b)

	// 26 items → 413; 13 → 413; 6 → 401, and no attempt after it.
	if n := attempts.Load(); n != 3 {
		t.Errorf("made %d attempts, want 3 (none after the 401)", n)
	}
}

// countingTransport counts every request the client attempts, including
// those whose context is already cancelled.
type countingTransport struct {
	n    *atomic.Int32
	next http.RoundTripper
}

func (c countingTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	c.n.Add(1)
	return c.next.RoundTrip(r)
}
