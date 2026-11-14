package engine

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"slices"
	"strconv"
	"strings"
)

// schemaVersion is the only ruleset schema_version this engine accepts.
const schemaVersion = 1

var flagTypes = []string{"boolean", "json", "number", "string"}

var operators = map[string]bool{
	"in": true, "not_in": true, "contains": true, "starts_with": true, "ends_with": true,
	"matches": true, "lt": true, "lte": true, "gt": true, "gte": true,
	"semver_eq": true, "semver_lt": true, "semver_gt": true,
	"before": true, "after": true, "in_segment": true,
}

// Parse validates a ruleset as in evaluation.md §11 and prepares its clause
// values. Unknown fields are ignored. Prerequisite flags and segment keys are
// not checked: a missing flag fails the prerequisite and a missing segment is
// never matched. An invalid ruleset returns a *ParseError naming the JSON path.
func Parse(data []byte) (rs *Ruleset, err error) {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var root any
	if err := dec.Decode(&root); err != nil {
		return nil, &ParseError{Path: "ruleset", Msg: "invalid JSON: " + err.Error()}
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, &ParseError{Path: "ruleset", Msg: "invalid JSON: data after the ruleset"}
	}
	defer func() {
		if r := recover(); r != nil {
			pe, ok := r.(*ParseError)
			if !ok {
				panic(r)
			}
			rs, err = nil, pe
		}
	}()
	return parseRuleset(root), nil
}

// fail aborts parsing; Parse recovers the *ParseError.
func fail(path, format string, args ...any) {
	panic(&ParseError{Path: path, Msg: fmt.Sprintf(format, args...)})
}

func parseRuleset(data any) *Ruleset {
	root := asObject(data, "ruleset")
	if n, ok := asInt(get(root, "schema_version", ""), "schema_version"); !ok || n != schemaVersion {
		fail("schema_version", "must be %d", schemaVersion)
	}
	rs := &Ruleset{
		Project:     asString(get(root, "project", ""), "project"),
		Environment: asString(get(root, "environment", ""), "environment"),
	}
	flags := asObject(get(root, "flags", ""), "flags")
	segments := asObject(get(root, "segments", ""), "segments")
	rs.Flags = make(map[string]*Flag, len(flags))
	for _, key := range sortedKeys(flags) {
		rs.Flags[key] = parseFlag(key, flags[key], "flags."+key)
	}
	rs.Segments = make(map[string]*Segment, len(segments))
	for _, key := range sortedKeys(segments) {
		rs.Segments[key] = parseSegment(segments[key], "segments."+key)
	}
	return rs
}

func parseFlag(key string, value any, path string) *Flag {
	obj := asObject(value, path)
	if k, ok := get(obj, "key", path).(string); !ok || k != key {
		fail(path+".key", "must equal '%s'", key)
	}
	f := &Flag{Key: key, Type: asString(get(obj, "type", path), path+".type")}
	if !slices.Contains(flagTypes, f.Type) {
		fail(path+".type", "must be one of %v", flagTypes)
	}
	f.Variations = parseVariations(get(obj, "variations", path), path+".variations")
	if off := get(obj, "off_variation", path); off != nil {
		f.OffVariation = known(off, f.Variations, path+".off_variation")
		f.HasOffVariation = true
	}
	prerequisites := asList(get(obj, "prerequisites", path), path+".prerequisites", false)
	targets := asList(get(obj, "targets", path), path+".targets", false)
	rules := asList(get(obj, "rules", path), path+".rules", false)
	f.Enabled = asBool(get(obj, "enabled", path), path+".enabled")
	version, ok := asInt(get(obj, "version", path), path+".version")
	if !ok {
		fail(path+".version", "must be an integer within the int64 range")
	}
	f.Version = version

	f.Prerequisites = make([]Prerequisite, len(prerequisites))
	for i, p := range prerequisites {
		itemPath := fmt.Sprintf("%s.prerequisites[%d]", path, i)
		p := asObject(p, itemPath)
		f.Prerequisites[i] = Prerequisite{
			Flag:      asString(get(p, "flag", itemPath), itemPath+".flag"),
			Variation: asString(get(p, "variation", itemPath), itemPath+".variation"),
		}
	}
	f.Targets = make([]Target, len(targets))
	for i, t := range targets {
		itemPath := fmt.Sprintf("%s.targets[%d]", path, i)
		t := asObject(t, itemPath)
		f.Targets[i] = Target{
			Variation: known(get(t, "variation", itemPath), f.Variations, itemPath+".variation"),
			Values:    stringSet(get(t, "values", itemPath), itemPath+".values"),
		}
	}
	f.Rules = make([]Rule, len(rules))
	for i, r := range rules {
		f.Rules[i] = parseRule(r, f.Variations, fmt.Sprintf("%s.rules[%d]", path, i))
	}
	f.Fallthrough = parseServe(get(obj, "fallthrough", path), f.Variations, path+".fallthrough")
	return f
}

func parseVariations(value any, path string) map[string]*Variation {
	items := asList(value, path, true)
	variations := make(map[string]*Variation, len(items))
	for i, item := range items {
		itemPath := fmt.Sprintf("%s[%d]", path, i)
		obj := asObject(item, itemPath)
		key := asString(get(obj, "key", itemPath), itemPath+".key")
		if _, dup := variations[key]; dup {
			fail(itemPath+".key", "duplicate variation '%s'", key)
		}
		raw := get(obj, "value", itemPath)
		v := &Variation{Key: key, Value: plain(raw)}
		v.Int, v.IsInt = integral(raw)
		variations[key] = v
	}
	return variations
}

// integral is the exact int64 of a JSON number that is an integer: an integer
// literal that fits int64, or a float literal with an integral value in range.
func integral(raw any) (int64, bool) {
	n, ok := raw.(json.Number)
	if !ok {
		return 0, false
	}
	if i, err := strconv.ParseInt(string(n), 10, 64); err == nil {
		return i, true
	}
	f, ok := parseFloat(string(n))
	if !ok || f != math.Trunc(f) || f < -(1<<63) || f >= 1<<63 {
		return 0, false
	}
	return int64(f), true
}

func parseRule(value any, variations map[string]*Variation, path string) Rule {
	obj := asObject(value, path)
	r := Rule{}
	if d, present := obj["description"]; present {
		r.Description = asString(d, path+".description")
	}
	r.Clauses = parseClauses(get(obj, "clauses", path), path+".clauses", false)
	r.Serve = parseServe(get(obj, "serve", path), variations, path+".serve")
	return r
}

func parseServe(value any, variations map[string]*Variation, path string) Serve {
	obj := asObject(value, path)
	variation, hasVariation := obj["variation"]
	rollout, hasRollout := obj["rollout"]
	if hasVariation == hasRollout {
		fail(path, "must have exactly one of variation or rollout")
	}
	if hasVariation {
		return Serve{Variation: known(variation, variations, path+".variation")}
	}
	return Serve{Rollout: parseRollout(rollout, variations, path+".rollout")}
}

func parseRollout(value any, variations map[string]*Variation, path string) *Rollout {
	obj := asObject(value, path)
	r := &Rollout{BucketBy: asString(get(obj, "bucket_by", path), path+".bucket_by")}
	weightsPath := path + ".weights"
	items := asList(get(obj, "weights", path), weightsPath, true)
	r.Weights = make([]Weight, len(items))
	sum := 0
	for i, item := range items {
		itemPath := fmt.Sprintf("%s[%d]", weightsPath, i)
		w := asObject(item, itemPath)
		variation := known(get(w, "variation", itemPath), variations, itemPath+".variation")
		weight, ok := asInt(get(w, "weight", itemPath), itemPath+".weight")
		if !ok || weight < 0 || weight > Buckets {
			fail(itemPath+".weight", "must be between 0 and %d", Buckets)
		}
		r.Weights[i] = Weight{Variation: variation, Weight: int(weight)}
		sum += int(weight)
	}
	if sum != Buckets {
		fail(weightsPath, "must add up to %d", Buckets)
	}
	return r
}

func parseClauses(value any, path string, inSegment bool) []Clause {
	items := asList(value, path, true)
	clauses := make([]Clause, len(items))
	for i, item := range items {
		clauses[i] = parseClause(item, fmt.Sprintf("%s[%d]", path, i), inSegment)
	}
	return clauses
}

func parseClause(value any, path string, inSegment bool) Clause {
	obj := asObject(value, path)
	c := Clause{
		Attribute: asString(get(obj, "attribute", path), path+".attribute"),
		Operator:  asString(get(obj, "operator", path), path+".operator"),
	}
	if !operators[c.Operator] {
		fail(path+".operator", "unknown operator %q", c.Operator)
	}
	if inSegment && c.Operator == "in_segment" {
		fail(path+".operator", "in_segment is not allowed in a segment rule")
	}
	valuesPath := path + ".values"
	raw := asList(get(obj, "values", path), valuesPath, true)
	for i, v := range raw {
		if !isScalar(v) {
			fail(fmt.Sprintf("%s[%d]", valuesPath, i), "must be a string, number or boolean")
		}
	}
	if n, present := obj["negate"]; present {
		c.Negate = asBool(n, path+".negate")
	}
	c.Values = make([]any, len(raw))
	for i, v := range raw {
		c.Values[i] = normalise(v)
	}
	c.prepare(raw)
	return c
}

// normalise turns a json.Number into int64 when it is an integer literal that
// fits, and into float64 otherwise. Other scalars are returned as they are.
func normalise(v any) any {
	n, ok := v.(json.Number)
	if !ok {
		return v
	}
	if i, err := strconv.ParseInt(string(n), 10, 64); err == nil {
		return i
	}
	f, _ := parseFloat(string(n))
	return f
}

// prepare fills the prepared values for c.Operator from the raw clause values,
// leaving out the values the operator cannot use.
func (c *Clause) prepare(raw []any) {
	switch c.Operator {
	case "in", "not_in":
		c.equal = c.Values
	case "contains", "starts_with", "ends_with":
		for _, v := range raw {
			if s, ok := v.(string); ok {
				c.strs = append(c.strs, s)
			}
		}
	case "matches":
		for _, v := range raw {
			if s, ok := v.(string); ok {
				if re, err := CompilePortable(s); err == nil {
					c.regexes = append(c.regexes, re)
				}
			}
		}
	case "lt", "lte", "gt", "gte":
		for _, v := range raw {
			if n, ok := toNumber(v); ok {
				c.nums = append(c.nums, n)
			}
		}
	case "semver_eq", "semver_lt", "semver_gt":
		for _, v := range raw {
			if s, ok := v.(string); ok {
				if sv, ok := ParseSemver(s); ok {
					c.semvers = append(c.semvers, sv)
				}
			}
		}
	case "before", "after":
		// Only a string is a date here; a number value never matches.
		for _, v := range raw {
			if s, ok := v.(string); ok {
				if ms, ok := ParseRFC3339Millis(s); ok {
					c.times = append(c.times, ms)
				}
			}
		}
	case "in_segment":
		for _, v := range raw {
			if s, ok := v.(string); ok {
				c.segments = append(c.segments, s)
			}
		}
	}
}

func parseSegment(value any, path string) *Segment {
	obj := asObject(value, path)
	rulesPath := path + ".rules"
	rules := asList(get(obj, "rules", path), rulesPath, false)
	s := &Segment{
		Included: stringSet(get(obj, "included", path), path+".included"),
		Excluded: stringSet(get(obj, "excluded", path), path+".excluded"),
		Rules:    make([][]Clause, len(rules)),
	}
	for i, r := range rules {
		rulePath := fmt.Sprintf("%s[%d]", rulesPath, i)
		rule := asObject(r, rulePath)
		s.Rules[i] = parseClauses(get(rule, "clauses", rulePath), rulePath+".clauses", true)
	}
	return s
}

// get returns obj[name], failing with "<path>.<name>: is required" when absent.
func get(obj map[string]any, name, path string) any {
	v, ok := obj[name]
	if !ok {
		if path != "" {
			name = path + "." + name
		}
		fail(name, "is required")
	}
	return v
}

func asObject(v any, path string) map[string]any {
	obj, ok := v.(map[string]any)
	if !ok {
		fail(path, "must be an object")
	}
	return obj
}

func asList(v any, path string, nonEmpty bool) []any {
	list, ok := v.([]any)
	if !ok {
		fail(path, "must be an array")
	}
	if nonEmpty && len(list) == 0 {
		fail(path, "must not be empty")
	}
	return list
}

func asString(v any, path string) string {
	s, ok := v.(string)
	if !ok {
		fail(path, "must be a string")
	}
	return s
}

func asBool(v any, path string) bool {
	b, ok := v.(bool)
	if !ok {
		fail(path, "must be a boolean")
	}
	return b
}

// asInt requires an integer literal (no '.', 'e' or 'E'). The bool is false
// when the integer does not fit int64.
func asInt(v any, path string) (int64, bool) {
	n, ok := v.(json.Number)
	if !ok || strings.ContainsAny(string(n), ".eE") {
		fail(path, "must be an integer")
	}
	i, err := strconv.ParseInt(string(n), 10, 64)
	return i, err == nil
}

func stringSet(v any, path string) map[string]struct{} {
	items := asList(v, path, false)
	set := make(map[string]struct{}, len(items))
	for i, item := range items {
		set[asString(item, fmt.Sprintf("%s[%d]", path, i))] = struct{}{}
	}
	return set
}

func known(v any, variations map[string]*Variation, path string) string {
	key := asString(v, path)
	if _, ok := variations[key]; !ok {
		fail(path, "unknown variation '%s'", key)
	}
	return key
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}
