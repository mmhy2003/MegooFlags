package sync

import (
	"bytes"
	"context"
	"flag"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
)

var update = flag.Bool("update", false, "rewrite testdata/export-golden")

// fakeAPI serves testdata/api: GET /api/projects/shop/a/b is a.b.json, with
// .archived before .json when the query has archived=true, and the project
// itself is project.json. Anything else is 404 not_found.
func fakeAPI(t *testing.T) *client.Client {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rest, ok := strings.CutPrefix(r.URL.Path, "/api/projects/shop")
		name := strings.ReplaceAll(strings.TrimPrefix(rest, "/"), "/", ".")
		if name == "" {
			name = "project"
		}
		if r.URL.Query().Get("archived") == "true" {
			name += ".archived"
		}
		body, err := os.ReadFile(filepath.Join("testdata", "api", name+".json"))
		w.Header().Set("Content-Type", "application/json")
		if !ok || r.Method != http.MethodGet || err != nil {
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"code":"not_found","message":"Not found"}`))
			return
		}
		_, _ = w.Write(body)
	}))
	t.Cleanup(srv.Close)
	return client.New(srv.URL, "test-token", client.WithMaxRetries(0))
}

func ptr[T any](v T) *T { return &v }

func boolFlag(on, off string) []client.Variation {
	return []client.Variation{
		{Key: "true", Name: on, Description: "", Value: true},
		{Key: "false", Name: off, Description: "", Value: false},
	}
}

func offEnv(variation string) flagfiles.FlagEnv {
	return flagfiles.FlagEnv{
		Targets:       []client.Target{},
		Rules:         []client.Rule{},
		Fallthrough:   client.Serve{Variation: ptr(variation)},
		Prerequisites: []client.Prerequisite{},
	}
}

// expected is the Dir that testdata/api describes.
func expected() *flagfiles.Dir {
	devCheckout := offEnv("true")
	devCheckout.Enabled = true
	return &flagfiles.Dir{
		Project: flagfiles.ProjectFile{
			APIVersion: flagfiles.APIVersion,
			Project:    flagfiles.ProjectMeta{Key: "shop", Name: "Shop", Description: "The web shop"},
			Environments: []flagfiles.Environment{
				{Key: "development", Name: "Development", Description: "", ApprovalsRequired: 0},
				{Key: "production", Name: "Production", Description: "Live traffic", ApprovalsRequired: 1},
			},
		},
		Segments: map[string]flagfiles.Segment{
			"beta": {
				Key: "beta", Name: "Beta", Description: "Beta testers",
				Included: []string{"u1", "u2"}, Excluded: []string{"u9"},
				Rules: []client.SegmentRule{{Description: "Staff email", Clauses: []client.Clause{
					{Attribute: "email", Operator: "ends_with", Values: []any{"@example.com"}},
				}}},
			},
			"staff": {
				Key: "staff", Name: "Staff", Description: "",
				Included: []string{}, Excluded: []string{}, Rules: []client.SegmentRule{},
			},
		},
		Flags: map[string]flagfiles.Flag{
			"banner-text": {
				Key: "banner-text", Name: "Banner text", Type: "string", Tags: []string{},
				Variations: []client.Variation{
					{Key: "hello", Name: "Hello", Value: "Hello!"},
					{Key: "welcome", Name: "Welcome", Value: "Welcome back"},
				},
				OffVariation: "hello", Permanent: true,
				Environments: map[string]flagfiles.FlagEnv{
					"development": offEnv("hello"),
					"production": {
						Enabled: true,
						Targets: []client.Target{},
						Rules: []client.Rule{{
							Description: "Mobile",
							Clauses:     []client.Clause{{Attribute: "device", Operator: "eq", Values: []any{"mobile"}}},
							Rollout: &client.Rollout{BucketBy: "key", Weights: []client.Weight{
								{Variation: "hello", Weight: 40000}, {Variation: "welcome", Weight: 60000},
							}},
						}},
						Fallthrough: client.Serve{Rollout: &client.Rollout{BucketBy: "key", Weights: []client.Weight{
							{Variation: "welcome", Weight: 25000}, {Variation: "hello", Weight: 75000},
						}}},
						Prerequisites: []client.Prerequisite{},
					},
				},
			},
			"new-checkout": {
				Key: "new-checkout", Name: "New checkout", Description: "The redesigned checkout", Type: "boolean",
				Tags: []string{"web", "checkout"}, Owner: ptr("mona@example.com"),
				Variations: boolFlag("On", "Off"), OffVariation: "false",
				Environments: map[string]flagfiles.FlagEnv{
					"development": devCheckout,
					"production": {
						Enabled: true,
						Targets: []client.Target{{Variation: "true", Values: []string{"u1", "u3"}}},
						Rules: []client.Rule{
							{
								Description: "Beta and staff",
								Clauses:     []client.Clause{{Attribute: "key", Operator: "in_segment", Values: []any{"beta", "staff"}}},
								Variation:   ptr("true"),
							},
							{
								Description: "Country",
								Clauses:     []client.Clause{{Attribute: "country", Operator: "in", Values: []any{"us", "ca"}, Negate: true}},
								Variation:   ptr("false"),
							},
						},
						Fallthrough:   client.Serve{Variation: ptr("false")},
						Prerequisites: []client.Prerequisite{{Flag: "banner-text", Variation: "hello"}},
					},
				},
			},
			"old-banner": {
				Key: "old-banner", Name: "Old banner", Description: "Replaced by banner-text", Type: "boolean",
				Tags: []string{"legacy"}, Variations: boolFlag("", ""), OffVariation: "false", Archived: true,
				Environments: map[string]flagfiles.FlagEnv{
					"development": offEnv("false"),
					"production":  offEnv("false"),
				},
			},
		},
	}
}

func TestSnapshot(t *testing.T) {
	got, err := Snapshot(context.Background(), fakeAPI(t), "shop", nil)
	if err != nil {
		t.Fatal(err)
	}
	if want := expected(); !reflect.DeepEqual(got, want) {
		t.Fatalf("Snapshot =\n%#v\nwant\n%#v", got, want)
	}
}

func TestSnapshotArchivedFlag(t *testing.T) {
	got, err := Snapshot(context.Background(), fakeAPI(t), "shop", nil)
	if err != nil {
		t.Fatal(err)
	}
	if f, ok := got.Flags["old-banner"]; !ok || !f.Archived || f.Permanent {
		t.Fatalf("old-banner = %+v, %v; want archived and not permanent", f, ok)
	}
	if got.Flags["new-checkout"].Archived {
		t.Fatal("new-checkout is archived")
	}
}

func TestSnapshotEnvFilter(t *testing.T) {
	got, err := Snapshot(context.Background(), fakeAPI(t), "shop", []string{"production"})
	if err != nil {
		t.Fatal(err)
	}
	want := expected()
	for key, f := range want.Flags {
		delete(f.Environments, "development")
		want.Flags[key] = f
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Snapshot =\n%#v\nwant\n%#v", got, want)
	}
	if len(got.Project.Environments) != 2 {
		t.Fatalf("project environments = %+v; the filter limits flag configs only", got.Project.Environments)
	}
}

func TestSnapshotUnknownEnv(t *testing.T) {
	_, err := Snapshot(context.Background(), fakeAPI(t), "shop", []string{"production", "staging"})
	if err == nil || err.Error() != `environment "staging" is not in project shop` {
		t.Fatalf("err = %v", err)
	}
}

func TestSnapshotAPIError(t *testing.T) {
	_, err := Snapshot(context.Background(), fakeAPI(t), "nope", nil)
	if !client.IsNotFound(err) {
		t.Fatalf("err = %v, want not_found", err)
	}
}

// TestExportGolden writes the snapshot and compares it with
// testdata/export-golden; go test ./internal/sync -update rewrites it.
func TestExportGolden(t *testing.T) {
	d, err := Snapshot(context.Background(), fakeAPI(t), "shop", nil)
	if err != nil {
		t.Fatal(err)
	}
	golden := filepath.Join("testdata", "export-golden")
	if *update {
		if err := flagfiles.Write(golden, d); err != nil {
			t.Fatal(err)
		}
	}
	out := t.TempDir()
	if err := flagfiles.Write(out, d); err != nil {
		t.Fatal(err)
	}
	compareDirs(t, out, golden)

	loaded, err := flagfiles.Load(out)
	if err != nil {
		t.Fatal(err)
	}
	if errs := flagfiles.Validate(loaded, nil); len(errs) > 0 {
		t.Fatalf("the export does not validate: %v", errs)
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
