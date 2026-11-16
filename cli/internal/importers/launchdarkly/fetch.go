// Package launchdarkly imports a LaunchDarkly project: Fetch reads it from
// the LaunchDarkly REST API or a saved file, and Convert turns it into a
// flags-as-code directory and a report.
package launchdarkly

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"

	"github.com/mmhy2003/MegooFlags/cli/internal/importers"
)

// DefaultURL is LaunchDarkly's API.
const DefaultURL = "https://app.launchdarkly.com"

// apiVersion is the LD-API-Version the types below follow.
const apiVersion = "20240415"

// Export is a LaunchDarkly project. Flags and Segments are keyed by
// LaunchDarkly environment key; each flag carries the config of that
// environment only. The JSON names are LaunchDarkly's, so a saved file can
// be assembled from the API's responses.
type Export struct {
	Environments []Environment        `json:"environments"`
	Flags        map[string][]Flag    `json:"flags"`
	Segments     map[string][]Segment `json:"segments"`
}

// Environment is a LaunchDarkly environment.
type Environment struct {
	Key  string `json:"key"`
	Name string `json:"name"`
}

// Flag is a feature flag as GET /api/v2/flags/{project}?env=... returns it.
type Flag struct {
	Key               string                `json:"key"`
	Name              string                `json:"name"`
	Description       string                `json:"description"`
	Kind              string                `json:"kind"`
	Tags              []string              `json:"tags"`
	Temporary         bool                  `json:"temporary"`
	Archived          bool                  `json:"archived"`
	Maintainer        *Maintainer           `json:"_maintainer,omitempty"`
	Purpose           string                `json:"purpose,omitempty"`
	MigrationSettings any                   `json:"migrationSettings,omitempty"`
	Variations        []Variation           `json:"variations"`
	Environments      map[string]FlagConfig `json:"environments"`
}

// Maintainer is the member who maintains a flag.
type Maintainer struct {
	Email string `json:"email"`
}

// Variation is a value a flag can serve; flags refer to it by index.
type Variation struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Value       any    `json:"value"`
}

// FlagConfig is a flag's targeting in one environment.
type FlagConfig struct {
	On             bool           `json:"on"`
	Targets        []Target       `json:"targets"`
	ContextTargets []Target       `json:"contextTargets"`
	Rules          []Rule         `json:"rules"`
	Fallthrough    Serve          `json:"fallthrough"`
	OffVariation   *int           `json:"offVariation"`
	Prerequisites  []Prerequisite `json:"prerequisites"`
}

// Target serves a variation to listed context keys of one kind.
type Target struct {
	Values      []string `json:"values"`
	Variation   int      `json:"variation"`
	ContextKind string   `json:"contextKind,omitempty"`
}

// Rule serves a variation or a rollout when all its clauses match.
type Rule struct {
	Description string   `json:"description"`
	Clauses     []Clause `json:"clauses"`
	Serve
}

// Serve is a variation index or a rollout.
type Serve struct {
	Variation *int     `json:"variation,omitempty"`
	Rollout   *Rollout `json:"rollout,omitempty"`
}

// Rollout splits contexts between variations; weights are in thousandths
// of a percent, as in MegooFlags.
type Rollout struct {
	Variations           []WeightedVariation `json:"variations"`
	BucketBy             string              `json:"bucketBy,omitempty"`
	ContextKind          string              `json:"contextKind,omitempty"`
	ExperimentAllocation any                 `json:"experimentAllocation,omitempty"`
}

// WeightedVariation is a variation's share of a rollout.
type WeightedVariation struct {
	Variation int `json:"variation"`
	Weight    int `json:"weight"`
}

// Clause compares a context attribute with values.
type Clause struct {
	Attribute   string `json:"attribute"`
	Op          string `json:"op"`
	Values      []any  `json:"values"`
	Negate      bool   `json:"negate"`
	ContextKind string `json:"contextKind,omitempty"`
}

// Prerequisite requires flag Key to serve variation index Variation.
type Prerequisite struct {
	Key       string `json:"key"`
	Variation int    `json:"variation"`
}

// Segment is a segment of one environment. Unbounded segments are big
// segments, whose members live outside the segment.
type Segment struct {
	Key              string          `json:"key"`
	Name             string          `json:"name"`
	Description      string          `json:"description"`
	Included         []string        `json:"included,omitempty"`
	Excluded         []string        `json:"excluded,omitempty"`
	IncludedContexts []SegmentTarget `json:"includedContexts,omitempty"`
	ExcludedContexts []SegmentTarget `json:"excludedContexts,omitempty"`
	Rules            []SegmentRule   `json:"rules,omitempty"`
	Unbounded        bool            `json:"unbounded"`
}

// SegmentTarget lists context keys of one kind.
type SegmentTarget struct {
	ContextKind string   `json:"contextKind"`
	Values      []string `json:"values"`
}

// SegmentRule matches when all its clauses match; with Weight it matches
// only that share of the contexts.
type SegmentRule struct {
	Description        string   `json:"description"`
	Clauses            []Clause `json:"clauses"`
	Weight             *int     `json:"weight,omitempty"`
	BucketBy           string   `json:"bucketBy,omitempty"`
	RolloutContextKind string   `json:"rolloutContextKind,omitempty"`
}

// Fetch reads src.File when it is set, and otherwise project src.Project
// from the API: its environments, then each environment's flags and
// segments, following _links.next.
func Fetch(ctx context.Context, src importers.Source) (*Export, error) {
	if src.File != "" {
		b, err := os.ReadFile(src.File)
		if err != nil {
			return nil, err
		}
		var e Export
		if err := json.Unmarshal(b, &e); err != nil {
			return nil, fmt.Errorf("%s: %w", src.File, err)
		}
		return &e, nil
	}
	if src.Project == "" {
		return nil, errors.New("the LaunchDarkly project is required")
	}
	base := strings.TrimRight(src.URL, "/")
	if base == "" {
		base = DefaultURL
	}
	f := fetcher{ctx: ctx, base: base, src: src}
	p := url.PathEscape(src.Project)

	envs, err := list[Environment](f, "/api/v2/projects/"+p+"/environments")
	if err != nil {
		return nil, err
	}
	e := &Export{Environments: envs, Flags: map[string][]Flag{}, Segments: map[string][]Segment{}}
	for _, env := range envs {
		q := url.Values{"env": {env.Key}, "summary": {"0"}, "limit": {"100"}, "offset": {"0"}}
		flags, err := list[Flag](f, "/api/v2/flags/"+p+"?"+q.Encode())
		if err != nil {
			return nil, err
		}
		q = url.Values{"limit": {"50"}, "offset": {"0"}}
		segments, err := list[Segment](f, "/api/v2/segments/"+p+"/"+url.PathEscape(env.Key)+"?"+q.Encode())
		if err != nil {
			return nil, err
		}
		e.Flags[env.Key], e.Segments[env.Key] = flags, segments
	}
	return e, nil
}

type fetcher struct {
	ctx  context.Context
	base string
	src  importers.Source
}

// page is a list response.
type page[T any] struct {
	Items []T `json:"items"`
	Links struct {
		Next *struct {
			Href string `json:"href"`
		} `json:"next"`
	} `json:"_links"`
}

// list GETs path and every page its _links.next leads to.
func list[T any](f fetcher, path string) ([]T, error) {
	items := []T{}
	for path != "" {
		var pg page[T]
		if err := f.get(path, &pg); err != nil {
			return nil, err
		}
		items = append(items, pg.Items...)
		path = ""
		if pg.Links.Next != nil {
			// Only the path and query are kept, so the token never goes
			// to another host.
			next, err := url.Parse(pg.Links.Next.Href)
			if err != nil {
				return nil, fmt.Errorf("next page %q: %w", pg.Links.Next.Href, err)
			}
			path = next.RequestURI()
		}
	}
	return items, nil
}

// get GETs path, relative to the base URL, into out.
func (f fetcher) get(path string, out any) error {
	req, err := http.NewRequestWithContext(f.ctx, http.MethodGet, f.base+path, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", f.src.Token)
	req.Header.Set("LD-API-Version", apiVersion)
	req.Header.Set("Accept", "application/json")
	resp, err := f.src.Client().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	name := path
	if i := strings.IndexByte(name, '?'); i >= 0 {
		name = name[:i]
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s: HTTP %d: %s", name, resp.StatusCode, strings.TrimSpace(string(body)))
	}
	if err := json.Unmarshal(body, out); err != nil {
		return fmt.Errorf("GET %s: %w", name, err)
	}
	return nil
}
