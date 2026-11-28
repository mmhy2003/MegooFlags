package importers

import (
	"reflect"
	"strings"
	"testing"
)

func TestNormalizeKeyKeepsValidKeys(t *testing.T) {
	r := &Report{Tool: "Test"}
	used := map[string]bool{}
	for _, key := range []string{"new-checkout.v2_x", "checkout"} {
		if got := NormalizeKey(key, used, r, "flag"); got != key {
			t.Fatalf("NormalizeKey(%q) = %q", key, got)
		}
	}
	if !r.Empty() {
		t.Fatalf("an unchanged key was reported: %v", r.Lines)
	}
	if !used["checkout"] || !used["new-checkout.v2_x"] {
		t.Fatalf("used = %v", used)
	}
}

func TestNormalizeKeyRewrites(t *testing.T) {
	cases := map[string]string{
		"New Checkout":           "new-checkout",
		"--_Beta":                "beta",
		"ÜberFlag":               "berflag",
		strings.Repeat("a", 120): strings.Repeat("a", 100),
		"!!!":                    "flag",
	}
	for raw, want := range cases {
		r := &Report{Tool: "Test"}
		got := NormalizeKey(raw, map[string]bool{}, r, "flag")
		if got != want {
			t.Errorf("NormalizeKey(%q) = %q, want %q", raw, got, want)
			continue
		}
		want := []ReportLine{{Resource: "flag", Key: want, Message: `key "` + raw + `" was renamed to "` + want + `"`}}
		if !reflect.DeepEqual(r.Lines, want) {
			t.Errorf("NormalizeKey(%q) report = %v, want %v", raw, r.Lines, want)
		}
	}
}

func TestNormalizeKeyCollisions(t *testing.T) {
	r := &Report{Tool: "Test"}
	used := map[string]bool{}
	got := []string{
		NormalizeKey("beta", used, r, "segment"),
		NormalizeKey("Beta", used, r, "segment"),
		NormalizeKey("BETA", used, r, "segment"),
		NormalizeKey(strings.Repeat("b", 100), used, r, "segment"),
		NormalizeKey(strings.Repeat("B", 100), used, r, "segment"),
	}
	want := []string{"beta", "beta-2", "beta-3", strings.Repeat("b", 100), strings.Repeat("b", 98) + "-2"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	if len(r.Lines) != 3 {
		t.Fatalf("report = %v, want three lines", r.Lines)
	}
	if r.Lines[0].Message != `key "Beta" was renamed to "beta-2"` {
		t.Fatalf("line = %q", r.Lines[0].Message)
	}
}

func TestParseEnvMap(t *testing.T) {
	got, err := ParseEnvMap([]string{"prod=production", " test = staging "})
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"prod": "production", "test": "staging"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v", got)
	}
	if got, err := ParseEnvMap(nil); err != nil || len(got) != 0 {
		t.Fatalf("nil: %v, %v", got, err)
	}
}

func TestParseEnvMapErrors(t *testing.T) {
	cases := map[string][]string{
		`--env-map "prod": want <source>=<target>`:                                   {"prod"},
		`--env-map "=production": want <source>=<target>`:                            {"=production"},
		`--env-map "prod=": want <source>=<target>`:                                  {"prod="},
		`--env-map "prod=Production": target must match ^[a-z0-9][a-z0-9._-]{0,99}$`: {"prod=Production"},
		`--env-map: "prod" is mapped twice`:                                          {"prod=a", "prod=b"},
		`--env-map: "a" and "b" both map to "production"`:                            {"a=production", "b=production"},
	}
	for want, pairs := range cases {
		_, err := ParseEnvMap(pairs)
		if err == nil || err.Error() != want {
			t.Errorf("ParseEnvMap(%q) err = %v, want %s", pairs, err, want)
		}
	}
}

func TestMarkdownEmpty(t *testing.T) {
	r := Report{Tool: "LaunchDarkly"}
	if !r.Empty() {
		t.Fatal("a new report is not empty")
	}
	want := "# Import report: LaunchDarkly\n\n" +
		"Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently from LaunchDarkly, " +
		"so users can land in a different bucket than before.\n\n" +
		"Everything else was translated.\n"
	if got := r.Markdown(); got != want {
		t.Fatalf("got\n%s\nwant\n%s", got, want)
	}
}

func TestMarkdownSortsLines(t *testing.T) {
	r := Report{Tool: "Unleash"}
	r.Add("segment", "beta", "", "rule %d skipped", 2)
	r.Add("flag", "b", "production", "context kind %s", "org")
	r.Add("flag", "b", "", "a|b")
	r.Add("flag", "a", "staging", "x")
	if r.Empty() {
		t.Fatal("report is empty")
	}
	want := "# Import report: Unleash\n\n" +
		"Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently from Unleash, " +
		"so users can land in a different bucket than before.\n\n" +
		"| Resource | Key | Environment | Message |\n" +
		"|---|---|---|---|\n" +
		"| flag | a | staging | x |\n" +
		"| flag | b |  | a\\|b |\n" +
		"| flag | b | production | context kind org |\n" +
		"| segment | beta |  | rule 2 skipped |\n"
	if got := r.Markdown(); got != want {
		t.Fatalf("got\n%s\nwant\n%s", got, want)
	}
}
