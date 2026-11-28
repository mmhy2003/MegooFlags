package provider

import (
	"cmp"
	"slices"

	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

// projectModel is the megooflags_project resource and data source.
type projectModel struct {
	Key            types.String       `tfsdk:"key"`
	Name           types.String       `tfsdk:"name"`
	Description    types.String       `tfsdk:"description"`
	StaleAfterDays types.Int64        `tfsdk:"stale_after_days"`
	Environments   []environmentModel `tfsdk:"environments"`
}

// environmentModel is one entry of a project's environments.
type environmentModel struct {
	Key               types.String `tfsdk:"key"`
	Name              types.String `tfsdk:"name"`
	Description       types.String `tfsdk:"description"`
	ApprovalsRequired types.Int64  `tfsdk:"approvals_required"`
}

// projectToModel maps an API project to the model, environments in sort
// order.
func projectToModel(p *client.Project) projectModel {
	envs := slices.Clone(p.Environments)
	slices.SortStableFunc(envs, func(a, b client.Environment) int { return cmp.Compare(a.SortOrder, b.SortOrder) })
	m := projectModel{
		Key:            types.StringValue(p.Key),
		Name:           types.StringValue(p.Name),
		Description:    types.StringValue(p.Description),
		StaleAfterDays: types.Int64Value(int64(p.StaleAfterDays)),
		Environments:   make([]environmentModel, 0, len(envs)),
	}
	for _, e := range envs {
		m.Environments = append(m.Environments, environmentModel{
			Key:               types.StringValue(e.Key),
			Name:              types.StringValue(e.Name),
			Description:       types.StringValue(e.Description),
			ApprovalsRequired: types.Int64Value(int64(e.ApprovalsRequired)),
		})
	}
	return m
}

// projectCreateFromModel builds the create request. Each environment's
// sort_order is its index; approvals and stale_after_days are set after.
func projectCreateFromModel(m projectModel) client.ProjectCreate {
	in := client.ProjectCreate{
		Key:         m.Key.ValueString(),
		Name:        m.Name.ValueString(),
		Description: m.Description.ValueString(),
	}
	for i, e := range m.Environments {
		in.Environments = append(in.Environments, environmentCreate(e, i))
	}
	return in
}

// projectUpdateFromModel builds the patch; an unknown or null
// stale_after_days is left as it is.
func projectUpdateFromModel(m projectModel) client.ProjectUpdate {
	in := client.ProjectUpdate{
		Name:        new(m.Name.ValueString()),
		Description: new(m.Description.ValueString()),
	}
	if !m.StaleAfterDays.IsUnknown() && !m.StaleAfterDays.IsNull() {
		in.StaleAfterDays = new(int(m.StaleAfterDays.ValueInt64()))
	}
	return in
}

func environmentCreate(e environmentModel, index int) client.EnvironmentCreate {
	return client.EnvironmentCreate{
		Key:         e.Key.ValueString(),
		Name:        e.Name.ValueString(),
		Description: e.Description.ValueString(),
		SortOrder:   new(index),
	}
}

// envUpdate patches the environment Key.
type envUpdate struct {
	Key    string
	Update client.EnvironmentUpdate
}

// envDiff turns the current environments into the desired ones: create,
// then update (which sets approvals on the new ones), then delete.
type envDiff struct {
	Create []client.EnvironmentCreate
	Update []envUpdate
	Delete []string
}

// diffEnvironments matches environments by key. The desired list index is
// the sort order.
func diffEnvironments(current []client.Environment, desired []environmentModel) envDiff {
	var d envDiff
	byKey := make(map[string]client.Environment, len(current))
	for _, e := range current {
		byKey[e.Key] = e
	}
	wanted := make(map[string]bool, len(desired))
	for i, want := range desired {
		key := want.Key.ValueString()
		wanted[key] = true
		approvals := int(want.ApprovalsRequired.ValueInt64())
		have, ok := byKey[key]
		if !ok {
			d.Create = append(d.Create, environmentCreate(want, i))
			if approvals != 0 {
				d.Update = append(d.Update, envUpdate{Key: key, Update: client.EnvironmentUpdate{ApprovalsRequired: new(approvals)}})
			}
			continue
		}
		var u client.EnvironmentUpdate
		changed := false
		if name := want.Name.ValueString(); name != have.Name {
			u.Name, changed = new(name), true
		}
		if desc := want.Description.ValueString(); desc != have.Description {
			u.Description, changed = new(desc), true
		}
		if i != have.SortOrder {
			u.SortOrder, changed = new(i), true
		}
		if approvals != have.ApprovalsRequired {
			u.ApprovalsRequired, changed = new(approvals), true
		}
		if changed {
			d.Update = append(d.Update, envUpdate{Key: key, Update: u})
		}
	}
	for _, e := range current {
		if !wanted[e.Key] {
			d.Delete = append(d.Delete, e.Key)
		}
	}
	return d
}
