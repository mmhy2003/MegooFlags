package main

import (
	"bytes"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// syncTestdata is the sync package's testdata, shared with these tests.
var syncTestdata = filepath.Join("..", "..", "internal", "sync", "testdata")

// fixtureAPI serves the sync package's API fixtures: a.b.json answers
// GET /api/projects/shop/a/b and project.json the project. The flag list
// answers with the live flags first, then flags.archived.json.
func fixtureAPI(t *testing.T) *fakeAPI {
	t.Helper()
	dir := filepath.Join(syncTestdata, "api")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	routes := map[string]reply{}
	for _, e := range entries {
		body, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
		name := strings.TrimSuffix(e.Name(), ".json")
		if name == "flags.archived" {
			continue
		}
		path := "/api/projects/shop"
		if name != "project" {
			path += "/" + strings.ReplaceAll(name, ".", "/")
		}
		routes["GET "+path] = reply{body: string(body)}
	}
	archived, err := os.ReadFile(filepath.Join(dir, "flags.archived.json"))
	if err != nil {
		t.Fatal(err)
	}
	api := newFakeAPI(t, routes)
	live := routes["GET /api/projects/shop/flags"]
	api.queue = map[string][]reply{"GET /api/projects/shop/flags": {live, {body: string(archived)}}}
	return api
}

func TestExportWritesFiles(t *testing.T) {
	api := fixtureAPI(t)
	s := api.session("shop")
	dir := filepath.Join(t.TempDir(), "flags-as-code")

	out := ok(t, s, "export", "--dir", dir)
	if out != "exported 3 flags and 2 segments to "+dir+"\n" {
		t.Fatalf("out = %q", out)
	}
	got, want := readTree(t, dir), readTree(t, filepath.Join(syncTestdata, "export-golden"))
	if len(got) != len(want) {
		t.Fatalf("files = %v, want %v", keys(got), keys(want))
	}
	for name, wb := range want {
		if !bytes.Equal(got[name], wb) {
			t.Errorf("%s =\n%s\nwant\n%s", name, got[name], wb)
		}
	}
	queries := map[string]bool{}
	for _, r := range api.requests {
		if r.Path == "/api/projects/shop/flags" {
			queries[r.Query] = true
		}
	}
	if !queries["limit=500"] || !queries["archived=true&limit=500"] {
		t.Fatalf("flag list queries = %v, want live and archived", queries)
	}
}

func TestExportEnvFilter(t *testing.T) {
	api := fixtureAPI(t)
	s := api.session("shop")
	dir := t.TempDir()

	ok(t, s, "export", "--dir", dir, "--env", "production")
	b, err := os.ReadFile(filepath.Join(dir, "flags", "new-checkout.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(b), "development:") || !strings.Contains(string(b), "production:") {
		t.Fatalf("new-checkout.yaml =\n%s", b)
	}
	for _, r := range api.requests {
		if strings.HasSuffix(r.Path, "/environments/development") {
			t.Fatalf("requested %s", r.Path)
		}
	}
}

func TestExportUsageAndErrors(t *testing.T) {
	api := fixtureAPI(t)
	if code, _, _ := api.session("shop").run("export"); code != ExitUsage {
		t.Fatalf("missing --dir: code = %d", code)
	}
	if code, _, _ := api.session("").run("export", "--dir", t.TempDir()); code != ExitUsage {
		t.Fatalf("no project: code = %d", code)
	}
	dir := filepath.Join(t.TempDir(), "out")
	code, _, errOut := api.session("nope").run("export", "--dir", dir)
	if code != ExitError || errOut != "error: not_found: Not found\n" {
		t.Fatalf("code = %d, stderr = %q", code, errOut)
	}
	if _, err := os.Stat(dir); !os.IsNotExist(err) {
		t.Fatalf("directory written on error: %v", err)
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

func keys[V any](m map[string]V) []string {
	var out []string
	for k := range m {
		out = append(out, k)
	}
	return out
}
