package launchdarkly

import (
	"fmt"
	"maps"
	"math"
	"reflect"
	"slices"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers"
)

// operators maps LaunchDarkly clause operators to MegooFlags ones;
// segmentMatch is handled on its own.
var operators = map[string]string{
	"in":                 "in",
	"endsWith":           "ends_with",
	"startsWith":         "starts_with",
	"matches":            "matches",
	"contains":           "contains",
	"lessThan":           "lt",
	"lessThanOrEqual":    "lte",
	"greaterThan":        "gt",
	"greaterThanOrEqual": "gte",
	"before":             "before",
	"after":              "after",
	"semVerEqual":        "semver_eq",
	"semVerLessThan":     "semver_lt",
	"semVerGreaterThan":  "semver_gt",
}

// Convert turns a LaunchDarkly project into a flags-as-code directory for
// project opts.Project. LaunchDarkly segments belong to one environment: a
// segment that is the same in every environment becomes one segment, any
// other becomes <key>-<env> in each environment, and that environment's
// flags refer to it. Whatever cannot be translated as it is goes into the
// report.
func Convert(e *Export, opts importers.Options) (*flagfiles.Dir, importers.Report) {
	c := &converter{
		e: e,
		r: &importers.Report{Tool: "LaunchDarkly"},
		d: &flagfiles.Dir{
			Project: flagfiles.ProjectFile{
				APIVersion: flagfiles.APIVersion,
				Project:    flagfiles.ProjectMeta{Key: opts.Project, Name: opts.Project},
			},
			Segments: map[string]flagfiles.Segment{},
			Flags:    map[string]flagfiles.Flag{},
		},
		envKeys:  map[string]string{},
		segRefs:  map[string]map[string]string{},
		bigSegs:  map[string]map[string]bool{},
		segments: map[string]bool{},
	}
	c.environments(opts.EnvMap)
	c.convertSegments()
	c.convertFlags()
	return c.d, *c.r
}

type converter struct {
	e *Export
	r *importers.Report
	d *flagfiles.Dir

	// envKeys maps LaunchDarkly environment keys to MegooFlags ones.
	envKeys map[string]string
	// segRefs maps, per LaunchDarkly environment, segment keys to the
	// MegooFlags segment that flags of that environment refer to.
	segRefs map[string]map[string]string
	// bigSegs holds, per LaunchDarkly environment, the unbounded segments.
	bigSegs map[string]map[string]bool
	// segments holds the MegooFlags segment keys in use.
	segments map[string]bool
}

// issue is a report line whose resource key is not known yet.
type issue struct{ env, message string }

func (c *converter) environments(envMap map[string]string) {
	used := map[string]bool{}
	for _, env := range c.e.Environments {
		if key, ok := envMap[env.Key]; ok {
			used[key] = true
		}
	}
	for _, env := range c.e.Environments {
		key, ok := envMap[env.Key]
		if !ok {
			key = importers.NormalizeKey(env.Key, used, c.r, "environment")
		}
		c.envKeys[env.Key] = key
		c.segRefs[env.Key] = map[string]string{}
		c.bigSegs[env.Key] = map[string]bool{}
		c.d.Project.Environments = append(c.d.Project.Environments, flagfiles.Environment{Key: key, Name: env.Name})
	}
}

func (c *converter) convertSegments() {
	byKey := map[string]map[string]Segment{}
	for _, env := range c.e.Environments {
		for _, s := range c.e.Segments[env.Key] {
			if byKey[s.Key] == nil {
				byKey[s.Key] = map[string]Segment{}
			}
			byKey[s.Key][env.Key] = s
		}
	}

	for _, ldKey := range slices.Sorted(maps.Keys(byKey)) {
		var envs []Environment
		converted := map[string]flagfiles.Segment{}
		issues := map[string][]issue{}
		for _, env := range c.e.Environments {
			s, ok := byKey[ldKey][env.Key]
			if !ok {
				continue
			}
			if s.Unbounded {
				c.bigSegs[env.Key][ldKey] = true
				c.r.Add("segment", ldKey, c.envKeys[env.Key], "big segments (unbounded) are not imported; rules that use it are skipped")
				continue
			}
			envs = append(envs, env)
			converted[env.Key], issues[env.Key] = c.segment(s, c.envKeys[env.Key])
		}
		if len(envs) == 0 {
			continue
		}

		first := converted[envs[0].Key]
		shared := len(envs) == len(c.e.Environments)
		for _, env := range envs[1:] {
			shared = shared && reflect.DeepEqual(converted[env.Key], first)
		}
		if shared {
			key := importers.NormalizeKey(ldKey, c.segments, c.r, "segment")
			first.Key = key
			c.d.Segments[key] = first
			for _, env := range envs {
				c.segRefs[env.Key][ldKey] = key
				c.flush("segment", key, issues[env.Key])
			}
			continue
		}
		for _, env := range envs {
			s := converted[env.Key]
			key := importers.NormalizeKey(ldKey+"-"+c.envKeys[env.Key], c.segments, c.r, "segment")
			s.Key = key
			s.Name = fmt.Sprintf("%s (%s)", s.Name, env.Name)
			c.d.Segments[key] = s
			c.segRefs[env.Key][ldKey] = key
			c.r.Add("segment", key, c.envKeys[env.Key], "segment %s differs between environments; imported as one segment per environment", ldKey)
			c.flush("segment", key, issues[env.Key])
		}
	}
}

func (c *converter) flush(resource, key string, issues []issue) {
	for _, is := range issues {
		c.r.Add(resource, key, is.env, "%s", is.message)
	}
}

// segment converts one environment's segment, with included and excluded
// sorted, so that equal segments compare equal.
func (c *converter) segment(s Segment, env string) (flagfiles.Segment, []issue) {
	var issues []issue
	note := func(format string, args ...any) {
		issues = append(issues, issue{env, fmt.Sprintf(format, args...)})
	}
	keys := func(list string, plain []string, byKind []SegmentTarget) []string {
		out := slices.Clone(plain)
		for _, t := range byKind {
			if !isUser(t.ContextKind) {
				note("%s of context kind %s are not imported", list, t.ContextKind)
				continue
			}
			out = append(out, t.Values...)
		}
		slices.Sort(out)
		return slices.Compact(out)
	}
	out := flagfiles.Segment{
		Name:        s.Name,
		Description: s.Description,
		Included:    keys("included contexts", s.Included, s.IncludedContexts),
		Excluded:    keys("excluded contexts", s.Excluded, s.ExcludedContexts),
		Rules:       []client.SegmentRule{},
	}
	for i, rule := range s.Rules {
		name := ruleName(i, rule.Description)
		if rule.Weight != nil {
			note("%s matches a percentage of contexts, which segments do not support: skipped", name)
			continue
		}
		clauses, err := c.clauses(rule.Clauses, "")
		if err != nil {
			note("%s skipped: %v", name, err)
			continue
		}
		out.Rules = append(out.Rules, client.SegmentRule{Description: rule.Description, Clauses: clauses})
	}
	return out, issues
}

// flagInfo is what other flags need to know about a flag: its key and its
// variation keys by index.
type flagInfo struct {
	key        string
	variations []string
}

func (c *converter) convertFlags() {
	// The flag details come from the first environment that has the flag;
	// configs maps each LaunchDarkly environment to the flag's config there.
	flags := map[string]Flag{}
	configs := map[string]map[string]FlagConfig{}
	for _, env := range c.e.Environments {
		for _, f := range c.e.Flags[env.Key] {
			if _, ok := flags[f.Key]; !ok {
				flags[f.Key] = f
				configs[f.Key] = map[string]FlagConfig{}
			}
			if cfg, ok := f.Environments[env.Key]; ok {
				configs[f.Key][env.Key] = cfg
			}
		}
	}

	ldKeys := slices.Sorted(maps.Keys(flags))
	infos := map[string]flagInfo{}
	migrations := map[string]bool{}
	used := map[string]bool{}
	for _, ldKey := range ldKeys {
		f := flags[ldKey]
		if f.Purpose == "migration" || f.MigrationSettings != nil {
			migrations[ldKey] = true
			c.r.Add("flag", ldKey, "", "migration flags are not imported")
			continue
		}
		info := flagInfo{key: importers.NormalizeKey(ldKey, used, c.r, "flag")}
		varUsed := map[string]bool{}
		for i, v := range f.Variations {
			base := importers.Slug(v.Name)
			if base == "" {
				base = fmt.Sprintf("v%d", i)
			}
			info.variations = append(info.variations, importers.Unique(base, varUsed))
		}
		infos[ldKey] = info
	}

	for _, ldKey := range ldKeys {
		if migrations[ldKey] {
			continue
		}
		fc := flagConverter{converter: c, info: infos[ldKey], infos: infos, migrations: migrations}
		c.d.Flags[fc.info.key] = fc.flag(flags[ldKey], configs[ldKey])
	}
}

type flagConverter struct {
	*converter
	info       flagInfo
	infos      map[string]flagInfo
	migrations map[string]bool
}

func (c flagConverter) report(env, format string, args ...any) {
	c.r.Add("flag", c.info.key, env, format, args...)
}

func (c flagConverter) flag(f Flag, configs map[string]FlagConfig) flagfiles.Flag {
	out := flagfiles.Flag{
		Key:          c.info.key,
		Name:         f.Name,
		Description:  f.Description,
		Type:         flagType(f),
		Tags:         slices.Clone(f.Tags),
		Permanent:    !f.Temporary,
		Archived:     f.Archived,
		Environments: map[string]flagfiles.FlagEnv{},
	}
	if out.Tags == nil {
		out.Tags = []string{}
	}
	if f.Maintainer != nil && f.Maintainer.Email != "" {
		email := f.Maintainer.Email
		out.Owner = &email
	}
	for i, v := range f.Variations {
		out.Variations = append(out.Variations, client.Variation{
			Key: c.info.variations[i], Name: v.Name, Description: v.Description, Value: scalar(v.Value),
		})
	}
	out.OffVariation = c.offVariation(f, configs)

	for _, env := range c.e.Environments {
		cfg, ok := configs[env.Key]
		if !ok {
			continue
		}
		out.Environments[c.envKeys[env.Key]] = c.config(env.Key, cfg, out.OffVariation)
	}
	return out
}

// offVariation picks the flag's one off variation: the first environment's
// that has one. Without any, a boolean flag's false variation, else the
// first one.
func (c flagConverter) offVariation(f Flag, configs map[string]FlagConfig) string {
	off := ""
	for _, env := range c.e.Environments {
		cfg, ok := configs[env.Key]
		if !ok || cfg.OffVariation == nil {
			continue
		}
		if key, ok := c.variation(*cfg.OffVariation); ok {
			off = key
			break
		}
	}
	if off == "" {
		for i, v := range f.Variations {
			if v.Value == false {
				off = c.info.variations[i]
			}
		}
		if off == "" && len(c.info.variations) > 0 {
			off = c.info.variations[0]
		}
		c.report("", "has no off variation in any environment; %s is served when the flag is off", off)
		return off
	}
	for _, env := range c.e.Environments {
		cfg, ok := configs[env.Key]
		if !ok {
			continue
		}
		mfEnv := c.envKeys[env.Key]
		if cfg.OffVariation == nil {
			c.report(mfEnv, "has no off variation here; %s is served when the flag is off", off)
		} else if key, _ := c.variation(*cfg.OffVariation); key != off {
			c.report(mfEnv, "off variation is %s here, but a MegooFlags flag has one off variation: %s is used", key, off)
		}
	}
	return off
}

func (c flagConverter) config(ldEnv string, cfg FlagConfig, off string) flagfiles.FlagEnv {
	env := c.envKeys[ldEnv]
	out := flagfiles.FlagEnv{
		Enabled:       cfg.On,
		Targets:       c.targets(env, append(slices.Clone(cfg.Targets), cfg.ContextTargets...)),
		Rules:         []client.Rule{},
		Prerequisites: []client.Prerequisite{},
	}

	for i, rule := range cfg.Rules {
		name := ruleName(i, rule.Description)
		if len(rule.Clauses) == 0 {
			c.report(env, "%s has no clauses: skipped", name)
			continue
		}
		clauses, err := c.clauses(rule.Clauses, ldEnv)
		if err == nil && rule.Rollout != nil && !isUser(rule.Rollout.ContextKind) {
			err = fmt.Errorf("its rollout buckets by context kind %s", rule.Rollout.ContextKind)
		}
		var serve client.Serve
		if err == nil {
			serve, err = c.serve(env, name, rule.Serve)
		}
		if err != nil {
			c.report(env, "%s skipped: %v", name, err)
			continue
		}
		out.Rules = append(out.Rules, client.Rule{
			Description: rule.Description, Clauses: clauses, Variation: serve.Variation, Rollout: serve.Rollout,
		})
	}

	if r := cfg.Fallthrough.Rollout; r != nil && !isUser(r.ContextKind) {
		c.report(env, "the fallthrough rollout buckets by context kind %s; it is kept and buckets user contexts", r.ContextKind)
	}
	fallthroughServe, err := c.serve(env, "the fallthrough", cfg.Fallthrough)
	if err != nil {
		c.report(env, "the fallthrough %v: %s is served instead", err, off)
		fallthroughServe = client.Serve{Variation: &off}
	}
	out.Fallthrough = fallthroughServe

	for _, p := range cfg.Prerequisites {
		other, ok := c.infos[p.Key]
		switch {
		case c.migrations[p.Key]:
			c.report(env, "prerequisite %s is a migration flag, which is not imported: skipped", p.Key)
		case !ok:
			c.report(env, "prerequisite %s does not exist: skipped", p.Key)
		case p.Variation < 0 || p.Variation >= len(other.variations):
			c.report(env, "prerequisite %s has no variation %d: skipped", p.Key, p.Variation)
		default:
			out.Prerequisites = append(out.Prerequisites, client.Prerequisite{Flag: other.key, Variation: other.variations[p.Variation]})
		}
	}
	return out
}

// targets merges targets and contextTargets of user contexts into one
// target per variation, in the order the variations first appear; a context
// key stays with the first variation that lists it.
func (c flagConverter) targets(env string, in []Target) []client.Target {
	out := []client.Target{}
	index := map[string]int{}
	seen := map[string]bool{}
	for _, t := range in {
		if len(t.Values) == 0 {
			continue
		}
		if !isUser(t.ContextKind) {
			c.report(env, "targets of context kind %s are not imported", t.ContextKind)
			continue
		}
		key, ok := c.variation(t.Variation)
		if !ok {
			c.report(env, "targets of unknown variation %d are not imported", t.Variation)
			continue
		}
		for _, v := range t.Values {
			if seen[v] {
				c.report(env, "context %s is targeted twice; the first variation is kept", v)
				continue
			}
			seen[v] = true
			i, ok := index[key]
			if !ok {
				i = len(out)
				index[key] = i
				out = append(out, client.Target{Variation: key})
			}
			out[i].Values = append(out[i].Values, v)
		}
	}
	return out
}

// serve converts a variation index or a rollout. name is "rule 2" or "the
// fallthrough", for the report.
func (c flagConverter) serve(env, name string, s Serve) (client.Serve, error) {
	switch {
	case s.Variation != nil:
		key, ok := c.variation(*s.Variation)
		if !ok {
			return client.Serve{}, fmt.Errorf("serves unknown variation %d", *s.Variation)
		}
		return client.Serve{Variation: &key}, nil
	case s.Rollout != nil:
		if s.Rollout.ExperimentAllocation != nil {
			c.report(env, "%s is an experiment; experiments are not imported, the rollout is kept", name)
		}
		rollout := &client.Rollout{BucketBy: s.Rollout.BucketBy}
		if rollout.BucketBy == "" {
			rollout.BucketBy = "key"
		}
		for _, w := range s.Rollout.Variations {
			key, ok := c.variation(w.Variation)
			if !ok {
				return client.Serve{}, fmt.Errorf("rolls out unknown variation %d", w.Variation)
			}
			rollout.Weights = append(rollout.Weights, client.Weight{Variation: key, Weight: w.Weight})
		}
		return client.Serve{Rollout: rollout}, nil
	}
	return client.Serve{}, fmt.Errorf("serves nothing")
}

func (c flagConverter) variation(i int) (string, bool) {
	if i < 0 || i >= len(c.info.variations) {
		return "", false
	}
	return c.info.variations[i], true
}

// clauses converts a rule's clauses. ldEnv is the environment whose
// segments segmentMatch refers to, or "" in a segment's rules, where
// segmentMatch is not supported.
func (c *converter) clauses(in []Clause, ldEnv string) ([]client.Clause, error) {
	out := []client.Clause{}
	for _, cl := range in {
		if cl.Op == "segmentMatch" {
			if ldEnv == "" {
				return nil, fmt.Errorf("it refers to other segments")
			}
			var keys []any
			for _, v := range cl.Values {
				ldKey, _ := v.(string)
				if c.bigSegs[ldEnv][ldKey] {
					return nil, fmt.Errorf("it uses big segment %s", ldKey)
				}
				key, ok := c.segRefs[ldEnv][ldKey]
				if !ok {
					return nil, fmt.Errorf("it uses unknown segment %v", v)
				}
				keys = append(keys, key)
			}
			out = append(out, client.Clause{Attribute: "key", Operator: "in_segment", Values: keys, Negate: cl.Negate})
			continue
		}
		if !isUser(cl.ContextKind) {
			return nil, fmt.Errorf("it uses context kind %s", cl.ContextKind)
		}
		op, ok := operators[cl.Op]
		if !ok {
			return nil, fmt.Errorf("operator %s is not supported", cl.Op)
		}
		values := make([]any, len(cl.Values))
		for i, v := range cl.Values {
			values[i] = scalar(v)
		}
		out = append(out, client.Clause{Attribute: cl.Attribute, Operator: op, Values: values, Negate: cl.Negate})
	}
	return out, nil
}

// flagType is boolean for boolean flags, else string or number when every
// value is one, else json.
func flagType(f Flag) string {
	if f.Kind == "boolean" {
		return "boolean"
	}
	strings, numbers := true, true
	for _, v := range f.Variations {
		_, s := v.Value.(string)
		_, n := v.Value.(float64)
		strings, numbers = strings && s, numbers && n
	}
	switch {
	case len(f.Variations) == 0:
		return "json"
	case strings:
		return "string"
	case numbers:
		return "number"
	}
	return "json"
}

// scalar turns a whole JSON number into an int64, so that it is written as
// 1700000000000 rather than 1.7e+12.
func scalar(v any) any {
	if n, ok := v.(float64); ok && n == math.Trunc(n) && math.Abs(n) < 1<<53 {
		return int64(n)
	}
	return v
}

func isUser(kind string) bool {
	return kind == "" || kind == "user"
}

func ruleName(i int, description string) string {
	if description == "" {
		return fmt.Sprintf("rule %d", i+1)
	}
	return fmt.Sprintf("rule %d (%s)", i+1, description)
}
