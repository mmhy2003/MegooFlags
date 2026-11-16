// Package flagsmith imports a Flagsmith project: Fetch reads it from the
// Flagsmith Admin API or a saved file, and Convert turns it into a
// flags-as-code directory and a report.
package flagsmith

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

// DefaultURL is Flagsmith's SaaS API.
const DefaultURL = "https://api.flagsmith.com"

// Export is a Flagsmith project: the environment document of each of its
// environments, keyed by environment name. The JSON names are Flagsmith's,
// so a saved file can be assembled from the API's responses.
type Export struct {
	Environments map[string]Document `json:"environments"`
}

// Document is the part of an environment document that is imported.
type Document struct {
	APIKey            string             `json:"api_key"`
	Name              string             `json:"name"`
	FeatureStates     []FeatureState     `json:"feature_states"`
	IdentityOverrides []IdentityOverride `json:"identity_overrides,omitempty"`
	Project           Project            `json:"project"`
}

// Project is the environment's project with its segments.
type Project struct {
	Name     string    `json:"name,omitempty"`
	Segments []Segment `json:"segments"`
}

// Segment matches the identities that match all of its rules.
// FeatureStates are its overrides in the document's environment.
type Segment struct {
	ID            int            `json:"id"`
	Name          string         `json:"name"`
	Rules         []Rule         `json:"rules"`
	FeatureStates []FeatureState `json:"feature_states,omitempty"`
}

// Rule matches when its conditions match as Type says (ALL, ANY or NONE)
// and all of its nested rules match.
type Rule struct {
	Type       string      `json:"type"`
	Rules      []Rule      `json:"rules,omitempty"`
	Conditions []Condition `json:"conditions,omitempty"`
}

// Condition compares the trait Property with Value.
type Condition struct {
	Operator string `json:"operator"`
	Property string `json:"property_"`
	Value    string `json:"value,omitempty"`
}

// FeatureState is a feature's state in an environment, a segment override
// (with FeatureSegment) or an identity override.
type FeatureState struct {
	Feature            Feature             `json:"feature"`
	Enabled            bool                `json:"enabled"`
	Value              any                 `json:"feature_state_value,omitempty"`
	FeatureSegment     *FeatureSegment     `json:"feature_segment,omitempty"`
	MultivariateValues []MultivariateValue `json:"multivariate_feature_state_values,omitempty"`
}

// Feature is a feature; its name is its key.
type Feature struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
	Type string `json:"type,omitempty"`
}

// FeatureSegment orders a segment's override among the feature's others:
// the lowest priority wins.
type FeatureSegment struct {
	Priority int `json:"priority"`
}

// MultivariateValue gives an option a percentage of the identities.
type MultivariateValue struct {
	PercentageAllocation float64            `json:"percentage_allocation"`
	Option               MultivariateOption `json:"multivariate_feature_option"`
}

// MultivariateOption is one of a multivariate feature's values.
type MultivariateOption struct {
	ID    int `json:"id,omitempty"`
	Value any `json:"value"`
}

// IdentityOverride is what an identity overrides.
type IdentityOverride struct {
	Identifier       string         `json:"identifier"`
	IdentityFeatures []FeatureState `json:"identity_features"`
}

// Fetch reads src.File when it is set, and otherwise the environments of
// project src.Project (an ID) from the Flagsmith API at src.URL, then the
// document of each.
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
		return nil, errors.New("the Flagsmith project ID is required")
	}
	base := strings.TrimRight(src.URL, "/")
	if base == "" {
		base = DefaultURL
	}
	f := fetcher{ctx: ctx, base: base, src: src}

	type environment struct {
		Name   string `json:"name"`
		APIKey string `json:"api_key"`
	}
	var envs []environment
	path := "/api/v1/environments/?project=" + url.QueryEscape(src.Project)
	for path != "" {
		var raw json.RawMessage
		if err := f.get(path, &raw); err != nil {
			return nil, err
		}
		// The list is a plain array, or a page with the next page's URL.
		var page struct {
			Results []environment `json:"results"`
			Next    *string       `json:"next"`
		}
		if err := json.Unmarshal(raw, &page.Results); err != nil {
			if err := json.Unmarshal(raw, &page); err != nil {
				return nil, fmt.Errorf("GET %s: %w", path, err)
			}
		}
		envs = append(envs, page.Results...)
		path = ""
		if page.Next != nil {
			// Only the path and query are kept, so the token never goes
			// to another host or over another scheme.
			next, err := url.Parse(*page.Next)
			if err != nil {
				return nil, fmt.Errorf("next page %q: %w", *page.Next, err)
			}
			path = next.RequestURI()
		}
	}

	e := &Export{Environments: map[string]Document{}}
	for _, env := range envs {
		var doc Document
		if err := f.get("/api/v1/environments/"+url.PathEscape(env.APIKey)+"/document/", &doc); err != nil {
			return nil, err
		}
		e.Environments[env.Name] = doc
	}
	return e, nil
}

type fetcher struct {
	ctx  context.Context
	base string
	src  importers.Source
}

// get decodes the response to path, relative to the base URL, into out.
func (f fetcher) get(path string, out any) error {
	req, err := http.NewRequestWithContext(f.ctx, http.MethodGet, f.base+path, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Api-Key "+f.src.Token)
	req.Header.Set("Accept", "application/json")
	resp, err := f.src.Client().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s: HTTP %d: %s", path, resp.StatusCode, strings.TrimSpace(string(b)))
	}
	if err := json.Unmarshal(b, out); err != nil {
		return fmt.Errorf("GET %s: %w", path, err)
	}
	return nil
}
