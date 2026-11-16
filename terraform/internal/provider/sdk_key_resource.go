package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

var (
	_ resource.ResourceWithConfigure   = (*sdkKeyResource)(nil)
	_ resource.ResourceWithImportState = (*sdkKeyResource)(nil)
)

// sdkKeyModel is the megooflags_sdk_key resource.
type sdkKeyModel struct {
	Project     types.String `tfsdk:"project"`
	Environment types.String `tfsdk:"environment"`
	Name        types.String `tfsdk:"name"`
	Kind        types.String `tfsdk:"kind"`
	ID          types.String `tfsdk:"id"`
	Prefix      types.String `tfsdk:"prefix"`
	CreatedAt   types.String `tfsdk:"created_at"`
	Secret      types.String `tfsdk:"secret"`
}

// sdkKeyFromCreated maps a new key to the model, keeping its secret.
func sdkKeyFromCreated(project, env string, k *client.SdkKeyCreated) sdkKeyModel {
	return sdkKeyModel{
		Project:     types.StringValue(project),
		Environment: types.StringValue(env),
		Name:        types.StringValue(k.Name),
		Kind:        types.StringValue(k.Kind),
		ID:          types.StringValue(k.ID),
		Prefix:      types.StringValue(k.Prefix),
		CreatedAt:   types.StringValue(k.CreatedAt),
		Secret:      types.StringValue(k.Key),
	}
}

// sdkKeyToModel maps a listed key to the model. The API never returns the
// secret again, so prior's (null after import) is kept.
func sdkKeyToModel(prior sdkKeyModel, k client.SdkKey) sdkKeyModel {
	return sdkKeyModel{
		Project:     prior.Project,
		Environment: prior.Environment,
		Name:        types.StringValue(k.Name),
		Kind:        types.StringValue(k.Kind),
		ID:          types.StringValue(k.ID),
		Prefix:      types.StringValue(k.Prefix),
		CreatedAt:   types.StringValue(k.CreatedAt),
		Secret:      prior.Secret,
	}
}

// findActiveSdkKey returns the key with the given id, or nil when it is
// missing or revoked: a revoked key is gone as far as Terraform is
// concerned.
func findActiveSdkKey(keys []client.SdkKey, id string) *client.SdkKey {
	for i := range keys {
		if keys[i].ID == id {
			if keys[i].RevokedAt != nil {
				return nil
			}
			return &keys[i]
		}
	}
	return nil
}

type sdkKeyResource struct {
	client *client.Client
}

func newSdkKeyResource() resource.Resource { return &sdkKeyResource{} }

func (r *sdkKeyResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_sdk_key"
}

func (r *sdkKeyResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	replace := []planmodifier.String{stringplanmodifier.RequiresReplace()}
	keep := []planmodifier.String{stringplanmodifier.UseStateForUnknown()}
	resp.Schema = schema.Schema{
		Description: "An SDK key of one environment. Destroying it revokes the key. " +
			"The secret is only known when Terraform creates the key: after terraform import, secret is null.",
		Attributes: map[string]schema.Attribute{
			"project": schema.StringAttribute{
				Description:   "Project key. Changing it replaces the key.",
				Required:      true,
				PlanModifiers: replace,
			},
			"environment": schema.StringAttribute{
				Description:   "Environment key. Changing it replaces the key.",
				Required:      true,
				PlanModifiers: replace,
			},
			"name": schema.StringAttribute{
				Description:   "Name. Changing it replaces the key.",
				Required:      true,
				PlanModifiers: replace,
			},
			"kind": schema.StringAttribute{
				Description:   "server (server SDKs, sees the whole ruleset) or client (browser and mobile SDKs). Changing it replaces the key.",
				Required:      true,
				PlanModifiers: replace,
				Validators:    []validator.String{stringvalidator.OneOf("server", "client")},
			},
			"id": schema.StringAttribute{
				Description:   "Key ID.",
				Computed:      true,
				PlanModifiers: keep,
			},
			"prefix": schema.StringAttribute{
				Description:   "The start of the secret, shown in the UI to recognise the key.",
				Computed:      true,
				PlanModifiers: keep,
			},
			"created_at": schema.StringAttribute{
				Description:   "When the key was created.",
				Computed:      true,
				PlanModifiers: keep,
			},
			"secret": schema.StringAttribute{
				Description:   "The key for the SDK. Only known for keys Terraform created; null after import.",
				Computed:      true,
				Sensitive:     true,
				PlanModifiers: keep,
			},
		},
	}
}

func (r *sdkKeyResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = providerClient(req.ProviderData, resp.Diagnostics.AddError)
}

func (r *sdkKeyResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan sdkKeyModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, env := plan.Project.ValueString(), plan.Environment.ValueString()
	k, err := r.client.CreateSdkKey(ctx, project, env, client.SdkKeyCreate{
		Name: plan.Name.ValueString(),
		Kind: plan.Kind.ValueString(),
	})
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Creating SDK key "+plan.Name.ValueString()))
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, sdkKeyFromCreated(project, env, k))...)
}

func (r *sdkKeyResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state sdkKeyModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	keys, err := r.client.ListSdkKeys(ctx, state.Project.ValueString(), state.Environment.ValueString())
	if client.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Reading SDK keys"))
		return
	}
	k := findActiveSdkKey(keys, state.ID.ValueString())
	if k == nil {
		resp.State.RemoveResource(ctx)
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, sdkKeyToModel(state, *k))...)
}

// Update is never needed: every argument replaces the key. It keeps the
// planned values.
func (r *sdkKeyResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan sdkKeyModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	resp.Diagnostics.Append(resp.State.Set(ctx, plan)...)
}

func (r *sdkKeyResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state sdkKeyModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	err := r.client.RevokeSdkKey(ctx, state.Project.ValueString(), state.Environment.ValueString(), state.ID.ValueString())
	if err != nil && !client.IsNotFound(err) {
		resp.Diagnostics.Append(diagFromError(err, "Revoking SDK key "+state.Name.ValueString()))
	}
}

func (r *sdkKeyResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	parts, err := parseImportID(req.ID, "project/environment/id")
	if err != nil {
		resp.Diagnostics.AddError("Invalid import ID", err.Error())
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("project"), parts[0])...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("environment"), parts[1])...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("id"), parts[2])...)
}
