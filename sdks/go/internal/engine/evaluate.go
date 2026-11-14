package engine

import "strings"

// MaxPrerequisiteDepth is the deepest prerequisite chain Evaluate follows; a
// deeper chain, or a cycle, is an error (evaluation.md §6).
const MaxPrerequisiteDepth = 32

// Reason is why a value was served.
type Reason string

const (
	ReasonStatic             Reason = "STATIC"
	ReasonDefault            Reason = "DEFAULT"
	ReasonTargetingMatch     Reason = "TARGETING_MATCH"
	ReasonSplit              Reason = "SPLIT"
	ReasonDisabled           Reason = "DISABLED"
	ReasonPrerequisiteFailed Reason = "PREREQUISITE_FAILED"
	ReasonError              Reason = "ERROR"
)

// ErrorCode is the OpenFeature error code of a Result with ReasonError.
type ErrorCode string

const (
	ErrNone           ErrorCode = ""
	ErrFlagNotFound   ErrorCode = "FLAG_NOT_FOUND"
	ErrTypeMismatch   ErrorCode = "TYPE_MISMATCH"
	ErrParse          ErrorCode = "PARSE_ERROR"
	ErrInvalidContext ErrorCode = "INVALID_CONTEXT"
	ErrGeneral        ErrorCode = "GENERAL"
)

// Result is the resolution of one flag. Served is false when the default was
// returned: on an error, or when the variation to serve is a null
// off_variation.
type Result struct {
	Value        any
	Variant      string
	Served       bool
	Reason       Reason
	ErrorCode    ErrorCode
	ErrorMessage string
	RuleIndex    int    // the matching rule, -1 when unset
	Prerequisite string // the failed prerequisite flag, "" when unset
	Int          int64  // copied from the served Variation
	IsInt        bool
}

// Metadata is the result's metadata as in evaluation.md: {"rule_index": n}
// and {"prerequisite": key} when set, {} otherwise. It is built on each call,
// so the evaluation itself does not allocate.
func (r Result) Metadata() map[string]any {
	m := map[string]any{}
	if r.RuleIndex >= 0 {
		m["rule_index"] = r.RuleIndex
	}
	if r.Prerequisite != "" {
		m["prerequisite"] = r.Prerequisite
	}
	return m
}

// Evaluate resolves flagKey for ctx, which must be a map[string]any. Errors
// return def with ReasonError. expectedType is "boolean", "string", "number"
// or "json", or "" for no type check; the check applies only to a served
// variation.
func Evaluate(rs *Ruleset, flagKey string, ctx any, def any, expectedType string) Result {
	context, ok := ctx.(map[string]any)
	if !ok {
		return errorResult(def, ErrInvalidContext, "context must be a JSON object")
	}
	flag := rs.Flags[flagKey]
	if flag == nil {
		return errorResult(def, ErrFlagNotFound, "flag '"+flagKey+"' not found")
	}
	res, ok := evalFlag(rs, flag, context, def, 0)
	if !ok {
		return errorResult(def, ErrGeneral, "prerequisite chain is too deep or has a cycle")
	}
	if expectedType != "" && res.Served && !hasType(res.Value, expectedType) {
		return errorResult(def, ErrTypeMismatch, "flag '"+flagKey+"' is not a "+expectedType)
	}
	return res
}

// evalFlag returns false when the prerequisite chain is deeper than
// MaxPrerequisiteDepth.
func evalFlag(rs *Ruleset, flag *Flag, ctx map[string]any, def any, depth int) (Result, bool) {
	if !flag.Enabled {
		return serveOff(flag, ReasonDisabled, def), true
	}
	for _, p := range flag.Prerequisites {
		met, ok := prerequisiteMet(rs, p, ctx, depth+1)
		if !ok {
			return Result{}, false
		}
		if !met {
			res := serveOff(flag, ReasonPrerequisiteFailed, def)
			res.Prerequisite = p.Flag
			return res, true
		}
	}
	if key, ok := ctx["key"].(string); ok {
		for _, t := range flag.Targets {
			if _, hit := t.Values[key]; hit {
				return serve(flag, t.Variation, ReasonTargetingMatch, def), true
			}
		}
	}
	for i := range flag.Rules {
		rule := &flag.Rules[i]
		if allClausesMatch(rs, rule.Clauses, ctx) {
			res := serveRule(flag, &rule.Serve, ctx, ReasonTargetingMatch, def)
			res.RuleIndex = i
			return res, true
		}
	}
	reason := ReasonDefault
	switch {
	case flag.Fallthrough.Rollout != nil:
		reason = ReasonSplit
	case len(flag.Targets) == 0 && len(flag.Rules) == 0 && len(flag.Prerequisites) == 0:
		reason = ReasonStatic
	}
	return serveRule(flag, &flag.Fallthrough, ctx, reason, def), true
}

// prerequisiteMet reports whether p's flag serves p.Variation; the second
// result is false when depth is over MaxPrerequisiteDepth.
func prerequisiteMet(rs *Ruleset, p Prerequisite, ctx map[string]any, depth int) (met, ok bool) {
	if depth > MaxPrerequisiteDepth {
		return false, false
	}
	pf := rs.Flags[p.Flag]
	if pf == nil || !pf.Enabled {
		return false, true
	}
	res, ok := evalFlag(rs, pf, ctx, nil, depth)
	if !ok {
		return false, false
	}
	return res.Reason != ReasonError && res.Served && res.Variant == p.Variation, true
}

func allClausesMatch(rs *Ruleset, clauses []Clause, ctx map[string]any) bool {
	for i := range clauses {
		if !clauseMatches(rs, &clauses[i], ctx) {
			return false
		}
	}
	return true
}

func clauseMatches(rs *Ruleset, c *Clause, ctx map[string]any) bool {
	if c.Operator == "in_segment" {
		hit := false
		for _, k := range c.segments {
			if inSegment(rs, rs.Segments[k], ctx) {
				hit = true
				break
			}
		}
		return hit != c.Negate
	}
	v := ctx[c.Attribute]
	if v == nil { // missing or null: false, negate not applied
		return false
	}
	hit := false
	if elems, isArray := elements(v); isArray {
		for _, e := range elems {
			if matchValue(c, e) {
				hit = true
				break
			}
		}
	} else {
		hit = matchValue(c, v)
	}
	return hit != c.Negate
}

func inSegment(rs *Ruleset, s *Segment, ctx map[string]any) bool {
	if s == nil {
		return false
	}
	if key, ok := ctx["key"].(string); ok {
		if _, hit := s.Excluded[key]; hit {
			return false
		}
		if _, hit := s.Included[key]; hit {
			return true
		}
	}
	for _, clauses := range s.Rules {
		if allClausesMatch(rs, clauses, ctx) {
			return true
		}
	}
	return false
}

// matchValue reports whether one element of the attribute matches the
// clause's prepared values (operators.py match_value).
func matchValue(c *Clause, e any) bool {
	switch c.Operator {
	case "in":
		for _, p := range c.equal {
			if jsonEqual(e, p) {
				return true
			}
		}
		return false
	case "not_in":
		if !isScalar(e) {
			return false
		}
		for _, p := range c.equal {
			if jsonEqual(e, p) {
				return false
			}
		}
		return true
	case "contains", "starts_with", "ends_with":
		s, ok := e.(string)
		if !ok {
			return false
		}
		for _, p := range c.strs {
			var hit bool
			switch c.Operator {
			case "contains":
				hit = strings.Contains(s, p)
			case "starts_with":
				hit = strings.HasPrefix(s, p)
			default:
				hit = strings.HasSuffix(s, p)
			}
			if hit {
				return true
			}
		}
		return false
	case "matches":
		s, ok := e.(string)
		if !ok {
			return false
		}
		for _, re := range c.regexes {
			if re.MatchString(s) {
				return true
			}
		}
		return false
	case "lt", "lte", "gt", "gte":
		n, ok := toNumber(e)
		if !ok {
			return false
		}
		for _, p := range c.nums {
			d := n.cmp(p)
			var hit bool
			switch c.Operator {
			case "lt":
				hit = d < 0
			case "lte":
				hit = d <= 0
			case "gt":
				hit = d > 0
			default:
				hit = d >= 0
			}
			if hit {
				return true
			}
		}
		return false
	case "semver_eq", "semver_lt", "semver_gt":
		s, ok := e.(string)
		if !ok {
			return false
		}
		sv, ok := ParseSemver(s)
		if !ok {
			return false
		}
		want := 0
		switch c.Operator {
		case "semver_lt":
			want = -1
		case "semver_gt":
			want = 1
		}
		for _, p := range c.semvers {
			if CompareSemver(sv, p) == want {
				return true
			}
		}
		return false
	case "before", "after":
		t, ok := toMillis(e)
		if !ok {
			return false
		}
		for _, p := range c.times {
			if (c.Operator == "before" && t < p) || (c.Operator == "after" && t > p) {
				return true
			}
		}
		return false
	}
	return false // in_segment is handled by clauseMatches
}

// hasType reports whether a served value has the OpenFeature type asked for.
func hasType(v any, expectedType string) bool {
	switch expectedType {
	case "boolean":
		_, ok := v.(bool)
		return ok
	case "string":
		_, ok := v.(string)
		return ok
	case "number":
		_, ok := toNumber(v) // a bool is not a number
		return ok
	}
	return true // json accepts anything
}

func serveOff(flag *Flag, reason Reason, def any) Result {
	if !flag.HasOffVariation {
		return Result{Value: def, Reason: reason, RuleIndex: -1}
	}
	return serve(flag, flag.OffVariation, reason, def)
}

func serve(flag *Flag, variation string, reason Reason, def any) Result {
	v := flag.Variations[variation]
	if v == nil { // Parse guarantees known variations; this keeps a bad Ruleset from panicking
		return Result{Value: def, Reason: reason, RuleIndex: -1}
	}
	return Result{
		Value: v.Value, Variant: variation, Served: true, Reason: reason,
		RuleIndex: -1, Int: v.Int, IsInt: v.IsInt,
	}
}

func serveRule(flag *Flag, s *Serve, ctx map[string]any, reason Reason, def any) Result {
	if s.Rollout == nil {
		return serve(flag, s.Variation, reason, def)
	}
	bucket := 0
	if text, ok := BucketText(ctx[s.Rollout.BucketBy]); ok {
		bucket = Bucket(flag.Key, text)
	}
	return serve(flag, pickVariation(s.Rollout.Weights, bucket), reason, def)
}

func errorResult(def any, code ErrorCode, msg string) Result {
	return Result{Value: def, Reason: ReasonError, ErrorCode: code, ErrorMessage: msg, RuleIndex: -1}
}
