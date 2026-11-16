package flagsmith

import (
	"cmp"
	"errors"
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

// operators maps Flagsmith condition operators to MegooFlags ones.
// NOT_EQUAL and NOT_CONTAINS become negated clauses (see negated); IS_SET,
// IS_NOT_SET, MODULO, PERCENTAGE_SPLIT and any other operator are not
// supported.
var operators = map[string]string{
	"EQUAL":                  "in",
	"NOT_EQUAL":              "in",
	"GREATER_THAN":           "gt",
	"GREATER_THAN_INCLUSIVE": "gte",
	"LESS_THAN":              "lt",
	"LESS_THAN_INCLUSIVE":    "lte",
	"CONTAINS":               "contains",
	"NOT_CONTAINS":           "contains",
	"REGEX":                  "matches",
	"IN":                     "in",
}

var negated = map[string]bool{"NOT_EQUAL": true, "NOT_CONTAINS": true}

var numeric = map[string]bool{"GREATER_THAN": true, "GREATER_THAN_INCLUSIVE": true, "LESS_THAN": true, "LESS_THAN_INCLUSIVE": true}

// totalWeight is what a MegooFlags rollout's weights add up to.
const totalWeight = 100000

// maxDepth is how deep segment rules may nest, and maxRules how many
// MegooFlags rules a segment's expansion may have.
const (
	maxDepth = 2
	maxRules = 20
)

var (
	errTooDeep    = errors.New("rules nested too deep")
	errMatchesAll = errors.New("matches everyone")
)

// Convert turns a Flagsmith project into a flags-as-code directory for
// project opts.Project. A feature becomes a boolean flag, or a flag typed
// by its values with its multivariate options as variations; segment
// overrides become in_segment rules in priority order. Whatever cannot be
// translated as it is goes into the report.
func Convert(e *Export, opts importers.Options) (*flagfiles.Dir, importers.Report) {
	c := &converter{
		e: e,
		r: &importers.Report{Tool: "Flagsmith"},
		d: &flagfiles.Dir{
			Project: flagfiles.ProjectFile{
				APIVersion: flagfiles.APIVersion,
				Project:    flagfiles.ProjectMeta{Key: opts.Project, Name: opts.Project},
			},
			Segments: map[string]flagfiles.Segment{},
			Flags:    map[string]flagfiles.Flag{},
		},
		envs:     slices.Sorted(maps.Keys(e.Environments)),
		envKeys:  map[string]string{},
		segKeys:  map[int]string{},
		flagKeys: map[string]string{},
	}
	c.environments(opts.EnvMap)
	c.convertSegments()
	c.convertFlags()
	c.reportIdentities()
	return c.d, *c.r
}

type converter struct {
	e *Export
	r *importers.Report
	d *flagfiles.Dir

	// envs are the Flagsmith environment names in order.
	envs []string
	// envKeys maps Flagsmith environment names to MegooFlags keys.
	envKeys map[string]string
	// segKeys maps Flagsmith segment IDs to MegooFlags segment keys.
	segKeys map[int]string
	// flagKeys maps Flagsmith feature names to MegooFlags flag keys.
	flagKeys map[string]string
}

func (c *converter) environments(envMap map[string]string) {
	used := map[string]bool{}
	for _, env := range c.envs {
		if key, ok := envMap[env]; ok {
			used[key] = true
		}
	}
	for _, env := range c.envs {
		key, ok := envMap[env]
		if !ok {
			key = importers.NormalizeKey(env, used, c.r, "environment")
		}
		c.envKeys[env] = key
		c.d.Project.Environments = append(c.d.Project.Environments, flagfiles.Environment{Key: key, Name: env})
	}
}

// convertSegments turns each segment, which every environment document
// repeats, into a segment whose rules are its rules in OR-of-AND form.
func (c *converter) convertSegments() {
	used := map[string]bool{}
	for _, env := range c.envs {
		for _, s := range c.e.Environments[env].Project.Segments {
			if _, ok := c.segKeys[s.ID]; ok {
				continue
			}
			key := importers.NormalizeKey(s.Name, used, c.r, "segment")
			c.segKeys[s.ID] = key
			out := flagfiles.Segment{
				Key: key, Name: s.Name,
				Included: []string{}, Excluded: []string{}, Rules: []client.SegmentRule{},
			}
			rules, notes, err := segmentRules(s.Rules)
			switch {
			case errors.Is(err, errMatchesAll):
				c.r.Add("segment", key, "", "has no conditions, so it matched everyone in Flagsmith; here it matches no one")
			case err != nil:
				c.r.Add("segment", key, "", "%v: skipped; the segment matches no one", err)
			default:
				for _, n := range notes {
					c.r.Add("segment", key, "", "%s", n)
				}
				if len(rules) == 0 {
					c.r.Add("segment", key, "", "no rule is left: the segment matches no one")
				}
				out.Rules = rules
			}
			c.d.Segments[key] = out
		}
	}
}

// literal is a condition, negated or not.
type literal struct {
	c      Condition
	negate bool
}

// segmentRules expands a segment's rules, which must all match, into
// MegooFlags segment rules, any of which matches: ALL concatenates
// conditions, ANY splits into several rules and NONE negates each
// condition. Nested rules must all match too. Rules nested more than
// maxDepth levels, or that expand to more than maxRules rules, are
// errTooDeep; rules that always match are errMatchesAll. A rule with a
// condition that cannot be translated is skipped, with a note.
func segmentRules(rules []Rule) ([]client.SegmentRule, []string, error) {
	dnf, err := expandAll(rules, 1)
	if err != nil {
		return nil, nil, err
	}
	out := []client.SegmentRule{}
	var notes []string
	for _, conj := range dnf {
		if len(conj) == 0 {
			return nil, nil, errMatchesAll
		}
		clauses := []client.Clause{}
		ok := true
		for _, l := range conj {
			cl, err := convertCondition(l)
			if err != nil {
				ok = false
				if n := err.Error() + ": the rules that use it are skipped"; !slices.Contains(notes, n) {
					notes = append(notes, n)
				}
				continue
			}
			clauses = append(clauses, cl)
		}
		if ok {
			out = append(out, client.SegmentRule{Clauses: clauses})
		}
	}
	return out, notes, nil
}

// expandAll is rules, which must all match, in OR-of-AND form.
func expandAll(rules []Rule, depth int) ([][]literal, error) {
	out := [][]literal{{}}
	for _, r := range rules {
		if depth > maxDepth {
			return nil, errTooDeep
		}
		dnf, err := expandRule(r, depth)
		if err != nil {
			return nil, err
		}
		if out, err = and(out, dnf); err != nil {
			return nil, err
		}
	}
	return out, nil
}

func expandRule(r Rule, depth int) ([][]literal, error) {
	var own [][]literal
	switch r.Type {
	case "ALL":
		conj := []literal{}
		for _, c := range r.Conditions {
			conj = append(conj, literal{c: c})
		}
		own = [][]literal{conj}
	case "ANY":
		for _, c := range r.Conditions {
			own = append(own, []literal{{c: c}})
		}
		if len(own) == 0 {
			own = [][]literal{{}}
		}
	case "NONE":
		conj := []literal{}
		for _, c := range r.Conditions {
			conj = append(conj, literal{c: c, negate: true})
		}
		own = [][]literal{conj}
	default:
		return nil, fmt.Errorf("rule type %s is not supported", r.Type)
	}
	nested, err := expandAll(r.Rules, depth+1)
	if err != nil {
		return nil, err
	}
	return and(own, nested)
}

// and is the OR-of-AND form of a and b: each conjunction of a with each of
// b.
func and(a, b [][]literal) ([][]literal, error) {
	if len(a)*len(b) > maxRules {
		return nil, errTooDeep
	}
	out := make([][]literal, 0, len(a)*len(b))
	for _, x := range a {
		for _, y := range b {
			out = append(out, append(slices.Clip(x), y...))
		}
	}
	return out, nil
}

// convertCondition turns a condition into a clause; the trait identifier
// becomes the context key.
func convertCondition(l literal) (client.Clause, error) {
	k := l.c
	name := "a condition"
	if k.Property != "" {
		name = "the condition on " + k.Property
	}
	op, ok := operators[k.Operator]
	if !ok {
		return client.Clause{}, fmt.Errorf("%s uses %s, which is not supported", name, k.Operator)
	}
	raw := []string{k.Value}
	if k.Operator == "IN" {
		raw = nil
		for _, v := range strings.Split(k.Value, ",") {
			if v = strings.TrimSpace(v); v != "" {
				raw = append(raw, v)
			}
		}
		if len(raw) == 0 {
			return client.Clause{}, fmt.Errorf("%s has no values", name)
		}
	}
	values := make([]any, len(raw))
	for i, v := range raw {
		values[i] = v
		if numeric[k.Operator] {
			n, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
			if err != nil {
				return client.Clause{}, fmt.Errorf("%s compares with %q, which is not a number", name, v)
			}
			values[i] = scalar(n)
		}
	}
	attribute := k.Property
	if attribute == "identifier" {
		attribute = "key"
	}
	return client.Clause{Attribute: attribute, Operator: op, Values: values, Negate: l.negate != negated[k.Operator]}, nil
}

// override is a segment's override of a feature in one environment.
type override struct {
	segment string
	state   FeatureState
}

// feature is a feature with its state and segment overrides in each
// Flagsmith environment that has it.
type feature struct {
	name string
	envs map[string]*featureEnv
}

type featureEnv struct {
	state     *FeatureState
	overrides []override
}

func (c *converter) convertFlags() {
	features := map[string]*feature{}
	get := func(name, env string) *featureEnv {
		f, ok := features[name]
		if !ok {
			f = &feature{name: name, envs: map[string]*featureEnv{}}
			features[name] = f
		}
		if f.envs[env] == nil {
			f.envs[env] = &featureEnv{}
		}
		return f.envs[env]
	}
	for _, env := range c.envs {
		doc := c.e.Environments[env]
		for i, s := range doc.FeatureStates {
			get(s.Feature.Name, env).state = &doc.FeatureStates[i]
		}
		for _, seg := range doc.Project.Segments {
			for _, s := range seg.FeatureStates {
				fe := get(s.Feature.Name, env)
				fe.overrides = append(fe.overrides, override{segment: c.segKeys[seg.ID], state: s})
			}
		}
	}

	names := slices.Sorted(maps.Keys(features))
	used := map[string]bool{}
	for _, name := range names {
		c.flagKeys[name] = importers.NormalizeKey(name, used, c.r, "flag")
	}
	for _, name := range names {
		f := features[name]
		for _, fe := range f.envs {
			slices.SortStableFunc(fe.overrides, func(a, b override) int { return cmp.Compare(priority(a.state), priority(b.state)) })
		}
		fc := &flagConverter{converter: c, key: c.flagKeys[name], used: map[string]bool{}, envDefault: map[string]string{}}
		c.d.Flags[fc.key] = fc.flag(f)
	}
}

// priority is an override's priority; an override without one comes last.
func priority(s FeatureState) int {
	if s.FeatureSegment == nil {
		return math.MaxInt
	}
	return s.FeatureSegment.Priority
}

// reportIdentities adds a line for each identity with overrides.
func (c *converter) reportIdentities() {
	for _, env := range c.envs {
		for _, id := range c.e.Environments[env].IdentityOverrides {
			var flags []string
			for _, s := range id.IdentityFeatures {
				flags = append(flags, c.flagKeys[s.Feature.Name])
			}
			c.r.Add("identity", id.Identifier, c.envKeys[env], "identity overrides are not imported: %s", strings.Join(flags, ", "))
		}
	}
}

type flagConverter struct {
	*converter
	key string
	// typ is the flag's type; variations are its variations so far and
	// used their keys.
	typ        string
	variations []client.Variation
	used       map[string]bool
	// envDefault is the variation of each environment's control value.
	envDefault map[string]string
}

func (c *flagConverter) report(env, format string, args ...any) {
	c.r.Add("flag", c.key, env, format, args...)
}

// hasValue reports whether a feature state value is set.
func hasValue(v any) bool {
	return v != nil && v != ""
}

// states calls fn with each of f's states and overrides that the
// directory manages, in environment and priority order.
func (c *flagConverter) states(f *feature, fn func(env string, fe *featureEnv)) {
	for _, env := range c.envs {
		if fe := f.envs[env]; fe != nil && fe.state != nil {
			fn(env, fe)
		}
	}
}

func (c *flagConverter) flag(f *feature) flagfiles.Flag {
	out := flagfiles.Flag{
		Key:          c.key,
		Name:         f.name,
		Type:         "boolean",
		Tags:         []string{},
		Environments: map[string]flagfiles.FlagEnv{},
	}
	var values []any
	c.states(f, func(_ string, fe *featureEnv) {
		all := append([]FeatureState{*fe.state}, overrideStates(fe)...)
		for _, s := range all {
			if hasValue(s.Value) {
				values = append(values, s.Value)
			}
			for _, mv := range s.MultivariateValues {
				values = append(values, mv.Option.Value)
			}
		}
	})

	if len(values) == 0 {
		out.Variations = []client.Variation{{Key: "true", Value: true}, {Key: "false", Value: false}}
		out.OffVariation = "false"
		c.states(f, func(env string, fe *featureEnv) {
			out.Environments[c.envKeys[env]] = c.booleanConfig(env, fe)
		})
		return out
	}

	c.typ = c.typeOf(values)
	out.Type = c.typ
	c.states(f, func(env string, fe *featureEnv) { c.addDefault(env, fe.state.Value) })
	c.states(f, func(_ string, fe *featureEnv) {
		for _, mv := range fe.state.MultivariateValues {
			if c.findPrefix(mv.Option.Value, "option-") == "" {
				c.add(fmt.Sprintf("option-%d", c.countPrefix("option-")+1), mv.Option.Value)
			}
		}
	})
	c.states(f, func(_ string, fe *featureEnv) {
		for _, o := range fe.overrides {
			if c.find(o.state.Value) == "" {
				c.add("segment-"+o.segment, o.state.Value)
			}
		}
	})
	out.Variations = c.variations
	out.OffVariation = "default"
	c.states(f, func(env string, fe *featureEnv) {
		out.Environments[c.envKeys[env]] = c.typedConfig(env, fe)
	})
	return out
}

func overrideStates(fe *featureEnv) []FeatureState {
	out := make([]FeatureState, len(fe.overrides))
	for i, o := range fe.overrides {
		out[i] = o.state
	}
	return out
}

// booleanConfig is a boolean flag's config: enabled serves true, and each
// segment override serves true, or false when it disables the flag.
func (c *flagConverter) booleanConfig(env string, fe *featureEnv) flagfiles.FlagEnv {
	on := "true"
	out := flagfiles.FlagEnv{
		Enabled:       fe.state.Enabled,
		Targets:       []client.Target{},
		Rules:         []client.Rule{},
		Fallthrough:   client.Serve{Variation: &on},
		Prerequisites: []client.Prerequisite{},
	}
	for _, o := range fe.overrides {
		serve := "false"
		if o.state.Enabled {
			serve = "true"
		}
		out.Rules = append(out.Rules, segmentRule(o.segment, serve))
	}
	return out
}

// typedConfig is a typed flag's config: the fallthrough splits between
// the multivariate options and the control value, and each segment
// override serves its value.
func (c *flagConverter) typedConfig(env string, fe *featureEnv) flagfiles.FlagEnv {
	envKey := c.envKeys[env]
	def := c.envDefault[env]
	out := flagfiles.FlagEnv{
		Enabled:       fe.state.Enabled,
		Targets:       []client.Target{},
		Rules:         []client.Rule{},
		Fallthrough:   client.Serve{Variation: &def},
		Prerequisites: []client.Prerequisite{},
	}
	rollout := &client.Rollout{BucketBy: "key"}
	left := totalWeight
	for _, mv := range fe.state.MultivariateValues {
		w := int(math.Round(mv.PercentageAllocation * 1000))
		if w <= 0 {
			continue
		}
		rollout.Weights = append(rollout.Weights, client.Weight{Variation: c.findPrefix(mv.Option.Value, "option-"), Weight: w})
		left -= w
	}
	if len(rollout.Weights) > 0 {
		if left > 0 {
			rollout.Weights = append(rollout.Weights, client.Weight{Variation: def, Weight: left})
		}
		out.Fallthrough = client.Serve{Rollout: rollout}
	}
	for _, o := range fe.overrides {
		serve := c.find(o.state.Value)
		if !o.state.Enabled {
			c.r.Add("flag", c.key, envKey, "the override for segment %s disables the flag, which a rule cannot do: it serves %s", o.segment, serve)
		}
		if len(o.state.MultivariateValues) > 0 {
			c.r.Add("flag", c.key, envKey, "the override for segment %s splits between options, which is not imported: it serves %s", o.segment, serve)
		}
		out.Rules = append(out.Rules, segmentRule(o.segment, serve))
	}
	return out
}

func segmentRule(segment, variation string) client.Rule {
	return client.Rule{
		Description: "",
		Clauses:     []client.Clause{{Attribute: "key", Operator: "in_segment", Values: []any{segment}}},
		Variation:   &variation,
	}
}

// typeOf is the type of a flag's values: string, number or boolean when
// they all have it, and otherwise string, with a report line.
func (c *flagConverter) typeOf(values []any) string {
	typ := ""
	for _, v := range values {
		t := "string"
		switch v.(type) {
		case bool:
			t = "boolean"
		case float64, int64:
			t = "number"
		}
		if typ != "" && t != typ {
			c.report("", "has values of different types: they are imported as strings")
			return "string"
		}
		typ = t
	}
	return typ
}

// coerce converts a value to the flag's type; a value that is not set
// becomes the type's zero value.
func (c *flagConverter) coerce(v any) any {
	if !hasValue(v) {
		switch c.typ {
		case "number":
			return int64(0)
		case "boolean":
			return false
		}
		return ""
	}
	v = scalar(v)
	if c.typ == "string" {
		switch x := v.(type) {
		case string:
			return x
		case float64:
			return strconv.FormatFloat(x, 'f', -1, 64)
		default:
			return fmt.Sprint(x)
		}
	}
	return v
}

// addDefault records an environment's control value: default for the
// first, and default-<env> for one that differs from all before it.
func (c *flagConverter) addDefault(env string, raw any) {
	envKey := c.envKeys[env]
	if !hasValue(raw) && c.typ != "string" {
		c.report(envKey, "has no value: %v is used", c.coerce(raw))
	}
	if len(c.variations) == 0 {
		c.envDefault[env] = c.add("default", raw)
		return
	}
	key := c.findPrefix(raw, "default")
	if key == "" {
		key = c.add("default-"+envKey, raw)
	}
	c.envDefault[env] = key
	if key != "default" {
		c.report(envKey, "its value differs from the first environment's: %s serves it here, and the flag serves default when it is off", key)
	}
}

// add adds a variation with a unique key from base and returns the key.
func (c *flagConverter) add(base string, raw any) string {
	key := importers.Unique(base, c.used)
	c.variations = append(c.variations, client.Variation{Key: key, Value: c.coerce(raw)})
	return key
}

// find is the key of the first variation with the value, or "".
func (c *flagConverter) find(raw any) string {
	return c.findPrefix(raw, "")
}

// findPrefix is the key of the first variation whose key starts with
// prefix and that has the value, or "".
func (c *flagConverter) findPrefix(raw any, prefix string) string {
	v := c.coerce(raw)
	for _, vr := range c.variations {
		if strings.HasPrefix(vr.Key, prefix) && reflect.DeepEqual(vr.Value, v) {
			return vr.Key
		}
	}
	return ""
}

func (c *flagConverter) countPrefix(prefix string) int {
	n := 0
	for _, vr := range c.variations {
		if strings.HasPrefix(vr.Key, prefix) {
			n++
		}
	}
	return n
}

// scalar turns a whole JSON number into an int64, so that it is written as
// 1700000000000 rather than 1.7e+12.
func scalar(v any) any {
	if n, ok := v.(float64); ok && n == math.Trunc(n) && math.Abs(n) < 1<<53 {
		return int64(n)
	}
	return v
}
