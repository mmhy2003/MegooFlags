package sync

import (
	"cmp"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"reflect"
	"slices"
	"strings"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
)

// Op is what a change does.
type Op string

const (
	OpCreate  Op = "create"
	OpUpdate  Op = "update"
	OpArchive Op = "archive"
	OpRestore Op = "restore"
	OpDelete  Op = "delete"
)

// Kind is what a change applies to.
type Kind string

const (
	KindProject       Kind = "project"
	KindEnvironment   Kind = "environment"
	KindSegment       Kind = "segment"
	KindSegmentConfig Kind = "segment_config"
	KindFlag          Kind = "flag"
	KindLifecycle     Kind = "lifecycle"
	KindVariation     Kind = "variation"
	KindConfig        Kind = "config"
)

// Change is one step that brings the server in line with the files. Key is
// the resource's key (the flag's for variations and configs), Env the
// environment of a config and Variation the variation's key. Summary names
// what differs. The pointer that matches Kind carries the files' version.
type Change struct {
	Op        Op
	Kind      Kind
	Key       string
	Env       string
	Variation string
	Summary   string

	Project        *flagfiles.ProjectMeta
	Environment    *flagfiles.Environment
	Segment        *flagfiles.Segment
	Flag           *flagfiles.Flag
	VariationValue *client.Variation
	Config         *flagfiles.FlagEnv
}

// Plan is the changes in the order Apply runs them.
type Plan struct {
	Changes []Change
}

// Options limits a Diff. Envs, when set, limits the flag configs compared
// to those environments. Prune archives live flags and deletes segments
// that the files do not have.
type Options struct {
	Envs  []string
	Prune bool
}

// Diff compares files with server, the server's Snapshot, and returns what
// to change on the server, in apply order: the project, its environments,
// segments, flags, new and changed variations, configs, removed variations
// and, with Prune, the flags and segments missing from the files.
// Environments only on the server are never removed. A flag's config is
// compared only in the environments its file lists (and Envs allows).
// Values are compared by meaning, so 1 equals 1.0, and the lists the files
// keep sorted are compared as sets.
func Diff(files, server *flagfiles.Dir, opts Options) Plan {
	var p Plan
	add := func(c Change) { p.Changes = append(p.Changes, c) }

	if fields := differing(
		part{"name", files.Project.Project.Name, server.Project.Project.Name},
		part{"description", files.Project.Project.Description, server.Project.Project.Description},
	); fields != "" {
		project := files.Project.Project
		add(Change{Op: OpUpdate, Kind: KindProject, Key: project.Key, Summary: fields, Project: &project})
	}

	for _, e := range files.Project.Environments {
		i := slices.IndexFunc(server.Project.Environments, func(s flagfiles.Environment) bool { return s.Key == e.Key })
		if i < 0 {
			add(Change{Op: OpCreate, Kind: KindEnvironment, Key: e.Key, Environment: &e})
			continue
		}
		s := server.Project.Environments[i]
		if fields := differing(
			part{"name", e.Name, s.Name},
			part{"description", e.Description, s.Description},
			part{"approvals_required", e.ApprovalsRequired, s.ApprovalsRequired},
		); fields != "" {
			add(Change{Op: OpUpdate, Kind: KindEnvironment, Key: e.Key, Summary: fields, Environment: &e})
		}
	}

	segmentKeys := slices.Sorted(maps.Keys(files.Segments))
	for _, key := range segmentKeys {
		s := files.Segments[key]
		old, ok := server.Segments[key]
		if !ok {
			add(Change{Op: OpCreate, Kind: KindSegment, Key: key, Segment: &s})
		} else if fields := differing(
			part{"name", s.Name, old.Name},
			part{"description", s.Description, old.Description},
		); fields != "" {
			add(Change{Op: OpUpdate, Kind: KindSegment, Key: key, Summary: fields, Segment: &s})
		}
	}
	for _, key := range segmentKeys {
		s, old := files.Segments[key], server.Segments[key]
		if fields := differing(
			part{"included", sortedStrings(s.Included), sortedStrings(old.Included)},
			part{"excluded", sortedStrings(s.Excluded), sortedStrings(old.Excluded)},
			part{"rules", s.Rules, old.Rules},
		); fields != "" {
			add(Change{Op: OpUpdate, Kind: KindSegmentConfig, Key: key, Summary: fields, Segment: &s})
		}
	}

	flagKeys := slices.Sorted(maps.Keys(files.Flags))
	for _, key := range flagKeys {
		f := files.Flags[key]
		old, ok := server.Flags[key]
		if !ok {
			add(Change{Op: OpCreate, Kind: KindFlag, Key: key, Flag: &f})
		} else if fields := differing(
			part{"name", f.Name, old.Name},
			part{"description", f.Description, old.Description},
			part{"type", f.Type, old.Type},
			part{"tags", sortedStrings(f.Tags), sortedStrings(old.Tags)},
			part{"owner", f.Owner, old.Owner},
			part{"off_variation", f.OffVariation, old.OffVariation},
		); fields != "" {
			// Apply refuses a changed type: the flag must be deleted and recreated.
			add(Change{Op: OpUpdate, Kind: KindFlag, Key: key, Summary: fields, Flag: &f})
		}
		if f.Permanent != old.Permanent {
			add(Change{Op: OpUpdate, Kind: KindLifecycle, Key: key, Summary: lifecycle(f.Permanent), Flag: &f})
		}
		if f.Archived != old.Archived {
			op := OpRestore
			if f.Archived {
				op = OpArchive
			}
			add(Change{Op: op, Kind: KindFlag, Key: key, Summary: string(op), Flag: &f})
		}
	}

	for _, key := range flagKeys {
		old, ok := server.Flags[key]
		if !ok {
			continue // created with its variations
		}
		for _, v := range files.Flags[key].Variations {
			i := slices.IndexFunc(old.Variations, func(o client.Variation) bool { return o.Key == v.Key })
			if i < 0 {
				add(Change{Op: OpCreate, Kind: KindVariation, Key: key, Variation: v.Key, VariationValue: &v})
				continue
			}
			o := old.Variations[i]
			if fields := differing(
				part{"name", v.Name, o.Name},
				part{"description", v.Description, o.Description},
				part{"value", v.Value, o.Value},
			); fields != "" {
				add(Change{Op: OpUpdate, Kind: KindVariation, Key: key, Variation: v.Key, Summary: fields, VariationValue: &v})
			}
		}
	}

	for _, key := range flagKeys {
		f, old := files.Flags[key], server.Flags[key]
		for _, env := range slices.Sorted(maps.Keys(f.Environments)) {
			if len(opts.Envs) > 0 && !slices.Contains(opts.Envs, env) {
				continue
			}
			e, s := f.Environments[env], old.Environments[env]
			if fields := differing(
				part{"enabled", e.Enabled, s.Enabled},
				part{"targets", e.Targets, s.Targets},
				part{"rules", canonicalRules(e.Rules), canonicalRules(s.Rules)},
				part{"fallthrough", e.Fallthrough, s.Fallthrough},
				part{"prerequisites", e.Prerequisites, s.Prerequisites},
			); fields != "" {
				add(Change{Op: OpUpdate, Kind: KindConfig, Key: key, Env: env, Summary: fields, Config: &e})
			}
		}
	}

	for _, key := range flagKeys {
		vs := files.Flags[key].Variations
		for _, o := range server.Flags[key].Variations {
			if !slices.ContainsFunc(vs, func(v client.Variation) bool { return v.Key == o.Key }) {
				add(Change{Op: OpDelete, Kind: KindVariation, Key: key, Variation: o.Key})
			}
		}
	}

	if opts.Prune {
		for _, key := range slices.Sorted(maps.Keys(server.Flags)) {
			if _, ok := files.Flags[key]; !ok && !server.Flags[key].Archived {
				add(Change{Op: OpArchive, Kind: KindFlag, Key: key, Summary: string(OpArchive)})
			}
		}
		for _, key := range slices.Sorted(maps.Keys(server.Segments)) {
			if _, ok := files.Segments[key]; !ok {
				add(Change{Op: OpDelete, Kind: KindSegment, Key: key})
			}
		}
	}
	return p
}

// Print writes one line per change: "+ flag new-checkout",
// "~ config new-checkout [production]: rules, fallthrough",
// "- variation new-checkout/old". Creates and restores are "+", archives
// and deletes "-", and updates "~".
func (p Plan) Print(w io.Writer) {
	for _, c := range p.Changes {
		fmt.Fprintln(w, c)
	}
}

// String is the change's line in Print.
func (c Change) String() string {
	sign := "~"
	switch c.Op {
	case OpCreate, OpRestore:
		sign = "+"
	case OpArchive, OpDelete:
		sign = "-"
	}
	line := sign + " " + string(c.Kind) + " " + c.Key
	if c.Variation != "" {
		line += "/" + c.Variation
	}
	if c.Env != "" {
		line += " [" + c.Env + "]"
	}
	if c.Summary != "" {
		line += ": " + c.Summary
	}
	return line
}

func lifecycle(permanent bool) string {
	if permanent {
		return "permanent"
	}
	return "temporary"
}

// part is one named component, from the files and from the server.
type part struct {
	name          string
	files, server any
}

// differing lists the names of the parts that differ, joined by ", ".
func differing(parts ...part) string {
	var names []string
	for _, p := range parts {
		if !sameMeaning(p.files, p.server) {
			names = append(names, p.name)
		}
	}
	return strings.Join(names, ", ")
}

// sameMeaning compares a and b as JSON values, so that numbers compare by
// value whatever their Go type. A null or empty list, and a field that is
// null or an empty list, mean "none" and are equal.
func sameMeaning(a, b any) bool {
	return reflect.DeepEqual(jsonValue(a), jsonValue(b))
}

func jsonValue(v any) any {
	b, err := json.Marshal(v)
	if err != nil {
		// Values come from YAML or JSON and always encode; compare as text if not.
		return fmt.Sprintf("%#v", v)
	}
	var out any
	if err := json.Unmarshal(b, &out); err != nil {
		return string(b)
	}
	return dropEmpty(out)
}

// dropEmpty turns empty lists into nil and leaves out object fields that are
// nil after that.
func dropEmpty(v any) any {
	switch v := v.(type) {
	case []any:
		if len(v) == 0 {
			return nil
		}
		for i, e := range v {
			v[i] = dropEmpty(e)
		}
	case map[string]any:
		for k, e := range v {
			if e = dropEmpty(e); e == nil {
				delete(v, k)
			} else {
				v[k] = e
			}
		}
	}
	return v
}

// canonicalRules sorts the values of in_segment clauses, as the files keep
// them, so that their order makes no difference.
func canonicalRules(rules []client.Rule) []client.Rule {
	out := make([]client.Rule, len(rules))
	for i, r := range rules {
		cs := slices.Clone(r.Clauses)
		for j, c := range cs {
			if c.Operator == "in_segment" {
				cs[j].Values = slices.SortedFunc(slices.Values(c.Values), func(a, b any) int {
					return cmp.Compare(fmt.Sprint(a), fmt.Sprint(b))
				})
			}
		}
		r.Clauses = cs
		out[i] = r
	}
	return out
}

func sortedStrings(s []string) []string {
	return slices.Sorted(slices.Values(s))
}
