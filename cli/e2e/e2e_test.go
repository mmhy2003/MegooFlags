//go:build e2e

// Package e2e_test runs the megooflags binary against a running MegooFlags
// stack. Set MEGOOFLAGS_E2E_URL (e.g. http://localhost:8080) and
// MEGOOFLAGS_API_TOKEN (an org admin token) to run it.
package e2e_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/open-feature/go-sdk/openfeature"
	"github.com/santhosh-tekuri/jsonschema/v6"

	"github.com/mmhy2003/MegooFlags/cli/client"
	megooflags "github.com/mmhy2003/MegooFlags/sdks/go"
)

var (
	// skipReason is set when the environment does not name a stack.
	skipReason string
	baseURL    string
	apiToken   string
	// binary is the megooflags binary built for the run.
	binary string
)

func TestMain(m *testing.M) {
	baseURL = strings.TrimRight(os.Getenv("MEGOOFLAGS_E2E_URL"), "/")
	apiToken = os.Getenv("MEGOOFLAGS_API_TOKEN")
	if baseURL == "" || apiToken == "" {
		skipReason = "MEGOOFLAGS_E2E_URL and MEGOOFLAGS_API_TOKEN must be set"
		os.Exit(m.Run())
	}
	os.Exit(buildAndRun(m))
}

// buildAndRun builds the binary into a temporary directory, runs the tests
// and removes the directory.
func buildAndRun(m *testing.M) int {
	dir, err := os.MkdirTemp("", "megooflags-e2e-")
	if err != nil {
		fmt.Fprintln(os.Stderr, "temp dir:", err)
		return 1
	}
	defer os.RemoveAll(dir)
	binary = filepath.Join(dir, "megooflags")
	// Built in the cli module, with its own go.mod and go.sum, as released.
	build := exec.Command("go", "build", "-o", binary, "./cmd/megooflags")
	build.Dir = ".."
	build.Stdout, build.Stderr = os.Stdout, os.Stderr
	if err := build.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "go build:", err)
		return 1
	}
	return m.Run()
}

// stack skips the test without a stack and returns an API client for it.
func stack(t *testing.T) *client.Client {
	t.Helper()
	if skipReason != "" {
		t.Skip(skipReason)
	}
	return client.New(baseURL, apiToken)
}

// result is the outcome of one run of the binary.
type result struct {
	stdout, stderr string
	code           int
}

func (r result) String() string {
	return fmt.Sprintf("exit %d\nstdout:\n%s\nstderr:\n%s", r.code, r.stdout, r.stderr)
}

// cli runs the binary with args. Its config file lives in an empty
// temporary directory, so only the environment configures it.
func cli(t *testing.T, args ...string) result {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, binary, args...)
	home := t.TempDir()
	cmd.Env = append(os.Environ(),
		"MEGOOFLAGS_URL="+baseURL,
		"MEGOOFLAGS_API_TOKEN="+apiToken,
		"MEGOOFLAGS_PROJECT=",
		"MEGOOFLAGS_OUTPUT=",
		"HOME="+home,
		"XDG_CONFIG_HOME="+home,
	)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	r := result{stdout: stdout.String(), stderr: stderr.String()}
	var exitErr *exec.ExitError
	switch {
	case err == nil:
	case errors.As(err, &exitErr):
		r.code = exitErr.ExitCode()
	default:
		t.Fatalf("megooflags %s: %v", strings.Join(args, " "), err)
	}
	t.Logf("megooflags %s: exit %d", strings.Join(args, " "), r.code)
	return r
}

// mustCLI runs the binary and fails unless it exits with want.
func mustCLI(t *testing.T, want int, args ...string) result {
	t.Helper()
	r := cli(t, args...)
	if r.code != want {
		t.Fatalf("megooflags %s: want exit %d, got %s", strings.Join(args, " "), want, r)
	}
	return r
}

// cliJSON runs the binary with -o json, expects exit 0 and decodes stdout.
func cliJSON(t *testing.T, out any, args ...string) {
	t.Helper()
	r := mustCLI(t, 0, append(args, "-o", "json")...)
	if err := json.Unmarshal([]byte(r.stdout), out); err != nil {
		t.Fatalf("megooflags %s: stdout is not JSON: %v\n%s", strings.Join(args, " "), err, r.stdout)
	}
}

// newProject returns a fresh project key and deletes the project, if it was
// created, when the test ends.
func newProject(t *testing.T, c *client.Client) string {
	t.Helper()
	b := make([]byte, 4)
	if _, err := rand.Read(b); err != nil {
		t.Fatalf("random: %v", err)
	}
	key := "clie2e-" + hex.EncodeToString(b)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if err := c.DeleteProject(ctx, key); err != nil && !client.IsCode(err, "not_found") {
			t.Logf("delete project %s: %v", key, err)
		}
	})
	return key
}

// createProject creates a project with development, staging and production
// through the CLI.
func createProject(t *testing.T, c *client.Client) string {
	t.Helper()
	p := newProject(t, c)
	mustCLI(t, 0, "project", "create", p, "--name", "CLI e2e "+p,
		"--env", "development:Development", "--env", "staging:Staging", "--env", "production:Production")
	return p
}

// writeFile writes content to name in a temporary directory and returns its
// path.
func writeFile(t *testing.T, name, content string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
	return path
}

// stagingConfig serves "true" to u1 and "false" to everyone else.
const stagingConfig = `enabled: true
targets:
  - variation: "true"
    values: [u1]
fallthrough:
  variation: "false"
`

// createTargetedFlag creates boolean flag f with stagingConfig in staging.
func createTargetedFlag(t *testing.T, p, f string) {
	t.Helper()
	mustCLI(t, 0, "flag", "create", f, "-p", p, "--name", "Checkout", "--type", "boolean")
	mustCLI(t, 0, "flag", "config", "set", f, "-p", p, "--env", "staging", "-f", writeFile(t, "config.yaml", stagingConfig))
}

func getConfig(t *testing.T, p, f, env string) client.FlagConfig {
	t.Helper()
	var cfg client.FlagConfig
	cliJSON(t, &cfg, "flag", "config", "get", f, "-p", p, "--env", env)
	return cfg
}

func TestFlagLifecycle(t *testing.T) {
	c := stack(t)
	p := createProject(t, c)

	var proj client.Project
	cliJSON(t, &proj, "project", "get", p)
	var envs []string
	for _, e := range proj.Environments {
		envs = append(envs, e.Key)
	}
	if want := []string{"development", "staging", "production"}; !reflect.DeepEqual(envs, want) {
		t.Fatalf("environments = %v, want %v", envs, want)
	}

	var prod client.Environment
	cliJSON(t, &prod, "env", "update", "production", "-p", p, "--approvals-required", "1")
	if prod.ApprovalsRequired != 1 {
		t.Fatalf("production approvals_required = %d, want 1", prod.ApprovalsRequired)
	}

	var flag client.Flag
	cliJSON(t, &flag, "flag", "create", "theme", "-p", p, "--name", "Theme", "--type", "string",
		"--variation", `blue="blue"`, "--variation", `green="green"`, "--off-variation", "blue")
	if flag.Type != "string" || len(flag.Variations) != 2 {
		t.Fatalf("created flag = %+v", flag)
	}

	var v client.Variation
	cliJSON(t, &v, "flag", "variation", "set", "theme", "dark", "-p", p, "--value", `"dark"`)
	if v.Key != "dark" || v.Value != "dark" {
		t.Fatalf("new variation = %+v", v)
	}
	cliJSON(t, &v, "flag", "variation", "set", "theme", "dark", "-p", p, "--value", `"black"`)
	if v.Value != "black" {
		t.Fatalf("updated variation value = %v, want black", v.Value)
	}

	cfgFile := writeFile(t, "theme.yaml", `enabled: false
targets:
  - variation: dark
    values: [u1, u2]
fallthrough:
  variation: green
`)
	mustCLI(t, 0, "flag", "config", "set", "theme", "-p", p, "--env", "staging", "-f", cfgFile)
	cfg := getConfig(t, p, "theme", "staging")
	if cfg.Enabled || len(cfg.Targets) != 1 || cfg.Targets[0].Variation != "dark" ||
		cfg.Fallthrough.Variation == nil || *cfg.Fallthrough.Variation != "green" {
		t.Fatalf("staging config = %+v", cfg)
	}

	var state client.FlagEnvironmentState
	cliJSON(t, &state, "flag", "on", "theme", "-p", p, "--env", "staging")
	if !state.Enabled {
		t.Fatalf("after on: %+v", state)
	}
	cliJSON(t, &state, "flag", "off", "theme", "-p", p, "--env", "staging")
	if state.Enabled {
		t.Fatalf("after off: %+v", state)
	}
	mustCLI(t, 0, "flag", "on", "theme", "-p", p, "--env", "staging")
	cliJSON(t, &state, "flag", "kill", "theme", "-p", p, "--env", "staging")
	if state.Enabled {
		t.Fatalf("after kill: %+v", state)
	}

	// Delete refuses a live flag and a missing --yes.
	mustCLI(t, 2, "flag", "delete", "theme", "-p", p)
	cliJSON(t, &flag, "flag", "archive", "theme", "-p", p)
	if !flag.Archived {
		t.Fatalf("after archive: archived = false")
	}
	cliJSON(t, &flag, "flag", "restore", "theme", "-p", p)
	if flag.Archived {
		t.Fatalf("after restore: archived = true")
	}
	mustCLI(t, 0, "flag", "archive", "theme", "-p", p)
	mustCLI(t, 0, "flag", "delete", "theme", "-p", p, "--yes")
	if r := cli(t, "flag", "get", "theme", "-p", p); r.code != 1 {
		t.Fatalf("get after delete: want exit 1, got %s", r)
	}
}

func TestPromote(t *testing.T) {
	c := stack(t)
	p := createProject(t, c)
	mustCLI(t, 0, "env", "update", "production", "-p", p, "--approvals-required", "1")
	createTargetedFlag(t, p, "checkout")

	r := mustCLI(t, 0, "promote", "checkout", "-p", p, "--from", "staging", "--to", "development")
	if !strings.Contains(r.stdout, "checkout -> development: copied") {
		t.Fatalf("promote output: %s", r)
	}
	src, dst := getConfig(t, p, "checkout", "staging"), getConfig(t, p, "checkout", "development")
	if !reflect.DeepEqual(src.Targets, dst.Targets) || !reflect.DeepEqual(src.Rules, dst.Rules) ||
		!reflect.DeepEqual(src.Fallthrough, dst.Fallthrough) {
		t.Fatalf("development config %+v does not match staging %+v", dst, src)
	}

	r = mustCLI(t, 4, "promote", "checkout", "-p", p, "--from", "staging", "--to", "production")
	if !strings.Contains(r.stdout, "checkout -> production: needs approval") {
		t.Fatalf("promote output: %s", r)
	}

	r = mustCLI(t, 0, "promote", "checkout", "-p", p, "--from", "staging", "--to", "production", "--change-requests")
	if !strings.Contains(r.stdout, "checkout -> production: opened change request") {
		t.Fatalf("promote output: %s", r)
	}
	var crs struct {
		Items []client.ChangeRequest `json:"items"`
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := c.Do(ctx, http.MethodGet, "/api/projects/"+p+"/change-requests", url.Values{"flag": {"checkout"}}, nil, &crs); err != nil {
		t.Fatalf("list change requests: %v", err)
	}
	if len(crs.Items) != 1 || crs.Items[0].Environment != "production" || crs.Items[0].Kind != "config" {
		t.Fatalf("change requests = %+v, want one config request in production", crs.Items)
	}
}

func TestEvaluateAndRuleset(t *testing.T) {
	c := stack(t)
	p := createProject(t, c)
	createTargetedFlag(t, p, "checkout")

	var out client.EvaluateOut
	cliJSON(t, &out, "evaluate", "checkout", "-p", p, "--env", "staging", "-c", "key=u1")
	if len(out.Results) != 1 || out.Results[0].Variant == nil || *out.Results[0].Variant != "true" ||
		out.Results[0].Value != true {
		t.Fatalf("evaluate u1 = %+v, want variant true", out.Results)
	}

	file := filepath.Join(t.TempDir(), "ruleset.json")
	r := mustCLI(t, 0, "ruleset", "pull", "-p", p, "--env", "staging", "-o", file)
	if !strings.Contains(r.stdout, "(1 flags)") {
		t.Fatalf("ruleset pull output: %s", r)
	}
	schema, err := jsonschema.NewCompiler().Compile("../../spec/ruleset.schema.json")
	if err != nil {
		t.Fatalf("compile schema: %v", err)
	}
	raw, err := os.ReadFile(file)
	if err != nil {
		t.Fatalf("read ruleset: %v", err)
	}
	doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(raw))
	if err != nil {
		t.Fatalf("ruleset is not JSON: %v", err)
	}
	if err := schema.Validate(doc); err != nil {
		t.Fatalf("ruleset does not match the schema: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	key, err := c.CreateSdkKey(ctx, p, "staging", client.SdkKeyCreate{Name: "cli-e2e", Kind: "server"})
	if err != nil {
		t.Fatalf("create SDK key: %v", err)
	}
	provider := megooflags.NewProvider(megooflags.Config{SDKKey: key.Key, BootstrapFile: file, Offline: true})
	defer provider.Shutdown()
	if err := openfeature.SetNamedProviderAndWait(p, provider); err != nil {
		t.Fatalf("SetNamedProviderAndWait: %v", err)
	}
	of := openfeature.NewClient(p)
	for user, want := range map[string]bool{"u1": true, "u2": false} {
		got, err := of.BooleanValue(ctx, "checkout", !want, openfeature.NewEvaluationContext(user, nil))
		if err != nil || got != want {
			t.Fatalf("provider: checkout for %s = %v (%v), want %v", user, got, err, want)
		}
	}
}

func TestFlagsAsCode(t *testing.T) {
	c := stack(t)
	a := createProject(t, c)
	createTargetedFlag(t, a, "checkout")
	mustCLI(t, 0, "segment", "create", "beta", "-p", a, "--name", "Beta")
	dir := t.TempDir()
	mustCLI(t, 0, "export", "-p", a, "--dir", dir)

	b := newProject(t, c)
	path := filepath.Join(dir, "megooflags.yaml")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read megooflags.yaml: %v", err)
	}
	rewritten := strings.Replace(string(raw), "  key: "+a+"\n", "  key: "+b+"\n", 1)
	if rewritten == string(raw) {
		t.Fatalf("project key %s not found in megooflags.yaml:\n%s", a, raw)
	}
	if err := os.WriteFile(path, []byte(rewritten), 0o644); err != nil {
		t.Fatalf("write megooflags.yaml: %v", err)
	}

	mustCLI(t, 0, "apply", "-p", b, "--dir", dir)
	r := mustCLI(t, 0, "diff", "-p", b, "--dir", dir)
	if !strings.Contains(r.stdout, "No differences.") {
		t.Fatalf("diff after apply: %s", r)
	}
	if got := getConfig(t, b, "checkout", "staging"); !got.Enabled || len(got.Targets) != 1 {
		t.Fatalf("checkout in %s staging = %+v", b, got)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if _, err := c.Toggle(ctx, b, "checkout", "staging", false, nil); err != nil {
		t.Fatalf("toggle: %v", err)
	}
	r = mustCLI(t, 3, "diff", "-p", b, "--dir", dir)
	if !strings.Contains(r.stdout, "checkout") {
		t.Fatalf("diff after toggle does not name the flag: %s", r)
	}
}

func TestImporters(t *testing.T) {
	cases := []struct {
		tool, flag, env, user string
		want                  any
	}{
		// u1 is an individual target of "true" in production.
		{"unleash", "new-checkout", "production", "u1", true},
		// production serves the default-prod variation to everyone.
		{"flagsmith", "max_items", "production", "u1", float64(25)},
		// u1 is an individual target of the On variation, now "true".
		{"launchdarkly", "newcheckout", "production", "u1", true},
	}
	c := stack(t)
	for _, tc := range cases {
		t.Run(tc.tool, func(t *testing.T) {
			p := newProject(t, c)
			dir := t.TempDir()
			mustCLI(t, 0, "import", tc.tool, "--file", "../internal/importers/"+tc.tool+"/testdata/export.json",
				"--project", p, "--out", dir)
			mustCLI(t, 0, "apply", "-p", p, "--dir", dir)
			// The server normalises targets and rollouts; that is no difference.
			if r := mustCLI(t, 0, "diff", "-p", p, "--dir", dir); !strings.Contains(r.stdout, "No differences.") {
				t.Fatalf("diff after apply: %s", r)
			}

			var out client.EvaluateOut
			cliJSON(t, &out, "evaluate", tc.flag, "-p", p, "--env", tc.env, "-c", "key="+tc.user)
			if len(out.Results) != 1 || out.Results[0].ErrorCode != nil || out.Results[0].Value != tc.want {
				t.Fatalf("evaluate %s for %s in %s = %+v, want value %v", tc.flag, tc.user, tc.env, out.Results, tc.want)
			}
		})
	}
}
