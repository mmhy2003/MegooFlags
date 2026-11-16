// Package importers holds what the Unleash, Flagsmith and LaunchDarkly
// importers share: the report of what they could not translate, their
// options and key normalization. Each tool's package has
// Fetch(ctx, Source) (*Export, error), which reads the tool's API or a saved
// file, and Convert(*Export, Options) (*flagfiles.Dir, Report).
package importers

import (
	"cmp"
	"fmt"
	"net/http"
	"regexp"
	"slices"
	"strings"
)

// keyPattern is the API's key pattern.
var keyPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,99}$`)

// maxKeyLen is the longest key the API accepts.
const maxKeyLen = 100

// ReportLine is one thing an importer could not translate as it was.
type ReportLine struct {
	Resource, Key, Env, Message string
}

// Report lists what an import did not translate as it was.
type Report struct {
	Tool  string
	Lines []ReportLine
}

// Add appends a line. env is "" when the line is not about one environment.
func (r *Report) Add(resource, key, env, format string, args ...any) {
	r.Lines = append(r.Lines, ReportLine{Resource: resource, Key: key, Env: env, Message: fmt.Sprintf(format, args...)})
}

// Empty reports whether there are no lines.
func (r Report) Empty() bool {
	return len(r.Lines) == 0
}

// Markdown renders import-report.md: a title, the bucketing notice, which
// is always there, and the lines sorted by resource, key and environment.
func (r Report) Markdown() string {
	var b strings.Builder
	fmt.Fprintf(&b, "# Import report: %s\n\n", r.Tool)
	fmt.Fprintf(&b, "Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently from %s, "+
		"so users can land in a different bucket than before.\n\n", r.Tool)
	if r.Empty() {
		b.WriteString("Everything else was translated.\n")
		return b.String()
	}
	lines := slices.Clone(r.Lines)
	slices.SortStableFunc(lines, func(a, b ReportLine) int {
		return cmp.Or(cmp.Compare(a.Resource, b.Resource), cmp.Compare(a.Key, b.Key), cmp.Compare(a.Env, b.Env))
	})
	b.WriteString("| Resource | Key | Environment | Message |\n|---|---|---|---|\n")
	for _, l := range lines {
		fmt.Fprintf(&b, "| %s | %s | %s | %s |\n", cell(l.Resource), cell(l.Key), cell(l.Env), cell(l.Message))
	}
	return b.String()
}

// cell escapes a table cell.
func cell(s string) string {
	return strings.NewReplacer("|", `\|`, "\n", " ").Replace(s)
}

// Options are what every importer's Convert takes. Project is the
// MegooFlags project key; EnvMap maps the tool's environment keys to
// MegooFlags ones.
type Options struct {
	Project string
	EnvMap  map[string]string
}

// Source is where Fetch reads the export: the tool's API at URL (the tool's
// default when empty) with Token, or the saved JSON File when it is set.
// Project is the tool's project. HTTP defaults to http.DefaultClient.
type Source struct {
	URL, Token, File, Project string
	HTTP                      *http.Client
}

// Client returns s.HTTP or http.DefaultClient.
func (s Source) Client() *http.Client {
	if s.HTTP != nil {
		return s.HTTP
	}
	return http.DefaultClient
}

// ParseEnvMap parses --env-map values such as "prod=production". Targets
// must be valid keys, and neither side may repeat.
func ParseEnvMap(pairs []string) (map[string]string, error) {
	m := map[string]string{}
	sources := map[string]string{}
	for _, pair := range pairs {
		from, to, ok := strings.Cut(pair, "=")
		from, to = strings.TrimSpace(from), strings.TrimSpace(to)
		if !ok || from == "" || to == "" {
			return nil, fmt.Errorf("--env-map %q: want <source>=<target>", pair)
		}
		if !keyPattern.MatchString(to) {
			return nil, fmt.Errorf("--env-map %q: target must match %s", pair, keyPattern)
		}
		if _, dup := m[from]; dup {
			return nil, fmt.Errorf("--env-map: %q is mapped twice", from)
		}
		if other, dup := sources[to]; dup {
			return nil, fmt.Errorf("--env-map: %q and %q both map to %q", other, from, to)
		}
		m[from] = to
		sources[to] = from
	}
	return m, nil
}

// NormalizeKey turns raw into a key the API accepts and that used does not
// hold yet, marks it used and returns it. It lowercases, replaces every
// character outside [a-z0-9._-] with "-", strips leading characters that are
// not letters or digits and keeps at most 100 characters; an empty result
// becomes resource. A taken key gets "-2", "-3" and so on. Every key that
// changes gets a report line.
func NormalizeKey(raw string, used map[string]bool, r *Report, resource string) string {
	base := Slug(raw)
	if base == "" {
		base = resource
	}
	key := Unique(base, used)
	if key != raw {
		r.Add(resource, key, "", "key %q was renamed to %q", raw, key)
	}
	return key
}

// Unique returns base, or base with "-2", "-3" and so on when used holds
// it, kept within 100 characters, and marks the result used.
func Unique(base string, used map[string]bool) string {
	key := base
	for n := 2; used[key]; n++ {
		suffix := fmt.Sprintf("-%d", n)
		key = base[:min(len(base), maxKeyLen-len(suffix))] + suffix
	}
	used[key] = true
	return key
}

// Slug is NormalizeKey without the fallback, the uniqueness and the report:
// it returns "" when nothing of raw is left.
func Slug(raw string) string {
	var b strings.Builder
	for _, c := range strings.ToLower(raw) {
		if c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '.' || c == '_' || c == '-' {
			b.WriteRune(c)
		} else {
			b.WriteByte('-')
		}
	}
	s := strings.TrimLeftFunc(b.String(), func(c rune) bool {
		return !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9')
	})
	return s[:min(len(s), maxKeyLen)]
}
