package engine

// Ruleset is a parsed, validated environment ruleset (spec/ruleset.schema.json).
// It is read-only once Parse returns it, so it is safe for concurrent use.
type Ruleset struct {
	Project, Environment string
	Flags                map[string]*Flag
	Segments             map[string]*Segment
}

// Flag is one flag of a ruleset.
type Flag struct {
	Key, Type       string
	Enabled         bool
	Version         int64
	Variations      map[string]*Variation
	OffVariation    string
	HasOffVariation bool // false when off_variation is null
	Prerequisites   []Prerequisite
	Targets         []Target
	Rules           []Rule
	Fallthrough     Serve
}

// Variation is one possible value of a flag. Value is plain JSON (numbers are
// float64); Int and IsInt keep the exact value of an integral number that
// fits int64.
type Variation struct {
	Key   string
	Value any
	Int   int64
	IsInt bool
}

// Prerequisite requires flag Flag to serve variation Variation.
type Prerequisite struct {
	Flag, Variation string
}

// Target serves Variation to the context keys in Values.
type Target struct {
	Variation string
	Values    map[string]struct{}
}

// Rule serves Serve when all of its clauses match.
type Rule struct {
	Description string
	Clauses     []Clause
	Serve       Serve
}

// Serve is either a fixed Variation or a Rollout (exactly one is set).
type Serve struct {
	Variation string
	Rollout   *Rollout
}

// Rollout splits contexts by the bucket of attribute BucketBy.
type Rollout struct {
	BucketBy string
	Weights  []Weight
}

// Segment matches a context that is included, or that is not excluded and
// matches any rule (each rule is a list of clauses that must all match).
type Segment struct {
	Included, Excluded map[string]struct{}
	Rules              [][]Clause
}

// Clause tests one context attribute with an operator.
type Clause struct {
	Attribute, Operator string
	Negate              bool
	Values              []any // scalars; numbers are int64 when integral and in range, else float64

	// Prepared values for the operator. Unusable values are left out, which
	// is the same as "never matches" (evaluation.md §11).
	equal    []any    // in, not_in
	strs     []string // contains, starts_with, ends_with
	nums     []number // lt, lte, gt, gte
	regexes  []*Regex // matches
	semvers  []Semver // semver_eq, semver_lt, semver_gt
	times    []int64  // before, after (epoch milliseconds)
	segments []string // in_segment
}

// ParseError is an invalid ruleset, with the JSON path of the first problem.
type ParseError struct {
	Path, Msg string
}

func (e *ParseError) Error() string { return "PARSE_ERROR: " + e.Path + ": " + e.Msg }
