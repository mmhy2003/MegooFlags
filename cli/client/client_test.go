package client

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const testToken = "mf_api_secret_token"

// newTestClient returns a client for srv whose Sleep records the delays.
func newTestClient(srv *httptest.Server, slept *[]time.Duration) *Client {
	c := New(srv.URL, testToken)
	c.Sleep = func(_ context.Context, d time.Duration) error {
		*slept = append(*slept, d)
		return nil
	}
	return c
}

func TestNewDefaults(t *testing.T) {
	c := New("http://x", "t")
	if c.MaxRetries != 3 {
		t.Errorf("MaxRetries = %d, want 3", c.MaxRetries)
	}
	if c.UserAgent != "megooflags-go-client" {
		t.Errorf("UserAgent = %q", c.UserAgent)
	}
	if c.HTTP == nil || c.Sleep == nil {
		t.Error("HTTP and Sleep must default to non-nil")
	}
}

func TestOptions(t *testing.T) {
	hc := &http.Client{}
	c := New("http://x", "t", WithHTTPClient(hc), WithUserAgent("ua/1"), WithMaxRetries(0))
	if c.HTTP != hc || c.UserAgent != "ua/1" || c.MaxRetries != 0 {
		t.Errorf("options not applied: %+v", c)
	}
}

func TestDoSendsHeadersAndBody(t *testing.T) {
	var got *http.Request
	var body map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = r
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"key":"shop"}`))
	}))
	defer srv.Close()
	var slept []time.Duration
	c := newTestClient(srv, &slept)

	var out struct{ Key string }
	q := url.Values{"a": {"b c"}}
	if err := c.Do(context.Background(), http.MethodPost, "/api/projects", q, map[string]string{"key": "shop"}, &out); err != nil {
		t.Fatal(err)
	}
	if h := got.Header.Get("Authorization"); h != "Bearer "+testToken {
		t.Errorf("Authorization = %q", h)
	}
	if h := got.Header.Get("Accept"); h != "application/json" {
		t.Errorf("Accept = %q", h)
	}
	if h := got.Header.Get("Content-Type"); h != "application/json" {
		t.Errorf("Content-Type = %q", h)
	}
	if h := got.Header.Get("User-Agent"); h != "megooflags-go-client" {
		t.Errorf("User-Agent = %q", h)
	}
	if got.URL.Path != "/api/projects" || got.URL.Query().Get("a") != "b c" {
		t.Errorf("URL = %s", got.URL)
	}
	if body["key"] != "shop" || out.Key != "shop" {
		t.Errorf("body = %v, out = %+v", body, out)
	}
}

func TestDoWithoutBodyHasNoContentType(t *testing.T) {
	var ct string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ct = r.Header.Get("Content-Type")
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()
	var slept []time.Duration
	out := struct{ Key string }{Key: "kept"}
	if err := newTestClient(srv, &slept).Do(context.Background(), http.MethodDelete, "/x", nil, nil, &out); err != nil {
		t.Fatal(err)
	}
	if ct != "" {
		t.Errorf("Content-Type = %q, want none", ct)
	}
	if out.Key != "kept" {
		t.Errorf("204 must skip decoding, out = %+v", out)
	}
}

func TestDoNilOutSkipsDecoding(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`not json`))
	}))
	defer srv.Close()
	var slept []time.Duration
	if err := newTestClient(srv, &slept).Do(context.Background(), http.MethodGet, "/x", nil, nil, nil); err != nil {
		t.Fatalf("nil out must skip decoding: %v", err)
	}
}

func TestDoDecodesJSONError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"code":"approval_required","message":"needs approval","details":{"environment":"production"}}`))
	}))
	defer srv.Close()
	var slept []time.Duration
	err := newTestClient(srv, &slept).Do(context.Background(), http.MethodPut, "/x", nil, map[string]int{}, nil)
	var apiErr *Error
	if !errors.As(err, &apiErr) {
		t.Fatalf("err = %v, want *Error", err)
	}
	if apiErr.Status != 409 || apiErr.Code != "approval_required" || apiErr.Message != "needs approval" {
		t.Errorf("err = %+v", apiErr)
	}
	if d, ok := apiErr.Details.(map[string]any); !ok || d["environment"] != "production" {
		t.Errorf("Details = %#v", apiErr.Details)
	}
	if err.Error() != "approval_required: needs approval (HTTP 409)" {
		t.Errorf("Error() = %q", err.Error())
	}
	if !IsCode(err, "approval_required") || IsCode(err, "not_found") || IsNotFound(err) {
		t.Error("IsCode/IsNotFound mismatch")
	}
}

func TestDoNonJSONError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`<html>nope</html>`))
	}))
	defer srv.Close()
	var slept []time.Duration
	err := newTestClient(srv, &slept).Do(context.Background(), http.MethodGet, "/x", nil, nil, nil)
	var apiErr *Error
	if !errors.As(err, &apiErr) {
		t.Fatalf("err = %v, want *Error", err)
	}
	if apiErr.Status != 404 || apiErr.Code != "http_404" {
		t.Errorf("err = %+v", apiErr)
	}
	if !IsNotFound(err) {
		t.Error("IsNotFound must be true for status 404")
	}
	if len(slept) != 0 {
		t.Errorf("404 must not be retried, slept %v", slept)
	}
}

func TestIsNotFoundByCode(t *testing.T) {
	if !IsNotFound(&Error{Status: 400, Code: "not_found"}) {
		t.Error("code not_found must count as not found")
	}
	if IsNotFound(errors.New("other")) || IsCode(nil, "x") {
		t.Error("non-API errors are never a code")
	}
}

func TestGetRetriedThenFails(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()
	var slept []time.Duration
	err := newTestClient(srv, &slept).Do(context.Background(), http.MethodGet, "/x", nil, nil, nil)
	if !IsCode(err, "http_503") {
		t.Fatalf("err = %v", err)
	}
	if n := calls.Load(); n != 4 {
		t.Errorf("calls = %d, want 1 + 3 retries", n)
	}
	want := []time.Duration{500 * time.Millisecond, time.Second, 2 * time.Second}
	if len(slept) != len(want) {
		t.Fatalf("slept = %v, want %v", slept, want)
	}
	for i := range want {
		if slept[i] != want[i] {
			t.Errorf("slept = %v, want %v", slept, want)
		}
	}
}

func TestGetRetriesThenSucceeds(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) < 3 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(`{"key":"ok"}`))
	}))
	defer srv.Close()
	var slept []time.Duration
	var out struct{ Key string }
	if err := newTestClient(srv, &slept).Do(context.Background(), http.MethodGet, "/x", nil, nil, &out); err != nil {
		t.Fatal(err)
	}
	if out.Key != "ok" || calls.Load() != 3 {
		t.Errorf("out = %+v, calls = %d", out, calls.Load())
	}
}

func TestGetRetriesNetworkErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	base := srv.URL
	srv.Close()
	var slept []time.Duration
	c := New(base, testToken)
	c.Sleep = func(_ context.Context, d time.Duration) error {
		slept = append(slept, d)
		return nil
	}
	err := c.Do(context.Background(), http.MethodGet, "/x", nil, nil, nil)
	if err == nil {
		t.Fatal("want a network error")
	}
	if len(slept) != 3 {
		t.Errorf("slept = %v, want 3 retries", slept)
	}
	if strings.Contains(err.Error(), testToken) {
		t.Errorf("error leaks the token: %v", err)
	}
}

func TestPostNeverRetried(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()
	var slept []time.Duration
	err := newTestClient(srv, &slept).Do(context.Background(), http.MethodPost, "/x", nil, map[string]int{"a": 1}, nil)
	if !IsCode(err, "http_503") {
		t.Fatalf("err = %v", err)
	}
	if calls.Load() != 1 || len(slept) != 0 {
		t.Errorf("calls = %d, slept = %v, want 1 call and no sleep", calls.Load(), slept)
	}
}

func TestRetryAfterHonoured(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			w.Header().Set("Retry-After", "7")
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()
	var slept []time.Duration
	if err := newTestClient(srv, &slept).Do(context.Background(), http.MethodGet, "/x", nil, nil, nil); err != nil {
		t.Fatal(err)
	}
	if len(slept) != 1 || slept[0] != 7*time.Second {
		t.Errorf("slept = %v, want [7s]", slept)
	}
}

func TestContextCancellation(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		<-r.Context().Done()
	}))
	defer srv.Close()
	var slept []time.Duration
	ctx, cancel := context.WithCancel(context.Background())
	go func() { time.Sleep(50 * time.Millisecond); cancel() }()
	err := newTestClient(srv, &slept).Do(ctx, http.MethodGet, "/x", nil, nil, nil)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
	if len(slept) != 0 {
		t.Errorf("a cancelled request must not be retried, slept %v", slept)
	}
}

func TestErrorTextHasNoToken(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"code":"unauthorized","message":"bad token ` + r.Header.Get("Authorization") + `"}`))
	}))
	defer srv.Close()
	var slept []time.Duration
	err := newTestClient(srv, &slept).Do(context.Background(), http.MethodGet, "/x", nil, nil, nil)
	if err == nil {
		t.Fatal("want an error")
	}
	if strings.Contains(err.Error(), testToken) {
		t.Errorf("error leaks the token: %q", err.Error())
	}
}

func TestListAllFollowsCursor(t *testing.T) {
	type item struct{ Key string }
	pages := map[string]string{
		"":   `{"items":[{"key":"a"},{"key":"b"}],"next_cursor":"c1"}`,
		"c1": `{"items":[{"key":"c"}],"next_cursor":"c2"}`,
		"c2": `{"items":[{"key":"d"}],"next_cursor":null}`,
	}
	var seen []url.Values
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		seen = append(seen, q)
		_, _ = w.Write([]byte(pages[q.Get("cursor")]))
	}))
	defer srv.Close()
	var slept []time.Duration
	query := url.Values{"archived": {"true"}}
	got, err := ListAll[item](context.Background(), newTestClient(srv, &slept), "/api/projects/shop/flags", query)
	if err != nil {
		t.Fatal(err)
	}
	var keys []string
	for _, it := range got {
		keys = append(keys, it.Key)
	}
	if strings.Join(keys, ",") != "a,b,c,d" {
		t.Errorf("keys = %v", keys)
	}
	if len(seen) != 3 {
		t.Fatalf("requests = %d, want 3", len(seen))
	}
	for _, q := range seen {
		if q.Get("limit") != "500" || q.Get("archived") != "true" {
			t.Errorf("query = %v", q)
		}
	}
	if query.Get("cursor") != "" || query.Get("limit") != "" {
		t.Errorf("ListAll must not modify the caller's query: %v", query)
	}
}

func TestRetryAfterIsCapped(t *testing.T) {
	var calls atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			w.Header().Set("Retry-After", "3600")
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()
	var slept []time.Duration
	if err := newTestClient(srv, &slept).Do(context.Background(), http.MethodGet, "/x", nil, nil, nil); err != nil {
		t.Fatal(err)
	}
	if len(slept) != 1 || slept[0] != MaxRetryWait {
		t.Errorf("slept = %v, want [%v]", slept, MaxRetryWait)
	}
}

func TestCancelStopsTheWaitBeforeARetry(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Retry-After", "20")
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer srv.Close()
	ctx, cancel := context.WithCancel(context.Background())
	go func() { time.Sleep(50 * time.Millisecond); cancel() }()
	started := time.Now()
	err := New(srv.URL, testToken).Do(ctx, http.MethodGet, "/x", nil, nil, nil)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
	if waited := time.Since(started); waited > 5*time.Second {
		t.Errorf("waited %v after the cancel", waited)
	}
}
