// Package sync compares the flags-as-code directory with the server and
// brings the server in line with it.
package sync

import (
	"context"
	"fmt"
	"slices"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
)

// Snapshot reads project from the server as a flagfiles.Dir: the project
// and its environments, every segment, and every flag, live and archived,
// with its config in each environment, or only in envs when it is not
// empty. It leaves out what the files do not manage: versions, timestamps,
// lifecycle state other than permanent, schedules, running rollouts, SDK
// keys, integrations and members. Environments hidden from the token are
// absent.
func Snapshot(ctx context.Context, c *client.Client, project string, envs []string) (*flagfiles.Dir, error) {
	p, err := c.GetProject(ctx, project)
	if err != nil {
		return nil, err
	}
	d := &flagfiles.Dir{
		Project: flagfiles.ProjectFile{
			APIVersion:   flagfiles.APIVersion,
			Project:      flagfiles.ProjectMeta{Key: p.Key, Name: p.Name, Description: p.Description},
			Environments: []flagfiles.Environment{},
		},
		Segments: map[string]flagfiles.Segment{},
		Flags:    map[string]flagfiles.Flag{},
	}
	var configEnvs []string
	for _, e := range p.Environments {
		d.Project.Environments = append(d.Project.Environments, flagfiles.Environment{
			Key: e.Key, Name: e.Name, Description: e.Description, ApprovalsRequired: e.ApprovalsRequired,
		})
		if len(envs) == 0 || slices.Contains(envs, e.Key) {
			configEnvs = append(configEnvs, e.Key)
		}
	}
	for _, e := range envs {
		if !slices.Contains(configEnvs, e) {
			return nil, fmt.Errorf("environment %q is not in project %s", e, project)
		}
	}

	segments, err := c.ListSegments(ctx, project)
	if err != nil {
		return nil, err
	}
	for _, s := range segments {
		full, err := c.GetSegment(ctx, project, s.Key)
		if err != nil {
			return nil, err
		}
		d.Segments[s.Key] = flagfiles.Segment{
			Key: s.Key, Name: full.Name, Description: full.Description,
			Included: orEmpty(full.Included), Excluded: orEmpty(full.Excluded),
			Rules: segmentRules(full.Rules),
		}
	}

	for _, archived := range []bool{false, true} {
		flags, err := c.ListFlags(ctx, project, client.ListFlagsOptions{Archived: archived})
		if err != nil {
			return nil, err
		}
		for _, summary := range flags {
			f, err := snapshotFlag(ctx, c, project, summary.Key, configEnvs)
			if err != nil {
				return nil, err
			}
			d.Flags[f.Key] = *f
		}
	}
	return d, nil
}

func snapshotFlag(ctx context.Context, c *client.Client, project, key string, envs []string) (*flagfiles.Flag, error) {
	f, err := c.GetFlag(ctx, project, key)
	if err != nil {
		return nil, err
	}
	out := &flagfiles.Flag{
		Key: f.Key, Name: f.Name, Description: f.Description, Type: f.Type,
		Tags: orEmpty(f.Tags), Owner: f.Owner,
		Variations: orEmpty(f.Variations), OffVariation: f.OffVariation,
		Permanent: f.Lifecycle == "permanent", Archived: f.Archived,
		Environments: map[string]flagfiles.FlagEnv{},
	}
	for _, e := range envs {
		cfg, err := c.GetFlagConfig(ctx, project, key, e)
		if err != nil {
			return nil, err
		}
		out.Environments[e] = flagfiles.FlagEnv{
			Enabled:       cfg.Enabled,
			Targets:       targets(cfg.Targets),
			Rules:         rules(cfg.Rules),
			Fallthrough:   cfg.Fallthrough,
			Prerequisites: orEmpty(cfg.Prerequisites),
		}
	}
	return out, nil
}

// The helpers below turn missing lists into empty ones, so that the files
// say [] rather than null and a Dir read back from them is equal.

func targets(in []client.Target) []client.Target {
	out := make([]client.Target, len(in))
	for i, t := range in {
		t.Values = orEmpty(t.Values)
		out[i] = t
	}
	return out
}

func rules(in []client.Rule) []client.Rule {
	out := make([]client.Rule, len(in))
	for i, r := range in {
		r.Clauses = clauses(r.Clauses)
		out[i] = r
	}
	return out
}

func segmentRules(in []client.SegmentRule) []client.SegmentRule {
	out := make([]client.SegmentRule, len(in))
	for i, r := range in {
		r.Clauses = clauses(r.Clauses)
		out[i] = r
	}
	return out
}

func clauses(in []client.Clause) []client.Clause {
	out := make([]client.Clause, len(in))
	for i, c := range in {
		c.Values = orEmpty(c.Values)
		out[i] = c
	}
	return out
}

func orEmpty[T any](s []T) []T {
	if s == nil {
		return []T{}
	}
	return s
}
