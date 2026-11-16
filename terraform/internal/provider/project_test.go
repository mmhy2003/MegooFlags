package provider

import (
	"context"
	"reflect"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

func envModel(key, name, desc string, approvals int64) environmentModel {
	return environmentModel{
		Key:               types.StringValue(key),
		Name:              types.StringValue(name),
		Description:       types.StringValue(desc),
		ApprovalsRequired: types.Int64Value(approvals),
	}
}

func TestProjectToModel(t *testing.T) {
	p := &client.Project{
		Key: "shop", Name: "Shop", Description: "The shop", StaleAfterDays: 30,
		Environments: []client.Environment{
			{Key: "prod", Name: "Production", SortOrder: 1, ApprovalsRequired: 2},
			{Key: "dev", Name: "Development", Description: "Local", SortOrder: 0},
		},
	}
	got := projectToModel(p)
	want := projectModel{
		Key:            types.StringValue("shop"),
		Name:           types.StringValue("Shop"),
		Description:    types.StringValue("The shop"),
		StaleAfterDays: types.Int64Value(30),
		Environments: []environmentModel{
			envModel("dev", "Development", "Local", 0),
			envModel("prod", "Production", "", 2),
		},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("projectToModel = %+v, want %+v", got, want)
	}
}

func TestProjectCreateFromModel(t *testing.T) {
	m := projectModel{
		Key:            types.StringValue("shop"),
		Name:           types.StringValue("Shop"),
		Description:    types.StringValue(""),
		StaleAfterDays: types.Int64Unknown(),
		Environments: []environmentModel{
			envModel("dev", "Development", "Local", 0),
			envModel("prod", "Production", "", 1),
		},
	}
	got := projectCreateFromModel(m)
	want := client.ProjectCreate{
		Key: "shop", Name: "Shop",
		Environments: []client.EnvironmentCreate{
			{Key: "dev", Name: "Development", Description: "Local", SortOrder: new(0)},
			{Key: "prod", Name: "Production", SortOrder: new(1)},
		},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("projectCreateFromModel = %+v, want %+v", got, want)
	}
}

func TestProjectUpdateFromModel(t *testing.T) {
	m := projectModel{
		Name:           types.StringValue("Shop"),
		Description:    types.StringValue(""),
		StaleAfterDays: types.Int64Value(14),
	}
	got := projectUpdateFromModel(m)
	want := client.ProjectUpdate{Name: new("Shop"), Description: new(""), StaleAfterDays: new(14)}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("projectUpdateFromModel = %+v, want %+v", got, want)
	}

	m.StaleAfterDays = types.Int64Unknown()
	if got := projectUpdateFromModel(m); got.StaleAfterDays != nil {
		t.Errorf("unknown stale_after_days sent as %d", *got.StaleAfterDays)
	}
}

func TestDiffEnvironments(t *testing.T) {
	current := []client.Environment{
		{Key: "dev", Name: "Development", SortOrder: 0},
		{Key: "staging", Name: "Staging", SortOrder: 1},
		{Key: "prod", Name: "Production", SortOrder: 2, ApprovalsRequired: 1},
	}
	desired := []environmentModel{
		envModel("dev", "Development", "", 0),
		envModel("prod", "Prod", "", 0),
		envModel("qa", "QA", "Testing", 2),
		envModel("demo", "Demo", "", 0),
	}
	got := diffEnvironments(current, desired)
	want := envDiff{
		Create: []client.EnvironmentCreate{
			{Key: "qa", Name: "QA", Description: "Testing", SortOrder: new(2)},
			{Key: "demo", Name: "Demo", SortOrder: new(3)},
		},
		Update: []envUpdate{
			{Key: "prod", Update: client.EnvironmentUpdate{Name: new("Prod"), SortOrder: new(1), ApprovalsRequired: new(0)}},
			{Key: "qa", Update: client.EnvironmentUpdate{ApprovalsRequired: new(2)}},
		},
		Delete: []string{"staging"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("diffEnvironments =\n %+v\nwant\n %+v", got, want)
	}
}

func TestDiffEnvironmentsUnchanged(t *testing.T) {
	current := []client.Environment{
		{Key: "dev", Name: "Development", Description: "Local", SortOrder: 0},
		{Key: "prod", Name: "Production", SortOrder: 1, ApprovalsRequired: 1},
	}
	desired := []environmentModel{
		envModel("dev", "Development", "Local", 0),
		envModel("prod", "Production", "", 1),
	}
	got := diffEnvironments(current, desired)
	if len(got.Create) != 0 || len(got.Update) != 0 || len(got.Delete) != 0 {
		t.Errorf("diffEnvironments = %+v, want no changes", got)
	}
}

func TestDiffEnvironmentsAfterCreate(t *testing.T) {
	// CreateProject returns the environments without approvals; the diff
	// against the plan is then only the approval updates.
	created := []client.Environment{
		{Key: "dev", Name: "Development", SortOrder: 0},
		{Key: "prod", Name: "Production", SortOrder: 1},
	}
	desired := []environmentModel{
		envModel("dev", "Development", "", 0),
		envModel("prod", "Production", "", 1),
	}
	got := diffEnvironments(created, desired)
	want := envDiff{Update: []envUpdate{{Key: "prod", Update: client.EnvironmentUpdate{ApprovalsRequired: new(1)}}}}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("diffEnvironments = %+v, want %+v", got, want)
	}
}

// validateProject runs the resource's ValidateConfig on environments with
// the given keys.
func validateProject(t *testing.T, keys ...string) resource.ValidateConfigResponse {
	t.Helper()
	ctx := context.Background()
	r := newProjectResource().(*projectResource)
	var sresp resource.SchemaResponse
	r.Schema(ctx, resource.SchemaRequest{}, &sresp)
	typ := sresp.Schema.Type().TerraformType(ctx).(tftypes.Object)
	envType := typ.AttributeTypes["environments"].(tftypes.List).ElementType.(tftypes.Object)
	envs := []tftypes.Value{}
	for _, k := range keys {
		envs = append(envs, tftypes.NewValue(envType, map[string]tftypes.Value{
			"key":                tftypes.NewValue(tftypes.String, k),
			"name":               tftypes.NewValue(tftypes.String, k),
			"description":        tftypes.NewValue(tftypes.String, nil),
			"approvals_required": tftypes.NewValue(tftypes.Number, nil),
		}))
	}
	raw := tftypes.NewValue(typ, map[string]tftypes.Value{
		"key":              tftypes.NewValue(tftypes.String, "shop"),
		"name":             tftypes.NewValue(tftypes.String, "Shop"),
		"description":      tftypes.NewValue(tftypes.String, nil),
		"stale_after_days": tftypes.NewValue(tftypes.Number, nil),
		"environments":     tftypes.NewValue(typ.AttributeTypes["environments"], envs),
	})
	var resp resource.ValidateConfigResponse
	r.ValidateConfig(ctx, resource.ValidateConfigRequest{Config: tfsdk.Config{Schema: sresp.Schema, Raw: raw}}, &resp)
	return resp
}

func TestProjectValidateConfig(t *testing.T) {
	if resp := validateProject(t, "dev", "prod"); resp.Diagnostics.HasError() {
		t.Errorf("valid config: %v", resp.Diagnostics)
	}
	if resp := validateProject(t); resp.Diagnostics.ErrorsCount() != 1 {
		t.Errorf("no environments: %v, want one error", resp.Diagnostics)
	}
	resp := validateProject(t, "dev", "prod", "dev")
	if resp.Diagnostics.ErrorsCount() != 1 || resp.Diagnostics[0].Summary() != "Duplicate environment key" {
		t.Errorf("duplicate key: %v, want one duplicate error", resp.Diagnostics)
	}
}
