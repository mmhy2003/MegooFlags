package main

import (
	"os"
	"path/filepath"
	"testing"
)

const (
	rulesetPath = "/api/projects/shop/environments/development/ruleset"
	// Odd spacing and key order: the file must keep the exact bytes.
	rulesetBody = `{"version":3,  "flags":{"b":{"on":true},"a":{"on":false}},"segments":{}}`
)

func TestRulesetPullWritesExactBytes(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"GET " + rulesetPath: {body: rulesetBody}})
	s := api.session("shop")
	path := filepath.Join(t.TempDir(), "ruleset.json")
	if err := os.WriteFile(path, []byte("old content that is longer than the new one ............................"), 0o644); err != nil {
		t.Fatal(err)
	}

	out := ok(t, s, "ruleset", "pull", "--env", "development", "-o", path)
	if out != "wrote "+path+" (2 flags)\n" {
		t.Fatalf("out = %q", out)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != rulesetBody {
		t.Fatalf("file = %q", got)
	}
	entries, _ := os.ReadDir(filepath.Dir(path))
	if len(entries) != 1 {
		t.Fatalf("temporary files left behind: %v", entries)
	}
	api.only(recorded{Method: "GET", Path: rulesetPath})
}

func TestRulesetPullToStdout(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"GET " + rulesetPath: {body: rulesetBody}})
	s := api.session("shop")
	if out := ok(t, s, "ruleset", "pull", "--env", "development", "-o", "-"); out != rulesetBody {
		t.Fatalf("out = %q", out)
	}
}

func TestRulesetPullUsageAndErrors(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("shop")
	if code, _, _ := s.run("ruleset", "pull", "--env", "development"); code != ExitUsage {
		t.Fatalf("missing -o: code = %d", code)
	}
	if code, _, _ := s.run("ruleset", "pull", "-o", "-"); code != ExitUsage {
		t.Fatalf("missing --env: code = %d", code)
	}
	path := filepath.Join(t.TempDir(), "ruleset.json")
	code, _, errOut := s.run("ruleset", "pull", "--env", "development", "-o", path)
	if code != ExitError || errOut != "error: not_found: Not found\n" {
		t.Fatalf("code = %d, stderr = %q", code, errOut)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("file written on error: %v", err)
	}
}
