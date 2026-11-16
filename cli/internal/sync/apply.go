package sync

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"slices"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
)

// ApplyOptions changes how Apply runs. DryRun prints the plan to Out and
// sends nothing. ChangeRequests opens a config change request for each
// config that needs approval instead of leaving it in NeedsApproval.
type ApplyOptions struct {
	DryRun, ChangeRequests bool
	Out                    io.Writer
}

// Result is what Apply did. Applied are the changes made, NeedsApproval the
// configs left for an environment that requires approvals, ChangeRequests
// the ids of the change requests opened for them, and Failures the changes
// that failed or were skipped.
type Result struct {
	Applied        []Change
	NeedsApproval  []Change
	ChangeRequests []string
	Failures       []Failure
}

// Failure is a change that failed, with why.
type Failure struct {
	Change Change
	Err    error
}

var errTypeChange = errors.New("type cannot change; delete and recreate the flag")

// errOffVariationLater marks a flag update whose off variation is sent
// after the flag's new variations.
var errOffVariationLater = errors.New("off variation set later")

// errHeldForApproval marks a config that waits for approval, in
// NeedsApproval or in a change request.
var errHeldForApproval = errors.New("needs approval")

// SnapshotOrEmpty is Snapshot, or an empty directory for project when the
// project does not exist yet, so that Diff plans to create everything.
func SnapshotOrEmpty(ctx context.Context, c *client.Client, project string, envs []string) (*flagfiles.Dir, error) {
	d, err := Snapshot(ctx, c, project, envs)
	if err == nil || !client.IsNotFound(err) {
		return d, err
	}
	// Snapshot also reads segments and flags; only a missing project means empty.
	if _, perr := c.GetProject(ctx, project); !client.IsNotFound(perr) {
		return nil, err
	}
	return &flagfiles.Dir{
		Project: flagfiles.ProjectFile{
			APIVersion:   flagfiles.APIVersion,
			Project:      flagfiles.ProjectMeta{Key: project},
			Environments: []flagfiles.Environment{},
		},
		Segments: map[string]flagfiles.Segment{},
		Flags:    map[string]flagfiles.Flag{},
	}, nil
}

// Apply makes the plan's changes in project, in order. It never bypasses
// approvals: a config that needs one is left in NeedsApproval, or with
// ChangeRequests proposed in a change request. A change that fails is
// recorded and Apply goes on; the changes of a flag whose create failed are
// skipped and recorded as failures too.
func Apply(ctx context.Context, c *client.Client, project string, plan Plan, opts ApplyOptions) Result {
	if opts.DryRun {
		if opts.Out != nil {
			plan.Print(opts.Out)
		}
		return Result{}
	}
	a := &applier{ctx: ctx, c: c, project: project, opts: opts, plan: plan, failedFlags: map[string]bool{}}
	for _, ch := range plan.Changes {
		if afterVariations(ch) {
			a.setOffVariations()
		}
		if a.failedFlags[ch.Key] && ch.Kind != KindSegment && ch.Kind != KindSegmentConfig &&
			ch.Kind != KindProject && ch.Kind != KindEnvironment {
			a.res.Failures = append(a.res.Failures, Failure{ch, fmt.Errorf("skipped: flag %s was not created", ch.Key)})
			continue
		}
		a.record(ch, a.apply(ch))
	}
	a.setOffVariations()
	return a.res
}

func (a *applier) record(ch Change, err error) {
	switch {
	case err == nil:
		a.res.Applied = append(a.res.Applied, ch)
	case errors.Is(err, errHeldForApproval), errors.Is(err, errOffVariationLater):
	default:
		if ch.Kind == KindFlag && ch.Op == OpCreate {
			a.failedFlags[ch.Key] = true
		}
		a.res.Failures = append(a.res.Failures, Failure{ch, err})
	}
}

// afterVariations reports whether ch comes after the new and changed
// variations in apply order: a config, a removed variation, or a prune.
func afterVariations(ch Change) bool {
	switch ch.Kind {
	case KindConfig:
		return true
	case KindVariation:
		return ch.Op == OpDelete
	case KindSegment:
		return ch.Op == OpDelete
	case KindFlag:
		return ch.Flag == nil // a pruned flag
	}
	return false
}

// setOffVariations sends the off variations held back from flag updates,
// now that the variations they name exist. The flag update counts as
// applied once its off variation is set.
func (a *applier) setOffVariations() {
	for _, ch := range a.pendingOff {
		_, err := a.c.UpdateFlag(a.ctx, a.project, ch.Key, client.FlagUpdate{OffVariation: &ch.Flag.OffVariation})
		a.record(ch, err)
	}
	a.pendingOff = nil
}

type applier struct {
	ctx     context.Context
	c       *client.Client
	project string
	opts    ApplyOptions
	plan    Plan
	res     Result

	// projectCreated is set when the project was created with the plan's
	// new environments.
	projectCreated bool
	failedFlags    map[string]bool
	// pendingOff are flag updates whose off variation waits for the flag's
	// new variations: the update may name one that does not exist yet.
	pendingOff []Change
}

func (a *applier) apply(ch Change) error {
	ctx, c, p := a.ctx, a.c, a.project
	var err error
	switch ch.Kind {
	case KindProject:
		return a.ensureProject(ch)
	case KindEnvironment:
		return a.environment(ch)
	case KindSegment:
		switch ch.Op {
		case OpCreate:
			s := ch.Segment
			_, err = c.CreateSegment(ctx, p, client.SegmentCreate{Key: ch.Key, Name: s.Name, Description: s.Description})
		case OpUpdate:
			_, err = c.UpdateSegment(ctx, p, ch.Key, client.SegmentUpdate{Name: &ch.Segment.Name, Description: &ch.Segment.Description})
		case OpDelete:
			err = c.DeleteSegment(ctx, p, ch.Key)
		}
	case KindSegmentConfig:
		s := ch.Segment
		_, err = c.PutSegmentConfig(ctx, p, ch.Key, client.SegmentConfig{Included: s.Included, Excluded: s.Excluded, Rules: s.Rules})
	case KindFlag:
		return a.flag(ch)
	case KindLifecycle:
		_, err = c.SetLifecycle(ctx, p, ch.Key, ch.Flag.Permanent)
	case KindVariation:
		v := ch.VariationValue
		switch ch.Op {
		case OpCreate:
			_, err = c.CreateVariation(ctx, p, ch.Key, variationIn(*v))
		case OpUpdate:
			_, err = c.UpdateVariation(ctx, p, ch.Key, ch.Variation, client.VariationUpdate{Name: &v.Name, Description: &v.Description, Value: &v.Value})
		case OpDelete:
			err = c.DeleteVariation(ctx, p, ch.Key, ch.Variation)
		}
	case KindConfig:
		return a.config(ch)
	default:
		err = fmt.Errorf("unknown change kind %q", ch.Kind)
	}
	return err
}

// ensureProject updates the project, or creates it with the environments the
// plan creates when it does not exist.
func (a *applier) ensureProject(ch Change) error {
	meta := ch.Project
	_, err := a.c.GetProject(a.ctx, a.project)
	if err == nil {
		_, err = a.c.UpdateProject(a.ctx, a.project, client.ProjectUpdate{Name: &meta.Name, Description: &meta.Description})
		return err
	}
	if !client.IsNotFound(err) {
		return err
	}
	in := client.ProjectCreate{Key: a.project, Name: meta.Name, Description: meta.Description}
	for _, e := range a.plan.Changes {
		if e.Kind == KindEnvironment && e.Op == OpCreate {
			in.Environments = append(in.Environments, client.EnvironmentCreate{
				Key: e.Key, Name: e.Environment.Name, Description: e.Environment.Description,
			})
		}
	}
	if _, err := a.c.CreateProject(a.ctx, in); err != nil {
		return err
	}
	a.projectCreated = true
	return nil
}

func (a *applier) environment(ch Change) error {
	e := ch.Environment
	if ch.Op == OpUpdate {
		_, err := a.c.UpdateEnvironment(a.ctx, a.project, ch.Key, client.EnvironmentUpdate{
			Name: &e.Name, Description: &e.Description, ApprovalsRequired: &e.ApprovalsRequired,
		})
		return err
	}
	if !a.projectCreated {
		_, err := a.c.CreateEnvironment(a.ctx, a.project, client.EnvironmentCreate{
			Key: ch.Key, Name: e.Name, Description: e.Description,
		})
		if err != nil {
			return err
		}
	}
	if e.ApprovalsRequired == 0 {
		return nil
	}
	_, err := a.c.UpdateEnvironment(a.ctx, a.project, ch.Key, client.EnvironmentUpdate{ApprovalsRequired: &e.ApprovalsRequired})
	return err
}

func (a *applier) flag(ch Change) error {
	ctx, c, p, f := a.ctx, a.c, a.project, ch.Flag
	var err error
	switch ch.Op {
	case OpCreate:
		in := client.FlagCreate{
			Key: ch.Key, Name: f.Name, Description: f.Description, Type: f.Type,
			Tags: f.Tags, Owner: f.Owner, OffVariation: &f.OffVariation,
		}
		// The API gives a boolean flag its true and false variations and
		// refuses a list; their names and descriptions are set afterwards.
		if f.Type != "boolean" {
			for _, v := range f.Variations {
				in.Variations = append(in.Variations, variationIn(v))
			}
		}
		if _, err = c.CreateFlag(ctx, p, in); err != nil || f.Type != "boolean" {
			break
		}
		for _, v := range f.Variations {
			if v.Name == "" && v.Description == "" {
				continue
			}
			name, description := v.Name, v.Description
			if _, err = c.UpdateVariation(ctx, p, ch.Key, v.Key, client.VariationUpdate{
				Name: &name, Description: &description,
			}); err != nil {
				break
			}
		}
	case OpUpdate:
		if slices.Contains(ch.Fields, "type") {
			return errTypeChange
		}
		tags := f.Tags
		in := client.FlagUpdate{
			Name: &f.Name, Description: &f.Description, Tags: &tags,
			Owner: f.Owner, ClearOwner: f.Owner == nil,
		}
		if !slices.Contains(ch.Fields, "off_variation") {
			in.OffVariation = &f.OffVariation
			_, err = c.UpdateFlag(ctx, p, ch.Key, in)
			break
		}
		if len(ch.Fields) > 1 {
			if _, err = c.UpdateFlag(ctx, p, ch.Key, in); err != nil {
				break
			}
		}
		a.pendingOff = append(a.pendingOff, ch)
		return errOffVariationLater
	case OpArchive:
		_, err = c.ArchiveFlag(ctx, p, ch.Key)
	case OpRestore:
		_, err = c.RestoreFlag(ctx, p, ch.Key)
	default:
		err = fmt.Errorf("unknown flag change %q", ch.Op)
	}
	return err
}

// config writes a flag's config in one environment based on its current
// version, and tries once more on version_conflict. An environment that
// requires approvals gets a change request with ChangeRequests, else the
// change goes to NeedsApproval.
func (a *applier) config(ch Change) error {
	e := ch.Config
	in := client.FlagConfigIn{
		Enabled: e.Enabled, Targets: e.Targets, Rules: e.Rules,
		Fallthrough: e.Fallthrough, Prerequisites: e.Prerequisites,
	}
	var err error
	for attempt := 0; ; attempt++ {
		var cur *client.FlagConfig
		if cur, err = a.c.GetFlagConfig(a.ctx, a.project, ch.Key, ch.Env); err != nil {
			return err
		}
		in.Version = cur.Version
		if _, err = a.c.PutFlagConfig(a.ctx, a.project, ch.Key, ch.Env, in); err == nil {
			return nil
		}
		if attempt > 0 || !client.IsCode(err, "version_conflict") {
			break
		}
	}
	if !client.IsCode(err, "approval_required") {
		return err
	}
	if !a.opts.ChangeRequests {
		a.res.NeedsApproval = append(a.res.NeedsApproval, ch)
		return errHeldForApproval
	}
	raw, err := json.Marshal(in)
	if err != nil {
		return err
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return err
	}
	cr, err := a.c.CreateChangeRequest(a.ctx, a.project, client.ChangeRequestCreate{
		Title:       fmt.Sprintf("Apply flags-as-code: %s in %s", ch.Key, ch.Env),
		Flag:        ch.Key,
		Environment: ch.Env,
		Kind:        "config",
		Payload:     payload,
	})
	if err != nil {
		return err
	}
	a.res.ChangeRequests = append(a.res.ChangeRequests, cr.ID)
	return errHeldForApproval
}

func variationIn(v client.Variation) client.VariationIn {
	return client.VariationIn{Key: v.Key, Name: v.Name, Description: v.Description, Value: v.Value}
}
