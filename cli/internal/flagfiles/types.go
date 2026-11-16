// Package flagfiles reads, checks and writes the flags-as-code directory:
// megooflags.yaml for the project and its environments, segments/<key>.yaml
// and flags/<key>.yaml. A file's name without .yaml is the resource's key.
package flagfiles

import (
	"regexp"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

// APIVersion is the only apiVersion megooflags.yaml may have.
const APIVersion = "megooflags/v1"

// ProjectFileName is the project file at the root of the directory.
const ProjectFileName = "megooflags.yaml"

// keyPattern is the API's key pattern; file names must match it.
var keyPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,99}$`)

// Dir is the whole directory. Segments and Flags are keyed by file name.
type Dir struct {
	Project  ProjectFile
	Segments map[string]Segment
	Flags    map[string]Flag
}

// ProjectFile is megooflags.yaml.
type ProjectFile struct {
	APIVersion   string        `yaml:"apiVersion"`
	Project      ProjectMeta   `yaml:"project"`
	Environments []Environment `yaml:"environments"`
}

// ProjectMeta is the project's details.
type ProjectMeta struct {
	Key         string `yaml:"key"`
	Name        string `yaml:"name"`
	Description string `yaml:"description"`
}

// Environment is an environment of the project; the list order is the sort
// order.
type Environment struct {
	Key               string `yaml:"key"`
	Name              string `yaml:"name"`
	Description       string `yaml:"description"`
	ApprovalsRequired int    `yaml:"approvals_required"`
}

// Segment is segments/<key>.yaml.
type Segment struct {
	Key         string               `yaml:"-"`
	Name        string               `yaml:"name"`
	Description string               `yaml:"description"`
	Included    []string             `yaml:"included"`
	Excluded    []string             `yaml:"excluded"`
	Rules       []client.SegmentRule `yaml:"rules"`
}

// Flag is flags/<key>.yaml. Environments holds the targeting of each
// environment the files manage.
type Flag struct {
	Key          string             `yaml:"-"`
	Name         string             `yaml:"name"`
	Description  string             `yaml:"description"`
	Type         string             `yaml:"type"`
	Tags         []string           `yaml:"tags"`
	Owner        *string            `yaml:"owner,omitempty"`
	Variations   []client.Variation `yaml:"variations"`
	OffVariation string             `yaml:"off_variation"`
	Permanent    bool               `yaml:"permanent"`
	Archived     bool               `yaml:"archived"`
	Environments map[string]FlagEnv `yaml:"environments"`
}

// FlagEnv is a flag's targeting in one environment.
type FlagEnv struct {
	Enabled       bool                  `yaml:"enabled"`
	Targets       []client.Target       `yaml:"targets"`
	Rules         []client.Rule         `yaml:"rules"`
	Fallthrough   client.Serve          `yaml:"fallthrough"`
	Prerequisites []client.Prerequisite `yaml:"prerequisites"`
}
