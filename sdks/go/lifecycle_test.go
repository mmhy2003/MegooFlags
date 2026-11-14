package megooflags

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/open-feature/go-sdk/openfeature"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/delivery"
	"github.com/mmhy2003/MegooFlags/sdks/go/internal/events"
)

const waitFor = 5 * time.Second

// fakeAPI serves /sdk/v1/stream, /sdk/v1/ruleset and /sdk/v1/events. Each
// frame sent with send goes to the open stream; drop ends it.
type fakeAPI struct {
	srv *httptest.Server

	frames    chan string
	drops     chan struct{}
	connected chan struct{} // one value per stream that answered 200
	closed    chan struct{}

	mu           sync.Mutex
	streamStatus int // 0: 200
	requests     map[string]int
	batches      []events.Batch
}

func newFakeAPI(t *testing.T) *fakeAPI {
	t.Helper()
	api := &fakeAPI{
		frames:    make(chan string),
		drops:     make(chan struct{}),
		connected: make(chan struct{}, 16),
		closed:    make(chan struct{}),
		requests:  map[string]int{},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /sdk/v1/stream", api.stream)
	mux.HandleFunc("GET /sdk/v1/ruleset", func(w http.ResponseWriter, r *http.Request) {
		api.count(r)
		w.WriteHeader(http.StatusServiceUnavailable)
	})
	mux.HandleFunc("POST /sdk/v1/events", func(w http.ResponseWriter, r *http.Request) {
		api.count(r)
		var b events.Batch
		if err := json.NewDecoder(r.Body).Decode(&b); err != nil {
			w.WriteHeader(http.StatusUnprocessableEntity)
			return
		}
		api.mu.Lock()
		api.batches = append(api.batches, b)
		api.mu.Unlock()
		w.WriteHeader(http.StatusAccepted)
	})
	api.srv = httptest.NewServer(mux)
	t.Cleanup(func() {
		close(api.closed)
		api.srv.Close()
	})
	return api
}

func (a *fakeAPI) count(r *http.Request) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.requests[r.URL.Path]++
}

func (a *fakeAPI) total() int {
	a.mu.Lock()
	defer a.mu.Unlock()
	n := 0
	for _, c := range a.requests {
		n += c
	}
	return n
}

func (a *fakeAPI) requestsTo(path string) int {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.requests[path]
}

func (a *fakeAPI) setStreamStatus(status int) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.streamStatus = status
}

func (a *fakeAPI) eventBatches() []events.Batch {
	a.mu.Lock()
	defer a.mu.Unlock()
	return append([]events.Batch(nil), a.batches...)
}

func (a *fakeAPI) stream(w http.ResponseWriter, r *http.Request) {
	a.count(r)
	a.mu.Lock()
	status := a.streamStatus
	a.mu.Unlock()
	if status != 0 {
		w.WriteHeader(status)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.WriteHeader(http.StatusOK)
	w.(http.Flusher).Flush()
	a.connected <- struct{}{}
	for {
		select {
		case f := <-a.frames:
			_, _ = w.Write([]byte(f))
			w.(http.Flusher).Flush()
		case <-a.drops:
			return
		case <-r.Context().Done():
			return
		case <-a.closed:
			return
		}
	}
}

// send writes frame to the open stream, waiting for one to connect.
func (a *fakeAPI) send(t *testing.T, frame string) {
	t.Helper()
	select {
	case a.frames <- frame:
	case <-time.After(waitFor):
		t.Fatal("no stream took the frame")
	}
}

func (a *fakeAPI) put(t *testing.T, id, raw string) {
	t.Helper()
	a.send(t, "event: put\nid: "+id+"\ndata: "+raw+"\n\n")
}

func (a *fakeAPI) drop(t *testing.T) {
	t.Helper()
	select {
	case a.drops <- struct{}{}:
	case <-time.After(waitFor):
		t.Fatal("no stream to drop")
	}
}

func (a *fakeAPI) waitConnected(t *testing.T) {
	t.Helper()
	select {
	case <-a.connected:
	case <-time.After(waitFor):
		t.Fatal("the stream did not connect")
	}
}

// ruleset returns testdata/ruleset.json, with mutate applied to its flags, as
// one line of JSON.
func ruleset(t *testing.T, mutate func(flags map[string]any)) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", "ruleset.json"))
	if err != nil {
		t.Fatal(err)
	}
	var rs map[string]any
	if err := json.Unmarshal(data, &rs); err != nil {
		t.Fatal(err)
	}
	if mutate != nil {
		mutate(rs["flags"].(map[string]any))
	}
	out, err := json.Marshal(rs)
	if err != nil {
		t.Fatal(err)
	}
	return string(out)
}

// changedRuleset serves red for string-flag (version 2), removes targeted and
// adds new-flag.
func changedRuleset(t *testing.T) string {
	return ruleset(t, func(flags map[string]any) {
		sf := flags["string-flag"].(map[string]any)
		sf["version"] = 2
		sf["fallthrough"] = map[string]any{"variation": "red"}
		delete(flags, "targeted")
		nf := map[string]any{}
		for k, v := range sf {
			nf[k] = v
		}
		nf["key"] = "new-flag"
		flags["new-flag"] = nf
	})
}

func onlineConfig(api *fakeAPI) Config {
	return Config{SDKKey: testKey, BaseURL: api.srv.URL, Logger: discardLogger()}
}

func startProvider(t *testing.T, cfg Config) *Provider {
	t.Helper()
	p := NewProvider(cfg)
	t.Cleanup(p.Shutdown)
	return p
}

// initAsync runs Init on its own goroutine.
func initAsync(p *Provider) <-chan error {
	ch := make(chan error, 1)
	go func() { ch <- p.Init(openfeature.EvaluationContext{}) }()
	return ch
}

func waitInit(t *testing.T, ch <-chan error) error {
	t.Helper()
	select {
	case err := <-ch:
		return err
	case <-time.After(waitFor):
		t.Fatal("Init did not return")
		return nil
	}
}

// waitEvent returns the next event of type typ, skipping others.
func waitEvent(t *testing.T, p *Provider, typ openfeature.EventType) openfeature.Event {
	t.Helper()
	deadline := time.After(waitFor)
	for {
		select {
		case ev := <-p.EventChannel():
			if ev.EventType == typ {
				return ev
			}
			t.Logf("skipping event %s", ev.EventType)
		case <-deadline:
			t.Fatalf("no %s event", typ)
		}
	}
}

func noEvent(t *testing.T, p *Provider, within time.Duration) {
	t.Helper()
	select {
	case ev := <-p.EventChannel():
		t.Fatalf("unexpected event %s %+v", ev.EventType, ev.ProviderEventDetails)
	case <-time.After(within):
	}
}

func stringValue(p *Provider, flag string) string {
	return p.StringEvaluation(context.Background(), flag, "default", nil).Value
}

func TestInitWaitsForFirstPut(t *testing.T) {
	api := newFakeAPI(t)
	p := startProvider(t, onlineConfig(api))
	done := initAsync(p)
	api.waitConnected(t)
	select {
	case err := <-done:
		t.Fatalf("Init returned %v before the first put", err)
	case <-time.After(100 * time.Millisecond):
	}
	api.put(t, "e1", ruleset(t, nil))
	if err := waitInit(t, done); err != nil {
		t.Fatalf("Init = %v, want nil", err)
	}
	if got := stringValue(p, "string-flag"); got != "blue" {
		t.Errorf("string-flag = %q, want blue", got)
	}
}

func TestInitTimeoutThenReady(t *testing.T) {
	api := newFakeAPI(t)
	cfg := onlineConfig(api)
	cfg.InitTimeout = 200 * time.Millisecond
	p := startProvider(t, cfg)
	err := waitInit(t, initAsync(p))
	if err == nil || !strings.Contains(err.Error(), "no ruleset within 200ms") {
		t.Fatalf("Init = %v, want the timeout error", err)
	}
	var initErr *openfeature.ProviderInitError
	if errors.As(err, &initErr) {
		t.Errorf("Init = %v, want a non-fatal error", err)
	}
	d := p.StringEvaluation(context.Background(), "string-flag", "default", nil)
	wantResolution(t, d.ProviderResolutionDetail, openfeature.ErrorReason, "", openfeature.ProviderNotReadyCode)

	api.put(t, "e1", ruleset(t, nil))
	waitEvent(t, p, openfeature.ProviderReady)
	if got := stringValue(p, "string-flag"); got != "blue" {
		t.Errorf("string-flag = %q, want blue", got)
	}
}

func TestInitReadyFromBootstrapThenRefresh(t *testing.T) {
	api := newFakeAPI(t)
	cfg := onlineConfig(api)
	cfg.BootstrapFile = filepath.Join("testdata", "ruleset.json")
	p := startProvider(t, cfg)
	if err := waitInit(t, initAsync(p)); err != nil {
		t.Fatalf("Init = %v, want nil at once", err)
	}
	if got := stringValue(p, "string-flag"); got != "blue" {
		t.Errorf("string-flag = %q, want the bootstrap's blue", got)
	}
	api.put(t, "e2", changedRuleset(t))
	ev := waitEvent(t, p, openfeature.ProviderConfigChange)
	if want := []string{"new-flag", "string-flag", "targeted"}; !reflect.DeepEqual(ev.FlagChanges, want) {
		t.Errorf("FlagChanges = %v, want %v", ev.FlagChanges, want)
	}
	if got := stringValue(p, "string-flag"); got != "red" {
		t.Errorf("string-flag = %q, want red after the put", got)
	}
}

func TestInitFromCacheOffline(t *testing.T) {
	api := newFakeAPI(t)
	dir := t.TempDir()
	cfg := onlineConfig(api)
	cfg.CacheDir = dir
	first := NewProvider(cfg)
	done := initAsync(first)
	api.put(t, "e2", changedRuleset(t))
	if err := waitInit(t, done); err != nil {
		t.Fatalf("first Init = %v", err)
	}
	first.Shutdown()
	before := api.total()

	p := startProvider(t, Config{SDKKey: testKey, BaseURL: api.srv.URL, CacheDir: dir, Offline: true, Logger: discardLogger()})
	if err := waitInit(t, initAsync(p)); err != nil {
		t.Fatalf("offline Init = %v, want nil", err)
	}
	if got := stringValue(p, "string-flag"); got != "red" {
		t.Errorf("string-flag = %q, want the cached red", got)
	}
	p.Shutdown()
	if after := api.total(); after != before {
		t.Errorf("the offline provider made %d HTTP requests", after-before)
	}
}

func TestInitOfflineWithoutRulesetFails(t *testing.T) {
	p := startProvider(t, Config{SDKKey: testKey, CacheDir: t.TempDir(), Offline: true, Logger: discardLogger()})
	if err := waitInit(t, initAsync(p)); err == nil {
		t.Fatal("Init = nil, want an error with no bootstrap or cached ruleset")
	}
}

func TestStartupOrderBootstrapBeforeCache(t *testing.T) {
	dir := t.TempDir()
	if err := delivery.NewCache(dir, testKey).Save([]byte(changedRuleset(t)), "e2"); err != nil {
		t.Fatal(err)
	}
	p := startProvider(t, Config{
		SDKKey: testKey, BootstrapFile: filepath.Join("testdata", "ruleset.json"),
		CacheDir: dir, Offline: true, Logger: discardLogger(),
	})
	if err := waitInit(t, initAsync(p)); err != nil {
		t.Fatalf("Init = %v", err)
	}
	if got := stringValue(p, "string-flag"); got != "blue" {
		t.Errorf("string-flag = %q, want the bootstrap's blue over the cache", got)
	}

	missing := startProvider(t, Config{
		SDKKey: testKey, BootstrapFile: filepath.Join(dir, "missing.json"),
		CacheDir: dir, Offline: true, Logger: discardLogger(),
	})
	if err := waitInit(t, initAsync(missing)); err != nil {
		t.Fatalf("Init = %v", err)
	}
	if got := stringValue(missing, "string-flag"); got != "red" {
		t.Errorf("string-flag = %q, want the cache's red when the bootstrap file is missing", got)
	}
}

func TestInitClientKeyFatal(t *testing.T) {
	p := startProvider(t, Config{SDKKey: "mf_client_abc", BaseURL: "http://localhost", Logger: discardLogger()})
	err := waitInit(t, initAsync(p))
	var initErr *openfeature.ProviderInitError
	if !errors.As(err, &initErr) || initErr.ErrorCode != openfeature.ProviderFatalCode {
		t.Fatalf("Init = %v, want a ProviderInitError with %s", err, openfeature.ProviderFatalCode)
	}
}

func TestInitRefusedKeyFatal(t *testing.T) {
	api := newFakeAPI(t)
	api.setStreamStatus(http.StatusUnauthorized)
	p := startProvider(t, onlineConfig(api))
	err := waitInit(t, initAsync(p))
	var initErr *openfeature.ProviderInitError
	if !errors.As(err, &initErr) || initErr.ErrorCode != openfeature.ProviderFatalCode {
		t.Fatalf("Init = %v, want a ProviderInitError with %s", err, openfeature.ProviderFatalCode)
	}
	d := p.StringEvaluation(context.Background(), "string-flag", "default", nil)
	wantResolution(t, d.ProviderResolutionDetail, openfeature.ErrorReason, "", openfeature.ProviderFatalCode)
}

func TestRevokedEmitsFatalErrorAndKeepsServing(t *testing.T) {
	api := newFakeAPI(t)
	p := startProvider(t, onlineConfig(api))
	done := initAsync(p)
	api.put(t, "e2", changedRuleset(t))
	if err := waitInit(t, done); err != nil {
		t.Fatalf("Init = %v", err)
	}
	api.send(t, "event: revoked\ndata: {}\n\n")
	ev := waitEvent(t, p, openfeature.ProviderError)
	if ev.ErrorCode != openfeature.ProviderFatalCode || ev.Message == "" {
		t.Errorf("event = %+v, want ErrorCode %s and a message", ev.ProviderEventDetails, openfeature.ProviderFatalCode)
	}
	if got := stringValue(p, "string-flag"); got != "red" {
		t.Errorf("string-flag = %q, want the last ruleset's red", got)
	}
	time.Sleep(200 * time.Millisecond) // several reconnect delays
	if n := api.requestsTo("/sdk/v1/stream"); n != 1 {
		t.Errorf("stream requests = %d, want 1 (no reconnect after revoked)", n)
	}
	p.Shutdown()
	if n := api.requestsTo("/sdk/v1/events"); n != 0 {
		t.Errorf("events requests = %d, want none after a fatal status", n)
	}
}

func TestStaleThenReadyEvents(t *testing.T) {
	api := newFakeAPI(t)
	p := startProvider(t, onlineConfig(api))
	done := initAsync(p)
	api.put(t, "e1", ruleset(t, nil))
	if err := waitInit(t, done); err != nil {
		t.Fatalf("Init = %v", err)
	}
	api.waitConnected(t)
	api.drop(t)
	waitEvent(t, p, openfeature.ProviderStale)
	if got := stringValue(p, "string-flag"); got != "blue" {
		t.Errorf("string-flag = %q while stale, want blue", got)
	}
	ev := waitEvent(t, p, openfeature.ProviderReady)
	if ev.ErrorCode != "" {
		t.Errorf("ready event = %+v", ev.ProviderEventDetails)
	}
	noEvent(t, p, 100*time.Millisecond)
}

func TestEventsSentOnShutdown(t *testing.T) {
	for _, sendContext := range []bool{false, true} {
		api := newFakeAPI(t)
		cfg := onlineConfig(api)
		cfg.SendContext = sendContext
		p := NewProvider(cfg)
		done := initAsync(p)
		api.put(t, "e1", ruleset(t, nil))
		if err := waitInit(t, done); err != nil {
			t.Fatalf("Init = %v", err)
		}
		alice := openfeature.FlattenedContext{openfeature.TargetingKey: "alice", "plan": "pro"}
		bob := openfeature.FlattenedContext{openfeature.TargetingKey: "bob"}
		for range 3 {
			p.StringEvaluation(context.Background(), "string-flag", "x", alice)
		}
		p.StringEvaluation(context.Background(), "string-flag", "x", bob)
		p.BooleanEvaluation(context.Background(), "bool-flag", false, alice)
		p.StringEvaluation(context.Background(), "missing", "x", alice)
		p.Shutdown()

		var exposures []events.Exposure
		counts := map[string]int64{}
		for _, b := range api.eventBatches() {
			exposures = append(exposures, b.Exposures...)
			for _, c := range b.Counts {
				v := "<nil>"
				if c.Variant != nil {
					v = *c.Variant
				}
				counts[c.FlagKey+"/"+v] += c.Count
			}
		}
		wantCounts := map[string]int64{"string-flag/blue": 4, "bool-flag/on": 1, "missing/<nil>": 1}
		if !reflect.DeepEqual(counts, wantCounts) {
			t.Errorf("SendContext=%v: counts = %v, want %v", sendContext, counts, wantCounts)
		}
		if len(exposures) != 3 {
			t.Fatalf("SendContext=%v: %d exposures, want 3 (alice and bob on string-flag, alice on bool-flag): %+v",
				sendContext, len(exposures), exposures)
		}
		for _, e := range exposures {
			if !sendContext && e.Context != nil {
				t.Errorf("exposure %s has context %v without SendContext", e.FlagKey, e.Context)
			}
			if sendContext && (e.Context == nil || e.Context["key"] == nil) {
				t.Errorf("exposure %s has context %v, want the evaluation context", e.FlagKey, e.Context)
			}
		}
		if sendContext {
			if e := exposures[0]; e.FlagKey != "string-flag" || e.Context["plan"] != "pro" || e.Context["key"] != "alice" {
				t.Errorf("first exposure = %+v, want alice's context", e)
			}
		}
	}
}

func TestShutdownIdempotent(t *testing.T) {
	api := newFakeAPI(t)
	cfg := onlineConfig(api)
	cfg.BootstrapFile = filepath.Join("testdata", "ruleset.json")
	p := NewProvider(cfg)
	if err := waitInit(t, initAsync(p)); err != nil {
		t.Fatalf("Init = %v", err)
	}
	api.waitConnected(t)
	p.Shutdown()
	p.Shutdown()
	if err := waitInit(t, initAsync(p)); err == nil {
		t.Error("Init after Shutdown = nil, want an error")
	}
	select {
	case _, ok := <-p.EventChannel():
		if !ok {
			t.Error("the event channel was closed")
		}
	default:
	}
	if got := stringValue(p, "string-flag"); got != "blue" {
		t.Errorf("string-flag = %q after Shutdown, want blue", got)
	}
}

func TestOpenFeatureClientEndToEnd(t *testing.T) {
	api := newFakeAPI(t)
	cfg := onlineConfig(api)
	cfg.BootstrapFile = filepath.Join("testdata", "ruleset.json")
	p := NewProvider(cfg)
	const domain = "megooflags-lifecycle-test"
	if err := openfeature.SetNamedProviderAndWait(domain, p); err != nil {
		t.Fatalf("SetNamedProviderAndWait = %v", err)
	}
	t.Cleanup(openfeature.Shutdown)
	client := openfeature.NewClient(domain)
	changes := make(chan openfeature.EventDetails, 4)
	onChange := func(d openfeature.EventDetails) { changes <- d }
	client.AddHandler(openfeature.ProviderConfigChange, &onChange)

	evalCtx := openfeature.NewEvaluationContext("alice", map[string]any{"plan": "pro"})
	on, err := client.BooleanValue(context.Background(), "bool-flag", false, evalCtx)
	if err != nil || !on {
		t.Errorf("BooleanValue = %v, %v; want true, nil", on, err)
	}
	d, err := client.StringValueDetails(context.Background(), "string-flag", "default", evalCtx)
	if err != nil || d.Value != "blue" || d.Variant != "blue" || d.Reason != openfeature.StaticReason {
		t.Errorf("StringValueDetails = %+v, %v; want blue/blue/STATIC", d, err)
	}

	api.put(t, "e2", changedRuleset(t))
	select {
	case ev := <-changes:
		if want := []string{"new-flag", "string-flag", "targeted"}; !reflect.DeepEqual(ev.FlagChanges, want) {
			t.Errorf("FlagChanges = %v, want %v", ev.FlagChanges, want)
		}
	case <-time.After(waitFor):
		t.Fatal("the handler did not receive PROVIDER_CONFIGURATION_CHANGED")
	}
	s, err := client.StringValue(context.Background(), "string-flag", "default", evalCtx)
	if err != nil || s != "red" {
		t.Errorf("StringValue = %q, %v; want red after the put", s, err)
	}
}
