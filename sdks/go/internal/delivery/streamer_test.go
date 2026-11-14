package delivery

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/transport"
)

const streamKey = "mf_server_secret-key-value"

// streamServer serves one handler per request, in order; the last handler
// serves every later request. It counts requests and records their headers
// and arrival times.
type streamServer struct {
	*httptest.Server
	mu       sync.Mutex
	handlers []http.HandlerFunc
	requests []*http.Request
	times    []time.Time
}

func newStreamServer(t *testing.T, handlers ...http.HandlerFunc) *streamServer {
	t.Helper()
	s := &streamServer{handlers: handlers}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		n := len(s.requests)
		s.requests = append(s.requests, r.Clone(context.Background()))
		s.times = append(s.times, time.Now())
		s.mu.Unlock()
		s.handlers[min(n, len(s.handlers)-1)](w, r)
	}))
	t.Cleanup(s.Close)
	return s
}

func (s *streamServer) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.requests)
}

func (s *streamServer) request(i int) (*http.Request, time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.requests[i], s.times[i]
}

// sse writes the header and the given chunks, then holds the stream open until
// the client goes away.
func sse(chunks ...string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		for _, c := range chunks {
			fmt.Fprint(w, c)
		}
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}
}

// sseThenClose writes the chunks and ends the response.
func sseThenClose(chunks ...string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		for _, c := range chunks {
			fmt.Fprint(w, c)
		}
	}
}

func status(code int, header ...string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		for i := 0; i+1 < len(header); i += 2 {
			w.Header().Set(header[i], header[i+1])
		}
		w.WriteHeader(code)
	}
}

type put struct{ raw, id string }

type disconnect struct {
	err      error
	failures int
}

// recorder collects the streamer's callbacks.
type recorder struct {
	mu          sync.Mutex
	puts        []put
	disconnects []disconnect
	connects    int
	putCh       chan put
	discCh      chan disconnect
}

func newRecorder() *recorder {
	return &recorder{putCh: make(chan put, 100), discCh: make(chan disconnect, 100)}
}

func (r *recorder) lastID() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.puts) == 0 {
		return ""
	}
	return r.puts[len(r.puts)-1].id
}

func (r *recorder) config(url string) StreamerConfig {
	return StreamerConfig{
		URL:         url,
		Key:         streamKey,
		UserAgent:   "megooflags-go/test",
		LastEventID: r.lastID,
		OnPut: func(raw []byte, id string) bool {
			p := put{string(raw), id}
			r.mu.Lock()
			r.puts = append(r.puts, p)
			r.mu.Unlock()
			r.putCh <- p
			return !strings.Contains(p.raw, "invalid")
		},
		OnConnected: func() {
			r.mu.Lock()
			r.connects++
			r.mu.Unlock()
		},
		OnDisconnected: func(err error, failures int) {
			d := disconnect{err, failures}
			r.mu.Lock()
			r.disconnects = append(r.disconnects, d)
			r.mu.Unlock()
			r.discCh <- d
		},
		DeadTimeout: 200 * time.Millisecond,
		StableAfter: 300 * time.Millisecond,
		Backoff:     &transport.Backoff{Base: 10 * time.Millisecond, Max: 40 * time.Millisecond, Rand: func() float64 { return 1 }},
	}
}

// run starts the streamer and returns a cancel function and the channel that
// receives Run's result.
func run(t *testing.T, cfg StreamerConfig) (context.CancelFunc, <-chan error) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		done <- NewStreamer(cfg).Run(ctx)
	}()
	t.Cleanup(func() {
		cancel()
		select {
		case <-finished:
		case <-time.After(5 * time.Second):
			t.Error("Run did not return after cancel")
		}
	})
	return cancel, done
}

func waitPut(t *testing.T, r *recorder) put {
	t.Helper()
	select {
	case p := <-r.putCh:
		return p
	case <-time.After(5 * time.Second):
		t.Fatal("no put received")
		return put{}
	}
}

func waitDisconnect(t *testing.T, r *recorder) disconnect {
	t.Helper()
	select {
	case d := <-r.discCh:
		return d
	case <-time.After(5 * time.Second):
		t.Fatal("no disconnect reported")
		return disconnect{}
	}
}

func waitResult(t *testing.T, done <-chan error) error {
	t.Helper()
	select {
	case err := <-done:
		return err
	case <-time.After(5 * time.Second):
		t.Fatal("Run did not return")
		return nil
	}
}

func waitRequests(t *testing.T, s *streamServer, n int) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for s.count() < n {
		if time.Now().After(deadline) {
			t.Fatalf("server saw %d requests, want %d", s.count(), n)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestStreamerPut(t *testing.T) {
	srv := newStreamServer(t, sse("retry: 10\n\n", "event: put\nid: 42\ndata: {\"flags\":{}}\n\n"))
	rec := newRecorder()
	run(t, rec.config(srv.URL))

	p := waitPut(t, rec)
	if p.raw != `{"flags":{}}` || p.id != "42" {
		t.Errorf("OnPut(%q, %q), want the raw data and id 42", p.raw, p.id)
	}
	rec.mu.Lock()
	defer rec.mu.Unlock()
	if rec.connects != 1 {
		t.Errorf("OnConnected called %d times, want 1", rec.connects)
	}
}

func TestStreamerSendsLastEventID(t *testing.T) {
	srv := newStreamServer(t,
		sseThenClose("event: put\nid: 7\ndata: {}\n\n"),
		sse(),
	)
	rec := newRecorder()
	run(t, rec.config(srv.URL))

	waitRequests(t, srv, 2)
	first, _ := srv.request(0)
	second, _ := srv.request(1)
	if got := first.Header.Get("Last-Event-ID"); got != "" {
		t.Errorf("first request Last-Event-ID = %q, want none", got)
	}
	if _, ok := first.Header["Last-Event-Id"]; ok {
		t.Error("first request carries an empty Last-Event-ID header")
	}
	if got := second.Header.Get("Last-Event-ID"); got != "7" {
		t.Errorf("second request Last-Event-ID = %q, want 7", got)
	}
}

func TestStreamerIgnoresUnknownEvents(t *testing.T) {
	srv := newStreamServer(t, sse(
		"event: mystery\ndata: x\n\n",
		"data: unnamed\n\n",
		"event: put\nid: 1\ndata: {\"ok\":true}\n\n",
	))
	rec := newRecorder()
	run(t, rec.config(srv.URL))

	if p := waitPut(t, rec); p.raw != `{"ok":true}` {
		t.Errorf("first OnPut data = %q, want the put event", p.raw)
	}
	time.Sleep(50 * time.Millisecond)
	rec.mu.Lock()
	defer rec.mu.Unlock()
	if len(rec.puts) != 1 || len(rec.disconnects) != 0 {
		t.Errorf("puts = %v, disconnects = %v; want one put and no disconnect", rec.puts, rec.disconnects)
	}
	if n := srv.count(); n != 1 {
		t.Errorf("server saw %d requests, want 1", n)
	}
}

func TestStreamerInvalidPutContinues(t *testing.T) {
	srv := newStreamServer(t, sse(
		"event: put\nid: 1\ndata: invalid\n\n",
		"event: put\nid: 2\ndata: {}\n\n",
	))
	rec := newRecorder()
	var logs bytes.Buffer
	cfg := rec.config(srv.URL)
	cfg.Logger = slog.New(slog.NewTextHandler(&logs, nil))
	cancel, done := run(t, cfg)

	waitPut(t, rec)
	if p := waitPut(t, rec); p.id != "2" {
		t.Errorf("second put id = %q, want 2", p.id)
	}
	cancel()
	waitResult(t, done)
	if n := srv.count(); n != 1 {
		t.Errorf("server saw %d requests, want 1", n)
	}
	if !strings.Contains(logs.String(), "invalid ruleset") {
		t.Errorf("log = %q, want an invalid ruleset warning", logs.String())
	}
}

func TestStreamerRevokedStops(t *testing.T) {
	srv := newStreamServer(t, sse("event: revoked\ndata: {}\n\n"))
	rec := newRecorder()
	_, done := run(t, rec.config(srv.URL))

	err := waitResult(t, done)
	var fe *transport.FatalError
	if !errors.As(err, &fe) || fe.Reason != "revoked" {
		t.Fatalf("Run = %v, want a revoked FatalError", err)
	}
	time.Sleep(50 * time.Millisecond)
	if n := srv.count(); n != 1 {
		t.Errorf("server saw %d requests, want 1", n)
	}
}

func TestStreamer401And403Stop(t *testing.T) {
	for _, code := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		t.Run(http.StatusText(code), func(t *testing.T) {
			srv := newStreamServer(t, status(code))
			rec := newRecorder()
			_, done := run(t, rec.config(srv.URL))

			err := waitResult(t, done)
			var fe *transport.FatalError
			if !errors.As(err, &fe) || fe.Status != code {
				t.Fatalf("Run = %v, want a FatalError with status %d", err, code)
			}
			time.Sleep(50 * time.Millisecond)
			if n := srv.count(); n != 1 {
				t.Errorf("server saw %d requests, want 1", n)
			}
		})
	}
}

func TestStreamer429WaitsRetryAfter(t *testing.T) {
	srv := newStreamServer(t, status(http.StatusTooManyRequests, "Retry-After", "1"), sse())
	rec := newRecorder()
	run(t, rec.config(srv.URL))

	d := waitDisconnect(t, rec)
	var ra *transport.RetryAfterError
	if !errors.As(d.err, &ra) || d.failures != 1 {
		t.Errorf("OnDisconnected(%v, %d), want a RetryAfterError and 1", d.err, d.failures)
	}
	waitRequests(t, srv, 2)
	_, t0 := srv.request(0)
	_, t1 := srv.request(1)
	if gap := t1.Sub(t0); gap < time.Second {
		t.Errorf("second request %v after the 429, want at least 1s", gap)
	}
}

func TestStreamer5xxBacksOff(t *testing.T) {
	srv := newStreamServer(t, status(http.StatusServiceUnavailable))
	rec := newRecorder()
	cancel, _ := run(t, rec.config(srv.URL))

	var failures []int
	for range 3 {
		d := waitDisconnect(t, rec)
		var he *transport.HTTPError
		if !errors.As(d.err, &he) || he.Status != http.StatusServiceUnavailable {
			t.Errorf("OnDisconnected err = %v, want HTTP 503", d.err)
		}
		failures = append(failures, d.failures)
	}
	cancel()
	if fmt.Sprint(failures) != "[1 2 3]" {
		t.Errorf("failures = %v, want [1 2 3]", failures)
	}
	// Rand returns 1, so the waits are exactly 10ms, 20ms and 40ms.
	waitRequests(t, srv, 3)
	_, t0 := srv.request(0)
	_, t1 := srv.request(1)
	_, t2 := srv.request(2)
	if g := t1.Sub(t0); g < 10*time.Millisecond {
		t.Errorf("first wait %v, want at least 10ms", g)
	}
	if g := t2.Sub(t1); g < 20*time.Millisecond {
		t.Errorf("second wait %v, want at least 20ms", g)
	}
	rec.mu.Lock()
	defer rec.mu.Unlock()
	if rec.connects != 0 {
		t.Errorf("OnConnected called %d times on 503s", rec.connects)
	}
}

func TestStreamerDeadConnection(t *testing.T) {
	srv := newStreamServer(t, sse())
	rec := newRecorder()
	run(t, rec.config(srv.URL))

	d := waitDisconnect(t, rec)
	if !errors.Is(d.err, errDeadConnection) || d.failures != 1 {
		t.Errorf("OnDisconnected(%v, %d), want errDeadConnection and 1", d.err, d.failures)
	}
	waitRequests(t, srv, 2)
	_, t0 := srv.request(0)
	_, t1 := srv.request(1)
	if gap := t1.Sub(t0); gap < 200*time.Millisecond || gap > 2*time.Second {
		t.Errorf("reconnected %v after connecting, want about the 200ms dead timeout", gap)
	}
}

func TestStreamerPingKeepsAlive(t *testing.T) {
	srv := newStreamServer(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		tick := time.NewTicker(50 * time.Millisecond)
		defer tick.Stop()
		for {
			select {
			case <-r.Context().Done():
				return
			case <-tick.C:
				fmt.Fprint(w, ": ping\n")
				w.(http.Flusher).Flush()
			}
		}
	})
	rec := newRecorder()
	run(t, rec.config(srv.URL))

	time.Sleep(500 * time.Millisecond)
	if n := srv.count(); n != 1 {
		t.Errorf("server saw %d requests within 500ms of pings, want 1", n)
	}
	rec.mu.Lock()
	defer rec.mu.Unlock()
	if len(rec.disconnects) != 0 {
		t.Errorf("disconnects = %v, want none", rec.disconnects)
	}
}

// pingFor keeps the stream alive with pings for d, then ends it.
func pingFor(d time.Duration) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		end := time.After(d)
		tick := time.NewTicker(50 * time.Millisecond)
		defer tick.Stop()
		for {
			select {
			case <-r.Context().Done():
				return
			case <-end:
				return
			case <-tick.C:
				fmt.Fprint(w, ": ping\n")
				w.(http.Flusher).Flush()
			}
		}
	}
}

func TestStreamerStableResetsBackoff(t *testing.T) {
	tests := []struct {
		name      string
		connected time.Duration
		want      string
	}{
		{"stable connection resets", 400 * time.Millisecond, "[1 2 1]"},
		{"short connection keeps counting", 0, "[1 2 3]"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			srv := newStreamServer(t,
				status(http.StatusServiceUnavailable),
				status(http.StatusServiceUnavailable),
				pingFor(tt.connected),
				sse(),
			)
			rec := newRecorder()
			cfg := rec.config(srv.URL)
			cfg.DeadTimeout = time.Second
			run(t, cfg)

			var failures []int
			for range 3 {
				failures = append(failures, waitDisconnect(t, rec).failures)
			}
			if got := fmt.Sprint(failures); got != tt.want {
				t.Errorf("failures = %s, want %s", got, tt.want)
			}
		})
	}
}

func TestStreamerCancel(t *testing.T) {
	t.Run("while connected", func(t *testing.T) {
		srv := newStreamServer(t, sse(": hello\n"))
		rec := newRecorder()
		cfg := rec.config(srv.URL)
		connected := make(chan struct{})
		cfg.OnConnected = func() { close(connected) }
		cancel, done := run(t, cfg)

		<-connected
		start := time.Now()
		cancel()
		if err := waitResult(t, done); !errors.Is(err, context.Canceled) {
			t.Errorf("Run = %v, want context.Canceled", err)
		}
		if d := time.Since(start); d > time.Second {
			t.Errorf("Run took %v to return after cancel", d)
		}
	})
	t.Run("while backing off", func(t *testing.T) {
		srv := newStreamServer(t, status(http.StatusServiceUnavailable))
		rec := newRecorder()
		cfg := rec.config(srv.URL)
		cfg.Backoff = &transport.Backoff{Base: time.Hour, Max: time.Hour}
		cancel, done := run(t, cfg)

		waitDisconnect(t, rec)
		start := time.Now()
		cancel()
		if err := waitResult(t, done); !errors.Is(err, context.Canceled) {
			t.Errorf("Run = %v, want context.Canceled", err)
		}
		if d := time.Since(start); d > time.Second {
			t.Errorf("Run took %v to return after cancel", d)
		}
	})
}

func TestStreamerNoKeyInURL(t *testing.T) {
	srv := newStreamServer(t, sse("event: put\nid: 1\ndata: invalid\n\n"))
	rec := newRecorder()
	var logs bytes.Buffer
	cfg := rec.config(srv.URL + "/sdk/v1/stream")
	cfg.Logger = slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug}))
	cancel, done := run(t, cfg)

	waitPut(t, rec)
	cancel()
	waitResult(t, done)

	r, _ := srv.request(0)
	if strings.Contains(r.URL.String(), streamKey) || strings.Contains(r.URL.RawQuery, "key") {
		t.Errorf("request URL %q carries the key", r.URL)
	}
	if r.URL.Path != "/sdk/v1/stream" {
		t.Errorf("path = %q", r.URL.Path)
	}
	for h, want := range map[string]string{
		"Authorization": "Bearer " + streamKey,
		"Accept":        "text/event-stream",
		"Cache-Control": "no-cache",
		"User-Agent":    "megooflags-go/test",
	} {
		if got := r.Header.Get(h); got != want {
			t.Errorf("%s = %q, want %q", h, got, want)
		}
	}
	if strings.Contains(logs.String(), streamKey) {
		t.Errorf("log carries the key: %q", logs.String())
	}
}

func TestNewStreamerDefaults(t *testing.T) {
	s := NewStreamer(StreamerConfig{URL: "http://localhost", Key: "k"})
	if s.cfg.DeadTimeout != 45*time.Second || s.cfg.StableAfter != 60*time.Second {
		t.Errorf("DeadTimeout = %v, StableAfter = %v; want 45s, 60s", s.cfg.DeadTimeout, s.cfg.StableAfter)
	}
	if b := s.cfg.Backoff; b == nil || b.Base != 3*time.Second || b.Max != 60*time.Second {
		t.Errorf("Backoff = %+v, want Base 3s, Max 60s", b)
	}
	if s.cfg.Client == nil || s.cfg.Logger == nil {
		t.Error("Client or Logger left nil")
	}
}
