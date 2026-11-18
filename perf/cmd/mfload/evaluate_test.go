package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

// fakeEvaluator records the requests it gets and fails every failEvery-th one
// (0: never).
type fakeEvaluator struct {
	failEvery int

	mu       sync.Mutex
	requests int
	paths    map[string]int
	contexts []map[string]any
	bad      []string
}

func newFakeEvaluator(t *testing.T, f *fakeEvaluator) *httptest.Server {
	f.paths = map[string]int{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Context map[string]any `json:"context"`
		}
		err := json.NewDecoder(r.Body).Decode(&body)
		f.mu.Lock()
		defer f.mu.Unlock()
		f.requests++
		f.paths[r.Method+" "+r.URL.Path]++
		switch {
		case err != nil:
			f.bad = append(f.bad, err.Error())
		case r.Header.Get("Authorization") != "Bearer "+testKey:
			f.bad = append(f.bad, "authorization "+r.Header.Get("Authorization"))
		case r.Header.Get("Content-Type") != "application/json":
			f.bad = append(f.bad, "content type "+r.Header.Get("Content-Type"))
		default:
			f.contexts = append(f.contexts, body.Context)
		}
		if f.failEvery > 0 && f.requests%f.failEvery == 0 {
			http.Error(w, "boom", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"value":true}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func checkUser(t *testing.T, id any, users int) {
	t.Helper()
	s, _ := id.(string)
	n, err := strconv.Atoi(strings.TrimPrefix(s, "user-"))
	if !strings.HasPrefix(s, "user-") || err != nil || n < 0 || n >= users {
		t.Errorf("user %v, want user-<0..%d>", id, users-1)
	}
}

func TestEvaluateBodies(t *testing.T) {
	const users = 50
	for _, tc := range []struct {
		mode, path, userAttr string
	}{
		{"ofrep", "POST /ofrep/v1/evaluate/flags/flag-00000", "targetingKey"},
		{"evaluate", "POST /sdk/v1/evaluate", "key"},
	} {
		t.Run(tc.mode, func(t *testing.T) {
			f := &fakeEvaluator{}
			srv := newFakeEvaluator(t, f)
			cfg := evaluateConfig{
				target: srv.URL + "/", key: testKey, mode: tc.mode, flag: "flag-00000",
				rps: 200, duration: 500 * time.Millisecond, warmup: 100 * time.Millisecond,
				users: users, concurrency: 8,
			}
			s, err := measureEvaluate(context.Background(), cfg)
			if err != nil {
				t.Fatal(err)
			}
			if len(f.bad) > 0 {
				t.Fatalf("bad requests: %v", f.bad[:min(3, len(f.bad))])
			}
			if f.paths[tc.path] != f.requests || f.requests != 120 {
				t.Errorf("requests %v, want 120 to %s (20 warm-up, 100 measured)", f.paths, tc.path)
			}
			plans := map[any]bool{}
			for _, c := range f.contexts {
				checkUser(t, c[tc.userAttr], users)
				plans[c["plan"]] = true
				if len(c) != 2 {
					t.Errorf("context %v, want %s and plan only", c, tc.userAttr)
				}
			}
			if len(plans) != 3 {
				t.Errorf("plans %v, want 3 different", plans)
			}
			m := s.Metrics
			if m["count"] != 100 || s.Errors != 0 || m["errors"] != 0 {
				t.Errorf("count %v errors %d, want the 100 measured requests and no errors", m["count"], s.Errors)
			}
			if m["rps"] < 150 || m["rps"] > 210 {
				t.Errorf("rps %v, want about 200", m["rps"])
			}
			for _, k := range []string{"p50_ms", "p95_ms", "p99_ms", "max_ms"} {
				if m[k] <= 0 {
					t.Errorf("%s = %v, want > 0", k, m[k])
				}
			}
		})
	}
}

func TestEvaluateErrorsCounted(t *testing.T) {
	f := &fakeEvaluator{failEvery: 10}
	srv := newFakeEvaluator(t, f)
	cfg := evaluateConfig{
		target: srv.URL, key: testKey, mode: "ofrep", flag: "flag-00000",
		rps: 100, duration: time.Second, users: 1000, concurrency: 4,
	}
	s, err := measureEvaluate(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	if s.Errors != 10 || s.Metrics["errors"] != 10 || s.Metrics["count"] != 90 {
		t.Errorf("errors %d (%v) count %v, want 10 errors and 90 successes", s.Errors, s.Metrics["errors"], s.Metrics["count"])
	}
}

func TestEvaluateRejectsUnknownMode(t *testing.T) {
	_, err := measureEvaluate(context.Background(), evaluateConfig{target: "http://x", key: testKey, mode: "bulk", rps: 1, duration: time.Second, users: 1, concurrency: 1})
	if err == nil {
		t.Fatal("no error for mode bulk")
	}
}
