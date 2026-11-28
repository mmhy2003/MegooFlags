package unleash

import (
	"cmp"
	"encoding/json"
	"fmt"
	"maps"
	"math"
	"reflect"
	"slices"
	"strconv"
	"strings"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers"
)

// operators maps Unleash constraint operators to MegooFlags ones. NOT_IN
// becomes a negated in; IN_CIDR, SEMVER_GTE, SEMVER_LTE and any other
// operator are not supported.
var operators = map[string]string{
	"IN":              "in",
	"NOT_IN":          "in",
	"STR_ENDS_WITH":   "ends_with",
	"STR_STARTS_WITH": "starts_with",
	"STR_CONTAINS":    "contains",
	"NUM_EQ":          "in",
	"NUM_GT":          "gt",
	"NUM_GTE":         "gte",
	"NUM_LT":          "lt",
	"NUM_LTE":         "lte",
	"DATE_AFTER":      "after",
	"DATE_BEFORE":     "before",
	"SEMVER_EQ":       "semver_eq",
	"SEMVER_GT":       "semver_gt",
	"SEMVER_LT":       "semver_lt",
	"REGEX":           "matches",
}

// permanentTypes are the feature types Unleash expects to live forever.
var permanentTypes = map[string]bool{"kill-switch": true, "permission": true}

// totalWeight is what a MegooFlags rollout's weights add up to.
const totalWeight = 100000

// Convert turns an Unleash project into a flags-as-code directory for
// project opts.Project. A feature becomes a boolean flag, or a string or
// json flag when it has variants; its strategies, in sortOrder, become
// targets, rules and the fallthrough. Whatever cannot be translated as it
// is goes into the report.
func Convert(e *Export, opts importers.Options) (*flagfiles.Dir, importers.Report) {
	c := &converter{
		e: e,
		r: &importers.Report{Tool: "Unleash"},
		d: &flagfiles.Dir{
			Project: flagfiles.ProjectFile{
				APIVersion: flagfiles.APIVersion,
				Project:    flagfiles.ProjectMeta{Key: opts.Project, Name: opts.Project},
			},
			Segments: map[string]flagfiles.Segment{},
			Flags:    map[string]flagfiles.Flag{},
		},
		envKeys: map[string]string{},
		segKeys: map[int]string{},
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

	// envKeys maps Unleash environment names to MegooFlags keys.
	envKeys map[string]string
	// segKeys maps Unleash segment IDs to MegooFlags segment keys.
	segKeys map[int]string
}

func (c *converter) environments(envMap map[string]string) {
	used := map[string]bool{}
	for _, env := range c.e.Environments {
		if key, ok := envMap[env]; ok {
			used[key] = true
		}
	}
	for _, env := range c.e.Environments {
		key, ok := envMap[env]
		if !ok {
			key = importers.NormalizeKey(env, used, c.r, "environment")
		}
		c.envKeys[env] = key
		c.d.Project.Environments = append(c.d.Project.Environments, flagfiles.Environment{Key: key, Name: env})
	}
}

// convertSegments turns each segment into a segment with one rule made of
// all its constraints.
func (c *converter) convertSegments() {
	used := map[string]bool{}
	for _, s := range c.e.Segments {
		key := importers.NormalizeKey(s.Name, used, c.r, "segment")
		c.segKeys[s.ID] = key
		out := flagfiles.Segment{
			Key: key, Name: s.Name, Description: s.Description,
			Included: []string{}, Excluded: []string{}, Rules: []client.SegmentRule{},
		}
		if len(s.Constraints) == 0 {
			c.r.Add("segment", key, "", "has no constraints, so it matched everyone in Unleash; here it matches no one")
		} else if clauses, notes, err := convertClauses(s.Constraints); err != nil {
			c.r.Add("segment", key, "", "constraints skipped: %v; the segment matches no one", err)
		} else {
			for _, n := range notes {
				c.r.Add("segment", key, "", "%s", n)
			}
			out.Rules = append(out.Rules, client.SegmentRule{Description: "", Clauses: clauses})
		}
		c.d.Segments[key] = out
	}
}

// feature is a feature with what each Unleash environment that has it
// holds about it.
type feature struct {
	Feature
	envs map[string]*featureEnv
}

type featureEnv struct {
	state        *FeatureEnvironment
	strategies   []Strategy
	dependencies []Dependency
}

func (f *feature) env(name string) *featureEnv {
	if f == nil {
		return nil
	}
	return f.envs[name]
}

// flagInfo is what other flags need to know about a flag: its key, its
// variants in order, the variation key of each variant name (its first
// payload's) and of each variant name and payload.
type flagInfo struct {
	key        string
	variants   []Variant
	variations map[string]string
	byPayload  map[string]string
}

func (i flagInfo) multivariate() bool {
	return len(i.variants) > 0
}

// keyOf is the variation key of variant v, by its name and payload.
func (i flagInfo) keyOf(v Variant) string {
	return i.byPayload[variantID(v)]
}

// variantID is v's name and payload.
func variantID(v Variant) string {
	if v.Payload == nil {
		return v.Name
	}
	return v.Name + "\x00" + v.Payload.Type + "\x00" + v.Payload.Value
}

func (c *converter) convertFlags() {
	features := map[string]*feature{}
	for _, env := range c.e.Environments {
		batch := c.e.PerEnv[env]
		for _, f := range batch.Features {
			if _, ok := features[f.Name]; !ok {
				features[f.Name] = &feature{Feature: f, envs: map[string]*featureEnv{}}
			}
			features[f.Name].envs[env] = &featureEnv{}
		}
		for i, s := range batch.FeatureEnvironments {
			if fe := features[s.FeatureName].env(env); fe != nil {
				fe.state = &batch.FeatureEnvironments[i]
			}
		}
		for _, s := range batch.FeatureStrategies {
			if fe := features[s.FeatureName].env(env); fe != nil {
				fe.strategies = append(fe.strategies, s)
			}
		}
		for _, d := range batch.Dependencies {
			if fe := features[d.Feature].env(env); fe != nil {
				fe.dependencies = append(fe.dependencies, d.Dependencies...)
			}
		}
	}

	names := slices.Sorted(maps.Keys(features))
	infos := map[string]flagInfo{}
	used := map[string]bool{}
	for _, name := range names {
		f := features[name]
		for _, fe := range f.envs {
			slices.SortStableFunc(fe.strategies, func(a, b Strategy) int { return cmp.Compare(a.SortOrder, b.SortOrder) })
		}
		infos[name] = c.info(f, importers.NormalizeKey(name, used, c.r, "flag"))
	}
	for _, name := range names {
		fc := flagConverter{converter: c, info: infos[name], infos: infos}
		c.d.Flags[fc.info.key] = fc.flag(features[name])
	}
}

// info collects a feature's variants by first appearance, in environment
// order (the environment's variants, then its strategies'), and names the
// variations: true and false, or a key per variant name and payload and
// disabled. A variant with another payload than before gets the key
// <name>-<environment>, with a report line. The off variation is under the
// name "".
func (c *converter) info(f *feature, key string) flagInfo {
	info := flagInfo{key: key, variations: map[string]string{}, byPayload: map[string]string{}}
	used := map[string]bool{}
	add := func(env string, vs []Variant) {
		for _, v := range vs {
			id := variantID(v)
			if _, ok := info.byPayload[id]; ok {
				continue
			}
			base := importers.Slug(v.Name)
			if base == "" {
				base = fmt.Sprintf("v%d", len(info.variants))
			}
			first, seen := info.variations[v.Name]
			if seen {
				base += "-" + env
			}
			info.byPayload[id] = importers.Unique(base, used)
			info.variants = append(info.variants, v)
			if seen {
				c.r.Add("flag", key, env, "variant %s has another payload in %s: it is variation %s there, and %s elsewhere",
					v.Name, env, info.byPayload[id], first)
			} else {
				info.variations[v.Name] = info.byPayload[id]
			}
		}
	}
	for _, env := range c.e.Environments {
		fe := f.envs[env]
		if fe == nil {
			continue
		}
		if fe.state != nil {
			add(c.envKeys[env], fe.state.Variants)
		}
		for _, s := range fe.strategies {
			add(c.envKeys[env], s.Variants)
		}
	}
	if info.multivariate() {
		info.variations[""] = importers.Unique("disabled", used)
	} else {
		info.variations[""] = "false"
	}
	return info
}

type flagConverter struct {
	*converter
	info  flagInfo
	infos map[string]flagInfo
}

func (c flagConverter) report(env, format string, args ...any) {
	c.r.Add("flag", c.info.key, env, format, args...)
}

// off is the off variation: false, or disabled for a flag with variants.
func (c flagConverter) off() string {
	return c.info.variations[""]
}

func (c flagConverter) flag(f *feature) flagfiles.Flag {
	out := flagfiles.Flag{
		Key:          c.info.key,
		Name:         f.Name,
		Description:  f.Description,
		Type:         "boolean",
		Tags:         []string{},
		OffVariation: c.off(),
		Permanent:    permanentTypes[f.Type],
		Archived:     f.Archived,
		Environments: map[string]flagfiles.FlagEnv{},
	}
	if c.info.multivariate() {
		out.Type = "string"
		for _, v := range c.info.variants {
			if v.Payload != nil && v.Payload.Type == "json" {
				out.Type = "json"
			}
			out.Variations = append(out.Variations, client.Variation{Key: c.info.keyOf(v), Name: v.Name, Value: c.value(v)})
		}
		out.Variations = append(out.Variations, client.Variation{Key: c.off(), Value: ""})
		c.report("", `has variants; %s (value "") is served when the flag is off or no variant applies`, c.off())
	} else {
		out.Variations = []client.Variation{{Key: "true", Value: true}, {Key: "false", Value: false}}
	}

	for _, env := range c.e.Environments {
		if fe := f.envs[env]; fe != nil {
			out.Environments[c.envKeys[env]] = c.config(c.envKeys[env], fe)
		}
	}
	return out
}

// value is a variant's value: its string payload, its parsed json payload,
// else its name.
func (c flagConverter) value(v Variant) any {
	if v.Payload == nil {
		return v.Name
	}
	switch v.Payload.Type {
	case "string":
		return v.Payload.Value
	case "json":
		var value any
		if err := json.Unmarshal([]byte(v.Payload.Value), &value); err != nil {
			c.report("", "variant %s has an invalid json payload: its name is the value", v.Name)
			return v.Name
		}
		return scalar(value)
	}
	c.report("", "variant %s has a %s payload, which is not imported: its name is the value", v.Name, v.Payload.Type)
	return v.Name
}

func (c flagConverter) config(env string, fe *featureEnv) flagfiles.FlagEnv {
	out := flagfiles.FlagEnv{
		Targets:       []client.Target{},
		Rules:         []client.Rule{},
		Prerequisites: c.prerequisites(env, fe.dependencies),
	}
	var envVariants []Variant
	if fe.state != nil {
		out.Enabled = fe.state.Enabled
		envVariants = fe.state.Variants
		for _, v := range envVariants {
			if len(v.Overrides) > 0 {
				c.report(env, "the overrides of variant %s are not imported", v.Name)
			}
		}
	}

	// An enabled feature without strategies is on for everyone.
	var fallthroughServe *client.Serve
	fallthroughFrom := 0
	if len(fe.strategies) == 0 {
		serve := c.serve(env, "the environment", totalWeight, "", envVariants)
		fallthroughServe = &serve
	}
	seen := map[string]bool{}
strategies:
	for i, s := range fe.strategies {
		name := strategyName(i, s)
		if s.Disabled {
			c.report(env, "%s is disabled: skipped", name)
			continue
		}
		switch s.Name {
		case "default", "flexibleRollout", "userWithId":
		case "remoteAddress":
			c.report(env, "%s matches IP addresses, which are not supported: skipped", name)
			continue
		default:
			c.report(env, "%s uses the %s strategy, which is not supported: skipped", name, s.Name)
			continue
		}

		clauses, notes, err := convertClauses(s.Constraints)
		// A context must be in every segment of a strategy, so each one
		// gets its own clause.
		for _, id := range s.Segments {
			if err != nil {
				break
			}
			key, ok := c.segKeys[id]
			if !ok {
				err = fmt.Errorf("it uses unknown segment %d", id)
				break
			}
			clauses = append(clauses, client.Clause{Attribute: "key", Operator: "in_segment", Values: []any{key}})
		}
		if err != nil {
			c.report(env, "%s skipped: %v", name, err)
			continue
		}
		for _, n := range notes {
			c.report(env, "%s: %s", name, n)
		}

		variants := s.Variants
		if len(variants) == 0 {
			variants = envVariants
		}
		var serve client.Serve
		switch s.Name {
		case "default":
			serve = c.serve(env, name, totalWeight, "", variants)
			if len(clauses) == 0 {
				if fallthroughServe != nil && c.info.multivariate() && !reflect.DeepEqual(*fallthroughServe, serve) {
					c.report(env, "%s replaces strategy %d's rollout as the fallthrough; the variants that rollout served are dropped", name, fallthroughFrom)
				}
				fallthroughServe = &serve
				break strategies
			}
		case "flexibleRollout":
			rollout, err := strconv.Atoi(param(s, "rollout"))
			if err != nil || rollout < 0 || rollout > 100 {
				c.report(env, "%s skipped: rollout %q is not a percentage", name, param(s, "rollout"))
				continue
			}
			serve = c.serve(env, name, rollout*1000, c.bucketBy(env, name, param(s, "stickiness")), variants)
			if len(clauses) == 0 {
				if fallthroughServe != nil {
					c.report(env, "%s skipped: an earlier rollout without constraints is already the fallthrough", name)
					continue
				}
				fallthroughServe, fallthroughFrom = &serve, i+1
				continue
			}
			if rollout < 100 {
				c.report(env, "%s: contexts that match its constraints but fall outside its %d%% get %s instead of trying later strategies",
					name, rollout, c.off())
			}
		case "userWithId":
			var ids []any
			var keys []string
			for _, id := range strings.Split(param(s, "userIds"), ",") {
				if id = strings.TrimSpace(id); id != "" && !slices.Contains(keys, id) {
					ids, keys = append(ids, id), append(keys, id)
				}
			}
			if len(ids) == 0 {
				c.report(env, "%s has no user IDs: skipped", name)
				continue
			}
			if !c.info.multivariate() && len(clauses) == 0 {
				out.Targets = c.target(env, out.Targets, keys, seen)
				continue
			}
			clauses = append([]client.Clause{{Attribute: "key", Operator: "in", Values: ids}}, clauses...)
			serve = c.serve(env, name, totalWeight, "", variants)
		}
		out.Rules = append(out.Rules, client.Rule{Description: s.Title, Clauses: clauses, Variation: serve.Variation, Rollout: serve.Rollout})
	}

	if fallthroughServe != nil {
		out.Fallthrough = *fallthroughServe
	} else {
		off := c.off()
		out.Fallthrough = client.Serve{Variation: &off}
	}
	return out
}

// target adds keys to the target of variation true; a key that an earlier
// strategy listed is reported.
func (c flagConverter) target(env string, targets []client.Target, keys []string, seen map[string]bool) []client.Target {
	if len(targets) == 0 {
		targets = []client.Target{{Variation: "true"}}
	}
	for _, k := range keys {
		if seen[k] {
			c.report(env, "user %s is listed twice; it is targeted once", k)
			continue
		}
		seen[k] = true
		targets[0].Values = append(targets[0].Values, k)
	}
	return targets
}

// serve is what a matching strategy serves to share (in thousandths of a
// percent) of the contexts: true, or the variants split by their weights,
// and false or disabled to the rest. bucketBy is the strategy's stickiness,
// or "" to use the variants'.
func (c flagConverter) serve(env, name string, share int, bucketBy string, variants []Variant) client.Serve {
	if !c.info.multivariate() {
		if share == totalWeight {
			on := "true"
			return client.Serve{Variation: &on}
		}
		return client.Serve{Rollout: &client.Rollout{BucketBy: bucketBy, Weights: []client.Weight{
			{Variation: "true", Weight: share}, {Variation: "false", Weight: totalWeight - share},
		}}}
	}
	if len(variants) == 0 {
		off := c.off()
		return client.Serve{Variation: &off}
	}
	if bucketBy == "" {
		bucketBy = c.bucketBy(env, name, variants[0].Stickiness)
	}
	weights := make([]int, len(variants))
	for i, v := range variants {
		weights[i] = v.Weight
	}
	rollout := &client.Rollout{BucketBy: bucketBy}
	for i, w := range split(share, weights) {
		rollout.Weights = append(rollout.Weights, client.Weight{Variation: c.info.keyOf(variants[i]), Weight: w})
	}
	if share < totalWeight {
		rollout.Weights = append(rollout.Weights, client.Weight{Variation: c.off(), Weight: totalWeight - share})
	}
	return client.Serve{Rollout: rollout}
}

// split shares total out in proportion to weights; the last weight gets
// what rounding leaves. Weights that are all zero share it equally.
func split(total int, weights []int) []int {
	sum := 0
	for _, w := range weights {
		sum += w
	}
	out := make([]int, len(weights))
	left := total
	for i, w := range weights {
		switch {
		case i == len(weights)-1:
			out[i] = left
		case sum == 0:
			out[i] = total / len(weights)
		default:
			out[i] = total * w / sum
		}
		left -= out[i]
	}
	return out
}

// bucketBy maps a stickiness to the attribute a rollout buckets by: the
// context key for default and userId, else the field itself. random
// stickiness is reported and buckets by the key.
func (c flagConverter) bucketBy(env, name, stickiness string) string {
	switch stickiness {
	case "", "default", "userId":
		return "key"
	case "random":
		c.report(env, "%s has random stickiness, which is not supported: it buckets by the context key", name)
		return "key"
	}
	return stickiness
}

// prerequisites turns dependencies into prerequisites: the parent must
// serve true, or the first listed variant.
func (c flagConverter) prerequisites(env string, deps []Dependency) []client.Prerequisite {
	out := []client.Prerequisite{}
	for _, d := range deps {
		parent, ok := c.infos[d.Feature]
		switch {
		case !ok:
			c.report(env, "dependency %s does not exist: skipped", d.Feature)
			continue
		case d.Enabled != nil && !*d.Enabled:
			c.report(env, "depends on %s being disabled, which prerequisites cannot express: skipped", d.Feature)
			continue
		}
		variation := "true"
		switch {
		case len(d.Variants) > 0:
			key, ok := parent.variations[d.Variants[0]]
			if !ok || d.Variants[0] == "" {
				c.report(env, "dependency %s has no variant %s: skipped", d.Feature, d.Variants[0])
				continue
			}
			variation = key
			if len(d.Variants) > 1 {
				c.report(env, "depends on %s serving any of %s; a prerequisite has one variation: %s",
					d.Feature, strings.Join(d.Variants, ", "), key)
			}
		case parent.multivariate():
			variation = parent.variations[parent.variants[0].Name]
			c.report(env, "depends on %s being enabled; a prerequisite has one variation: %s", d.Feature, variation)
		}
		out = append(out, client.Prerequisite{Flag: parent.key, Variation: variation})
	}
	return out
}

// currentTimeNote is the note on constraints on currentTime.
const currentTimeNote = "currentTime is set by Unleash SDKs; MegooFlags SDKs do not set it, so the app must send it"

// convertClauses converts constraints into clauses, with notes on what
// changed in meaning. An unsupported constraint is an error, since
// dropping it would widen what matches.
func convertClauses(in []Constraint) ([]client.Clause, []string, error) {
	out := []client.Clause{}
	var notes []string
	for _, k := range in {
		if k.Operator == "IN_CIDR" || k.ContextName == "remoteAddress" {
			return nil, nil, fmt.Errorf("the constraint on %s matches IP addresses, which is not supported", k.ContextName)
		}
		op, ok := operators[k.Operator]
		if !ok {
			return nil, nil, fmt.Errorf("operator %s is not supported", k.Operator)
		}
		raw := k.Values
		if len(raw) == 0 && k.Value != "" {
			raw = []string{k.Value}
		}
		if len(raw) == 0 {
			return nil, nil, fmt.Errorf("the constraint on %s has no values", k.ContextName)
		}
		values := make([]any, len(raw))
		for i, v := range raw {
			values[i] = v
			if strings.HasPrefix(k.Operator, "NUM_") {
				n, err := strconv.ParseFloat(v, 64)
				if err != nil {
					return nil, nil, fmt.Errorf("the constraint on %s compares with %q, which is not a number", k.ContextName, v)
				}
				values[i] = scalar(n)
			}
		}
		if k.ContextName == "currentTime" && !slices.Contains(notes, currentTimeNote) {
			notes = append(notes, currentTimeNote)
		}
		if k.CaseInsensitive {
			notes = append(notes, fmt.Sprintf("the constraint on %s is case-insensitive; it is imported case-sensitive", k.ContextName))
		}
		attribute := k.ContextName
		if attribute == "userId" {
			attribute = "key"
		}
		out = append(out, client.Clause{Attribute: attribute, Operator: op, Values: values, Negate: k.Inverted != (k.Operator == "NOT_IN")})
	}
	return out, notes, nil
}

// param is a strategy parameter as a string.
func param(s Strategy, name string) string {
	switch v := s.Parameters[name].(type) {
	case nil:
		return ""
	case string:
		return v
	case float64:
		return strconv.FormatFloat(v, 'f', -1, 64)
	default:
		return fmt.Sprint(v)
	}
}

// scalar turns a whole JSON number into an int64, so that it is written as
// 1700000000000 rather than 1.7e+12.
func scalar(v any) any {
	if n, ok := v.(float64); ok && n == math.Trunc(n) && math.Abs(n) < 1<<53 {
		return int64(n)
	}
	return v
}

func strategyName(i int, s Strategy) string {
	if s.Title == "" {
		return fmt.Sprintf("strategy %d (%s)", i+1, s.Name)
	}
	return fmt.Sprintf("strategy %d (%s, %s)", i+1, s.Name, s.Title)
}
