// Package unleash imports an Unleash project: Fetch reads it from the
// Unleash Admin API or a saved file, and Convert turns it into a
// flags-as-code directory and a report.
package unleash

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"

	"github.com/mmhy2003/MegooFlags/cli/internal/importers"
)

// Export is an Unleash project: its enabled environments, the
// features-batch export of each environment, keyed by environment name, and
// the instance's segments. The JSON names are Unleash's, so a saved file can
// be assembled from the API's responses.
type Export struct {
	Environments []string               `json:"environments"`
	PerEnv       map[string]BatchExport `json:"perEnv"`
	Segments     []Segment              `json:"segments"`
}

// BatchExport is the part of POST /api/admin/features-batch/export's
// response that is imported.
type BatchExport struct {
	Features            []Feature            `json:"features"`
	FeatureStrategies   []Strategy           `json:"featureStrategies"`
	FeatureEnvironments []FeatureEnvironment `json:"featureEnvironments"`
	Dependencies        []Dependencies       `json:"dependencies,omitempty"`
}

// Feature is a feature toggle; its name is its key.
type Feature struct {
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	Type        string `json:"type"`
	Archived    bool   `json:"archived,omitempty"`
}

// Strategy is one of a feature's activation strategies in the exported
// environment. The feature is on for a context when any strategy matches.
type Strategy struct {
	Name        string         `json:"name"`
	FeatureName string         `json:"featureName"`
	Title       string         `json:"title,omitempty"`
	Disabled    bool           `json:"disabled,omitempty"`
	SortOrder   int            `json:"sortOrder"`
	Segments    []int          `json:"segments,omitempty"`
	Constraints []Constraint   `json:"constraints,omitempty"`
	Parameters  map[string]any `json:"parameters,omitempty"`
	Variants    []Variant      `json:"variants,omitempty"`
}

// Constraint compares a context field with values; single-value operators
// use Value, the others Values.
type Constraint struct {
	ContextName     string   `json:"contextName"`
	Operator        string   `json:"operator"`
	Values          []string `json:"values,omitempty"`
	Value           string   `json:"value,omitempty"`
	Inverted        bool     `json:"inverted,omitempty"`
	CaseInsensitive bool     `json:"caseInsensitive,omitempty"`
}

// Variant is a value a matching strategy serves; weights are in tenths of
// a percent.
type Variant struct {
	Name       string     `json:"name"`
	Weight     int        `json:"weight"`
	WeightType string     `json:"weightType,omitempty"`
	Stickiness string     `json:"stickiness,omitempty"`
	Payload    *Payload   `json:"payload,omitempty"`
	Overrides  []Override `json:"overrides,omitempty"`
}

// Payload is a variant's value: Type is string, json, csv or number.
type Payload struct {
	Type  string `json:"type"`
	Value string `json:"value"`
}

// Override serves a variant to listed context values (environment variants
// only).
type Override struct {
	ContextName string   `json:"contextName"`
	Values      []string `json:"values"`
}

// FeatureEnvironment is a feature's state in the exported environment.
// Variants are the environment's variants, which strategies without
// variants of their own serve.
type FeatureEnvironment struct {
	FeatureName string    `json:"featureName"`
	Environment string    `json:"environment,omitempty"`
	Enabled     bool      `json:"enabled"`
	Variants    []Variant `json:"variants,omitempty"`
}

// Dependencies lists the parent features Feature depends on.
type Dependencies struct {
	Feature      string       `json:"feature"`
	Dependencies []Dependency `json:"dependencies"`
}

// Dependency requires parent Feature to be enabled (or disabled when
// Enabled is false) and, with Variants, to serve one of them.
type Dependency struct {
	Feature  string   `json:"feature"`
	Enabled  *bool    `json:"enabled,omitempty"`
	Variants []string `json:"variants,omitempty"`
}

// Segment is a segment as GET /api/admin/segments returns it; strategies
// refer to it by ID.
type Segment struct {
	ID          int          `json:"id"`
	Name        string       `json:"name"`
	Description string       `json:"description,omitempty"`
	Constraints []Constraint `json:"constraints"`
}

// Fetch reads src.File when it is set, and otherwise project src.Project
// from the Unleash instance at src.URL: its enabled environments, the
// features-batch export of each, then the segments.
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
	base := strings.TrimRight(src.URL, "/")
	if base == "" {
		return nil, errors.New("the Unleash URL is required")
	}
	if src.Project == "" {
		return nil, errors.New("the Unleash project is required")
	}
	f := fetcher{ctx: ctx, base: base, src: src}

	var envs struct {
		Environments []struct {
			Name    string `json:"name"`
			Enabled bool   `json:"enabled"`
		} `json:"environments"`
	}
	if err := f.do(http.MethodGet, "/api/admin/environments", nil, &envs); err != nil {
		return nil, err
	}
	e := &Export{Environments: []string{}, PerEnv: map[string]BatchExport{}}
	for _, env := range envs.Environments {
		if !env.Enabled {
			continue
		}
		var batch BatchExport
		body := map[string]string{"environment": env.Name, "project": src.Project}
		if err := f.do(http.MethodPost, "/api/admin/features-batch/export", body, &batch); err != nil {
			return nil, err
		}
		e.Environments = append(e.Environments, env.Name)
		e.PerEnv[env.Name] = batch
	}

	var segments struct {
		Segments []Segment `json:"segments"`
	}
	if err := f.do(http.MethodGet, "/api/admin/segments", nil, &segments); err != nil {
		return nil, err
	}
	e.Segments = segments.Segments
	return e, nil
}

type fetcher struct {
	ctx  context.Context
	base string
	src  importers.Source
}

// do sends a request to path, relative to the base URL, with in as its
// JSON body when it is not nil, and decodes the response into out.
func (f fetcher) do(method, path string, in, out any) error {
	var body io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return err
		}
		body = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(f.ctx, method, f.base+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", f.src.Token)
	req.Header.Set("Accept", "application/json")
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
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
		return fmt.Errorf("%s %s: HTTP %d: %s", method, path, resp.StatusCode, strings.TrimSpace(string(b)))
	}
	if err := json.Unmarshal(b, out); err != nil {
		return fmt.Errorf("%s %s: %w", method, path, err)
	}
	return nil
}
