package client

import "encoding/json"

// Timestamps are kept as the API's ISO 8601 strings.

// Project is a project. Environments is empty in list results.
type Project struct {
	Key            string        `json:"key"`
	Name           string        `json:"name"`
	Description    string        `json:"description"`
	StaleAfterDays int           `json:"stale_after_days"`
	Environments   []Environment `json:"environments,omitempty"`
	CreatedAt      string        `json:"created_at"`
	UpdatedAt      string        `json:"updated_at"`
}

// ProjectCreate creates a project. Without Environments the API creates its
// default environments.
type ProjectCreate struct {
	Key          string              `json:"key"`
	Name         string              `json:"name"`
	Description  string              `json:"description,omitempty"`
	Environments []EnvironmentCreate `json:"environments,omitempty"`
}

// ProjectUpdate changes the fields that are not nil.
type ProjectUpdate struct {
	Name           *string `json:"name,omitempty"`
	Description    *string `json:"description,omitempty"`
	StaleAfterDays *int    `json:"stale_after_days,omitempty"`
}

// Environment is an environment of a project.
type Environment struct {
	Key               string `json:"key"`
	Name              string `json:"name"`
	Description       string `json:"description"`
	SortOrder         int    `json:"sort_order"`
	ApprovalsRequired int    `json:"approvals_required"`
	CreatedAt         string `json:"created_at"`
	UpdatedAt         string `json:"updated_at"`
}

// EnvironmentCreate creates an environment.
type EnvironmentCreate struct {
	Key         string `json:"key"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
	SortOrder   *int   `json:"sort_order,omitempty"`
}

// EnvironmentUpdate changes the fields that are not nil.
type EnvironmentUpdate struct {
	Name              *string `json:"name,omitempty"`
	Description       *string `json:"description,omitempty"`
	SortOrder         *int    `json:"sort_order,omitempty"`
	ApprovalsRequired *int    `json:"approvals_required,omitempty"`
}

// Segment is a segment. Included, Excluded and Rules are empty in list
// results.
type Segment struct {
	Key         string        `json:"key"`
	Name        string        `json:"name"`
	Description string        `json:"description"`
	Included    []string      `json:"included,omitempty"`
	Excluded    []string      `json:"excluded,omitempty"`
	Rules       []SegmentRule `json:"rules,omitempty"`
	CreatedAt   string        `json:"created_at"`
	UpdatedAt   string        `json:"updated_at"`
}

// SegmentCreate creates an empty segment.
type SegmentCreate struct {
	Key         string `json:"key"`
	Name        string `json:"name"`
	Description string `json:"description,omitempty"`
}

// SegmentUpdate changes the details that are not nil.
type SegmentUpdate struct {
	Name        *string `json:"name,omitempty"`
	Description *string `json:"description,omitempty"`
}

// SegmentConfig replaces a segment's whole config; an empty list clears it.
type SegmentConfig struct {
	Included []string      `json:"included,omitempty" yaml:"included,omitempty"`
	Excluded []string      `json:"excluded,omitempty" yaml:"excluded,omitempty"`
	Rules    []SegmentRule `json:"rules,omitempty" yaml:"rules,omitempty"`
}

// SegmentRule matches a context when all of its clauses match.
type SegmentRule struct {
	Description string   `json:"description" yaml:"description"`
	Clauses     []Clause `json:"clauses" yaml:"clauses"`
}

// FlagEnvironmentState is a flag's state in one environment.
type FlagEnvironmentState struct {
	Environment string `json:"environment"`
	Enabled     bool   `json:"enabled"`
	Version     int    `json:"version"`
	UpdatedAt   string `json:"updated_at"`
}

// FlagSummary is a flag as listed.
type FlagSummary struct {
	Key             string                 `json:"key"`
	Name            string                 `json:"name"`
	Description     string                 `json:"description"`
	Type            string                 `json:"type"`
	Tags            []string               `json:"tags"`
	Owner           *string                `json:"owner"`
	Archived        bool                   `json:"archived"`
	ArchivedAt      *string                `json:"archived_at"`
	Lifecycle       string                 `json:"lifecycle"`
	LastEvaluatedAt *string                `json:"last_evaluated_at"`
	CreatedAt       string                 `json:"created_at"`
	UpdatedAt       string                 `json:"updated_at"`
	Environments    []FlagEnvironmentState `json:"environments"`
}

// Flag is a flag with its variations.
type Flag struct {
	FlagSummary
	Variations   []Variation `json:"variations"`
	OffVariation string      `json:"off_variation"`
}

// FlagCreate creates a flag. Without Variations the API creates the
// defaults for the type.
type FlagCreate struct {
	Key          string        `json:"key"`
	Name         string        `json:"name"`
	Description  string        `json:"description,omitempty"`
	Type         string        `json:"type"`
	Tags         []string      `json:"tags,omitempty"`
	Owner        *string       `json:"owner,omitempty"`
	Variations   []VariationIn `json:"variations,omitempty"`
	OffVariation *string       `json:"off_variation,omitempty"`
}

// FlagUpdate changes the fields that are not nil. ClearOwner sends
// "owner": null, which removes the owner; it wins over Owner.
type FlagUpdate struct {
	Name         *string
	Description  *string
	Tags         *[]string
	Owner        *string
	ClearOwner   bool
	OffVariation *string
}

// MarshalJSON encodes the set fields, and owner as null when ClearOwner is
// set.
func (u FlagUpdate) MarshalJSON() ([]byte, error) {
	m := map[string]any{}
	if u.Name != nil {
		m["name"] = *u.Name
	}
	if u.Description != nil {
		m["description"] = *u.Description
	}
	if u.Tags != nil {
		tags := *u.Tags
		if tags == nil {
			tags = []string{}
		}
		m["tags"] = tags
	}
	if u.ClearOwner {
		m["owner"] = nil
	} else if u.Owner != nil {
		m["owner"] = *u.Owner
	}
	if u.OffVariation != nil {
		m["off_variation"] = *u.OffVariation
	}
	return json.Marshal(m)
}

// Variation is one value a flag can serve.
type Variation struct {
	Key         string `json:"key" yaml:"key"`
	Name        string `json:"name" yaml:"name"`
	Description string `json:"description" yaml:"description"`
	Value       any    `json:"value" yaml:"value"`
}

// VariationIn creates a variation.
type VariationIn struct {
	Key         string `json:"key"`
	Name        string `json:"name,omitempty"`
	Description string `json:"description,omitempty"`
	Value       any    `json:"value"`
}

// VariationUpdate changes the fields that are not nil. Value points to the
// new value, so a pointer to nil sets the value to JSON null.
type VariationUpdate struct {
	Name        *string `json:"name,omitempty"`
	Description *string `json:"description,omitempty"`
	Value       *any    `json:"value,omitempty"`
}

// FlagConfig is a flag's targeting in one environment.
type FlagConfig struct {
	Environment   string         `json:"environment"`
	Enabled       bool           `json:"enabled"`
	Version       int            `json:"version"`
	Targets       []Target       `json:"targets"`
	Rules         []Rule         `json:"rules"`
	Fallthrough   Serve          `json:"fallthrough"`
	Prerequisites []Prerequisite `json:"prerequisites"`
	UpdatedAt     string         `json:"updated_at"`
}

// FlagConfigIn replaces a flag's targeting in one environment. Version is
// the version it is based on; the API answers version_conflict when it has
// moved on.
type FlagConfigIn struct {
	Enabled       bool           `json:"enabled"`
	Version       int            `json:"version"`
	Targets       []Target       `json:"targets,omitempty"`
	Rules         []Rule         `json:"rules,omitempty"`
	Fallthrough   Serve          `json:"fallthrough"`
	Prerequisites []Prerequisite `json:"prerequisites,omitempty"`
}

// Target serves a variation to the listed context keys.
type Target struct {
	Variation string   `json:"variation" yaml:"variation"`
	Values    []string `json:"values" yaml:"values"`
}

// Rule serves a variation or a rollout when all of its clauses match.
type Rule struct {
	Description string   `json:"description" yaml:"description"`
	Clauses     []Clause `json:"clauses" yaml:"clauses"`
	Variation   *string  `json:"variation,omitempty" yaml:"variation,omitempty"`
	Rollout     *Rollout `json:"rollout,omitempty" yaml:"rollout,omitempty"`
}

// Clause compares a context attribute with values.
type Clause struct {
	Attribute string `json:"attribute" yaml:"attribute"`
	Operator  string `json:"operator" yaml:"operator"`
	Values    []any  `json:"values" yaml:"values"`
	Negate    bool   `json:"negate" yaml:"negate"`
}

// Serve is either a fixed variation or a rollout.
type Serve struct {
	Variation *string  `json:"variation,omitempty" yaml:"variation,omitempty"`
	Rollout   *Rollout `json:"rollout,omitempty" yaml:"rollout,omitempty"`
}

// Rollout splits contexts between variations by the hash of BucketBy.
type Rollout struct {
	BucketBy string   `json:"bucket_by,omitempty" yaml:"bucket_by,omitempty"`
	Weights  []Weight `json:"weights" yaml:"weights"`
}

// Weight is a variation's share of a rollout in thousandths of a percent
// (100000 in total).
type Weight struct {
	Variation string `json:"variation" yaml:"variation"`
	Weight    int    `json:"weight" yaml:"weight"`
}

// Prerequisite requires another flag to serve a variation first.
type Prerequisite struct {
	Flag      string `json:"flag" yaml:"flag"`
	Variation string `json:"variation" yaml:"variation"`
}

// CopyIn copies a flag's config from one environment to others.
type CopyIn struct {
	From           string   `json:"from"`
	To             []string `json:"to"`
	IncludeEnabled bool     `json:"include_enabled,omitempty"`
}

// ChangeRequestCreate proposes a change in an environment that requires
// approvals. Kind is config, toggle, schedule or rollout.
type ChangeRequestCreate struct {
	Title       string         `json:"title"`
	Description string         `json:"description,omitempty"`
	Flag        string         `json:"flag"`
	Environment string         `json:"environment"`
	Kind        string         `json:"kind"`
	Payload     map[string]any `json:"payload"`
}

// ChangeRequest is a proposed change. Comments and reviews are not decoded.
type ChangeRequest struct {
	ID                string         `json:"id"`
	Title             string         `json:"title"`
	Description       string         `json:"description"`
	Flag              string         `json:"flag"`
	Environment       string         `json:"environment"`
	Kind              string         `json:"kind"`
	Status            string         `json:"status"`
	Author            string         `json:"author"`
	Approvals         int            `json:"approvals"`
	ApprovalsRequired int            `json:"approvals_required"`
	BaseVersion       int            `json:"base_version"`
	Payload           map[string]any `json:"payload"`
	Proposed          *FlagConfigIn  `json:"proposed"`
	Current           *FlagConfig    `json:"current"`
	Error             *string        `json:"error"`
	CreatedAt         string         `json:"created_at"`
	ClosedAt          *string        `json:"closed_at"`
}

// EvaluateIn evaluates Flags, or every flag when it is empty, for Context.
type EvaluateIn struct {
	Context map[string]any `json:"context"`
	Flags   []string       `json:"flags,omitempty"`
}

// EvaluateOut holds one result per flag.
type EvaluateOut struct {
	Results []EvaluationResult `json:"results"`
}

// EvaluationResult is the evaluation of one flag.
type EvaluationResult struct {
	Key          string         `json:"key"`
	Value        any            `json:"value"`
	Variant      *string        `json:"variant"`
	Reason       string         `json:"reason"`
	ErrorCode    *string        `json:"error_code"`
	ErrorMessage *string        `json:"error_message"`
	Metadata     map[string]any `json:"metadata"`
}

// SdkKey is an SDK key without its secret.
type SdkKey struct {
	ID            string  `json:"id"`
	Name          string  `json:"name"`
	Kind          string  `json:"kind"`
	Prefix        string  `json:"prefix"`
	CreatedAt     string  `json:"created_at"`
	ExpiresAt     *string `json:"expires_at"`
	RevokedAt     *string `json:"revoked_at"`
	RotatedFromID *string `json:"rotated_from_id"`
}

// SdkKeyCreate creates an SDK key. Kind is server or client.
type SdkKeyCreate struct {
	Name string `json:"name"`
	Kind string `json:"kind"`
}

// SdkKeyCreated is a new SDK key; Key is shown only once.
type SdkKeyCreated struct {
	ID            string  `json:"id"`
	Name          string  `json:"name"`
	Kind          string  `json:"kind"`
	Prefix        string  `json:"prefix"`
	Key           string  `json:"key"`
	CreatedAt     string  `json:"created_at"`
	ExpiresAt     *string `json:"expires_at"`
	RotatedFromID *string `json:"rotated_from_id"`
}

// Me is the caller. Kind is user, service_token or personal_token.
type Me struct {
	ID         string  `json:"id"`
	Kind       string  `json:"kind"`
	Label      string  `json:"label"`
	Name       *string `json:"name"`
	Email      *string `json:"email"`
	AuthMethod string  `json:"auth_method"`
	RequireMFA bool    `json:"require_mfa"`
}
