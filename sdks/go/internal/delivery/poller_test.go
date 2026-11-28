package delivery

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/transport"
)

const pollKey = "mf_server_poll-key-value"

// pollRecorder records the OnRuleset calls of a Poller.
type pollRecorder struct {
	calls []pollCall
	valid bool
}

type pollCall struct {
	raw  string
	etag string
}

func (r *pollRecorder) onRuleset(raw []byte, etag string) bool {
	r.calls = append(r.calls, pollCall{string(raw), etag})
	return r.valid
}

func newTestPoller(url, etag string, rec *pollRecorder) *Poller {
	return NewPoller(PollerConfig{
		URL:       url,
		Key:       pollKey,
		UserAgent: "megooflags-go/test",
		ETag:      func() string { return etag },
		OnRuleset: rec.onRuleset,
	})
}

func TestPollerFirstPollNoIfNoneMatch(t *testing.T) {
	var got *http.Request
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r.Clone(context.Background())
		w.Header().Set("ETag", `"e1"`)
		_, _ = w.Write([]byte(`{"flags":[]}`))
	}))
	defer srv.Close()
	rec := &pollRecorder{valid: true}

	if err := newTestPoller(srv.URL+"/sdk/v1/ruleset", "", rec).Poll(context.Background()); err != nil {
		t.Fatalf("Poll: %v", err)
	}
	if v, ok := got.Header["If-None-Match"]; ok {
		t.Errorf("If-None-Match = %q, want no header", v)
	}
	if a := got.Header.Get("Authorization"); a != "Bearer "+pollKey {
		t.Errorf("Authorization = %q", a)
	}
	if got.Method != http.MethodGet || got.URL.Path != "/sdk/v1/ruleset" {
		t.Errorf("request = %s %s", got.Method, got.URL.Path)
	}
	if len(rec.calls) != 1 || rec.calls[0] != (pollCall{`{"flags":[]}`, `"e1"`}) {
		t.Errorf("OnRuleset calls = %+v", rec.calls)
	}
}

func TestPollerSendsIfNoneMatch(t *testing.T) {
	var inm string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		inm = r.Header.Get("If-None-Match")
		w.WriteHeader(http.StatusNotModified)
	}))
	defer srv.Close()
	rec := &pollRecorder{valid: true}

	if err := newTestPoller(srv.URL, `"e1"`, rec).Poll(context.Background()); err != nil {
		t.Fatalf("Poll = %v, want nil", err)
	}
	if inm != `"e1"` {
		t.Errorf("If-None-Match = %q, want %q", inm, `"e1"`)
	}
	if len(rec.calls) != 0 {
		t.Errorf("OnRuleset called on 304: %+v", rec.calls)
	}
}

func statusServer(t *testing.T, status int, header map[string]string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for k, v := range header {
			w.Header().Set(k, v)
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte(`{"detail":"nope"}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestPoller401And403AreFatal(t *testing.T) {
	for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden} {
		rec := &pollRecorder{valid: true}
		err := newTestPoller(statusServer(t, status, nil).URL, "", rec).Poll(context.Background())
		var fatal *transport.FatalError
		if !errors.As(err, &fatal) || fatal.Status != status {
			t.Errorf("%d: Poll = %v, want *transport.FatalError", status, err)
		}
		if len(rec.calls) != 0 {
			t.Errorf("%d: OnRuleset called", status)
		}
	}
}

func TestPoller429RetryAfter(t *testing.T) {
	srv := statusServer(t, http.StatusTooManyRequests, map[string]string{"Retry-After": "7"})
	err := newTestPoller(srv.URL, "", &pollRecorder{}).Poll(context.Background())
	var ra *transport.RetryAfterError
	if !errors.As(err, &ra) || ra.After != 7*time.Second {
		t.Fatalf("Poll = %v, want *transport.RetryAfterError{After: 7s}", err)
	}
}

func TestPoller500(t *testing.T) {
	err := newTestPoller(statusServer(t, http.StatusInternalServerError, nil).URL, "", &pollRecorder{}).Poll(context.Background())
	var he *transport.HTTPError
	if !errors.As(err, &he) || he.Status != http.StatusInternalServerError {
		t.Fatalf("Poll = %v, want *transport.HTTPError{500}", err)
	}
}

func TestPollerInvalidRuleset(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("ETag", `"bad"`)
		_, _ = w.Write([]byte(`not json`))
	}))
	defer srv.Close()
	rec := &pollRecorder{valid: false}

	err := newTestPoller(srv.URL, `"e1"`, rec).Poll(context.Background())
	if err == nil {
		t.Fatal("Poll = nil, want an error for an invalid ruleset")
	}
	if len(rec.calls) != 1 || rec.calls[0] != (pollCall{"not json", `"bad"`}) {
		t.Errorf("OnRuleset calls = %+v", rec.calls)
	}
}

func TestPollerTimeout(t *testing.T) {
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-release:
		case <-time.After(5 * time.Second):
		}
	}))
	defer srv.Close()
	defer close(release)
	p := NewPoller(PollerConfig{URL: srv.URL, Key: pollKey, Timeout: 50 * time.Millisecond})

	start := time.Now()
	err := p.Poll(context.Background())
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Poll = %v, want context.DeadlineExceeded", err)
	}
	if d := time.Since(start); d > 2*time.Second {
		t.Errorf("Poll took %v, want about 50ms", d)
	}
}

func TestPollerDefaultTimeout(t *testing.T) {
	if got := NewPoller(PollerConfig{}).cfg.Timeout; got != 10*time.Second {
		t.Errorf("default Timeout = %v, want 10s", got)
	}
}
