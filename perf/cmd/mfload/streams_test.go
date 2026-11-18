package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/mmhy2003/MegooFlags/perf/internal/result"
)

const (
	testKey    = "mf_server_test"
	testToken  = "mf_api_test"
	togglePath = "/api/projects/perf/flags/kill-me/environments/production/toggle"
)

// fakeServer serves /sdk/v1/stream and the toggle endpoint. After a toggle it
// sends the new state to every open stream following delay.
type fakeServer struct {
	t     *testing.T
	delay time.Duration
	// dropFirst closes the first stream after its first put.
	dropFirst bool
	// accept is how many streams it accepts before answering 503 (0: no limit).
	accept int
	// silent lists the stream numbers (from 1) that never get a change.
	silent map[int]bool

	mu      sync.Mutex
	enabled bool
	opened  int
	subs    map[int]chan bool
	toggles int
}

func newFakeServer(t *testing.T, f *fakeServer) *httptest.Server {
	f.t = t
	f.subs = map[int]chan bool{}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /sdk/v1/stream", f.stream)
	mux.HandleFunc("POST "+togglePath, f.toggle)
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func put(w http.ResponseWriter, enabled bool) {
	data := fmt.Sprintf(`{"flags":{"other":{"enabled":true},"kill-me":{"enabled":%t}}}`, enabled)
	fmt.Fprintf(w, "id: %t\nevent: put\ndata: %s\n\n", enabled, data)
	w.(http.Flusher).Flush()
}

func (f *fakeServer) stream(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") != "Bearer "+testKey {
		http.Error(w, "bad key", http.StatusUnauthorized)
		return
	}
	f.mu.Lock()
	if f.accept > 0 && f.opened >= f.accept {
		f.mu.Unlock()
		http.Error(w, "full", http.StatusServiceUnavailable)
		return
	}
	f.opened++
	n := f.opened
	ch := make(chan bool, 16)
	if !f.silent[n] {
		f.subs[n] = ch
	}
	enabled := f.enabled
	f.mu.Unlock()
	defer func() {
		f.mu.Lock()
		delete(f.subs, n)
		f.mu.Unlock()
	}()

	w.Header().Set("Content-Type", "text/event-stream")
	fmt.Fprint(w, "retry: 1000\n\n")
	put(w, enabled)
	if f.dropFirst && n == 1 {
		return
	}
	ping := time.NewTicker(20 * time.Millisecond)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-ping.C:
			fmt.Fprint(w, ": ping\n\n")
			w.(http.Flusher).Flush()
		case enabled := <-ch:
			put(w, enabled)
		}
	}
}

func (f *fakeServer) toggle(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") != "Bearer "+testToken {
		http.Error(w, "bad token", http.StatusUnauthorized)
		return
	}
	var body struct {
		Enabled *bool `json:"enabled"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Enabled == nil {
		http.Error(w, "bad body", http.StatusUnprocessableEntity)
		return
	}
	f.mu.Lock()
	f.toggles++
	f.enabled = *body.Enabled
	enabled := f.enabled
	f.mu.Unlock()
	time.AfterFunc(f.delay, func() {
		f.mu.Lock()
		defer f.mu.Unlock()
		for _, ch := range f.subs {
			ch <- enabled
		}
	})
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"enabled":%t}`, enabled)
}

// counts returns how many streams were opened and toggles received.
func (f *fakeServer) counts() (opened, toggles int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.opened, f.toggles
}

func runStreams(t *testing.T, srv *httptest.Server, extra ...string) result.Scenario {
	t.Helper()
	out := filepath.Join(t.TempDir(), "streams.json")
	args := append([]string{"streams",
		"--target", srv.URL, "--key", testKey,
		"--api", srv.URL, "--token", testToken,
		"--project", "perf", "--env", "production", "--flag", "kill-me",
		"--out", out}, extra...)
	var stdout, stderr bytes.Buffer
	if code := run(args, &stdout, &stderr); code != 0 {
		t.Fatalf("mfload streams exited %d: %s", code, stderr.String())
	}
	data, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	var s result.Scenario
	if err := json.Unmarshal(data, &s); err != nil {
		t.Fatal(err)
	}
	return s
}

func TestStreamsMeasuresPropagation(t *testing.T) {
	f := &fakeServer{delay: 50 * time.Millisecond}
	srv := newFakeServer(t, f)
	s := runStreams(t, srv, "--streams", "3", "--ramp-seconds", "0.1", "--hold", "0.9", "--flip-every", "0.3")
	m := s.Metrics
	if m["streams_opened"] != 3 || m["streams_dropped"] != 0 || m["missed"] != 0 || s.Errors != 0 {
		t.Errorf("metrics = %v, errors = %d; want 3 opened, no drops, misses or errors", m, s.Errors)
	}
	if _, toggles := f.counts(); m["flips"] != 3 || toggles != 3 {
		t.Errorf("flips = %v with %d toggles, want 3", m["flips"], toggles)
	}
	if m["prop_p95_ms"] < 40 || m["prop_p95_ms"] > 500 {
		t.Errorf("prop_p95_ms = %v, want between 40 and 500", m["prop_p95_ms"])
	}
	if m["prop_p50_ms"] > m["prop_p95_ms"] || m["prop_p95_ms"] > m["prop_max_ms"] {
		t.Errorf("percentiles out of order: %v", m)
	}
}

func TestStreamsUsesEveryTarget(t *testing.T) {
	a := &fakeServer{delay: 10 * time.Millisecond}
	b := &fakeServer{delay: 10 * time.Millisecond}
	srvA, srvB := newFakeServer(t, a), newFakeServer(t, b)
	s := runStreams(t, srvA, "--target", srvB.URL, "--streams", "4", "--ramp-seconds", "0", "--hold", "0.1", "--flip-every", "0")
	openedA, _ := a.counts()
	openedB, _ := b.counts()
	if s.Metrics["streams_opened"] != 4 || openedA != 2 || openedB != 2 {
		t.Errorf("opened %v streams, %d on a and %d on b; want 4 split 2/2", s.Metrics["streams_opened"], openedA, openedB)
	}
	if s.Metrics["flips"] != 0 {
		t.Errorf("flips = %v with --flip-every 0, want 0", s.Metrics["flips"])
	}
}

func TestStreamsCountsDrops(t *testing.T) {
	f := &fakeServer{delay: 10 * time.Millisecond, dropFirst: true}
	srv := newFakeServer(t, f)
	s := runStreams(t, srv, "--streams", "3", "--ramp-seconds", "0.1", "--hold", "0.4", "--flip-every", "0.2")
	if s.Metrics["streams_opened"] != 3 || s.Metrics["streams_dropped"] != 1 {
		t.Errorf("metrics = %v, want 3 opened and 1 dropped", s.Metrics)
	}
	if s.Metrics["missed"] != 0 {
		t.Errorf("missed = %v, want 0: a dropped stream is not a miss", s.Metrics["missed"])
	}
}

func TestStreamsCountsMissed(t *testing.T) {
	defer func(d time.Duration) { missAfter = d }(missAfter)
	missAfter = 200 * time.Millisecond
	f := &fakeServer{delay: 10 * time.Millisecond, silent: map[int]bool{2: true}}
	srv := newFakeServer(t, f)
	s := runStreams(t, srv, "--streams", "2", "--ramp-seconds", "0", "--hold", "0.6", "--flip-every", "0.3")
	if s.Metrics["flips"] != 2 || s.Metrics["missed"] != 2 || s.Metrics["streams_dropped"] != 0 {
		t.Errorf("metrics = %v, want 2 flips each missed by one stream", s.Metrics)
	}
}

func TestStreamsRampUntilError(t *testing.T) {
	f := &fakeServer{accept: 5}
	srv := newFakeServer(t, f)
	s := runStreams(t, srv, "--ramp-until-error", "--max", "50", "--ramp-seconds", "0.5", "--hold", "0", "--flip-every", "0")
	if s.Metrics["capacity"] != 5 || s.Metrics["streams_opened"] != 5 {
		t.Errorf("metrics = %v, want capacity 5", s.Metrics)
	}
	if opened, _ := f.counts(); opened != 5 {
		t.Errorf("server accepted %d streams, want 5", opened)
	}
}

func TestStreamsRequiresTargetAndKey(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run([]string{"streams", "--streams", "1", "--out", filepath.Join(t.TempDir(), "x.json")}, &stdout, &stderr); code == 0 {
		t.Fatal("mfload streams without --target and --key succeeded")
	}
}
