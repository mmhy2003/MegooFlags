package flagfiles

import (
	"maps"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

func ptr[T any](v T) *T { return &v }

func mustLoad(t *testing.T, dir string) *Dir {
	t.Helper()
	d, err := Load(dir)
	if err != nil {
		t.Fatalf("Load(%s): %v", dir, err)
	}
	return d
}

// yamlFiles returns every .yaml file under dir, by path relative to dir.
func yamlFiles(t *testing.T, dir string) map[string]string {
	t.Helper()
	files := map[string]string{}
	err := filepath.WalkDir(dir, func(path string, e os.DirEntry, err error) error {
		if err != nil || e.IsDir() || filepath.Ext(path) != ".yaml" {
			return err
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(dir, path)
		files[filepath.ToSlash(rel)] = string(raw)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return files
}

func TestLoadValid(t *testing.T) {
	d := mustLoad(t, "testdata/valid")

	if d.Project.APIVersion != APIVersion || d.Project.Project.Key != "shop" {
		t.Fatalf("project: %+v", d.Project)
	}
	if got := len(d.Project.Environments); got != 2 || d.Project.Environments[1].ApprovalsRequired != 1 {
		t.Fatalf("environments: %+v", d.Project.Environments)
	}
	if got := slices.Sorted(maps.Keys(d.Segments)); !slices.Equal(got, []string{"beta", "staff"}) {
		t.Fatalf("segments: %v", got)
	}
	if d.Segments["beta"].Key != "beta" || d.Segments["beta"].Rules[0].Clauses[0].Values[0] != "@example.com" {
		t.Fatalf("beta: %+v", d.Segments["beta"])
	}
	if got := slices.Sorted(maps.Keys(d.Flags)); !slices.Equal(got, []string{"banner-text", "checkout-config", "new-checkout"}) {
		t.Fatalf("flags: %v", got)
	}

	nc := d.Flags["new-checkout"]
	if nc.Key != "new-checkout" || nc.Type != "boolean" || nc.Owner == nil || *nc.Owner != "mona@example.com" {
		t.Fatalf("new-checkout: %+v", nc)
	}
	if nc.Variations[0].Value != true || nc.OffVariation != "false" {
		t.Fatalf("new-checkout variations: %+v", nc.Variations)
	}
	prod := nc.Environments["production"]
	if !prod.Enabled || *prod.Fallthrough.Variation != "false" || prod.Prerequisites[0] != (client.Prerequisite{Flag: "banner-text", Variation: "hello"}) {
		t.Fatalf("new-checkout production: %+v", prod)
	}
	if !slices.Equal(prod.Rules[0].Clauses[0].Values, []any{"beta", "staff"}) {
		t.Fatalf("in_segment values: %v", prod.Rules[0].Clauses[0].Values)
	}

	bt := d.Flags["banner-text"]
	if bt.Owner != nil || !bt.Permanent {
		t.Fatalf("banner-text: %+v", bt)
	}
	rollout := bt.Environments["production"].Fallthrough.Rollout
	if rollout == nil || rollout.Weights[0] != (client.Weight{Variation: "welcome", Weight: 25000}) {
		t.Fatalf("banner-text fallthrough: %+v", bt.Environments["production"].Fallthrough)
	}

	cc := d.Flags["checkout-config"]
	if !cc.Archived || cc.Variations[2].Value != nil {
		t.Fatalf("checkout-config: %+v", cc)
	}
	want := map[string]any{"max_items": 10, "steps": []any{"cart", "pay"}}
	if !reflect.DeepEqual(cc.Variations[0].Value, want) {
		t.Fatalf("json value: %#v", cc.Variations[0].Value)
	}

	if errs := Validate(d, nil); len(errs) != 0 {
		t.Fatalf("Validate: %v", errs)
	}
}

func TestRoundTrip(t *testing.T) {
	d := mustLoad(t, "testdata/valid")
	out := t.TempDir()
	if err := Write(out, d); err != nil {
		t.Fatalf("Write: %v", err)
	}

	want, got := yamlFiles(t, "testdata/valid"), yamlFiles(t, out)
	if !slices.Equal(slices.Sorted(maps.Keys(got)), slices.Sorted(maps.Keys(want))) {
		t.Fatalf("files: got %v, want %v", slices.Sorted(maps.Keys(got)), slices.Sorted(maps.Keys(want)))
	}
	for name, w := range want {
		if got[name] != w {
			t.Errorf("%s differs:\n--- got\n%s\n--- want\n%s", name, got[name], w)
		}
	}

	again := mustLoad(t, out)
	if !reflect.DeepEqual(again, d) {
		t.Fatalf("reloaded Dir differs:\n%#v\n%#v", again, d)
	}
}

func TestLoadUnknownFieldReportsFileAndLine(t *testing.T) {
	_, err := Load("testdata/invalid/unknown-field")
	if err == nil || !strings.Contains(err.Error(), "flags/bad.yaml:3: ") || !strings.Contains(err.Error(), "colour") {
		t.Fatalf("got %v", err)
	}
}

func TestLoadSyntaxErrorReportsFileAndLine(t *testing.T) {
	_, err := Load("testdata/invalid/syntax")
	if err == nil || !strings.HasPrefix(err.Error(), "flags/broken.yaml:") {
		t.Fatalf("got %v", err)
	}
}

func TestLoadBadAPIVersion(t *testing.T) {
	_, err := Load("testdata/invalid/api-version")
	if err == nil || !strings.Contains(err.Error(), "megooflags.yaml:1: ") || !strings.Contains(err.Error(), "megooflags/v2") {
		t.Fatalf("got %v", err)
	}
}

func TestLoadBadFileName(t *testing.T) {
	_, err := Load("testdata/invalid/file-name")
	if err == nil || !strings.Contains(err.Error(), "segments/Beta_Testers.yaml") {
		t.Fatalf("got %v", err)
	}
}

func TestLoadMissingProjectFile(t *testing.T) {
	_, err := Load(t.TempDir())
	if err == nil || !strings.Contains(err.Error(), "megooflags.yaml") {
		t.Fatalf("got %v", err)
	}
}

func TestValidateRules(t *testing.T) {
	cases := []struct {
		name   string
		mutate func(d *Dir)
		want   string
	}{
		{"unknown environment", func(d *Dir) {
			f := d.Flags["new-checkout"]
			f.Environments["staging"] = f.Environments["development"]
		}, "flags/new-checkout.yaml: environments.staging: environment staging is not in megooflags.yaml"},
		{"target variation", func(d *Dir) {
			d.Flags["new-checkout"].Environments["production"].Targets[0].Variation = "maybe"
		}, "flags/new-checkout.yaml: environments.production.targets[0].variation: unknown variation maybe"},
		{"rule variation", func(d *Dir) {
			d.Flags["new-checkout"].Environments["production"].Rules[1].Variation = ptr("maybe")
		}, "flags/new-checkout.yaml: environments.production.rules[1].variation: unknown variation maybe"},
		{"rule rollout variation", func(d *Dir) {
			d.Flags["banner-text"].Environments["production"].Rules[0].Rollout.Weights[1].Variation = "maybe"
		}, "flags/banner-text.yaml: environments.production.rules[0].rollout.weights[1].variation: unknown variation maybe"},
		{"fallthrough variation", func(d *Dir) {
			env := d.Flags["new-checkout"].Environments["development"]
			env.Fallthrough.Variation = ptr("maybe")
			d.Flags["new-checkout"].Environments["development"] = env
		}, "flags/new-checkout.yaml: environments.development.fallthrough.variation: unknown variation maybe"},
		{"serve nothing", func(d *Dir) {
			env := d.Flags["new-checkout"].Environments["development"]
			env.Fallthrough = client.Serve{}
			d.Flags["new-checkout"].Environments["development"] = env
		}, "flags/new-checkout.yaml: environments.development.fallthrough: must serve either a variation or a rollout"},
		{"off variation", func(d *Dir) {
			f := d.Flags["new-checkout"]
			f.OffVariation = "maybe"
			d.Flags["new-checkout"] = f
		}, "flags/new-checkout.yaml: off_variation: unknown variation maybe"},
		{"prerequisite flag", func(d *Dir) {
			d.Flags["new-checkout"].Environments["production"].Prerequisites[0].Flag = "nope"
		}, "flags/new-checkout.yaml: environments.production.prerequisites[0].flag: unknown flag nope"},
		{"prerequisite variation", func(d *Dir) {
			d.Flags["new-checkout"].Environments["production"].Prerequisites[0].Variation = "bye"
		}, "flags/new-checkout.yaml: environments.production.prerequisites[0].variation: flag banner-text has no variation bye"},
		{"in_segment", func(d *Dir) {
			d.Flags["new-checkout"].Environments["production"].Rules[0].Clauses[0].Values[1] = "vip"
		}, "flags/new-checkout.yaml: environments.production.rules[0].clauses[0].values: unknown segment vip"},
		{"rollout sum", func(d *Dir) {
			d.Flags["banner-text"].Environments["production"].Fallthrough.Rollout.Weights[0].Weight = 20000
		}, "flags/banner-text.yaml: environments.production.fallthrough.rollout.weights: weights sum to 95000, not 100000"},
		{"duplicate variation", func(d *Dir) {
			d.Flags["checkout-config"].Variations[1].Key = "default"
		}, "flags/checkout-config.yaml: variations[1].key: duplicate variation default"},
		{"type", func(d *Dir) {
			f := d.Flags["checkout-config"]
			f.Type = "object"
			d.Flags["checkout-config"] = f
		}, "flags/checkout-config.yaml: type: must be boolean, string, number or json, not object"},
		{"value type", func(d *Dir) {
			d.Flags["banner-text"].Variations[1].Value = 3
		}, "flags/banner-text.yaml: variations[1].value: must be a string"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := mustLoad(t, "testdata/valid")
			tc.mutate(d)
			errs := Validate(d, nil)
			if len(errs) != 1 || errs[0].Error() != tc.want {
				t.Fatalf("got %v\nwant [%s]", errs, tc.want)
			}
		})
	}
}

func TestValidateNumberValues(t *testing.T) {
	f := Flag{Key: "limit", Name: "Limit", Type: "number", OffVariation: "a",
		Variations: []client.Variation{{Key: "a", Value: 1}, {Key: "b", Value: 2.5}, {Key: "c", Value: true}, {Key: "d", Value: "4"}}}
	d := &Dir{Project: ProjectFile{APIVersion: APIVersion}, Flags: map[string]Flag{"limit": f}}
	got := errorStrings(Validate(d, nil))
	want := []string{
		"flags/limit.yaml: variations[2].value: must be a number",
		"flags/limit.yaml: variations[3].value: must be a number",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("got %v", got)
	}
}

func TestValidateBooleanVariations(t *testing.T) {
	f := Flag{Key: "dark", Name: "Dark", Type: "boolean", OffVariation: "off",
		Variations: []client.Variation{{Key: "on", Value: true}, {Key: "off", Value: false}}}
	d := &Dir{Project: ProjectFile{APIVersion: APIVersion}, Flags: map[string]Flag{"dark": f}}
	got := errorStrings(Validate(d, nil))
	want := []string{`flags/dark.yaml: variations: a boolean flag has exactly the variations "true": true and "false": false`}
	if !slices.Equal(got, want) {
		t.Fatalf("got %v", got)
	}

	f.Variations = []client.Variation{{Key: "true", Name: "On", Value: true}, {Key: "false", Value: false}}
	f.OffVariation = "false"
	d.Flags["dark"] = f
	if errs := Validate(d, nil); len(errs) != 0 {
		t.Fatalf("true and false with names should be valid: %v", errs)
	}
}

func TestValidateServerSegments(t *testing.T) {
	d := mustLoad(t, "testdata/valid")
	d.Flags["new-checkout"].Environments["production"].Rules[0].Clauses[0].Values[1] = "vip"
	if errs := Validate(d, []string{"vip"}); len(errs) != 0 {
		t.Fatalf("a server segment should satisfy in_segment: %v", errs)
	}
}

func TestValidateCollectsAllErrors(t *testing.T) {
	d := mustLoad(t, "testdata/valid")
	d.Flags["banner-text"].Variations[1].Value = 3
	d.Flags["new-checkout"].Environments["production"].Targets[0].Variation = "maybe"
	d.Flags["checkout-config"].Variations[1].Key = "default"
	got := errorStrings(Validate(d, nil))
	want := []string{
		"flags/banner-text.yaml: variations[1].value: must be a string",
		"flags/checkout-config.yaml: variations[1].key: duplicate variation default",
		"flags/new-checkout.yaml: environments.production.targets[0].variation: unknown variation maybe",
	}
	if !slices.Equal(got, want) {
		t.Fatalf("got %v", got)
	}
}

func TestWriteRemovesStaleFiles(t *testing.T) {
	out := t.TempDir()
	d := mustLoad(t, "testdata/valid")
	if err := Write(out, d); err != nil {
		t.Fatal(err)
	}
	notes := filepath.Join(out, "flags", "README.md")
	if err := os.WriteFile(notes, []byte("keep"), 0o644); err != nil {
		t.Fatal(err)
	}

	delete(d.Flags, "banner-text")
	delete(d.Segments, "staff")
	if err := Write(out, d); err != nil {
		t.Fatal(err)
	}
	got := slices.Sorted(maps.Keys(yamlFiles(t, out)))
	want := []string{"flags/checkout-config.yaml", "flags/new-checkout.yaml", "megooflags.yaml", "segments/beta.yaml"}
	if !slices.Equal(got, want) {
		t.Fatalf("got %v", got)
	}
	if _, err := os.Stat(notes); err != nil {
		t.Fatalf("a non-YAML file was removed: %v", err)
	}
}

func TestWriteSortsSetsAndKeepsOrderedLists(t *testing.T) {
	d := mustLoad(t, "testdata/valid")
	nc := d.Flags["new-checkout"]
	nc.Tags = []string{"web", "checkout"}
	prod := nc.Environments["production"]
	prod.Rules[0].Clauses[0].Values = []any{"staff", "beta"}
	d.Flags["new-checkout"] = nc
	beta := d.Segments["beta"]
	beta.Included = []string{"u2", "u1"}
	d.Segments["beta"] = beta

	out := t.TempDir()
	if err := Write(out, d); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(yamlFiles(t, out), yamlFiles(t, "testdata/valid")) {
		t.Fatal("sorted output differs from the canonical files")
	}
	if !slices.Equal(nc.Tags, []string{"web", "checkout"}) || !slices.Equal(beta.Included, []string{"u2", "u1"}) {
		t.Fatal("Write changed its input")
	}

	// Order carries meaning elsewhere: rules, variations and in-clause
	// values keep theirs.
	again := mustLoad(t, out)
	if again.Flags["new-checkout"].Environments["production"].Rules[0].Description != "Beta and staff" ||
		!slices.Equal(again.Flags["new-checkout"].Environments["production"].Rules[1].Clauses[0].Values, []any{"us", "ca"}) ||
		again.Flags["banner-text"].Environments["production"].Fallthrough.Rollout.Weights[0].Variation != "welcome" {
		t.Fatal("an ordered list was reordered")
	}
}

func TestWriteRejectsBadKey(t *testing.T) {
	d := mustLoad(t, "testdata/valid")
	d.Flags["../escape"] = d.Flags["banner-text"]
	if err := Write(t.TempDir(), d); err == nil || !strings.Contains(err.Error(), "../escape") {
		t.Fatalf("got %v", err)
	}
}

func errorStrings(errs []error) []string {
	out := make([]string, len(errs))
	for i, e := range errs {
		out[i] = e.Error()
	}
	return out
}
