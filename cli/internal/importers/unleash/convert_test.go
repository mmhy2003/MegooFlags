package unleash

import (
	"context"
	"encoding/json"
	"flag"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers/importertest"
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
	importertest.CompareDirs(t, out, golden)

	loaded, err := flagfiles.Load(out)
	if err != nil {
		t.Fatal(err)
	}
	if errs := flagfiles.Validate(loaded, nil); len(errs) > 0 {
		t.Fatalf("the import does not validate: %v", errs)
	}
}

// convertOne converts feature f with strategies in one environment,
// enabled, with envVariants as the environment's variants.
func convertOne(t *testing.T, strategies []Strategy, envVariants []Variant) (flagfiles.FlagEnv, []importers.ReportLine) {
	t.Helper()
	e := &Export{
		Environments: []string{"production"},
		PerEnv: map[string]BatchExport{"production": {
			Features:            []Feature{{Name: "f", Type: "release"}},
			FeatureStrategies:   strategies,
			FeatureEnvironments: []FeatureEnvironment{{FeatureName: "f", Enabled: true, Variants: envVariants}},
		}},
	}
	d, report := Convert(e, importers.Options{Project: "shop"})
	if errs := flagfiles.Validate(d, nil); len(errs) > 0 {
		t.Fatalf("the import does not validate: %v", errs)
	}
	return d.Flags["f"].Environments["production"], report.Lines
}

func hasLine(lines []importers.ReportLine, part string) bool {
	return slices.ContainsFunc(lines, func(l importers.ReportLine) bool { return strings.Contains(l.Message, part) })
}

func TestConvertEnabledWithoutStrategiesServesOn(t *testing.T) {
	env, _ := convertOne(t, nil, nil)
	if env.Fallthrough.Variation == nil || *env.Fallthrough.Variation != "true" {
		t.Fatalf("fallthrough = %+v", env.Fallthrough)
	}

	variants := []Variant{{Name: "a", Weight: 600}, {Name: "b", Weight: 400}}
	env, _ = convertOne(t, nil, variants)
	want := &client.Rollout{BucketBy: "key", Weights: []client.Weight{{Variation: "a", Weight: 60000}, {Variation: "b", Weight: 40000}}}
	if !reflect.DeepEqual(env.Fallthrough.Rollout, want) {
		t.Fatalf("fallthrough = %+v", env.Fallthrough)
	}

	// Strategies that are all skipped leave the fallthrough off.
	env, _ = convertOne(t, []Strategy{{Name: "default", FeatureName: "f", Disabled: true}}, nil)
	if env.Fallthrough.Variation == nil || *env.Fallthrough.Variation != "false" {
		t.Fatalf("fallthrough = %+v", env.Fallthrough)
	}
}

func TestConvertReportsPartialRolloutRules(t *testing.T) {
	plan := []Constraint{{ContextName: "plan", Operator: "IN", Values: []string{"pro"}}}
	_, lines := convertOne(t, []Strategy{
		{Name: "flexibleRollout", FeatureName: "f", Constraints: plan, Parameters: map[string]any{"rollout": "25"}},
		{Name: "flexibleRollout", FeatureName: "f", Constraints: plan, Parameters: map[string]any{"rollout": "100"}, SortOrder: 1},
	}, nil)
	if !hasLine(lines, "strategy 1 (flexibleRollout): contexts that match its constraints but fall outside its 25% get false instead of trying later strategies") {
		t.Fatalf("report = %#v", lines)
	}
	if hasLine(lines, "strategy 2") {
		t.Fatalf("report = %#v", lines)
	}
}

func TestConvertReportsCurrentTime(t *testing.T) {
	_, lines := convertOne(t, []Strategy{{Name: "default", FeatureName: "f", Constraints: []Constraint{
		{ContextName: "currentTime", Operator: "DATE_AFTER", Value: "2026-01-01T00:00:00.000Z"},
		{ContextName: "currentTime", Operator: "DATE_BEFORE", Value: "2027-01-01T00:00:00.000Z"},
	}}}, nil)
	n := 0
	for _, l := range lines {
		if strings.Contains(l.Message, "currentTime is set by Unleash SDKs; MegooFlags SDKs do not set it, so the app must send it") {
			n++
		}
	}
	if n != 1 {
		t.Fatalf("report = %#v", lines)
	}
}

func TestConvertReportsReplacedMultivariateFallthrough(t *testing.T) {
	env, lines := convertOne(t, []Strategy{
		{Name: "flexibleRollout", FeatureName: "f", Parameters: map[string]any{"rollout": "50"}, Variants: []Variant{{Name: "a", Weight: 1000}}},
		{Name: "default", FeatureName: "f", SortOrder: 1, Variants: []Variant{{Name: "b", Weight: 1000}}},
	}, nil)
	if env.Fallthrough.Rollout == nil || env.Fallthrough.Rollout.Weights[0].Variation != "b" {
		t.Fatalf("fallthrough = %+v", env.Fallthrough)
	}
	if !hasLine(lines, "strategy 2 (default) replaces strategy 1's rollout as the fallthrough; the variants that rollout served are dropped") {
		t.Fatalf("report = %#v", lines)
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
