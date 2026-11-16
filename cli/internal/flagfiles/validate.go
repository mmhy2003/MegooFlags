package flagfiles

import (
	"fmt"
	"maps"
	"math"
	"slices"
	"strings"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

// FlagTypes are the flag types, in the order the error message lists them.
var FlagTypes = []string{"boolean", "string", "number", "json"}

// totalWeight is what a rollout's weights add up to (100%).
const totalWeight = 100000

// Validate checks the references and values that the files must get right
// before anything is sent to the API, and returns every problem it finds,
// ordered by flag key. serverSegments are segment keys that exist on the
// server but not in the files. Each error reads
// "flags/<key>.yaml: <field path>: <message>".
func Validate(d *Dir, serverSegments []string) []error {
	envs := map[string]bool{}
	for _, e := range d.Project.Environments {
		envs[e.Key] = true
	}
	segments := map[string]bool{}
	for key := range d.Segments {
		segments[key] = true
	}
	for _, key := range serverSegments {
		segments[key] = true
	}

	v := &validator{d: d, envs: envs, segments: segments}
	keys := slices.Sorted(maps.Keys(d.Flags))
	for _, key := range keys {
		v.flag(key, d.Flags[key])
	}
	return v.errs
}

type validator struct {
	d        *Dir
	envs     map[string]bool
	segments map[string]bool
	errs     []error

	file       string
	variations map[string]bool
}

func (v *validator) errorf(field, format string, args ...any) {
	v.errs = append(v.errs, fmt.Errorf("%s: %s: %s", v.file, field, fmt.Sprintf(format, args...)))
}

func (v *validator) flag(key string, f Flag) {
	v.file = "flags/" + key + ".yaml"
	validType := slices.Contains(FlagTypes, f.Type)
	if !validType {
		v.errorf("type", "must be %s or %s, not %s",
			strings.Join(FlagTypes[:len(FlagTypes)-1], ", "), FlagTypes[len(FlagTypes)-1], f.Type)
	}

	v.variations = map[string]bool{}
	for i, vr := range f.Variations {
		if v.variations[vr.Key] {
			v.errorf(fmt.Sprintf("variations[%d].key", i), "duplicate variation %s", vr.Key)
		}
		v.variations[vr.Key] = true
		if validType && !valueHasType(vr.Value, f.Type) {
			v.errorf(fmt.Sprintf("variations[%d].value", i), "must be a %s", f.Type)
		}
	}
	if f.Type == "boolean" && !booleanVariations(f.Variations) {
		v.errorf("variations", `a boolean flag has exactly the variations "true": true and "false": false`)
	}
	v.variation("off_variation", f.OffVariation)

	envKeys := slices.Sorted(maps.Keys(f.Environments))
	for _, env := range envKeys {
		v.env("environments."+env, env, f.Environments[env])
	}
}

func (v *validator) env(field, key string, e FlagEnv) {
	if !v.envs[key] {
		v.errorf(field, "environment %s is not in %s", key, ProjectFileName)
	}
	for i, t := range e.Targets {
		v.variation(fmt.Sprintf("%s.targets[%d].variation", field, i), t.Variation)
	}
	for i, r := range e.Rules {
		rf := fmt.Sprintf("%s.rules[%d]", field, i)
		for j, c := range r.Clauses {
			v.clause(fmt.Sprintf("%s.clauses[%d]", rf, j), c)
		}
		v.serve(rf, client.Serve{Variation: r.Variation, Rollout: r.Rollout})
	}
	v.serve(field+".fallthrough", e.Fallthrough)
	for i, p := range e.Prerequisites {
		pf := fmt.Sprintf("%s.prerequisites[%d]", field, i)
		other, ok := v.d.Flags[p.Flag]
		if !ok {
			v.errorf(pf+".flag", "unknown flag %s", p.Flag)
			continue
		}
		if !slices.ContainsFunc(other.Variations, func(vr client.Variation) bool { return vr.Key == p.Variation }) {
			v.errorf(pf+".variation", "flag %s has no variation %s", p.Flag, p.Variation)
		}
	}
}

func (v *validator) clause(field string, c client.Clause) {
	if c.Operator != "in_segment" {
		return
	}
	for _, value := range c.Values {
		key, ok := value.(string)
		if !ok {
			v.errorf(field+".values", "in_segment values must be segment keys, not %v", value)
		} else if !v.segments[key] {
			v.errorf(field+".values", "unknown segment %s", key)
		}
	}
}

func (v *validator) serve(field string, s client.Serve) {
	if (s.Variation == nil) == (s.Rollout == nil) {
		v.errorf(field, "must serve either a variation or a rollout")
		return
	}
	if s.Variation != nil {
		v.variation(field+".variation", *s.Variation)
		return
	}
	sum := 0
	for i, w := range s.Rollout.Weights {
		v.variation(fmt.Sprintf("%s.rollout.weights[%d].variation", field, i), w.Variation)
		sum += w.Weight
	}
	if sum != totalWeight {
		v.errorf(field+".rollout.weights", "weights sum to %d, not %d", sum, totalWeight)
	}
}

func (v *validator) variation(field, key string) {
	if !v.variations[key] {
		v.errorf(field, "unknown variation %s", key)
	}
}

// booleanVariations reports whether vs are the two variations the API gives
// every boolean flag, in any order: "true" with value true and "false" with
// value false.
func booleanVariations(vs []client.Variation) bool {
	if len(vs) != 2 {
		return false
	}
	for _, vr := range vs {
		if b, ok := vr.Value.(bool); !ok || vr.Key != fmt.Sprint(b) {
			return false
		}
	}
	return vs[0].Key != vs[1].Key
}

// valueHasType reports whether a decoded YAML value is valid for the flag
// type, as the API checks it: json accepts anything, null included.
func valueHasType(value any, flagType string) bool {
	switch flagType {
	case "boolean":
		_, ok := value.(bool)
		return ok
	case "string":
		_, ok := value.(string)
		return ok
	case "number":
		switch n := value.(type) {
		case int, int64, uint64:
			return true
		case float64:
			return !math.IsInf(n, 0) && !math.IsNaN(n)
		}
		return false
	}
	return true
}
