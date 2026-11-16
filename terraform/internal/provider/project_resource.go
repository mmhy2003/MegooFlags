package provider

import (
	"context"
	"fmt"

	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/int64default"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/int64planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

var (
	_ resource.ResourceWithConfigure      = (*projectResource)(nil)
	_ resource.ResourceWithImportState    = (*projectResource)(nil)
	_ resource.ResourceWithValidateConfig = (*projectResource)(nil)
)

type projectResource struct {
	client *client.Client
}

func newProjectResource() resource.Resource { return &projectResource{} }

func (r *projectResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_project"
}

func (r *projectResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "A project and its environments.",
		Attributes: map[string]schema.Attribute{
			"key": schema.StringAttribute{
				Description:   "Project key. Changing it replaces the project.",
				Required:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.RequiresReplace()},
			},
			"name": schema.StringAttribute{
				Description: "Display name.",
				Required:    true,
			},
			"description": schema.StringAttribute{
				Description: "Description.",
				Optional:    true,
				Computed:    true,
				Default:     stringdefault.StaticString(""),
			},
			"stale_after_days": schema.Int64Attribute{
				Description:   "A flag no environment has evaluated for this many days is stale (1 to 365). The server default is 30.",
				Optional:      true,
				Computed:      true,
				PlanModifiers: []planmodifier.Int64{int64planmodifier.UseStateForUnknown()},
			},
			"environments": schema.ListNestedAttribute{
				Description: "Environments in display order, matched by key. Removing one deletes it and its flag configuration.",
				Required:    true,
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"key": schema.StringAttribute{
							Description: "Environment key.",
							Required:    true,
						},
						"name": schema.StringAttribute{
							Description: "Display name.",
							Required:    true,
						},
						"description": schema.StringAttribute{
							Description: "Description.",
							Optional:    true,
							Computed:    true,
							Default:     stringdefault.StaticString(""),
						},
						"approvals_required": schema.Int64Attribute{
							Description: "Approvals a change request needs here (0 to 10). Terraform can only change flags in environments with 0.",
							Optional:    true,
							Computed:    true,
							Default:     int64default.StaticInt64(0),
						},
					},
				},
			},
		},
	}
}

func (r *projectResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = providerClient(req.ProviderData, resp.Diagnostics.AddError)
}

func (r *projectResource) ValidateConfig(ctx context.Context, req resource.ValidateConfigRequest, resp *resource.ValidateConfigResponse) {
	var cfg projectModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &cfg)...)
	if resp.Diagnostics.HasError() || cfg.Environments == nil {
		return
	}
	if len(cfg.Environments) == 0 {
		resp.Diagnostics.AddAttributeError(path.Root("environments"), "No environments",
			"A project needs at least one environment.")
	}
	seen := map[string]bool{}
	for i, e := range cfg.Environments {
		if e.Key.IsUnknown() || e.Key.IsNull() {
			continue
		}
		if k := e.Key.ValueString(); seen[k] {
			resp.Diagnostics.AddAttributeError(path.Root("environments").AtListIndex(i).AtName("key"),
				"Duplicate environment key", fmt.Sprintf("Environment %q is listed more than once.", k))
		} else {
			seen[k] = true
		}
	}
}

func (r *projectResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan projectModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	key := plan.Key.ValueString()
	p, err := r.client.CreateProject(ctx, projectCreateFromModel(plan))
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Creating project "+key))
		return
	}
	// Save the project first, so a failure below leaves it in state
	// (tainted) instead of orphaned.
	resp.Diagnostics.Append(resp.State.Set(ctx, projectToModel(p))...)
	if resp.Diagnostics.HasError() {
		return
	}
	if !plan.StaleAfterDays.IsUnknown() && !plan.StaleAfterDays.IsNull() && int(plan.StaleAfterDays.ValueInt64()) != p.StaleAfterDays {
		days := int(plan.StaleAfterDays.ValueInt64())
		if _, err := r.client.UpdateProject(ctx, key, client.ProjectUpdate{StaleAfterDays: &days}); err != nil {
			resp.Diagnostics.Append(diagFromError(err, "Updating project "+key))
			return
		}
	}
	resp.Diagnostics.Append(r.applyEnvironments(ctx, key, p.Environments, plan.Environments)...)
	r.readInto(ctx, key, &resp.State, &resp.Diagnostics)
}

func (r *projectResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state projectModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	key := state.Key.ValueString()
	p, err := r.client.GetProject(ctx, key)
	if client.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Reading project "+key))
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, projectToModel(p))...)
}

func (r *projectResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan projectModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	key := plan.Key.ValueString()
	if _, err := r.client.UpdateProject(ctx, key, projectUpdateFromModel(plan)); err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Updating project "+key))
		return
	}
	current, err := r.client.ListEnvironments(ctx, key)
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Reading environments of project "+key))
		return
	}
	resp.Diagnostics.Append(r.applyEnvironments(ctx, key, current, plan.Environments)...)
	r.readInto(ctx, key, &resp.State, &resp.Diagnostics)
}

func (r *projectResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state projectModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	key := state.Key.ValueString()
	if err := r.client.DeleteProject(ctx, key); err != nil && !client.IsNotFound(err) {
		resp.Diagnostics.Append(diagFromError(err, "Deleting project "+key))
	}
}

func (r *projectResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	resource.ImportStatePassthroughID(ctx, path.Root("key"), req, resp)
}

// applyEnvironments creates, updates and then deletes environments of
// project key so that current becomes desired.
func (r *projectResource) applyEnvironments(ctx context.Context, key string, current []client.Environment, desired []environmentModel) diag.Diagnostics {
	var diags diag.Diagnostics
	d := diffEnvironments(current, desired)
	for _, in := range d.Create {
		if _, err := r.client.CreateEnvironment(ctx, key, in); err != nil {
			diags.Append(diagFromError(err, "Creating environment "+in.Key))
			return diags
		}
	}
	for _, u := range d.Update {
		if _, err := r.client.UpdateEnvironment(ctx, key, u.Key, u.Update); err != nil {
			diags.Append(diagFromError(err, "Updating environment "+u.Key))
			return diags
		}
	}
	for _, env := range d.Delete {
		if err := r.client.DeleteEnvironment(ctx, key, env); err != nil && !client.IsNotFound(err) {
			diags.Append(diagFromError(err, "Deleting environment "+env))
			return diags
		}
	}
	return diags
}

// readInto reads project key and stores it in state, so the state matches
// the server even after a failed change.
func (r *projectResource) readInto(ctx context.Context, key string, state *tfsdk.State, diags *diag.Diagnostics) {
	p, err := r.client.GetProject(ctx, key)
	if err != nil {
		diags.Append(diagFromError(err, "Reading project "+key))
		return
	}
	diags.Append(state.Set(ctx, projectToModel(p))...)
}
