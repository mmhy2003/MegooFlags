package flagfiles

import (
	"bytes"
	"cmp"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"go.yaml.in/yaml/v3"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

// Write writes d to dir canonically, so that the same Dir always gives the
// same bytes: fields in declared order with an indent of 2, environment
// keys sorted, tags, included, excluded and in_segment values sorted, and
// every other list in its own order. It creates the directories and removes
// the *.yaml files in flags/ and segments/ that d no longer has. d is not
// changed.
func Write(dir string, d *Dir) error {
	for key := range d.Segments {
		if !keyPattern.MatchString(key) {
			return fmt.Errorf("segment key %q must match %s", key, keyPattern)
		}
	}
	for key := range d.Flags {
		if !keyPattern.MatchString(key) {
			return fmt.Errorf("flag key %q must match %s", key, keyPattern)
		}
	}
	for _, sub := range []string{"segments", "flags"} {
		if err := os.MkdirAll(filepath.Join(dir, sub), 0o755); err != nil {
			return err
		}
	}

	if err := writeYAML(filepath.Join(dir, ProjectFileName), d.Project); err != nil {
		return err
	}
	for key, s := range d.Segments {
		if err := writeYAML(filepath.Join(dir, "segments", key+".yaml"), canonicalSegment(s)); err != nil {
			return err
		}
	}
	for key, f := range d.Flags {
		if err := writeYAML(filepath.Join(dir, "flags", key+".yaml"), canonicalFlag(f)); err != nil {
			return err
		}
	}

	if err := removeStale(filepath.Join(dir, "segments"), d.Segments); err != nil {
		return err
	}
	return removeStale(filepath.Join(dir, "flags"), d.Flags)
}

func writeYAML(path string, v any) error {
	var buf bytes.Buffer
	enc := yaml.NewEncoder(&buf)
	enc.SetIndent(2)
	if err := enc.Encode(v); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	if err := enc.Close(); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	return os.WriteFile(path, buf.Bytes(), 0o644)
}

// removeStale deletes the *.yaml files in dir whose key is not in keep.
func removeStale[V any](dir string, keep map[string]V) error {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return err
	}
	var errs []error
	for _, e := range entries {
		key, ok := strings.CutSuffix(e.Name(), ".yaml")
		if _, kept := keep[key]; e.IsDir() || !ok || kept {
			continue
		}
		errs = append(errs, os.Remove(filepath.Join(dir, e.Name())))
	}
	return errors.Join(errs...)
}

func canonicalSegment(s Segment) Segment {
	s.Included = sortedCopy(s.Included)
	s.Excluded = sortedCopy(s.Excluded)
	return s
}

func canonicalFlag(f Flag) Flag {
	f.Tags = sortedCopy(f.Tags)
	if f.Environments != nil {
		envs := make(map[string]FlagEnv, len(f.Environments))
		for key, e := range f.Environments {
			e.Rules = canonicalRules(e.Rules)
			envs[key] = e
		}
		f.Environments = envs
	}
	return f
}

// canonicalRules sorts the values of in_segment clauses, which are a set of
// segment keys; other clause values keep their order.
func canonicalRules(rules []client.Rule) []client.Rule {
	if rules == nil {
		return nil
	}
	out := make([]client.Rule, len(rules))
	for i, r := range rules {
		if r.Clauses != nil {
			clauses := slices.Clone(r.Clauses)
			for j, c := range clauses {
				if c.Operator == "in_segment" {
					clauses[j].Values = slices.SortedFunc(slices.Values(c.Values), func(a, b any) int {
						return cmp.Compare(fmt.Sprint(a), fmt.Sprint(b))
					})
				}
			}
			r.Clauses = clauses
		}
		out[i] = r
	}
	return out
}

// sortedCopy sorts a copy of s; nil stays nil.
func sortedCopy(s []string) []string {
	if s == nil {
		return nil
	}
	return slices.Sorted(slices.Values(s))
}
