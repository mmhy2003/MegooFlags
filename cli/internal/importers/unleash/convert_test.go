package unleash

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"io"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers"
)

var update = flag.Bool("update", false, "rewrite testdata/golden")

// TestConvertGolden converts testdata/export.json, writes it with the
// report and compares the result with testdata/golden; go test
// ./internal/importers/unleash -update rewrites it.
func TestConvertGolden(t *testing.T) {
	e, err := Fetch(context.Background(), importers.Source{File: filepath.Join("testdata", "export.json")})
	if err != nil {
		t.Fatal(err)
	}
	d, report := Convert(e, importers.Options{Project: "shop", EnvMap: map[string]string{"production": "prod"}})
	write := func(dir string) {
		t.Helper()
		if err := flagfiles.Write(dir, d); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "import-report.md"), []byte(report.Markdown()), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	golden := filepath.Join("testdata", "golden")
	if *update {
		write(golden)
	}
	out := t.TempDir()
	write(out)
	compareDirs(t, out, golden)

	loaded, err := flagfiles.Load(out)
	if err != nil {
		t.Fatal(err)
	}
	if errs := flagfiles.Validate(loaded, nil); len(errs) > 0 {
		t.Fatalf("the import does not validate: %v", errs)
	}
}

func TestFetchSendsTokenAndExportsEachEnabledEnvironment(t *testing.T) {
	var requests []string
	exports := map[string]string{
		"development": `{"features": [{"name": "a", "type": "release"}], "featureStrategies": [], "featureEnvironments": [], "dependencies": [], "tagTypes": []}`,
		"production":  `{"features": [{"name": "a", "type": "release"}, {"name": "b", "type": "release"}], "featureStrategies": [], "featureEnvironments": []}`,
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "user:abc" {
			t.Errorf("%s %s: headers %v", r.Method, r.URL, r.Header)
		}
		requests = append(requests, r.Method+" "+r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		switch r.Method + " " + r.URL.Path {
		case "GET /api/admin/environments":
			_, _ = io.WriteString(w, `{"version": 1, "environments": [
				{"name": "development", "type": "development", "enabled": true, "sortOrder": 100},
				{"name": "staging", "type": "test", "enabled": false, "sortOrder": 200},
				{"name": "production", "type": "production", "enabled": true, "sortOrder": 300}]}`)
		case "POST /api/admin/features-batch/export":
			if ct := r.Header.Get("Content-Type"); ct != "application/json" {
				t.Errorf("Content-Type = %q", ct)
			}
			var body map[string]any
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatal(err)
			}
			env, _ := body["environment"].(string)
			if !reflect.DeepEqual(body, map[string]any{"environment": env, "project": "web"}) {
				t.Errorf("export body = %v", body)
			}
			_, _ = io.WriteString(w, exports[env])
		case "GET /api/admin/segments":
			_, _ = io.WriteString(w, `{"segments": [{"id": 4, "name": "beta", "description": "", "project": null,
				"constraints": [{"contextName": "plan", "operator": "IN", "values": ["pro"], "inverted": false, "caseInsensitive": false}]}]}`)
		default:
			http.Error(w, `{"message": "not found"}`, http.StatusNotFound)
		}
	}))
	defer srv.Close()

	got, err := Fetch(context.Background(), importers.Source{URL: srv.URL + "/", Token: "user:abc", Project: "web", HTTP: srv.Client()})
	if err != nil {
		t.Fatal(err)
	}
	want := &Export{
		Environments: []string{"development", "production"},
		PerEnv: map[string]BatchExport{
			"development": {Features: []Feature{{Name: "a", Type: "release"}}, FeatureStrategies: []Strategy{}, FeatureEnvironments: []FeatureEnvironment{}, Dependencies: []Dependencies{}},
			"production":  {Features: []Feature{{Name: "a", Type: "release"}, {Name: "b", Type: "release"}}, FeatureStrategies: []Strategy{}, FeatureEnvironments: []FeatureEnvironment{}},
		},
		Segments: []Segment{{ID: 4, Name: "beta", Constraints: []Constraint{{ContextName: "plan", Operator: "IN", Values: []string{"pro"}}}}},
	}
	if !reflect.DeepEqual(got, want) {
		g, _ := json.Marshal(got)
		w, _ := json.Marshal(want)
		t.Fatalf("got  %s\nwant %s", g, w)
	}
	wantRequests := []string{
		"GET /api/admin/environments",
		"POST /api/admin/features-batch/export",
		"POST /api/admin/features-batch/export",
		"GET /api/admin/segments",
	}
	if !reflect.DeepEqual(requests, wantRequests) {
		t.Fatalf("requests %v", requests)
	}
}

func TestFetchError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"message":"You must log in to use Unleash."}`, http.StatusUnauthorized)
	}))
	defer srv.Close()
	_, err := Fetch(context.Background(), importers.Source{URL: srv.URL, Token: "bad", Project: "web"})
	want := `GET /api/admin/environments: HTTP 401: {"message":"You must log in to use Unleash."}`
	if err == nil || err.Error() != want {
		t.Fatalf("err = %v", err)
	}
}

func TestFetchNeedsURLAndProject(t *testing.T) {
	for _, src := range []importers.Source{{Token: "t", Project: "web"}, {URL: "http://unleash", Token: "t"}} {
		if _, err := Fetch(context.Background(), src); err == nil {
			t.Fatalf("%+v: no error", src)
		}
	}
	_, err := Fetch(context.Background(), importers.Source{Token: "t", Project: "web"})
	if err == nil || !strings.Contains(err.Error(), "URL") {
		t.Fatalf("err = %v", err)
	}
}

// compareDirs fails t unless the regular files under got and want have the
// same names and bytes.
func compareDirs(t *testing.T, got, want string) {
	t.Helper()
	g, w := readTree(t, got), readTree(t, want)
	for name, wb := range w {
		gb, ok := g[name]
		if !ok {
			t.Errorf("%s is missing", name)
		} else if !bytes.Equal(gb, wb) {
			t.Errorf("%s =\n%s\nwant\n%s", name, gb, wb)
		}
	}
	for name := range g {
		if _, ok := w[name]; !ok {
			t.Errorf("%s is not expected", name)
		}
	}
}

func readTree(t *testing.T, root string) map[string][]byte {
	t.Helper()
	files := map[string][]byte{}
	err := filepath.WalkDir(root, func(path string, e fs.DirEntry, err error) error {
		if err != nil || e.IsDir() {
			return err
		}
		b, err := os.ReadFile(path)
		rel, _ := filepath.Rel(root, path)
		files[filepath.ToSlash(rel)] = b
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	return files
}
