//go:build e2e

// Package e2e_test runs the provider against a running MegooFlags stack.
// Set MEGOOFLAGS_E2E_URL (e.g. http://localhost:8080) to run it.
package e2e_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/open-feature/go-sdk/openfeature"

	megooflags "github.com/mmhy2003/MegooFlags/sdks/go"
)

const requestTimeout = 10 * time.Second

func baseURL(t *testing.T) string {
	t.Helper()
	base := strings.TrimRight(os.Getenv("MEGOOFLAGS_E2E_URL"), "/")
	if base == "" {
		t.Skip("MEGOOFLAGS_E2E_URL is not set")
	}
	return base
}

func randomHex(t *testing.T, n int) string {
	t.Helper()
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		t.Fatalf("random: %v", err)
	}
	return hex.EncodeToString(b)
}

// do sends a JSON request and returns the status and the raw body.
func do(t *testing.T, c *http.Client, method, url string, header http.Header, body any) (int, []byte) {
	t.Helper()
	var r io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			t.Fatalf("encode %s %s: %v", method, url, err)
		}
		r = bytes.NewReader(b)
	}
	ctx, cancel := context.WithTimeout(context.Background(), requestTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, method, url, r)
	if err != nil {
		t.Fatalf("%s %s: %v", method, url, err)
	}
	for k, v := range header {
		req.Header[k] = v
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, url, err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("%s %s: read body: %v", method, url, err)
	}
	return resp.StatusCode, data
}

// api calls the management API with a bearer token.
func api(t *testing.T, method, path, token string, body any) (int, map[string]any) {
	t.Helper()
	h := http.Header{}
	h.Set("Authorization", "Bearer "+token)
	status, data := do(t, http.DefaultClient, method, baseURL(t)+path, h, body)
	out := map[string]any{}
	if len(data) > 0 {
		if err := json.Unmarshal(data, &out); err != nil {
			t.Fatalf("%s %s: status %d, body is not a JSON object: %s", method, path, status, data)
		}
	}
	return status, out
}

// mustAPI calls api and fails unless the status is want.
func mustAPI(t *testing.T, want int, method, path, token string, body any) map[string]any {
	t.Helper()
	status, out := api(t, method, path, token, body)
	if status != want {
		t.Fatalf("%s %s: status %d, want %d: %v", method, path, status, want, out)
	}
	return out
}

// token returns MEGOOFLAGS_API_TOKEN or mints an org admin service token the
// way scripts/smoke.sh does: first-run setup (or sign-in when the stack is
// already set up), then POST /api/tokens with the session.
func token(t *testing.T) string {
	t.Helper()
	if tok := os.Getenv("MEGOOFLAGS_API_TOKEN"); tok != "" {
		return tok
	}
	base := baseURL(t)
	email := os.Getenv("MEGOOFLAGS_E2E_EMAIL")
	if email == "" {
		email = "admin@example.com"
	}
	password := os.Getenv("MEGOOFLAGS_E2E_PASSWORD")
	setupPassword := password
	if setupPassword == "" {
		setupPassword = "sdk-go-e2e-" + randomHex(t, 16)
	}
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatalf("cookie jar: %v", err)
	}
	c := &http.Client{Jar: jar}
	h := http.Header{}
	h.Set("Origin", base)

	status, data := do(t, c, http.MethodPost, base+"/api/setup", h,
		map[string]string{"email": email, "name": "SDK Go E2E", "password": setupPassword})
	switch status {
	case http.StatusCreated, http.StatusOK:
	case http.StatusConflict:
		if password == "" {
			t.Fatalf("the stack is already set up; set MEGOOFLAGS_API_TOKEN or MEGOOFLAGS_E2E_PASSWORD")
		}
		status, data = do(t, c, http.MethodPost, base+"/api/auth/sign-in", h,
			map[string]string{"email": email, "password": password})
		if status < 200 || status > 299 {
			t.Fatalf("POST /api/auth/sign-in: status %d: %s", status, data)
		}
	default:
		t.Fatalf("POST /api/setup: status %d: %s", status, data)
	}

	status, data = do(t, c, http.MethodPost, base+"/api/tokens", h, map[string]any{
		"name":     "sdk-go-e2e",
		"bindings": []map[string]string{{"role": "admin", "scope": "org"}},
	})
	if status != http.StatusCreated {
		t.Fatalf("POST /api/tokens: status %d: %s", status, data)
	}
	var created struct {
		Secret string `json:"secret"`
	}
	if err := json.Unmarshal(data, &created); err != nil || created.Secret == "" {
		t.Fatalf("POST /api/tokens: no secret in %s", data)
	}
	return created.Secret
}

func TestEndToEnd(t *testing.T) {
	base := baseURL(t)
	tok := token(t)

	// 1–2. A project with a non-approval environment, an enabled boolean
	// flag and a server key.
	project := "sdkgo-" + randomHex(t, 4)
	mustAPI(t, http.StatusCreated, http.MethodPost, "/api/projects", tok, map[string]any{
		"key":          project,
		"name":         project,
		"environments": []map[string]string{{"key": "e2e", "name": "E2E"}},
	})
	t.Cleanup(func() { api(t, http.MethodDelete, "/api/projects/"+project, tok, nil) })

	flagPath := "/api/projects/" + project + "/flags/e2e-flag/environments/e2e"
	mustAPI(t, http.StatusCreated, http.MethodPost, "/api/projects/"+project+"/flags", tok,
		map[string]any{"key": "e2e-flag", "name": "E2E", "type": "boolean"})
	mustAPI(t, http.StatusOK, http.MethodPost, flagPath+"/toggle", tok, map[string]any{"enabled": true})
	created := mustAPI(t, http.StatusCreated, http.MethodPost,
		"/api/projects/"+project+"/environments/e2e/sdk-keys", tok,
		map[string]any{"name": "go-e2e", "kind": "server"})
	sdkKey, _ := created["key"].(string)
	if sdkKey == "" {
		t.Fatalf("POST sdk-keys: no key in %v", created)
	}

	// 3. A proxy in front of the stack, so the test can stop "the API".
	target, err := url.Parse(base)
	if err != nil {
		t.Fatalf("parse %q: %v", base, err)
	}
	proxy := httptest.NewServer(&httputil.ReverseProxy{
		Rewrite:       func(r *httputil.ProxyRequest) { r.SetURL(target) },
		FlushInterval: -1,
	})
	defer proxy.Close()

	// 4. The provider.
	cacheDir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(t.Output(), nil)) // shown with -v or on failure
	p := megooflags.NewProvider(megooflags.Config{
		SDKKey:        sdkKey,
		BaseURL:       proxy.URL,
		CacheDir:      cacheDir,
		FlushInterval: 10 * time.Second,
		Logger:        logger,
	})
	defer p.Shutdown()
	changes := make(chan openfeature.EventDetails, 16)
	onChange := func(d openfeature.EventDetails) {
		if d.ProviderName == p.Metadata().Name {
			select {
			case changes <- d:
			default:
			}
		}
	}
	openfeature.AddHandler(openfeature.ProviderConfigChange, &onChange)
	defer openfeature.RemoveHandler(openfeature.ProviderConfigChange, &onChange)
	if err := openfeature.SetNamedProviderAndWait(project, p); err != nil {
		t.Fatalf("SetNamedProviderAndWait: %v", err)
	}
	client := openfeature.NewClient(project)
	evalCtx := openfeature.NewEvaluationContext("user-1", nil)
	value := func() bool {
		t.Helper()
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		d, err := client.BooleanValueDetails(ctx, "e2e-flag", false, evalCtx)
		if err != nil {
			t.Fatalf("BooleanValueDetails: %v (%+v)", err, d)
		}
		return d.Value
	}

	// Check 1: the downloaded ruleset is evaluated.
	if !value() {
		t.Fatalf("e2e-flag = false, want true")
	}

	// Check 2: a toggle arrives over SSE within 5 s.
	t0 := time.Now()
	mustAPI(t, http.StatusOK, http.MethodPost, flagPath+"/toggle", tok, map[string]any{"enabled": false})
	for value() {
		if time.Since(t0) > 5*time.Second {
			t.Fatalf("e2e-flag still true 5 s after the toggle")
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Logf("toggle propagated in %s", time.Since(t0))
	select {
	case <-changes:
	case <-time.After(5 * time.Second):
		t.Fatalf("no PROVIDER_CONFIGURATION_CHANGED event after the toggle")
	}

	// Check 3: evaluation counts are sent at shutdown and accepted.
	for range 20 {
		value()
	}
	p.Shutdown()
	deadline := time.Now().Add(10 * time.Second)
	for {
		out := mustAPI(t, http.StatusOK, http.MethodGet, flagPath+"/evaluations?range=24h", tok, nil)
		total, _ := out["total"].(float64)
		if total >= 21 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("evaluations total = %v after 10 s, want >= 21: %v", total, out)
		}
		time.Sleep(500 * time.Millisecond)
	}

	// Check 4: with the API gone, a new provider serves the cached ruleset.
	proxy.Close()
	p2 := megooflags.NewProvider(megooflags.Config{
		SDKKey:      sdkKey,
		BaseURL:     proxy.URL,
		CacheDir:    cacheDir,
		InitTimeout: time.Second,
		Logger:      logger,
	})
	defer p2.Shutdown()
	if err := openfeature.SetNamedProviderAndWait(project+"-offline", p2); err != nil {
		t.Fatalf("SetNamedProviderAndWait offline: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	d, err := openfeature.NewClient(project+"-offline").BooleanValueDetails(ctx, "e2e-flag", true, evalCtx)
	if err != nil || d.Value {
		t.Fatalf("offline e2e-flag = %v, %v (%+v), want false from the cache", d.Value, err, d)
	}
}
