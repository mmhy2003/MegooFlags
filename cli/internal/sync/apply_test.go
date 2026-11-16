package sync

import (
	"bytes"
	"context"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
)

// golden loads testdata/export-golden. With approvals false, production
// requires no approvals, so every config can be written directly.
func golden(t *testing.T, approvals bool) *flagfiles.Dir {
	t.Helper()
	d, err := flagfiles.Load(filepath.Join("testdata", "export-golden"))
	if err != nil {
		t.Fatal(err)
	}
	if !approvals {
		for i := range d.Project.Environments {
			d.Project.Environments[i].ApprovalsRequired = 0
		}
	}
	return d
}

// applyFiles brings the store in line with files and returns the result.
func applyFiles(t *testing.T, c *client.Client, files *flagfiles.Dir, opts Options, aopts ApplyOptions) Result {
	t.Helper()
	server, err := SnapshotOrEmpty(context.Background(), c, "shop", opts.Envs)
	if err != nil {
		t.Fatal(err)
	}
	return Apply(context.Background(), c, "shop", Diff(files, server, opts), aopts)
}

// noDiff fails unless the server now matches files.
func noDiff(t *testing.T, c *client.Client, files *flagfiles.Dir, opts Options) {
	t.Helper()
	server, err := Snapshot(context.Background(), c, "shop", opts.Envs)
	if err != nil {
		t.Fatal(err)
	}
	if p := Diff(files, server, opts); len(p.Changes) != 0 {
		t.Fatalf("still differs:\n%s", strings.Join(printed(p), "\n"))
	}
}

func noFailures(t *testing.T, r Result) {
	t.Helper()
	for _, f := range r.Failures {
		t.Errorf("failed: %s: %v", f.Change, f.Err)
	}
	if t.Failed() {
		t.FailNow()
	}
}

// seeded returns a store that already holds the golden files, with no
// approvals anywhere, and has forgotten the requests that put them there.
func seeded(t *testing.T) (*store, *client.Client) {
	t.Helper()
	s, c := newStore(t, "shop")
	noFailures(t, applyFiles(t, c, golden(t, false), Options{}, ApplyOptions{}))
	s.requests, s.writes = nil, 0
	return s, c
}

func TestApplyCreatesMissingProjectThenNoDiff(t *testing.T) {
	s, c := newStore(t, "shop")
	files := golden(t, false)

	r := applyFiles(t, c, files, Options{}, ApplyOptions{})
	noFailures(t, r)
	if len(r.NeedsApproval) != 0 || len(r.ChangeRequests) != 0 {
		t.Fatalf("needs approval %v, change requests %v", r.NeedsApproval, r.ChangeRequests)
	}
	if w := s.writesTo(); len(w) == 0 || w[0] != "POST /api/projects" {
		t.Fatalf("writes = %v, want the project created first", w)
	}
	if n := len(s.project.Environments); n != 2 {
		t.Fatalf("environments = %+v", s.project.Environments)
	}
	if !slices.ContainsFunc(r.Applied, func(c Change) bool { return c.Kind == KindFlag && c.Op == OpArchive && c.Key == "old-banner" }) {
		t.Fatalf("applied = %v", r.Applied)
	}
	noDiff(t, c, files, Options{})
}

func TestApplyCreatesProjectWithApprovals(t *testing.T) {
	s, c := newStore(t, "shop")
	files := golden(t, true)
	files.Flags = nil

	noFailures(t, applyFiles(t, c, files, Options{}, ApplyOptions{}))
	if got := s.project.Environments[1]; got.Key != "production" || got.ApprovalsRequired != 1 {
		t.Fatalf("production = %+v", got)
	}
	noDiff(t, c, files, Options{})
}

func TestApplyUpdatesThenNoDiff(t *testing.T) {
	_, c := seeded(t)
	files := golden(t, false)
	files.Project.Project.Name = "Shop 2"
	files.Project.Environments[0].Description = "Laptops"
	files.Project.Environments = append(files.Project.Environments,
		flagfiles.Environment{Key: "staging", Name: "Staging", ApprovalsRequired: 2})
	beta := files.Segments["beta"]
	beta.Name, beta.Included = "Beta 2", []string{"u7"}
	files.Segments["beta"] = beta
	files.Segments["vip"] = flagfiles.Segment{Key: "vip", Name: "VIP", Included: []string{"u5"}}
	editFlag(files, "banner-text", func(f *flagfiles.Flag) {
		f.Description = "Top of the page"
		f.Permanent = false
		f.Variations = []client.Variation{
			{Key: "hello", Name: "Hi there", Value: "Hello!"},
			{Key: "welcome", Name: "Welcome", Value: "Welcome!"},
			{Key: "hey", Name: "Hey", Value: "Hey!"},
		}
	})
	editEnv(files, "new-checkout", "development", func(e *flagfiles.FlagEnv) { e.Enabled = false })
	editFlag(files, "old-banner", func(f *flagfiles.Flag) {
		f.Archived = false
		f.Variations = f.Variations[1:] // drop "true"
	})

	noFailures(t, applyFiles(t, c, files, Options{}, ApplyOptions{}))
	noDiff(t, c, files, Options{})
}

func TestApplyCreatesPrerequisiteBeforeDependentConfig(t *testing.T) {
	s, c := newStore(t, "shop")
	files := golden(t, false)
	files.Segments = nil
	dependent := files.Flags["old-banner"]
	dependent.Archived = false
	dependent.Environments = map[string]flagfiles.FlagEnv{"development": {
		Enabled:       true,
		Fallthrough:   client.Serve{Variation: ptr("true")},
		Prerequisites: []client.Prerequisite{{Flag: "z-base", Variation: "true"}},
	}}
	base := files.Flags["old-banner"]
	base.Archived, base.Environments = false, nil
	files.Flags = map[string]flagfiles.Flag{"a-dependent": dependent, "z-base": base}

	noFailures(t, applyFiles(t, c, files, Options{}, ApplyOptions{}))
	w := s.writesTo()
	lastCreate := -1
	for i, req := range w {
		if req == "POST /api/projects/shop/flags" {
			lastCreate = i
		}
	}
	put := slices.Index(w, "PUT /api/projects/shop/flags/a-dependent/environments/development")
	if lastCreate < 0 || put < lastCreate {
		t.Fatalf("writes = %v", w)
	}
	noDiff(t, c, files, Options{})
}

func TestApplyRetriesVersionConflictOnce(t *testing.T) {
	s, c := seeded(t)
	files := golden(t, false)
	editEnv(files, "new-checkout", "development", func(e *flagfiles.FlagEnv) { e.Enabled = false })
	s.conflicts["new-checkout/development"] = 1

	r := applyFiles(t, c, files, Options{}, ApplyOptions{})
	noFailures(t, r)
	if len(r.Applied) != 1 || s.writes != 2 {
		t.Fatalf("applied %v, writes %v", r.Applied, s.writesTo())
	}
	noDiff(t, c, files, Options{})

	editEnv(files, "new-checkout", "development", func(e *flagfiles.FlagEnv) { e.Enabled = true })
	s.conflicts["new-checkout/development"] = 2
	r = applyFiles(t, c, files, Options{}, ApplyOptions{})
	if len(r.Failures) != 1 || !client.IsCode(r.Failures[0].Err, "version_conflict") || len(r.Applied) != 0 {
		t.Fatalf("result = %+v", r)
	}
}

// A new project whose files require approvals is created with them, so its
// configs there are never written directly: they need approval, or go into
// change requests. Approvals are never bypassed, not even on create.
func TestApplyApprovalRequired(t *testing.T) {
	production := []string{
		"~ config banner-text [production]: enabled, rules, fallthrough",
		"~ config new-checkout [production]: enabled, targets, rules, fallthrough, prerequisites",
		"~ config old-banner [production]: fallthrough",
	}

	s, c := newStore(t, "shop")
	r := applyFiles(t, c, golden(t, true), Options{}, ApplyOptions{})
	noFailures(t, r)
	sameLines(t, lines(r.NeedsApproval), production)
	if len(r.ChangeRequests) != 0 || len(s.changeRequests) != 0 {
		t.Fatalf("change requests = %v", r.ChangeRequests)
	}

	s, c = newStore(t, "shop")
	r = applyFiles(t, c, golden(t, true), Options{}, ApplyOptions{ChangeRequests: true})
	noFailures(t, r)
	if len(r.NeedsApproval) != 0 || !slices.Equal(r.ChangeRequests, []string{"cr-1", "cr-2", "cr-3"}) {
		t.Fatalf("needs approval %v, change requests %v", r.NeedsApproval, r.ChangeRequests)
	}
	cr := s.changeRequests[1]
	if cr.Title != "Apply flags-as-code: new-checkout in production" || cr.Kind != "config" ||
		cr.Flag != "new-checkout" || cr.Environment != "production" {
		t.Fatalf("change request = %+v", cr)
	}
	if cr.Payload["enabled"] != true || cr.Payload["version"] != float64(1) ||
		cr.Payload["fallthrough"].(map[string]any)["variation"] != "false" || len(cr.Payload["prerequisites"].([]any)) != 1 {
		t.Fatalf("payload = %v", cr.Payload)
	}
}

func TestApplyPrune(t *testing.T) {
	s, c := seeded(t)
	if _, err := c.CreateFlag(context.Background(), "shop", client.FlagCreate{
		Key: "legacy", Name: "Legacy", Type: "boolean", OffVariation: ptr("false"),
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := c.CreateSegment(context.Background(), "shop", client.SegmentCreate{Key: "temp", Name: "Temp"}); err != nil {
		t.Fatal(err)
	}
	files := golden(t, false)

	noFailures(t, applyFiles(t, c, files, Options{}, ApplyOptions{}))
	if s.flags["legacy"].flag.Archived || s.segments["temp"] == nil {
		t.Fatal("pruned without --prune")
	}

	r := applyFiles(t, c, files, Options{Prune: true}, ApplyOptions{})
	noFailures(t, r)
	sameLines(t, lines(r.Applied), []string{"- flag legacy: archive", "- segment temp"})
	if !s.flags["legacy"].flag.Archived || s.segments["temp"] != nil {
		t.Fatalf("legacy archived = %v, temp = %v", s.flags["legacy"].flag.Archived, s.segments["temp"])
	}
	noDiff(t, c, files, Options{Prune: true})
}

func TestApplyContinuesPastFailedFlag(t *testing.T) {
	s, c := newStore(t, "shop")
	s.failCreate["old-banner"] = true
	files := golden(t, false)

	r := applyFiles(t, c, files, Options{}, ApplyOptions{})
	var failed []string
	for _, f := range r.Failures {
		failed = append(failed, f.Change.String()+": "+f.Err.Error())
	}
	sameLines(t, failed, []string{
		"+ flag old-banner: validation_error: flag old-banner cannot be created (HTTP 422)",
		"- flag old-banner: archive: skipped: flag old-banner was not created",
		"~ config old-banner [development]: fallthrough: skipped: flag old-banner was not created",
		"~ config old-banner [production]: fallthrough: skipped: flag old-banner was not created",
	})
	for _, w := range s.writesTo() {
		if strings.Contains(w, "/flags/old-banner") {
			t.Fatalf("wrote %s", w)
		}
	}
	delete(files.Flags, "old-banner")
	noDiff(t, c, files, Options{})
}

func TestApplyRefusesTypeChange(t *testing.T) {
	s, c := seeded(t)
	files := golden(t, false)
	editFlag(files, "new-checkout", func(f *flagfiles.Flag) { f.Type = "string" })

	r := applyFiles(t, c, files, Options{}, ApplyOptions{})
	if len(r.Failures) != 1 || r.Failures[0].Err.Error() != "type cannot change; delete and recreate the flag" {
		t.Fatalf("failures = %+v", r.Failures)
	}
	if s.writes != 0 {
		t.Fatalf("writes = %v", s.writesTo())
	}
}

func TestApplyDryRunMakesNoRequests(t *testing.T) {
	s, c := seeded(t)
	files := golden(t, false)
	files.Project.Project.Name = "Shop 2"
	editEnv(files, "new-checkout", "development", func(e *flagfiles.FlagEnv) { e.Enabled = false })
	server, err := Snapshot(context.Background(), c, "shop", nil)
	if err != nil {
		t.Fatal(err)
	}
	plan := Diff(files, server, Options{})
	s.requests = nil

	var out bytes.Buffer
	r := Apply(context.Background(), c, "shop", plan, ApplyOptions{DryRun: true, Out: &out})
	if len(s.requests) != 0 {
		t.Fatalf("requests = %v", s.requests)
	}
	if out.String() != "~ project shop: name\n~ config new-checkout [development]: enabled\n" {
		t.Fatalf("out = %q", out.String())
	}
	if len(r.Applied)+len(r.NeedsApproval)+len(r.ChangeRequests)+len(r.Failures) != 0 {
		t.Fatalf("result = %+v", r)
	}
}

func lines(cs []Change) []string {
	var out []string
	for _, c := range cs {
		out = append(out, c.String())
	}
	return out
}

func TestApplyNewVariationBecomesOffVariation(t *testing.T) {
	s, c := seeded(t)
	files := golden(t, false)
	editFlag(files, "banner-text", func(f *flagfiles.Flag) {
		f.Name = "Banner"
		f.Variations = append(slices.Clone(f.Variations), client.Variation{Key: "none", Name: "None", Value: ""})
		f.OffVariation = "none"
	})

	noFailures(t, applyFiles(t, c, files, Options{}, ApplyOptions{}))
	w := s.writesTo()
	create := slices.Index(w, "POST /api/projects/shop/flags/banner-text/variations")
	last := slices.Index(w, "PATCH /api/projects/shop/flags/banner-text")
	for i, req := range w {
		if req == "PATCH /api/projects/shop/flags/banner-text" {
			last = i
		}
	}
	if create < 0 || last < create {
		t.Fatalf("writes = %v, want off_variation set after the variation exists", w)
	}
	noDiff(t, c, files, Options{})
}

func TestApplyRenamedOffVariation(t *testing.T) {
	_, c := seeded(t)
	files := golden(t, false)
	editFlag(files, "old-banner", func(f *flagfiles.Flag) {
		f.Variations = []client.Variation{{Key: "true", Value: true}, {Key: "off", Value: false}}
		f.OffVariation = "off"
		envs := map[string]flagfiles.FlagEnv{}
		for k, e := range f.Environments {
			e.Fallthrough = client.Serve{Variation: ptr("off")}
			envs[k] = e
		}
		f.Environments = envs
	})

	noFailures(t, applyFiles(t, c, files, Options{}, ApplyOptions{}))
	noDiff(t, c, files, Options{})
}

func TestApplyOffVariationFailureIsReported(t *testing.T) {
	s, c := seeded(t)
	files := golden(t, false)
	editFlag(files, "banner-text", func(f *flagfiles.Flag) {
		f.Variations = append(slices.Clone(f.Variations), client.Variation{Key: "none", Value: ""})
		f.OffVariation = "none"
	})
	s.failVariation["banner-text/none"] = true

	r := applyFiles(t, c, files, Options{}, ApplyOptions{})
	var failed []string
	for _, f := range r.Failures {
		failed = append(failed, f.Change.String())
	}
	sameLines(t, failed, []string{"+ variation banner-text/none", "~ flag banner-text: off_variation"})
	if len(r.Applied) != 0 {
		t.Fatalf("applied = %v", lines(r.Applied))
	}
}
