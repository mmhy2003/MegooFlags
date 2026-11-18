package seed

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/mmhy2003/MegooFlags/perf/internal/api"
)

const testToken = "mf_api_test"

var testRuleset = []byte(`{"flags":{"flag-00000":{"enabled":true}}}`)

// fakeAPI is the part of the management and SDK API that Run calls.
type fakeAPI struct {
	t  *testing.T
	mu sync.Mutex
	// failNext answers the next requests with these statuses before serving them.
	failNext []int
	projects []string
	flags    map[string][]string // project -> flag keys
	configs  map[string]map[string]any
	keys     map[string]string // project/env -> kind
	listed   string            // query of the last flag list
}

func newFakeAPI(t *testing.T, failNext ...int) (*fakeAPI, *httptest.Server) {
	f := &fakeAPI{t: t, failNext: failNext, flags: map[string][]string{}, configs: map[string]map[string]any{}, keys: map[string]string{}}
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/projects", f.createProject)
	mux.HandleFunc("POST /api/projects/{project}/flags", f.createFlag)
	mux.HandleFunc("GET /api/projects/{project}/flags", f.listFlags)
	mux.HandleFunc("PUT /api/projects/{project}/flags/{flag}/environments/{env}", f.replaceConfig)
	mux.HandleFunc("POST /api/projects/{project}/environments/{env}/sdk-keys", f.createKey)
	mux.HandleFunc("GET /sdk/v1/ruleset", f.ruleset)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/sdk/v1/ruleset" && r.Header.Get("Authorization") != "Bearer "+testToken {
			http.Error(w, "no token", http.StatusUnauthorized)
			return
		}
		f.mu.Lock()
		if len(f.failNext) > 0 {
			status := f.failNext[0]
			f.failNext = f.failNext[1:]
			f.mu.Unlock()
			http.Error(w, "try later", status)
			return
		}
		f.mu.Unlock()
		mux.ServeHTTP(w, r)
	}))
	t.Cleanup(srv.Close)
	return f, srv
}

func decode(t *testing.T, r *http.Request) map[string]any {
	var body map[string]any
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		t.Errorf("%s %s: %v", r.Method, r.URL.Path, err)
	}
	return body
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

var envs = []string{"development", "staging", "production"}

func (f *fakeAPI) createProject(w http.ResponseWriter, r *http.Request) {
	body := decode(f.t, r)
	f.mu.Lock()
	f.projects = append(f.projects, body["key"].(string))
	f.mu.Unlock()
	var out []map[string]any
	for _, e := range envs {
		out = append(out, map[string]any{"key": e, "name": e})
	}
	writeJSON(w, http.StatusCreated, map[string]any{"key": body["key"], "environments": out})
}

func (f *fakeAPI) createFlag(w http.ResponseWriter, r *http.Request) {
	body := decode(f.t, r)
	if body["type"] != "boolean" {
		f.t.Errorf("flag type %v, want boolean", body["type"])
	}
	project := r.PathValue("project")
	f.mu.Lock()
	f.flags[project] = append(f.flags[project], body["key"].(string))
	f.mu.Unlock()
	var states []map[string]any
	for _, e := range envs {
		states = append(states, map[string]any{"environment": e, "enabled": false, "version": 3})
	}
	writeJSON(w, http.StatusCreated, map[string]any{"key": body["key"], "environments": states})
}

func (f *fakeAPI) listFlags(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	f.listed = r.URL.RawQuery
	f.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{"items": []any{}, "next_cursor": nil})
}

func (f *fakeAPI) replaceConfig(w http.ResponseWriter, r *http.Request) {
	body := decode(f.t, r)
	f.mu.Lock()
	f.configs[r.PathValue("project")+"/"+r.PathValue("flag")+"/"+r.PathValue("env")] = body
	f.mu.Unlock()
	writeJSON(w, http.StatusOK, body)
}

func (f *fakeAPI) createKey(w http.ResponseWriter, r *http.Request) {
	body := decode(f.t, r)
	project, env := r.PathValue("project"), r.PathValue("env")
	f.mu.Lock()
	f.keys[project+"/"+env] = body["kind"].(string)
	f.mu.Unlock()
	writeJSON(w, http.StatusCreated, map[string]any{"key": "mf_server_" + project})
}

func (f *fakeAPI) ruleset(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("Authorization") != "Bearer mf_server_perf" {
		http.Error(w, "wrong key", http.StatusUnauthorized)
		return
	}
	_, _ = w.Write(testRuleset)
}

func TestSeedCreatesFlags(t *testing.T) {
	f, srv := newFakeAPI(t, http.StatusTooManyRequests, http.StatusServiceUnavailable)
	client := api.New(srv.URL, testToken, false).WithRetry(5, time.Millisecond)

	out, metrics, err := Run(context.Background(), client, Config{Project: "perf", Flags: 25, EvalFlags: 5, Concurrency: 16})
	if err != nil {
		t.Fatal(err)
	}

	want := Output{Project: "perf", Env: "development", ServerKey: "mf_server_perf", EvalProject: "perf-eval", EvalKey: "mf_server_perf-eval", Flag: "flag-00000"}
	if out != want {
		t.Errorf("output %+v, want %+v", out, want)
	}
	if strings.Join(f.projects, ",") != "perf,perf-eval" {
		t.Errorf("projects %v", f.projects)
	}
	if n := len(f.flags["perf"]); n != 25 {
		t.Errorf("created %d flags in perf, want 25", n)
	}
	if n := len(f.flags["perf-eval"]); n != 5 {
		t.Errorf("created %d flags in perf-eval, want 5", n)
	}
	for i := range 25 {
		key := fmt.Sprintf("flag-%05d", i)
		cfg, ok := f.configs["perf/"+key+"/development"]
		if !ok {
			t.Errorf("%s has no config in development", key)
			continue
		}
		checkConfig(t, key, cfg)
	}
	if n := len(f.configs); n != 30 {
		t.Errorf("%d configs replaced, want 30 (only in development)", n)
	}
	if f.keys["perf/development"] != "server" || f.keys["perf-eval/development"] != "server" || len(f.keys) != 2 {
		t.Errorf("sdk keys %v, want one server key per project in development", f.keys)
	}
	if f.listed != "limit=100" {
		t.Errorf("flag list query %q, want limit=100", f.listed)
	}
	if metrics["ruleset_bytes"] != float64(len(testRuleset)) {
		t.Errorf("ruleset_bytes %v, want %d", metrics["ruleset_bytes"], len(testRuleset))
	}
	for _, m := range []string{"ruleset_download_ms", "flag_list_ms"} {
		if v, ok := metrics[m]; !ok || v <= 0 {
			t.Errorf("%s = %v, want > 0", m, v)
		}
	}
	if metrics["flags"] != 25 {
		t.Errorf("flags metric %v, want 25", metrics["flags"])
	}
}

// checkConfig checks one flag's config: on, version from the creation, one
// rule with an attribute clause and a 50 % rollout, and a fixed fallthrough.
func checkConfig(t *testing.T, key string, cfg map[string]any) {
	t.Helper()
	if cfg["enabled"] != true || cfg["version"] != float64(3) {
		t.Errorf("%s: enabled %v version %v, want true and 3", key, cfg["enabled"], cfg["version"])
	}
	rules, _ := cfg["rules"].([]any)
	if len(rules) != 1 {
		t.Fatalf("%s: rules %v, want one", key, cfg["rules"])
	}
	rule := rules[0].(map[string]any)
	clause := rule["clauses"].([]any)[0].(map[string]any)
	if clause["attribute"] != "plan" || clause["operator"] != "in" {
		t.Errorf("%s: clause %v, want an `in` clause on plan", key, clause)
	}
	weights := rule["rollout"].(map[string]any)["weights"].([]any)
	for _, w := range weights {
		if w.(map[string]any)["weight"] != float64(50000) {
			t.Errorf("%s: weights %v, want 50000 each", key, weights)
		}
	}
	if len(weights) != 2 {
		t.Errorf("%s: weights %v, want two", key, weights)
	}
	if cfg["fallthrough"].(map[string]any)["variation"] != "false" {
		t.Errorf("%s: fallthrough %v, want false", key, cfg["fallthrough"])
	}
}

func TestSeedFailsAfterRetries(t *testing.T) {
	_, srv := newFakeAPI(t, 500, 500, 500)
	client := api.New(srv.URL, testToken, false).WithRetry(2, time.Millisecond)

	_, _, err := Run(context.Background(), client, Config{Project: "perf", Flags: 1, EvalFlags: 1, Concurrency: 1})
	if err == nil || !strings.Contains(err.Error(), "500") {
		t.Fatalf("error %v, want the 500 after two attempts", err)
	}
}
