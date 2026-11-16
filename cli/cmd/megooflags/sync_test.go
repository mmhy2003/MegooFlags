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

// exportedDir copies the export golden directory, which matches the fixture
// API, to a temporary directory.
func exportedDir(t *testing.T) string {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "flags-as-code")
	if err := os.CopyFS(dir, os.DirFS(filepath.Join(syncTestdata, "export-golden"))); err != nil {
		t.Fatal(err)
	}
	return dir
}

func editFile(t *testing.T, path, old, new string) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), old) {
		t.Fatalf("%s has no %q", path, old)
	}
	if err := os.WriteFile(path, []byte(strings.Replace(string(b), old, new, 1)), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestDiffNoDifferences(t *testing.T) {
	code, out, errOut := fixtureAPI(t).session("shop").run("diff", "--dir", exportedDir(t))
	if code != ExitOK || out != "No differences.\n" || errOut != "" {
		t.Fatalf("code = %d, stdout = %q, stderr = %q", code, out, errOut)
	}
}

func TestDiffDrift(t *testing.T) {
	dir := exportedDir(t)
	editFile(t, filepath.Join(dir, "megooflags.yaml"), "name: Shop", "name: Shop 2")
	editFile(t, filepath.Join(dir, "flags", "new-checkout.yaml"), "fallthrough:\n      variation: \"false\"", "fallthrough:\n      variation: \"true\"")
	editFile(t, filepath.Join(dir, "flags", "new-checkout.yaml"), "development:\n    enabled: true", "development:\n    enabled: false")

	code, out, errOut := fixtureAPI(t).session("shop").run("diff", "--dir", dir, "--env", "production")
	want := "~ project shop: name\n~ config new-checkout [production]: fallthrough\n"
	if code != ExitDrift || out != want || errOut != "" {
		t.Fatalf("code = %d, stdout = %q, stderr = %q", code, out, errOut)
	}
}

func TestDiffPrune(t *testing.T) {
	dir := exportedDir(t)
	if err := os.Remove(filepath.Join(dir, "segments", "staff.yaml")); err != nil {
		t.Fatal(err)
	}
	api := fixtureAPI(t)
	if code, out, _ := api.session("shop").run("diff", "--dir", dir); code != ExitOK {
		t.Fatalf("without --prune: code = %d, stdout = %q", code, out)
	}
	api = fixtureAPI(t)
	code, out, _ := api.session("shop").run("diff", "--dir", dir, "--prune")
	if code != ExitDrift || out != "- segment staff\n" {
		t.Fatalf("code = %d, stdout = %q", code, out)
	}
}

func TestDiffInvalidFiles(t *testing.T) {
	dir := exportedDir(t)
	editFile(t, filepath.Join(dir, "flags", "new-checkout.yaml"), "off_variation: \"false\"", "off_variation: maybe")
	code, out, errOut := fixtureAPI(t).session("shop").run("diff", "--dir", dir)
	if code != ExitError || out != "" ||
		!strings.Contains(errOut, "flags/new-checkout.yaml: off_variation: unknown variation maybe\n") {
		t.Fatalf("code = %d, stdout = %q, stderr = %q", code, out, errOut)
	}

	api := fixtureAPI(t)
	code, _, errOut = api.session("shop").run("diff", "--dir", filepath.Join(t.TempDir(), "missing"))
	if code != ExitError || errOut == "" || len(api.requests) != 0 {
		t.Fatalf("missing dir: code = %d, stderr = %q, requests = %d", code, errOut, len(api.requests))
	}
	if code, _, _ := api.session("shop").run("diff"); code != ExitUsage {
		t.Fatalf("missing --dir: code = %d", code)
	}
}
