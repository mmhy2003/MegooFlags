package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/internal/importers"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers/importertest"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers/unleash"
)

// importTestdata is an importer's testdata, shared with these tests.
func importTestdata(tool string) string {
	return filepath.Join("..", "..", "internal", "importers", tool, "testdata")
}

func TestImportFileMatchesGolden(t *testing.T) {
	for _, tc := range []struct{ tool, envMap, summary string }{
		{"unleash", "production=prod", "4 flags, 3 segments"},
		{"flagsmith", "Production=prod", "3 flags, 6 segments"},
		{"launchdarkly", "test=staging", "3 flags, 3 segments"},
	} {
		t.Run(tc.tool, func(t *testing.T) {
			dir := filepath.Join(t.TempDir(), "flags")
			s := newSession(t)
			out := ok(t, s, "import", tc.tool, "--file", filepath.Join(importTestdata(tc.tool), "export.json"),
				"--project", "shop", "--out", dir, "--env-map", tc.envMap)
			importertest.CompareDirs(t, dir, filepath.Join(importTestdata(tc.tool), "golden"))
			lines := strings.Split(strings.TrimSuffix(out, "\n"), "\n")
			if len(lines) != 2 || !strings.HasPrefix(lines[0], tc.summary+"; ") ||
				!strings.HasSuffix(lines[0], " items in import-report.md") ||
				lines[1] != "Review the files, then run: megooflags apply --dir "+dir {
				t.Fatalf("out = %q", out)
			}
		})
	}
}

func TestImportStrict(t *testing.T) {
	s := newSession(t)
	dir := t.TempDir()
	code, out, errOut := s.run("import", "unleash", "--file", filepath.Join(importTestdata("unleash"), "export.json"),
		"--project", "shop", "--out", dir, "--strict")
	if code != ExitError || !strings.Contains(out, "import-report.md") || !strings.Contains(errOut, "--strict") {
		t.Fatalf("code = %d, stdout = %q, stderr = %q", code, out, errOut)
	}
	if _, err := os.Stat(filepath.Join(dir, "import-report.md")); err != nil {
		t.Fatalf("the report is not written: %v", err)
	}

	// An export with nothing untranslatable passes --strict.
	empty := filepath.Join(t.TempDir(), "empty.json")
	if err := os.WriteFile(empty, []byte(`{"environments":[{"key":"production","name":"Production"}],"flags":{},"segments":{}}`), 0o644); err != nil {
		t.Fatal(err)
	}
	out = ok(t, s, "import", "launchdarkly", "--file", empty, "--project", "shop", "--out", t.TempDir(), "--strict")
	if !strings.HasPrefix(out, "0 flags, 0 segments; 0 items in import-report.md\n") {
		t.Fatalf("out = %q", out)
	}
}

func TestImportAPIWithoutToken(t *testing.T) {
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"unleash", "--url", "http://unleash.invalid"}, "set UNLEASH_API_TOKEN to the Unleash API token"},
		{[]string{"flagsmith", "--source-project", "7"}, "set FLAGSMITH_API_KEY to the Flagsmith API token"},
		{[]string{"launchdarkly"}, "set LAUNCHDARKLY_API_TOKEN to the LaunchDarkly API token"},
		{[]string{"launchdarkly", "--token-env", "LD_TOKEN"}, "set LD_TOKEN to the LaunchDarkly API token"},
	} {
		s := newSession(t)
		args := append([]string{"import"}, tc.args...)
		code, _, errOut := s.run(append(args, "--project", "shop", "--out", t.TempDir())...)
		if code != ExitError || !strings.Contains(errOut, tc.want) {
			t.Errorf("%v: code = %d, stderr = %q", tc.args, code, errOut)
		}
	}
}

func TestImportUsageErrors(t *testing.T) {
	file := filepath.Join(importTestdata("unleash"), "export.json")
	for _, args := range [][]string{
		{"import"},
		{"import", "split"},
		{"import", "unleash", "--file", file, "--out", "x"},
		{"import", "unleash", "--file", file, "--project", "shop"},
		{"import", "unleash", "--file", file, "--url", "http://u", "--project", "shop", "--out", "x"},
		{"import", "unleash", "--project", "shop", "--out", "x"},
		{"import", "flagsmith", "--project", "shop", "--out", "x"},
		{"import", "unleash", "--file", file, "--project", "shop", "--out", "x", "--env-map", "prod"},
	} {
		s := newSession(t)
		s.env["UNLEASH_API_TOKEN"], s.env["FLAGSMITH_API_KEY"] = "t", "t"
		if code, _, errOut := s.run(args...); code != ExitUsage {
			t.Errorf("%v: code = %d, stderr = %q", args, code, errOut)
		}
	}
}

// unleashAPI serves the Unleash fixture as the Admin API does, for token.
func unleashAPI(t *testing.T, token string) *httptest.Server {
	t.Helper()
	e, err := unleash.Fetch(t.Context(), importers.Source{File: filepath.Join(importTestdata("unleash"), "export.json")})
	if err != nil {
		t.Fatal(err)
	}
	send := func(w http.ResponseWriter, v any) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(v)
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != token {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		switch r.Method + " " + r.URL.Path {
		case "GET /api/admin/environments":
			type env struct {
				Name    string `json:"name"`
				Enabled bool   `json:"enabled"`
			}
			envs := []env{{Name: "archive", Enabled: false}}
			for _, name := range e.Environments {
				envs = append(envs, env{Name: name, Enabled: true})
			}
			send(w, map[string]any{"environments": envs})
		case "POST /api/admin/features-batch/export":
			var body struct{ Environment, Project string }
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Project != "web" {
				t.Errorf("export body = %+v, %v", body, err)
			}
			send(w, e.PerEnv[body.Environment])
		case "GET /api/admin/segments":
			send(w, map[string]any{"segments": e.Segments})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestImportSaveRoundTrip(t *testing.T) {
	srv := unleashAPI(t, "user:abc")
	s := newSession(t)
	s.env["MY_UNLEASH"] = "user:abc"
	work := t.TempDir()
	raw := filepath.Join(work, "raw.json")
	golden := filepath.Join(importTestdata("unleash"), "golden")

	fromAPI := filepath.Join(work, "api")
	ok(t, s, "import", "unleash", "--url", srv.URL, "--token-env", "MY_UNLEASH", "--source-project", "web",
		"--project", "shop", "--out", fromAPI, "--env-map", "production=prod", "--save", raw)
	importertest.CompareDirs(t, fromAPI, golden)

	b, err := os.ReadFile(raw)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(b), "{\n  \"environments\": [") {
		t.Fatalf("raw.json is not indented JSON:\n%.200s", b)
	}
	fromFile := filepath.Join(work, "file")
	ok(t, s, "import", "unleash", "--file", raw, "--project", "shop", "--out", fromFile, "--env-map", "production=prod")
	importertest.CompareDirs(t, fromFile, golden)
}

func TestImportFetchError(t *testing.T) {
	srv := unleashAPI(t, "user:abc")
	s := newSession(t)
	s.env["UNLEASH_API_TOKEN"] = "wrong"
	dir := filepath.Join(t.TempDir(), "out")
	code, _, errOut := s.run("import", "unleash", "--url", srv.URL, "--project", "shop", "--out", dir)
	if code != ExitError || !strings.Contains(errOut, "HTTP 401") {
		t.Fatalf("code = %d, stderr = %q", code, errOut)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("directory written on error: %v", err)
	}
}

func TestImportEnvMapRenamesEnvironments(t *testing.T) {
	file := filepath.Join(importTestdata("launchdarkly"), "export.json")
	s := newSession(t)
	envKeys := func(args ...string) string {
		t.Helper()
		dir := t.TempDir()
		ok(t, s, append([]string{"import", "launchdarkly", "--file", file, "--project", "shop", "--out", dir}, args...)...)
		b, err := os.ReadFile(filepath.Join(dir, "megooflags.yaml"))
		if err != nil {
			t.Fatal(err)
		}
		var keys []string
		for _, line := range strings.Split(string(b), "\n") {
			if k, ok := strings.CutPrefix(line, "  - key: "); ok {
				keys = append(keys, k)
			}
		}
		return strings.Join(keys, ",")
	}
	if got := envKeys(); got != "production,test" {
		t.Fatalf("without --env-map: %s", got)
	}
	if got := envKeys("--env-map", "test=qa", "--env-map", "production=prod"); got != "prod,qa" {
		t.Fatalf("with --env-map: %s", got)
	}
}
