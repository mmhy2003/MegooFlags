package flagsmith

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
// ./internal/importers/flagsmith -update rewrites it.
func TestConvertGolden(t *testing.T) {
	e, err := Fetch(context.Background(), importers.Source{File: filepath.Join("testdata", "export.json")})
	if err != nil {
		t.Fatal(err)
	}
	d, report := Convert(e, importers.Options{Project: "shop", EnvMap: map[string]string{"Production": "prod"}})
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

func cond(property, operator, value string) Condition {
	return Condition{Property: property, Operator: operator, Value: value}
}

func clause(attribute, operator string, negate bool, values ...any) client.Clause {
	return client.Clause{Attribute: attribute, Operator: operator, Values: values, Negate: negate}
}

func TestSegmentRulesExpandsToOrOfAnd(t *testing.T) {
	a, b, c := cond("plan", "EQUAL", "pro"), cond("plan", "EQUAL", "team"), cond("identifier", "CONTAINS", "@example.com")
	got, notes, err := segmentRules([]Rule{{Type: "ALL", Conditions: []Condition{c}, Rules: []Rule{{Type: "ANY", Conditions: []Condition{a, b}}}}})
	if err != nil || len(notes) > 0 {
		t.Fatalf("err = %v, notes = %v", err, notes)
	}
	want := []client.SegmentRule{
		{Clauses: []client.Clause{clause("key", "contains", false, "@example.com"), clause("plan", "in", false, "pro")}},
		{Clauses: []client.Clause{clause("key", "contains", false, "@example.com"), clause("plan", "in", false, "team")}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got  %+v\nwant %+v", got, want)
	}
}

func TestSegmentRulesNoneNegatesEachCondition(t *testing.T) {
	got, _, err := segmentRules([]Rule{{Type: "ALL", Rules: []Rule{{Type: "NONE", Conditions: []Condition{
		cond("country", "NOT_EQUAL", "US"), cond("age", "GREATER_THAN", "17"), cond("tags", "IN", "a, b"),
	}}}}})
	if err != nil {
		t.Fatal(err)
	}
	want := []client.SegmentRule{{Clauses: []client.Clause{
		clause("country", "in", false, "US"), clause("age", "gt", true, int64(17)), clause("tags", "in", true, "a", "b"),
	}}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got  %+v\nwant %+v", got, want)
	}
}

func TestSegmentRulesTooDeep(t *testing.T) {
	leaf := Rule{Type: "ALL", Conditions: []Condition{cond("plan", "EQUAL", "pro")}}
	deep := []Rule{{Type: "ALL", Rules: []Rule{{Type: "ANY", Rules: []Rule{leaf}}}}}
	if _, _, err := segmentRules(deep); err == nil || err.Error() != "rules nested too deep" {
		t.Fatalf("err = %v", err)
	}

	// 3 × 3 × 3 = 27 rules is more than 20.
	any3 := Rule{Type: "ANY", Conditions: []Condition{cond("a", "EQUAL", "1"), cond("a", "EQUAL", "2"), cond("a", "EQUAL", "3")}}
	wide := []Rule{{Type: "ALL", Rules: []Rule{any3, any3, any3}}}
	if _, _, err := segmentRules(wide); err == nil || err.Error() != "rules nested too deep" {
		t.Fatalf("err = %v", err)
	}
}

func TestSegmentRulesSkipsRulesWithUnsupportedConditions(t *testing.T) {
	got, notes, err := segmentRules([]Rule{{Type: "ANY", Conditions: []Condition{
		cond("plan", "IS_SET", ""), cond("country", "EQUAL", "se"), cond("age", "GREATER_THAN", "old"),
	}}})
	if err != nil {
		t.Fatal(err)
	}
	want := []client.SegmentRule{{Clauses: []client.Clause{clause("country", "in", false, "se")}}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v", got)
	}
	wantNotes := []string{
		"the condition on plan uses IS_SET, which is not supported: the rules that use it are skipped",
		`the condition on age compares with "old", which is not a number: the rules that use it are skipped`,
	}
	if !reflect.DeepEqual(notes, wantNotes) {
		t.Fatalf("notes = %q", notes)
	}
}

func TestFetchSendsApiKeyAndReadsEachDocument(t *testing.T) {
	for name, pages := range map[string][]string{
		"array": {`[{"id": 1, "name": "Development", "api_key": "dev-key"}, {"id": 2, "name": "Production", "api_key": "prod-key"}]`},
		"paginated": {
			`{"count": 2, "next": "http://elsewhere.example/api/v1/environments/?project=7&page=2", "results": [{"id": 1, "name": "Development", "api_key": "dev-key"}]}`,
			`{"count": 2, "next": null, "results": [{"id": 2, "name": "Production", "api_key": "prod-key"}]}`,
		},
	} {
		t.Run(name, func(t *testing.T) {
			var requests []string
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("Authorization") != "Api-Key secret" {
					t.Errorf("%s: headers %v", r.URL, r.Header)
				}
				requests = append(requests, r.URL.RequestURI())
				w.Header().Set("Content-Type", "application/json")
				switch r.URL.Path {
				case "/api/v1/environments/":
					if r.URL.Query().Get("project") != "7" {
						t.Errorf("query = %v", r.URL.Query())
					}
					page := 0
					if r.URL.Query().Get("page") == "2" {
						page = 1
					}
					_, _ = io.WriteString(w, pages[page])
				case "/api/v1/environments/dev-key/document/":
					_, _ = io.WriteString(w, `{"api_key": "dev-key", "name": "Development", "feature_states": [], "project": {"id": 7, "name": "Web", "segments": []}}`)
				case "/api/v1/environments/prod-key/document/":
					_, _ = io.WriteString(w, `{"api_key": "prod-key", "name": "Production", "feature_states": [{"feature": {"id": 3, "name": "a", "type": "STANDARD"}, "enabled": true, "feature_state_value": null, "feature_segment": null, "multivariate_feature_state_values": []}], "identity_overrides": [], "project": {"id": 7, "name": "Web", "segments": []}}`)
				default:
					http.Error(w, `{"detail": "Not found."}`, http.StatusNotFound)
				}
			}))
			defer srv.Close()

			got, err := Fetch(context.Background(), importers.Source{URL: srv.URL + "/", Token: "secret", Project: "7", HTTP: srv.Client()})
			if err != nil {
				t.Fatal(err)
			}
			want := &Export{Environments: map[string]Document{
				"Development": {APIKey: "dev-key", Name: "Development", FeatureStates: []FeatureState{}, Project: Project{Name: "Web", Segments: []Segment{}}},
				"Production": {APIKey: "prod-key", Name: "Production", FeatureStates: []FeatureState{
					{Feature: Feature{ID: 3, Name: "a", Type: "STANDARD"}, Enabled: true, MultivariateValues: []MultivariateValue{}},
				}, IdentityOverrides: []IdentityOverride{}, Project: Project{Name: "Web", Segments: []Segment{}}},
			}}
			if !reflect.DeepEqual(got, want) {
				g, _ := json.Marshal(got)
				w, _ := json.Marshal(want)
				t.Fatalf("got  %s\nwant %s", g, w)
			}
			wantRequests := []string{"/api/v1/environments/?project=7"}
			if len(pages) > 1 {
				wantRequests = append(wantRequests, "/api/v1/environments/?project=7&page=2")
			}
			wantRequests = append(wantRequests, "/api/v1/environments/dev-key/document/", "/api/v1/environments/prod-key/document/")
			if !reflect.DeepEqual(requests, wantRequests) {
				t.Fatalf("requests %v", requests)
			}
		})
	}
}

func TestFetchError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"detail":"Invalid API key."}`, http.StatusUnauthorized)
	}))
	defer srv.Close()
	_, err := Fetch(context.Background(), importers.Source{URL: srv.URL, Token: "bad", Project: "7"})
	want := `GET /api/v1/environments/?project=7: HTTP 401: {"detail":"Invalid API key."}`
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

func TestConvertMergesOptionsWithTheSameValue(t *testing.T) {
	theme := Feature{ID: 1, Name: "theme"}
	e := &Export{Environments: map[string]Document{"prod": {FeatureStates: []FeatureState{{
		Feature: theme, Enabled: true, Value: "blue", MultivariateValues: []MultivariateValue{
			{PercentageAllocation: 20, Option: MultivariateOption{ID: 1, Value: "green"}},
			{PercentageAllocation: 30, Option: MultivariateOption{ID: 2, Value: "green"}},
		},
	}}}}}
	d, _ := Convert(e, importers.Options{Project: "shop"})
	if errs := flagfiles.Validate(d, nil); len(errs) > 0 {
		t.Fatalf("the import does not validate: %v", errs)
	}
	want := &client.Rollout{BucketBy: "key", Weights: []client.Weight{{Variation: "option-1", Weight: 50000}, {Variation: "default", Weight: 50000}}}
	if got := d.Flags["theme"].Environments["prod"].Fallthrough.Rollout; !reflect.DeepEqual(got, want) {
		t.Fatalf("rollout = %+v", got)
	}
	if n := len(d.Flags["theme"].Variations); n != 2 {
		t.Fatalf("variations = %+v", d.Flags["theme"].Variations)
	}
}

func TestConvertReportsIdentityOverridesOfUnknownFeatures(t *testing.T) {
	e := &Export{Environments: map[string]Document{"prod": {IdentityOverrides: []IdentityOverride{{
		Identifier: "alice", IdentityFeatures: []FeatureState{{Feature: Feature{ID: 9, Name: "Gone Feature"}, Enabled: true}},
	}}}}}
	_, report := Convert(e, importers.Options{Project: "shop"})
	want := []importers.ReportLine{{Resource: "identity", Key: "alice", Env: "prod", Message: "identity overrides are not imported: Gone Feature"}}
	if !reflect.DeepEqual(report.Lines, want) {
		t.Fatalf("report = %#v", report.Lines)
	}
}

// overriddenWhileOff is one environment where flag is off by default and a
// segment override turns it on for vip.
func overriddenWhileOff(value, override any) *Export {
	f := Feature{ID: 1, Name: "flag"}
	return &Export{Environments: map[string]Document{"prod": {
		FeatureStates: []FeatureState{{Feature: f, Enabled: false, Value: value}},
		Project: Project{Segments: []Segment{
			{ID: 1, Name: "vip", FeatureStates: []FeatureState{{Feature: f, Enabled: true, Value: override, FeatureSegment: &FeatureSegment{}}}},
			{ID: 2, Name: "blocked", FeatureStates: []FeatureState{{Feature: f, Enabled: false, Value: override, FeatureSegment: &FeatureSegment{Priority: 1}}}},
		}},
	}}}
}

// A segment override that turns on a flag that is off in the environment
// keeps working: the flag is on, and the fallthrough serves the off value.
func TestConvertOverrideEnablesAFlagThatIsOff(t *testing.T) {
	for _, tc := range []struct {
		name            string
		value, override any
		rules           []string
		serve           string
	}{
		{name: "boolean", rules: []string{"true", "false"}, serve: "false"},
		{name: "typed", value: 10.0, override: 50.0, rules: []string{"segment-vip", "default"}, serve: "default"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d, report := Convert(overriddenWhileOff(tc.value, tc.override), importers.Options{Project: "shop"})
			if errs := flagfiles.Validate(d, nil); len(errs) > 0 {
				t.Fatalf("the import does not validate: %v", errs)
			}
			f := d.Flags["flag"]
			e := f.Environments["prod"]
			if !e.Enabled {
				t.Fatal("the flag is off, so the vip override never applies")
			}
			if e.Fallthrough.Variation == nil || *e.Fallthrough.Variation != tc.serve || f.OffVariation != tc.serve {
				t.Fatalf("fallthrough = %+v, off = %s, want %s", e.Fallthrough, f.OffVariation, tc.serve)
			}
			var served []string
			for _, r := range e.Rules {
				served = append(served, *r.Variation)
			}
			if !reflect.DeepEqual(served, tc.rules) {
				t.Fatalf("rules serve %v, want %v", served, tc.rules)
			}
			for _, l := range report.Lines {
				if strings.Contains(l.Message, "disables the flag") {
					t.Fatalf("nothing is lost, but the report says %q", l.Message)
				}
			}
		})
	}
}

// Rounded allocations never add up to more than 100%.
func TestConvertRoundedAllocationsStayWithinTheTotal(t *testing.T) {
	theme := Feature{ID: 1, Name: "theme"}
	e := &Export{Environments: map[string]Document{"prod": {FeatureStates: []FeatureState{{
		Feature: theme, Enabled: true, Value: "blue", MultivariateValues: []MultivariateValue{
			{PercentageAllocation: 50.0005, Option: MultivariateOption{ID: 1, Value: "green"}},
			{PercentageAllocation: 49.9995, Option: MultivariateOption{ID: 2, Value: "red"}},
		},
	}}}}}
	d, _ := Convert(e, importers.Options{Project: "shop"})
	if errs := flagfiles.Validate(d, nil); len(errs) > 0 {
		t.Fatalf("the import does not validate: %v", errs)
	}
}
