package delivery

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/transport"
)

const syncKey = "mf_server_sync-key-value"

// rulesetJSON is a valid empty ruleset for environment env.
func rulesetJSON(env string) string {
	return fmt.Sprintf(`{"schema_version":1,"project":"p","environment":%q,"flags":{},"segments":{}}`, env)
}

func putEvent(id, raw string) string {
	return "event: put\nid: " + id + "\ndata: " + raw + "\n\n"
}

// route serves one handler per request, in order; the last handler serves
// every later request. It records requests and their arrival times.
type route struct {
	mu       sync.Mutex
	handlers []http.HandlerFunc
	requests []*http.Request
	times    []time.Time
}

func (rt *route) serve(w http.ResponseWriter, r *http.Request) {
	rt.mu.Lock()
	n := len(rt.requests)
	rt.requests = append(rt.requests, r.Clone(context.Background()))
	rt.times = append(rt.times, time.Now())
	h := rt.handlers[min(n, len(rt.handlers)-1)]
	rt.mu.Unlock()
	h(w, r)
}

func (rt *route) count() int {
	rt.mu.Lock()
	defer rt.mu.Unlock()
	return len(rt.requests)
}

func (rt *route) request(i int) *http.Request {
	rt.mu.Lock()
	defer rt.mu.Unlock()
	return rt.requests[i]
}

func (rt *route) arrivals() []time.Time {
	rt.mu.Lock()
	defer rt.mu.Unlock()
	return append([]time.Time(nil), rt.times...)
}

// fakeAPI serves /sdk/v1/stream and /sdk/v1/ruleset.
type fakeAPI struct {
	*httptest.Server
	stream, ruleset *route
}

func newFakeAPI(t *testing.T, stream, ruleset []http.HandlerFunc) *fakeAPI {
	t.Helper()
	api := &fakeAPI{stream: &route{handlers: stream}, ruleset: &route{handlers: ruleset}}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /sdk/v1/stream", api.stream.serve)
	mux.HandleFunc("GET /sdk/v1/ruleset", api.ruleset.serve)
	api.Server = httptest.NewServer(mux)
	t.Cleanup(api.Close)
	return api
}

// rulesetBody answers 304 when If-None-Match matches etag, and the ruleset
// otherwise.
func rulesetBody(raw, etag string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("If-None-Match") == etag {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		w.Header().Set("ETag", etag)
		fmt.Fprint(w, raw)
	}
}

// syncBuffer is a bytes.Buffer safe for the logger and the test to share.
type syncBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

type statusEvent struct {
	status Status
	err    error
}

// syncRecorder collects the Sync callbacks.
type syncRecorder struct {
	statusCh chan statusEvent
	changeCh chan [2]*Snapshot
	logs     syncBuffer
}

func newSyncRecorder() *syncRecorder {
	return &syncRecorder{statusCh: make(chan statusEvent, 1000), changeCh: make(chan [2]*Snapshot, 1000)}
}

func (r *syncRecorder) config(baseURL string) SyncConfig {
	return SyncConfig{
		BaseURL:      baseURL,
		Key:          syncKey,
		UserAgent:    "megooflags-go/test",
		Logger:       slog.New(slog.NewTextHandler(&r.logs, nil)),
		PollInterval: 50 * time.Millisecond,
		Store:        &Store{},
		OnChange:     func(old, n *Snapshot) { r.changeCh <- [2]*Snapshot{old, n} },
		OnStatus:     func(st Status, err error) { r.statusCh <- statusEvent{st, err} },
		DeadTimeout:  time.Second,
		StableAfter:  time.Second,
		RetryBase:    10 * time.Millisecond,
		RetryMax:     20 * time.Millisecond,
	}
}

func startSync(t *testing.T, cfg SyncConfig) *Sync {
	t.Helper()
	s := NewSync(cfg)
	s.Start()
	t.Cleanup(s.Stop)
	return s
}

func (r *syncRecorder) waitChange(t *testing.T) [2]*Snapshot {
	t.Helper()
	select {
	case c := <-r.changeCh:
		return c
	case <-time.After(5 * time.Second):
		t.Fatal("no OnChange")
		return [2]*Snapshot{}
	}
}

func (r *syncRecorder) waitStatus(t *testing.T, want Status) statusEvent {
	t.Helper()
	timeout := time.After(5 * time.Second)
	for {
		select {
		case ev := <-r.statusCh:
			if ev.status == want {
				return ev
			}
		case <-timeout:
			t.Fatalf("no status %v", want)
			return statusEvent{}
		}
	}
}

func waitRoute(t *testing.T, rt *route, n int) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for rt.count() < n {
		if time.Now().After(deadline) {
			t.Fatalf("route saw %d requests, want %d", rt.count(), n)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func TestSyncStreamPutUpdatesStoreAndCache(t *testing.T) {
	raw := rulesetJSON("e1")
	api := newFakeAPI(t, []http.HandlerFunc{sse(putEvent("v1", raw))}, []http.HandlerFunc{status(500)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	cfg.Cache = NewCache(t.TempDir(), syncKey)
	startSync(t, cfg)

	rec.waitStatus(t, StatusReady)
	c := rec.waitChange(t)
	if c[0] != nil {
		t.Errorf("old = %+v, want nil", c[0])
	}
	snap := cfg.Store.Load()
	if snap != c[1] || snap.Source != "stream" || snap.ETag != "v1" || snap.Ruleset.Environment != "e1" || string(snap.Raw) != raw {
		t.Fatalf("store = %+v", snap)
	}
	gotRaw, etag, err := cfg.Cache.Load()
	if err != nil || etag != "v1" || string(gotRaw) != raw {
		t.Errorf("cache = (%q, %q, %v)", gotRaw, etag, err)
	}
	if req := api.stream.request(0); req.Header.Get("Authorization") != "Bearer "+syncKey {
		t.Errorf("Authorization = %q", req.Header.Get("Authorization"))
	}
	if n := api.ruleset.count(); n != 0 {
		t.Errorf("ruleset polled %d times while the stream is up", n)
	}
}

func TestSyncInvalidPutKeepsPrevious(t *testing.T) {
	invalid := strings.Replace(rulesetJSON("e2"), `"schema_version":1`, `"schema_version":2`, 1)
	api := newFakeAPI(t,
		[]http.HandlerFunc{sse(putEvent("v1", rulesetJSON("e1")), putEvent("v2", invalid))},
		[]http.HandlerFunc{status(500)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	startSync(t, cfg)

	rec.waitChange(t)
	deadline := time.Now().Add(5 * time.Second)
	for !strings.Contains(rec.logs.String(), "source=stream") {
		if time.Now().After(deadline) {
			t.Fatalf("log = %q, want the rejected stream ruleset", rec.logs.String())
		}
		time.Sleep(5 * time.Millisecond)
	}
	if !strings.Contains(rec.logs.String(), "level=WARN") || !strings.Contains(rec.logs.String(), "schema_version") {
		t.Errorf("log = %q, want a warning naming the parse error", rec.logs.String())
	}
	if snap := cfg.Store.Load(); snap.ETag != "v1" || snap.Ruleset.Environment != "e1" {
		t.Errorf("store = %+v, want the first ruleset", snap)
	}
	select {
	case c := <-rec.changeCh:
		t.Errorf("unexpected OnChange %+v", c)
	default:
	}
}

func TestSyncFallsBackToPolling(t *testing.T) {
	raw := rulesetJSON("e1")
	api := newFakeAPI(t, []http.HandlerFunc{status(500)}, []http.HandlerFunc{rulesetBody(raw, `"r1"`)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	cfg.Cache = NewCache(t.TempDir(), syncKey)
	startSync(t, cfg)

	if ev := rec.waitStatus(t, StatusStale); ev.err == nil {
		t.Error("stale status without an error")
	}
	waitRoute(t, api.ruleset, 2)
	if n := api.stream.count(); n < 2 {
		t.Errorf("polled after %d stream failures, want 2", n)
	}
	if got := api.ruleset.request(0).Header.Get("If-None-Match"); got != "" {
		t.Errorf("first poll If-None-Match = %q, want none", got)
	}
	if got := api.ruleset.request(1).Header.Get("If-None-Match"); got != `"r1"` {
		t.Errorf("second poll If-None-Match = %q", got)
	}
	snap := cfg.Store.Load()
	if snap == nil || snap.Source != "poll" || snap.ETag != `"r1"` || snap.Ruleset.Environment != "e1" {
		t.Fatalf("store = %+v", snap)
	}
	if _, etag, err := cfg.Cache.Load(); err != nil || etag != `"r1"` {
		t.Errorf("cache etag = %q, %v", etag, err)
	}
}

func TestSyncStopsPollingWhenStreamReconnects(t *testing.T) {
	api := newFakeAPI(t,
		[]http.HandlerFunc{status(500), status(500), sse(": hello\n\n")},
		[]http.HandlerFunc{rulesetBody(rulesetJSON("e1"), `"r1"`)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	startSync(t, cfg)

	waitRoute(t, api.ruleset, 1)
	rec.waitStatus(t, StatusReady)
	time.Sleep(20 * time.Millisecond)
	before := api.ruleset.count()
	time.Sleep(4 * cfg.PollInterval)
	if n := api.ruleset.count(); n != before {
		t.Errorf("ruleset polled %d more times while the stream is connected", n-before)
	}
}

func TestSyncPollIntervalRespected(t *testing.T) {
	// The stream flaps: two failures, then a short-lived connection, again and
	// again, so the poll loop is started and stopped many times.
	var mu sync.Mutex
	n := 0
	flap := func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		n++
		up := n%3 == 0
		mu.Unlock()
		if !up {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		time.Sleep(5 * time.Millisecond)
	}
	api := newFakeAPI(t, []http.HandlerFunc{flap}, []http.HandlerFunc{rulesetBody(rulesetJSON("e1"), `"r1"`)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	cfg.PollInterval = 100 * time.Millisecond
	s := startSync(t, cfg)

	time.Sleep(900 * time.Millisecond)
	s.Stop()
	times := api.ruleset.arrivals()
	if len(times) < 3 {
		t.Fatalf("only %d polls in 900ms", len(times))
	}
	if api.stream.count() < 9 {
		t.Fatalf("stream flapped only %d times", api.stream.count())
	}
	// Arrival times are taken on the server, so allow a little network jitter.
	for i := 1; i < len(times); i++ {
		if gap := times[i].Sub(times[i-1]); gap < cfg.PollInterval-5*time.Millisecond {
			t.Errorf("polls %d and %d are %v apart, want >= %v", i-1, i, gap, cfg.PollInterval)
		}
	}
}

func TestSyncDisableStream(t *testing.T) {
	api := newFakeAPI(t, []http.HandlerFunc{sse()}, []http.HandlerFunc{rulesetBody(rulesetJSON("e1"), `"r1"`)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	cfg.DisableStream = true
	startSync(t, cfg)

	rec.waitStatus(t, StatusReady)
	if snap := cfg.Store.Load(); snap == nil || snap.Source != "poll" {
		t.Fatalf("store = %+v", snap)
	}
	waitRoute(t, api.ruleset, 3)
	if n := api.stream.count(); n != 0 {
		t.Errorf("stream called %d times with DisableStream", n)
	}
}

func TestSyncDisableStreamPollFailureIsStale(t *testing.T) {
	api := newFakeAPI(t, []http.HandlerFunc{sse()}, []http.HandlerFunc{status(500)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	cfg.DisableStream = true
	startSync(t, cfg)

	ev := rec.waitStatus(t, StatusStale)
	var he *transport.HTTPError
	if !errors.As(ev.err, &he) || he.Status != 500 {
		t.Errorf("stale err = %v, want HTTP 500", ev.err)
	}
}

func TestSyncPollRetryAfter(t *testing.T) {
	api := newFakeAPI(t, []http.HandlerFunc{sse()},
		[]http.HandlerFunc{status(429, "Retry-After", "1"), rulesetBody(rulesetJSON("e1"), `"r1"`)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	cfg.DisableStream = true
	startSync(t, cfg)

	waitRoute(t, api.ruleset, 2)
	times := api.ruleset.arrivals()
	if gap := times[1].Sub(times[0]); gap < 950*time.Millisecond {
		t.Errorf("poll after a 429 came %v later, want >= Retry-After 1s", gap)
	}
}

// assertQuiet checks that neither route is called again.
func assertQuiet(t *testing.T, api *fakeAPI) {
	t.Helper()
	time.Sleep(20 * time.Millisecond)
	streams, polls := api.stream.count(), api.ruleset.count()
	time.Sleep(250 * time.Millisecond)
	if n := api.stream.count(); n != streams {
		t.Errorf("stream called %d more times after a fatal error", n-streams)
	}
	if n := api.ruleset.count(); n != polls {
		t.Errorf("ruleset called %d more times after a fatal error", n-polls)
	}
}

func TestSyncRevokedIsFatal(t *testing.T) {
	api := newFakeAPI(t,
		[]http.HandlerFunc{status(500), status(500), sse("event: revoked\ndata: {}\n\n")},
		[]http.HandlerFunc{rulesetBody(rulesetJSON("e1"), `"r1"`)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	cfg.PollInterval = 20 * time.Millisecond
	startSync(t, cfg)

	ev := rec.waitStatus(t, StatusFatal)
	var fe *transport.FatalError
	if !errors.As(ev.err, &fe) || fe.Reason != "revoked" {
		t.Errorf("fatal err = %v, want revoked", ev.err)
	}
	if api.ruleset.count() == 0 {
		t.Error("the poll loop never ran before the revocation")
	}
	assertQuiet(t, api)
}

func TestSync403OnPollIsFatal(t *testing.T) {
	api := newFakeAPI(t, []http.HandlerFunc{status(500)}, []http.HandlerFunc{status(403)})
	rec := newSyncRecorder()
	cfg := rec.config(api.URL)
	startSync(t, cfg)

	ev := rec.waitStatus(t, StatusFatal)
	var fe *transport.FatalError
	if !errors.As(ev.err, &fe) || fe.Status != 403 {
		t.Errorf("fatal err = %v, want 403", ev.err)
	}
	assertQuiet(t, api)
	if n := len(rec.statusCh); n != 0 {
		t.Errorf("%d status callbacks after the fatal error", n)
	}
}

func TestSyncStopIdempotent(t *testing.T) {
	api := newFakeAPI(t, []http.HandlerFunc{sse()}, []http.HandlerFunc{status(500)})
	rec := newSyncRecorder()
	s := NewSync(rec.config(api.URL))
	s.Start()
	rec.waitStatus(t, StatusReady)

	done := make(chan struct{})
	go func() {
		var wg sync.WaitGroup
		for range 3 {
			wg.Go(s.Stop)
		}
		wg.Wait()
		s.Stop()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("Stop did not return")
	}

	NewSync(rec.config(api.URL)).Stop() // never started
}

func TestApplyFromBootstrapDoesNotWriteCache(t *testing.T) {
	rec := newSyncRecorder()
	cfg := rec.config("http://127.0.0.1:1")
	cfg.Cache = NewCache(t.TempDir(), syncKey)
	s := NewSync(cfg)

	for _, source := range []string{"bootstrap", "cache"} {
		if err := s.Apply([]byte(rulesetJSON(source)), "", source); err != nil {
			t.Fatalf("Apply(%s): %v", source, err)
		}
		if snap := cfg.Store.Load(); snap.Source != source || snap.Ruleset.Environment != source {
			t.Errorf("store = %+v", snap)
		}
		if _, _, err := cfg.Cache.Load(); !errors.Is(err, os.ErrNotExist) {
			t.Errorf("cache after %s: %v, want not exist", source, err)
		}
	}
	c1, c2 := rec.waitChange(t), rec.waitChange(t)
	if c1[0] != nil || c2[0] != c1[1] {
		t.Errorf("OnChange old/new chain broken: %+v, %+v", c1, c2)
	}

	if err := s.Apply([]byte("{"), "x", "poll"); err == nil {
		t.Error("Apply accepted invalid JSON")
	}
	if snap := cfg.Store.Load(); snap.Source != "cache" {
		t.Errorf("store changed by an invalid ruleset: %+v", snap)
	}
	if err := s.Apply([]byte(rulesetJSON("p")), `"e"`, "poll"); err != nil {
		t.Fatal(err)
	}
	if _, etag, err := cfg.Cache.Load(); err != nil || etag != `"e"` {
		t.Errorf("cache after poll = %q, %v", etag, err)
	}
}
