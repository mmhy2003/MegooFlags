package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"

	"github.com/mmhy2003/MegooFlags/cli/internal/config"
)

// recorded is one request the fake API received.
type recorded struct {
	Method, Path, Query string
	Body                any
}

// reply is the fake API's answer to "METHOD /path".
type reply struct {
	status int
	body   string
}

// fakeAPI answers the routes it is given, 404 not_found for the rest, and
// records every request.
type fakeAPI struct {
	t        *testing.T
	srv      *httptest.Server
	routes   map[string]reply
	requests []recorded
}

func newFakeAPI(t *testing.T, routes map[string]reply) *fakeAPI {
	t.Helper()
	f := &fakeAPI{t: t, routes: routes}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		req := recorded{Method: r.Method, Path: r.URL.EscapedPath(), Query: r.URL.RawQuery}
		if len(raw) > 0 {
			if err := json.Unmarshal(raw, &req.Body); err != nil {
				t.Errorf("%s %s: body is not JSON: %s", r.Method, r.URL.Path, raw)
			}
		}
		f.requests = append(f.requests, req)
		if r.Header.Get("Authorization") != "Bearer "+testToken {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		rep, ok := f.routes[r.Method+" "+req.Path]
		if !ok {
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"code":"not_found","message":"Not found"}`))
			return
		}
		if rep.status == 0 {
			rep.status = http.StatusOK
		}
		w.WriteHeader(rep.status)
		_, _ = w.Write([]byte(rep.body))
	}))
	t.Cleanup(f.srv.Close)
	return f
}

// session returns a CLI session logged in to the fake API with project.
func (f *fakeAPI) session(project string) *session {
	f.t.Helper()
	s := newSession(f.t)
	if err := config.Save(s.configPath(), config.File{URL: f.srv.URL, Token: testToken, Project: project}); err != nil {
		f.t.Fatal(err)
	}
	return s
}

// only asserts that exactly want was requested, in order.
func (f *fakeAPI) only(want ...recorded) {
	f.t.Helper()
	if len(f.requests) != len(want) {
		f.t.Fatalf("requests = %+v, want %+v", f.requests, want)
	}
	for i := range want {
		got, w := f.requests[i], want[i]
		gb, _ := json.Marshal(got.Body)
		wb, _ := json.Marshal(w.Body)
		if got.Method != w.Method || got.Path != w.Path || got.Query != w.Query || string(gb) != string(wb) {
			f.t.Fatalf("request %d = %s %s?%s %s, want %s %s?%s %s", i,
				got.Method, got.Path, got.Query, gb, w.Method, w.Path, w.Query, wb)
		}
	}
}

func (f *fakeAPI) reset() { f.requests = nil }

// ok runs args and fails the test unless the exit code is 0.
func ok(t *testing.T, s *session, args ...string) string {
	t.Helper()
	code, out, errOut := s.run(args...)
	if code != ExitOK {
		t.Fatalf("%v: code = %d, stderr = %s", args, code, errOut)
	}
	return out
}

// tableRows splits table output into rows of fields.
func tableRows(out string) [][]string {
	var rows [][]string
	for _, line := range strings.Split(strings.TrimRight(out, "\n"), "\n") {
		rows = append(rows, strings.Fields(line))
	}
	return rows
}

func wantRows(t *testing.T, out string, want ...string) {
	t.Helper()
	rows := tableRows(out)
	if len(rows) != len(want) {
		t.Fatalf("out = %q, want %d rows", out, len(want))
	}
	for i, w := range want {
		if strings.Join(rows[i], " ") != w {
			t.Fatalf("row %d = %q, want %q\nout = %s", i, strings.Join(rows[i], " "), w, out)
		}
	}
}

func decodeJSON(t *testing.T, out string) any {
	t.Helper()
	var v any
	if err := json.Unmarshal([]byte(out), &v); err != nil {
		t.Fatalf("%v: %s", err, out)
	}
	return v
}

func decodeYAML(t *testing.T, out string) any {
	t.Helper()
	var v any
	if err := yaml.Unmarshal([]byte(out), &v); err != nil {
		t.Fatalf("%v: %s", err, out)
	}
	// Normalise through JSON so YAML and JSON results compare equal.
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return decodeJSON(t, string(raw))
}

func sameJSON(t *testing.T, got any, want string) {
	t.Helper()
	gb, _ := json.Marshal(got)
	wb, _ := json.Marshal(decodeJSON(t, want))
	if string(gb) != string(wb) {
		t.Fatalf("got %s\nwant %s", gb, wb)
	}
}

const (
	projectShop    = `{"key":"shop","name":"Shop","description":"Web shop","stale_after_days":30,"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z"}`
	projectBlog    = `{"key":"blog","name":"Blog","description":"","stale_after_days":30,"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}`
	envDev         = `{"key":"development","name":"Development","description":"","sort_order":0,"approvals_required":0,"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}`
	envProd        = `{"key":"production","name":"Production","description":"Live","sort_order":2,"approvals_required":1,"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}`
	projectShopEnv = `{"key":"shop","name":"Shop","description":"Web shop","stale_after_days":30,"environments":[` + envDev + `,` + envProd + `],"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z"}`
	segmentBeta    = `{"key":"beta","name":"Beta testers","description":"","created_at":"2026-01-01T00:00:00Z","updated_at":"2026-03-04T05:06:07Z"}`
	segmentBetaCfg = `{"key":"beta","name":"Beta testers","description":"","included":["u1","u2"],"excluded":["u3"],"rules":[{"description":"staff","clauses":[{"attribute":"email","operator":"ends_with","values":["@example.com"],"negate":false}]}],"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-03-04T05:06:07Z"}`
)

func TestProjectList(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"GET /api/projects":                   {body: `{"items":[` + projectShop + `,` + projectBlog + `],"next_cursor":null}`},
		"GET /api/projects/shop/environments": {body: `[` + envDev + `,` + envProd + `]`},
		"GET /api/projects/blog/environments": {body: `[` + envDev + `]`},
	})
	s := api.session("")

	wantRows(t, ok(t, s, "project", "list"), "KEY NAME ENVIRONMENTS", "shop Shop 2", "blog Blog 1")
	api.only(
		recorded{Method: "GET", Path: "/api/projects", Query: "limit=500"},
		recorded{Method: "GET", Path: "/api/projects/shop/environments"},
		recorded{Method: "GET", Path: "/api/projects/blog/environments"},
	)

	api.reset()
	want := `[` + projectShop + `,` + projectBlog + `]`
	sameJSON(t, decodeJSON(t, ok(t, s, "project", "list", "-o", "json")), want)
	sameJSON(t, decodeYAML(t, ok(t, s, "project", "list", "-o", "yaml")), want)
	api.only(
		recorded{Method: "GET", Path: "/api/projects", Query: "limit=500"},
		recorded{Method: "GET", Path: "/api/projects", Query: "limit=500"},
	)
}

func TestProjectGet(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"GET /api/projects/shop": {body: projectShopEnv}})
	s := api.session("")

	wantRows(t, ok(t, s, "project", "get", "shop"), "KEY NAME ENVIRONMENTS", "shop Shop 2")
	sameJSON(t, decodeJSON(t, ok(t, s, "project", "get", "shop", "-o", "json")), projectShopEnv)
	sameJSON(t, decodeYAML(t, ok(t, s, "project", "get", "shop", "-o", "yaml")), projectShopEnv)
	get := recorded{Method: "GET", Path: "/api/projects/shop"}
	api.only(get, get, get)
}

func TestProjectCreate(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"POST /api/projects": {status: 201, body: projectShopEnv}})
	s := api.session("")

	out := ok(t, s, "project", "create", "shop", "--name", "Shop", "--description", "Web shop",
		"--env", "development:Development", "--env", "production:Production: live")
	wantRows(t, out, "KEY NAME ENVIRONMENTS", "shop Shop 2")
	api.only(recorded{Method: "POST", Path: "/api/projects", Body: decodeJSON(t,
		`{"key":"shop","name":"Shop","description":"Web shop","environments":[`+
			`{"key":"development","name":"Development"},{"key":"production","name":"Production: live"}]}`)})

	api.reset()
	sameJSON(t, decodeJSON(t, ok(t, s, "project", "create", "shop", "--name", "Shop", "-o", "json")), projectShopEnv)
	api.only(recorded{Method: "POST", Path: "/api/projects", Body: decodeJSON(t, `{"key":"shop","name":"Shop"}`)})
}

func TestProjectCreateUsageErrors(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("")
	for _, args := range [][]string{
		{"project", "create", "shop"},
		{"project", "create", "shop", "--name", "Shop", "--env", "production"},
		{"project", "create", "shop", "--name", "Shop", "--env", ":Production"},
		{"project", "create", "shop", "--name", "Shop", "--env", "production:"},
	} {
		if code, _, _ := s.run(args...); code != ExitUsage {
			t.Fatalf("%v: code = %d, want %d", args, code, ExitUsage)
		}
	}
	api.only()
}

func TestProjectUpdate(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"PATCH /api/projects/shop": {body: projectShopEnv}})
	s := api.session("")

	out := ok(t, s, "project", "update", "shop", "--name", "Shop", "--description", "", "--stale-after-days", "45", "-o", "yaml")
	sameJSON(t, decodeYAML(t, out), projectShopEnv)
	api.only(recorded{Method: "PATCH", Path: "/api/projects/shop", Body: decodeJSON(t, `{"name":"Shop","description":"","stale_after_days":45}`)})

	api.reset()
	ok(t, s, "project", "update", "shop", "--stale-after-days", "7")
	api.only(recorded{Method: "PATCH", Path: "/api/projects/shop", Body: decodeJSON(t, `{"stale_after_days":7}`)})

	api.reset()
	if code, _, errOut := s.run("project", "update", "shop"); code != ExitUsage || !strings.Contains(errOut, "nothing to update") {
		t.Fatalf("no changes: code = %d, stderr = %q", code, errOut)
	}
	api.only()
}

func TestProjectDelete(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"DELETE /api/projects/shop": {status: 204}})
	s := api.session("")

	code, out, errOut := s.run("project", "delete", "shop")
	if code != ExitUsage || !strings.Contains(errOut, "refusing to delete without --yes") || out != "" {
		t.Fatalf("without --yes: code = %d, out = %q, stderr = %q", code, out, errOut)
	}
	api.only()

	if out := ok(t, s, "project", "delete", "shop", "--yes"); out != "Deleted project shop\n" {
		t.Fatalf("out = %q", out)
	}
	api.only(recorded{Method: "DELETE", Path: "/api/projects/shop"})
}

func TestProjectNotFound(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("")
	code, out, errOut := s.run("project", "get", "nope")
	if code != ExitError || out != "" || !strings.HasPrefix(errOut, "error: not_found:") {
		t.Fatalf("code = %d, out = %q, stderr = %q", code, out, errOut)
	}
}

func TestEnvList(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"GET /api/projects/shop/environments": {body: `[` + envDev + `,` + envProd + `]`},
	})
	s := api.session("shop")

	wantRows(t, ok(t, s, "env", "list"), "KEY NAME APPROVALS", "development Development 0", "production Production 1")
	want := `[` + envDev + `,` + envProd + `]`
	sameJSON(t, decodeJSON(t, ok(t, s, "env", "list", "-o", "json")), want)
	sameJSON(t, decodeYAML(t, ok(t, s, "env", "list", "-o", "yaml")), want)
	list := recorded{Method: "GET", Path: "/api/projects/shop/environments"}
	api.only(list, list, list)
}

func TestEnvProjectFromFlagAndEnvironment(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"GET /api/projects/blog/environments/production": {body: envProd},
		"GET /api/projects/team/environments/production": {body: envProd},
	})
	s := api.session("shop")

	wantRows(t, ok(t, s, "env", "get", "production", "-p", "blog"), "KEY NAME APPROVALS", "production Production 1")
	s.env["MEGOOFLAGS_PROJECT"] = "team"
	sameJSON(t, decodeJSON(t, ok(t, s, "env", "get", "production", "-o", "json")), envProd)
	sameJSON(t, decodeYAML(t, ok(t, s, "env", "get", "production", "-o", "yaml")), envProd)
	api.only(
		recorded{Method: "GET", Path: "/api/projects/blog/environments/production"},
		recorded{Method: "GET", Path: "/api/projects/team/environments/production"},
		recorded{Method: "GET", Path: "/api/projects/team/environments/production"},
	)
}

func TestEnvCreate(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"POST /api/projects/shop/environments": {status: 201, body: envProd}})
	s := api.session("shop")

	out := ok(t, s, "env", "create", "production", "--name", "Production", "--description", "Live", "--sort-order", "2")
	wantRows(t, out, "KEY NAME APPROVALS", "production Production 1")
	api.only(recorded{Method: "POST", Path: "/api/projects/shop/environments", Body: decodeJSON(t,
		`{"key":"production","name":"Production","description":"Live","sort_order":2}`)})

	api.reset()
	ok(t, s, "env", "create", "qa", "--name", "QA", "--sort-order", "0")
	api.only(recorded{Method: "POST", Path: "/api/projects/shop/environments", Body: decodeJSON(t,
		`{"key":"qa","name":"QA","sort_order":0}`)})

	api.reset()
	if code, _, _ := s.run("env", "create", "qa"); code != ExitUsage {
		t.Fatalf("without --name: code = %d", code)
	}
	api.only()
}

func TestEnvUpdate(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"PATCH /api/projects/shop/environments/production": {body: envProd}})
	s := api.session("shop")

	out := ok(t, s, "env", "update", "production", "--approvals-required", "1", "--sort-order", "2", "-o", "json")
	sameJSON(t, decodeJSON(t, out), envProd)
	api.only(recorded{Method: "PATCH", Path: "/api/projects/shop/environments/production", Body: decodeJSON(t,
		`{"sort_order":2,"approvals_required":1}`)})

	api.reset()
	ok(t, s, "env", "update", "production", "--name", "Prod", "--description", "Live")
	api.only(recorded{Method: "PATCH", Path: "/api/projects/shop/environments/production", Body: decodeJSON(t,
		`{"name":"Prod","description":"Live"}`)})

	api.reset()
	if code, _, errOut := s.run("env", "update", "production"); code != ExitUsage || !strings.Contains(errOut, "nothing to update") {
		t.Fatalf("no changes: code = %d, stderr = %q", code, errOut)
	}
	api.only()
}

func TestEnvDelete(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"DELETE /api/projects/shop/environments/qa": {status: 204}})
	s := api.session("shop")

	if code, _, errOut := s.run("env", "delete", "qa"); code != ExitUsage || !strings.Contains(errOut, "refusing to delete without --yes") {
		t.Fatalf("without --yes: code = %d, stderr = %q", code, errOut)
	}
	api.only()
	if out := ok(t, s, "env", "delete", "qa", "--yes"); out != "Deleted environment qa\n" {
		t.Fatalf("out = %q", out)
	}
	api.only(recorded{Method: "DELETE", Path: "/api/projects/shop/environments/qa"})
}

func TestProjectScopedCommandsNeedProject(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("")
	for _, args := range [][]string{
		{"env", "list"},
		{"env", "get", "production"},
		{"env", "create", "qa", "--name", "QA"},
		{"env", "update", "qa", "--name", "QA"},
		{"env", "delete", "qa", "--yes"},
		{"segment", "list"},
		{"segment", "get", "beta"},
		{"segment", "create", "beta", "--name", "Beta"},
		{"segment", "update", "beta", "--name", "Beta"},
		{"segment", "delete", "beta", "--yes"},
	} {
		code, _, errOut := s.run(args...)
		if code != ExitUsage || !strings.Contains(errOut, "no project: pass -p or set MEGOOFLAGS_PROJECT") {
			t.Fatalf("%v: code = %d, stderr = %q", args, code, errOut)
		}
	}
	api.only()
}

func TestEnvNotFound(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("shop")
	code, _, errOut := s.run("env", "get", "nope")
	if code != ExitError || !strings.HasPrefix(errOut, "error: not_found:") {
		t.Fatalf("code = %d, stderr = %q", code, errOut)
	}
}

func TestSegmentList(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"GET /api/projects/shop/segments": {body: `{"items":[` + segmentBeta + `],"next_cursor":null}`},
	})
	s := api.session("shop")

	wantRows(t, ok(t, s, "segment", "list"), "KEY NAME UPDATED", "beta Beta testers 2026-03-04T05:06:07Z")
	want := `[` + segmentBeta + `]`
	sameJSON(t, decodeJSON(t, ok(t, s, "segment", "list", "-o", "json")), want)
	sameJSON(t, decodeYAML(t, ok(t, s, "segment", "list", "-o", "yaml")), want)
	list := recorded{Method: "GET", Path: "/api/projects/shop/segments", Query: "limit=500"}
	api.only(list, list, list)
}

func TestSegmentGetShowsConfig(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"GET /api/projects/shop/segments/beta": {body: segmentBetaCfg}})
	s := api.session("shop")

	wantRows(t, ok(t, s, "segment", "get", "beta"),
		"KEY NAME UPDATED",
		"beta Beta testers 2026-03-04T05:06:07Z",
		"",
		"INCLUDED u1, u2",
		"EXCLUDED u3",
		"RULES 1",
		"staff: email ends_with [\"@example.com\"]",
	)
	sameJSON(t, decodeJSON(t, ok(t, s, "segment", "get", "beta", "-o", "json")), segmentBetaCfg)
	sameJSON(t, decodeYAML(t, ok(t, s, "segment", "get", "beta", "-o", "yaml")), segmentBetaCfg)
	get := recorded{Method: "GET", Path: "/api/projects/shop/segments/beta"}
	api.only(get, get, get)
}

func TestSegmentGetEmptyConfig(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"GET /api/projects/shop/segments/beta": {body: segmentBeta}})
	s := api.session("shop")
	wantRows(t, ok(t, s, "segment", "get", "beta"),
		"KEY NAME UPDATED",
		"beta Beta testers 2026-03-04T05:06:07Z",
		"",
		"INCLUDED -",
		"EXCLUDED -",
		"RULES 0",
	)
}

func TestSegmentCreate(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"POST /api/projects/shop/segments": {status: 201, body: segmentBeta}})
	s := api.session("shop")

	out := ok(t, s, "segment", "create", "beta", "--name", "Beta testers", "--description", "Early", "-o", "json")
	sameJSON(t, decodeJSON(t, out), segmentBeta)
	api.only(recorded{Method: "POST", Path: "/api/projects/shop/segments", Body: decodeJSON(t,
		`{"key":"beta","name":"Beta testers","description":"Early"}`)})

	if code, _, _ := s.run("segment", "create", "beta"); code != ExitUsage {
		t.Fatalf("without --name: code = %d", code)
	}
}

func writeFile(t *testing.T, name, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

const segmentConfigBody = `{"included":["u1","u2"],"excluded":["u3"],"rules":[{"description":"staff","clauses":[{"attribute":"email","operator":"ends_with","values":["@example.com"],"negate":false}]}]}`

func TestSegmentUpdateDetailsThenConfig(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"PATCH /api/projects/shop/segments/beta": {body: segmentBeta},
		"PUT /api/projects/shop/segments/beta":   {body: segmentBetaCfg},
	})
	s := api.session("shop")
	path := writeFile(t, "beta.yaml", `included: [u1, u2]
excluded: [u3]
rules:
  - description: staff
    clauses:
      - attribute: email
        operator: ends_with
        values: ["@example.com"]
`)

	out := ok(t, s, "segment", "update", "beta", "--name", "Beta testers", "-f", path, "-o", "json")
	sameJSON(t, decodeJSON(t, out), segmentBetaCfg)
	api.only(
		recorded{Method: "PATCH", Path: "/api/projects/shop/segments/beta", Body: decodeJSON(t, `{"name":"Beta testers"}`)},
		recorded{Method: "PUT", Path: "/api/projects/shop/segments/beta", Body: decodeJSON(t, segmentConfigBody)},
	)
}

func TestSegmentUpdateConfigFromJSON(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"PUT /api/projects/shop/segments/beta": {body: segmentBetaCfg}})
	s := api.session("shop")
	path := writeFile(t, "beta.json", segmentConfigBody)

	wantRows(t, ok(t, s, "segment", "update", "beta", "-f", path),
		"KEY NAME UPDATED",
		"beta Beta testers 2026-03-04T05:06:07Z",
		"",
		"INCLUDED u1, u2",
		"EXCLUDED u3",
		"RULES 1",
		"staff: email ends_with [\"@example.com\"]",
	)
	api.only(recorded{Method: "PUT", Path: "/api/projects/shop/segments/beta", Body: decodeJSON(t, segmentConfigBody)})
}

func TestSegmentUpdateDetailsOnly(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"PATCH /api/projects/shop/segments/beta": {body: segmentBeta}})
	s := api.session("shop")
	sameJSON(t, decodeYAML(t, ok(t, s, "segment", "update", "beta", "--description", "Early", "-o", "yaml")), segmentBeta)
	api.only(recorded{Method: "PATCH", Path: "/api/projects/shop/segments/beta", Body: decodeJSON(t, `{"description":"Early"}`)})
}

func TestSegmentUpdateUsageErrors(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("shop")
	bad := writeFile(t, "bad.yaml", "included: [u1]\nincludes: [u2]\n")
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"segment", "update", "beta"}, "nothing to update"},
		{[]string{"segment", "update", "beta", "-f", filepath.Join(t.TempDir(), "missing.yaml")}, "missing.yaml"},
		{[]string{"segment", "update", "beta", "-f", bad}, "includes"},
	} {
		code, _, errOut := s.run(tc.args...)
		if code != ExitUsage || !strings.Contains(errOut, tc.want) {
			t.Fatalf("%v: code = %d, stderr = %q", tc.args, code, errOut)
		}
	}
	api.only()
}

func TestSegmentDelete(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"DELETE /api/projects/shop/segments/beta": {status: 204}})
	s := api.session("shop")

	if code, _, errOut := s.run("segment", "delete", "beta"); code != ExitUsage || !strings.Contains(errOut, "refusing to delete without --yes") {
		t.Fatalf("without --yes: code = %d, stderr = %q", code, errOut)
	}
	api.only()
	if out := ok(t, s, "segment", "delete", "beta", "--yes"); out != "Deleted segment beta\n" {
		t.Fatalf("out = %q", out)
	}
	api.only(recorded{Method: "DELETE", Path: "/api/projects/shop/segments/beta"})
}

func TestSegmentNotFound(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("shop")
	code, _, errOut := s.run("segment", "delete", "nope", "--yes")
	if code != ExitError || !strings.HasPrefix(errOut, "error: not_found:") {
		t.Fatalf("code = %d, stderr = %q", code, errOut)
	}
}

func TestPathSegmentsAreEscaped(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"GET /api/projects/a%2Fb/segments/c%20d": {body: segmentBeta}})
	s := api.session("a/b")
	ok(t, s, "segment", "get", "c d", "-o", "json")
	api.only(recorded{Method: "GET", Path: "/api/projects/a%2Fb/segments/c%20d"})
}
