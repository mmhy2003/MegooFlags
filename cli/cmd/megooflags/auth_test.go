package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/internal/config"
)

const testToken = "mf_pat_s3cret-token-value"

// session runs CLI commands with a temporary XDG_CONFIG_HOME.
type session struct {
	t     *testing.T
	env   map[string]string
	stdin string
}

func newSession(t *testing.T) *session {
	t.Helper()
	return &session{t: t, env: map[string]string{"XDG_CONFIG_HOME": t.TempDir()}}
}

func (s *session) configPath() string {
	return filepath.Join(s.env["XDG_CONFIG_HOME"], "megooflags", "config.yaml")
}

func (s *session) run(args ...string) (code int, stdout, stderr string) {
	s.t.Helper()
	var out, errb bytes.Buffer
	code = run(context.Background(), args, IO{
		In:     strings.NewReader(s.stdin),
		Out:    &out,
		Err:    &errb,
		Getenv: func(k string) string { return s.env[k] },
	})
	stdout, stderr = out.String(), errb.String()
	if strings.Contains(stdout, testToken) || strings.Contains(stderr, testToken) {
		s.t.Fatalf("%v: output contains the token\nstdout: %s\nstderr: %s", args, stdout, stderr)
	}
	return code, stdout, stderr
}

// meServer answers GET /api/auth/me for testToken and 401 otherwise.
func meServer(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodGet || r.URL.Path != "/api/auth/me" {
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"code":"not_found","message":"Not found"}`))
			return
		}
		if r.Header.Get("Authorization") != "Bearer "+testToken {
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte(`{"code":"unauthorized","message":"Invalid or expired token"}`))
			return
		}
		_, _ = w.Write([]byte(`{"id":"u1","kind":"personal_token","label":"Mona (laptop)","name":"Mona","email":"mona@example.com","auth_method":"token","require_mfa":false}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestLoginFromStdinSavesConfig(t *testing.T) {
	srv := meServer(t)
	s := newSession(t)
	if err := config.Save(s.configPath(), config.File{URL: "https://old", Project: "shop", Output: "yaml"}); err != nil {
		t.Fatal(err)
	}
	s.stdin = testToken + "\n"

	code, out, errOut := s.run("login", "--url", srv.URL)
	if code != ExitOK {
		t.Fatalf("code = %d, stderr = %s", code, errOut)
	}
	if want := "Logged in to " + srv.URL + " as Mona (laptop)\n"; out != want {
		t.Fatalf("out = %q, want %q", out, want)
	}
	f, err := config.Load(s.configPath())
	if err != nil {
		t.Fatal(err)
	}
	want := config.File{URL: srv.URL, Token: testToken, Project: "shop", Output: "yaml"}
	if f != want {
		t.Fatalf("file = %+v, want %+v", f, want)
	}
	if runtime.GOOS != "windows" {
		info, err := os.Stat(s.configPath())
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != 0o600 {
			t.Fatalf("mode = %o", info.Mode().Perm())
		}
	}
}

func TestLoginPrefersTokenFromEnvironment(t *testing.T) {
	srv := meServer(t)
	s := newSession(t)
	s.env["MEGOOFLAGS_API_TOKEN"] = testToken
	s.stdin = "ignored\n"

	if code, _, errOut := s.run("login", "--url", srv.URL); code != ExitOK {
		t.Fatalf("code = %d, stderr = %s", code, errOut)
	}
	f, err := config.Load(s.configPath())
	if err != nil {
		t.Fatal(err)
	}
	if f.Token != testToken {
		t.Fatal("token from the environment was not saved")
	}
}

func TestLoginBadTokenFails(t *testing.T) {
	srv := meServer(t)
	s := newSession(t)
	s.stdin = "wrong\n"

	code, out, errOut := s.run("login", "--url", srv.URL)
	if code != ExitError {
		t.Fatalf("code = %d", code)
	}
	if out != "" {
		t.Fatalf("out = %q", out)
	}
	if !strings.Contains(errOut, "error: unauthorized: Invalid or expired token") {
		t.Fatalf("stderr = %q", errOut)
	}
	if _, err := os.Stat(s.configPath()); !os.IsNotExist(err) {
		t.Fatalf("config file written after a failed login: %v", err)
	}
}

func TestLoginNeedsURLAndToken(t *testing.T) {
	s := newSession(t)
	s.stdin = testToken + "\n"
	if code, _, errOut := s.run("login"); code != ExitUsage || !strings.Contains(errOut, "no URL") {
		t.Fatalf("no URL: code = %d, stderr = %q", code, errOut)
	}
	s.stdin = "\n"
	if code, _, errOut := s.run("login", "--url", "https://flags.example"); code != ExitUsage || !strings.Contains(errOut, "no API token") {
		t.Fatalf("empty token: code = %d, stderr = %q", code, errOut)
	}
}

func loggedIn(t *testing.T) (*session, *httptest.Server) {
	t.Helper()
	srv := meServer(t)
	s := newSession(t)
	if err := config.Save(s.configPath(), config.File{URL: srv.URL, Token: testToken, Project: "shop"}); err != nil {
		t.Fatal(err)
	}
	return s, srv
}

func TestWhoamiJSON(t *testing.T) {
	s, srv := loggedIn(t)
	code, out, errOut := s.run("whoami", "-o", "json")
	if code != ExitOK {
		t.Fatalf("code = %d, stderr = %s", code, errOut)
	}
	var got map[string]string
	if err := json.Unmarshal([]byte(out), &got); err != nil {
		t.Fatalf("%v: %s", err, out)
	}
	want := map[string]string{"kind": "personal_token", "label": "Mona (laptop)", "url": srv.URL}
	if len(got) != len(want) || got["kind"] != want["kind"] || got["label"] != want["label"] || got["url"] != want["url"] {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestWhoamiTable(t *testing.T) {
	s, srv := loggedIn(t)
	code, out, _ := s.run("whoami")
	if code != ExitOK {
		t.Fatalf("code = %d", code)
	}
	lines := strings.Split(strings.TrimRight(out, "\n"), "\n")
	if len(lines) != 2 || strings.Fields(lines[0])[0] != "KIND" || !strings.Contains(lines[0], "LABEL") || !strings.Contains(lines[0], "URL") {
		t.Fatalf("out = %q", out)
	}
	if !strings.Contains(lines[1], "personal_token") || !strings.Contains(lines[1], "Mona (laptop)") || !strings.Contains(lines[1], srv.URL) {
		t.Fatalf("out = %q", out)
	}
}

func TestWhoamiWithoutTokenFails(t *testing.T) {
	s := newSession(t)
	s.env["MEGOOFLAGS_URL"] = "https://flags.example"
	code, _, errOut := s.run("whoami")
	if code != ExitError || !strings.Contains(errOut, "no API token: run `megooflags login` or set MEGOOFLAGS_API_TOKEN") {
		t.Fatalf("code = %d, stderr = %q", code, errOut)
	}
}

func TestLogoutClearsToken(t *testing.T) {
	s, srv := loggedIn(t)
	code, out, errOut := s.run("logout")
	if code != ExitOK || out != "Logged out\n" {
		t.Fatalf("code = %d, out = %q, stderr = %s", code, out, errOut)
	}
	f, err := config.Load(s.configPath())
	if err != nil {
		t.Fatal(err)
	}
	if want := (config.File{URL: srv.URL, Project: "shop"}); f != want {
		t.Fatalf("file = %+v, want %+v", f, want)
	}
	if code, _, _ := s.run("whoami"); code != ExitError {
		t.Fatalf("whoami after logout: code = %d", code)
	}
}
