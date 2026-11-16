package launchdarkly

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
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

func loadFixture(t *testing.T) *Export {
	t.Helper()
	e, err := Fetch(context.Background(), importers.Source{File: filepath.Join("testdata", "export.json")})
	if err != nil {
		t.Fatal(err)
	}
	return e
}

// TestConvertGolden converts testdata/export.json, writes it with the
// report and compares the result with testdata/golden; go test
// ./internal/importers/launchdarkly -update rewrites it.
func TestConvertGolden(t *testing.T) {
	d, report := Convert(loadFixture(t), importers.Options{Project: "shop", EnvMap: map[string]string{"test": "staging"}})
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

func TestConvertDefaultsWithoutEnvMap(t *testing.T) {
	e := &Export{
		Environments: []Environment{{Key: "Prod", Name: "Prod"}},
		Flags: map[string][]Flag{"Prod": {{
			Key: "f", Name: "F", Kind: "boolean",
			Variations:   []Variation{{Value: true}, {Value: false}},
			Environments: map[string]FlagConfig{"Prod": {On: true, Fallthrough: Serve{Variation: ptr(0)}}},
		}}},
	}
	d, report := Convert(e, importers.Options{Project: "shop"})
	if got := d.Project.Environments[0].Key; got != "prod" {
		t.Fatalf("environment key = %q", got)
	}
	f := d.Flags["f"]
	if f.OffVariation != "v1" || f.Environments["prod"].Fallthrough.Variation == nil {
		t.Fatalf("flag = %+v", f)
	}
	want := []importers.ReportLine{
		{Resource: "environment", Key: "prod", Message: `key "Prod" was renamed to "prod"`},
		{Resource: "flag", Key: "f", Message: "has no off variation in any environment; v1 is served when the flag is off"},
	}
	if !reflect.DeepEqual(report.Lines, want) {
		t.Fatalf("report = %#v", report.Lines)
	}
}

func TestFetchFollowsPagesAndSendsHeaders(t *testing.T) {
	var paths []string
	pages := map[string]string{
		"/api/v2/projects/web/environments": `{"items": [{"key": "production", "name": "Production", "color": "417505"}, {"key": "test", "name": "Test"}], "_links": {}}`,
		"/api/v2/flags/web?env=production&limit=100&offset=0&summary=0": `{"items": [{"key": "a", "kind": "boolean"}],
			"_links": {"next": {"href": "/api/v2/flags/web?env=production&summary=0&limit=100&offset=100"}}}`,
		"/api/v2/flags/web?env=production&limit=100&offset=100&summary=0": `{"items": [{"key": "b", "kind": "boolean"}], "_links": {"self": {"href": "x"}}}`,
		"/api/v2/flags/web?env=test&limit=100&offset=0&summary=0":         `{"items": [], "_links": {}}`,
		"/api/v2/segments/web/production?limit=50&offset=0": `{"items": [{"key": "s1"}],
			"_links": {"next": {"href": "/api/v2/segments/web/production?limit=50&offset=50"}}}`,
		"/api/v2/segments/web/production?limit=50&offset=50": `{"items": [{"key": "s2", "unbounded": true}]}`,
		"/api/v2/segments/web/test?limit=50&offset=0":        `{"items": []}`,
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "api-123" || r.Header.Get("LD-API-Version") != "20240415" {
			t.Errorf("%s: headers %v", r.URL, r.Header)
		}
		key := r.URL.Path
		if r.URL.RawQuery != "" {
			key += "?" + r.URL.Query().Encode()
		}
		paths = append(paths, key)
		body, ok := pages[key]
		if !ok {
			http.Error(w, `{"code": "not_found"}`, http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(body))
	}))
	defer srv.Close()

	got, err := Fetch(context.Background(), importers.Source{URL: srv.URL + "/", Token: "api-123", Project: "web", HTTP: srv.Client()})
	if err != nil {
		t.Fatal(err)
	}
	want := &Export{
		Environments: []Environment{{Key: "production", Name: "Production"}, {Key: "test", Name: "Test"}},
		Flags:        map[string][]Flag{"production": {{Key: "a", Kind: "boolean"}, {Key: "b", Kind: "boolean"}}, "test": {}},
		Segments:     map[string][]Segment{"production": {{Key: "s1"}, {Key: "s2", Unbounded: true}}, "test": {}},
	}
	if !reflect.DeepEqual(got, want) {
		g, _ := json.Marshal(got)
		w, _ := json.Marshal(want)
		t.Fatalf("got  %s\nwant %s", g, w)
	}
	if len(paths) != len(pages) {
		t.Fatalf("requests %v", paths)
	}
}

func TestFetchError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"code":"unauthorized","message":"Invalid account ID header"}`, http.StatusUnauthorized)
	}))
	defer srv.Close()
	_, err := Fetch(context.Background(), importers.Source{URL: srv.URL, Token: "bad", Project: "web"})
	want := `GET /api/v2/projects/web/environments: HTTP 401: {"code":"unauthorized","message":"Invalid account ID header"}`
	if err == nil || err.Error() != want {
		t.Fatalf("err = %v", err)
	}
}

func TestFetchNeedsProject(t *testing.T) {
	_, err := Fetch(context.Background(), importers.Source{Token: "t"})
	if err == nil || !strings.Contains(err.Error(), "project") {
		t.Fatalf("err = %v", err)
	}
}

func ptr[T any](v T) *T { return &v }

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
