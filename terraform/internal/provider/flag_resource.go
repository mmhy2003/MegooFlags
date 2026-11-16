package provider

import (
	"context"
	"fmt"
	"strings"

	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/listplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/setdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

var (
	_ resource.ResourceWithConfigure   = (*flagResource)(nil)
	_ resource.ResourceWithImportState = (*flagResource)(nil)
)

// flagTypes are the flag types the API accepts.
var flagTypes = []string{"boolean", "string", "number", "json"}

// flagModel is the megooflags_flag resource.
type flagModel struct {
	Project      types.String `tfsdk:"project"`
	Key          types.String `tfsdk:"key"`
	Type         types.String `tfsdk:"type"`
	Name         types.String `tfsdk:"name"`
	Description  types.String `tfsdk:"description"`
	Tags         types.Set    `tfsdk:"tags"`
	Owner        types.String `tfsdk:"owner"`
	Variations   types.List   `tfsdk:"variations"`
	OffVariation types.String `tfsdk:"off_variation"`
	Permanent    types.Bool   `tfsdk:"permanent"`
}

// variationModel is one entry of a flag's variations.
type variationModel struct {
	Key         types.String `tfsdk:"key"`
	Name        types.String `tfsdk:"name"`
	Description types.String `tfsdk:"description"`
	ValueJSON   jsonValue    `tfsdk:"value_json"`
}

var variationObjectType = types.ObjectType{AttrTypes: map[string]attr.Type{
	"key":         types.StringType,
	"name":        types.StringType,
	"description": types.StringType,
	"value_json":  jsonValueType{},
}}

func variationsToList(ctx context.Context, vs []variationModel) (types.List, diag.Diagnostics) {
	return types.ListValueFrom(ctx, variationObjectType, vs)
}

// variationsFromList returns nil for a null or unknown list.
func variationsFromList(ctx context.Context, l types.List) ([]variationModel, diag.Diagnostics) {
	if l.IsNull() || l.IsUnknown() {
		return nil, nil
	}
	var vs []variationModel
	diags := l.ElementsAs(ctx, &vs, false)
	return vs, diags
}

// parseFlagID splits an import ID "project/key".
func parseFlagID(id string) (project, key string, err error) {
	parts := strings.Split(id, "/")
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return "", "", fmt.Errorf("expected an import ID of the form project/key, got %q", id)
	}
	return parts[0], parts[1], nil
}

// flagToModel maps an API flag to the model. Variations keep the order of
// prior (the configuration or state), with any others after them in API
// order, and keep prior's value_json text when it is the same value.
func flagToModel(ctx context.Context, project string, f *client.Flag, prior []variationModel) (flagModel, diag.Diagnostics) {
	var diags diag.Diagnostics
	m := flagModel{
		Project:      types.StringValue(project),
		Key:          types.StringValue(f.Key),
		Type:         types.StringValue(f.Type),
		Name:         types.StringValue(f.Name),
		Description:  types.StringValue(f.Description),
		Owner:        types.StringPointerValue(f.Owner),
		OffVariation: types.StringValue(f.OffVariation),
		Permanent:    types.BoolValue(f.Lifecycle == "permanent"),
	}
	tags := f.Tags
	if tags == nil {
		tags = []string{}
	}
	m.Tags, diags = types.SetValueFrom(ctx, types.StringType, tags)

	byKey := make(map[string]client.Variation, len(f.Variations))
	for _, v := range f.Variations {
		byKey[v.Key] = v
	}
	ordered := make([]client.Variation, 0, len(f.Variations))
	priorJSON := map[string]string{}
	for _, p := range prior {
		k := p.Key.ValueString()
		if v, ok := byKey[k]; ok {
			ordered = append(ordered, v)
			delete(byKey, k)
			priorJSON[k] = p.ValueJSON.ValueString()
		}
	}
	for _, v := range f.Variations {
		if _, ok := byKey[v.Key]; ok {
			ordered = append(ordered, v)
		}
	}
	vs := make([]variationModel, 0, len(ordered))
	for _, v := range ordered {
		text, err := encodeJSON(v.Value)
		if err != nil {
			diags.AddError("Encoding variation "+v.Key, err.Error())
			return m, diags
		}
		if p, ok := priorJSON[v.Key]; ok {
			if same, err := jsonEqual(p, text); err == nil && same {
				text = p
			}
		}
		vs = append(vs, variationModel{
			Key:         types.StringValue(v.Key),
			Name:        types.StringValue(v.Name),
			Description: types.StringValue(v.Description),
			ValueJSON:   newJSONValue(text),
		})
	}
	l, d := variationsToList(ctx, vs)
	diags.Append(d...)
	m.Variations = l
	return m, diags
}

// flagUpdateFromModel builds the flag patch. A null owner clears it; unknown
// values are left as they are.
func flagUpdateFromModel(ctx context.Context, m flagModel) (client.FlagUpdate, diag.Diagnostics) {
	var diags diag.Diagnostics
	in := client.FlagUpdate{
		Name:        new(m.Name.ValueString()),
		Description: new(m.Description.ValueString()),
	}
	if !m.Tags.IsNull() && !m.Tags.IsUnknown() {
		tags := []string{}
		diags.Append(m.Tags.ElementsAs(ctx, &tags, false)...)
		in.Tags = &tags
	}
	switch {
	case m.Owner.IsNull():
		in.ClearOwner = true
	case !m.Owner.IsUnknown():
		in.Owner = new(m.Owner.ValueString())
	}
	if !m.OffVariation.IsNull() && !m.OffVariation.IsUnknown() {
		in.OffVariation = new(m.OffVariation.ValueString())
	}
	return in, diags
}

// variationUpdate patches the variation Key.
type variationUpdate struct {
	Key    string
	Update client.VariationUpdate
}

// variationDiff turns the current variations into the desired ones.
type variationDiff struct {
	Create []client.VariationIn
	Update []variationUpdate
	Delete []string
}

// diffVariations matches variations by key and compares values as JSON.
func diffVariations(current []client.Variation, desired []variationModel) (variationDiff, error) {
	var d variationDiff
	byKey := make(map[string]client.Variation, len(current))
	for _, v := range current {
		byKey[v.Key] = v
	}
	wanted := make(map[string]bool, len(desired))
	for _, want := range desired {
		key := want.Key.ValueString()
		wanted[key] = true
		value, err := decodeJSON(want.ValueJSON.ValueString())
		if err != nil {
			return d, fmt.Errorf("variation %s: value_json: %w", key, err)
		}
		have, ok := byKey[key]
		if !ok {
			d.Create = append(d.Create, client.VariationIn{
				Key: key, Name: want.Name.ValueString(), Description: want.Description.ValueString(), Value: value,
			})
			continue
		}
		var u client.VariationUpdate
		changed := false
		if name := want.Name.ValueString(); name != have.Name {
			u.Name, changed = new(name), true
		}
		if desc := want.Description.ValueString(); desc != have.Description {
			u.Description, changed = new(desc), true
		}
		haveJSON, err := encodeJSON(have.Value)
		if err != nil {
			return d, fmt.Errorf("variation %s: %w", key, err)
		}
		if same, err := jsonEqual(haveJSON, want.ValueJSON.ValueString()); err != nil || !same {
			u.Value, changed = &value, true
		}
		if changed {
			d.Update = append(d.Update, variationUpdate{Key: key, Update: u})
		}
	}
	for _, v := range current {
		if !wanted[v.Key] {
			d.Delete = append(d.Delete, v.Key)
		}
	}
	return d, nil
}

// applyFlagUpdate changes flag current to match plan: it creates the new
// variations and updates the changed ones, patches the flag (so the off
// variation can move off a variation about to go), deletes the removed
// variations, then sets the lifecycle when it changed.
func applyFlagUpdate(ctx context.Context, c *client.Client, current *client.Flag, plan flagModel) diag.Diagnostics {
	var diags diag.Diagnostics
	project, key := plan.Project.ValueString(), plan.Key.ValueString()
	desired, d := variationsFromList(ctx, plan.Variations)
	diags.Append(d...)
	if diags.HasError() {
		return diags
	}
	var vd variationDiff
	if desired != nil {
		var err error
		if vd, err = diffVariations(current.Variations, desired); err != nil {
			diags.AddAttributeError(path.Root("variations"), "Invalid variation", err.Error())
			return diags
		}
	}
	for _, in := range vd.Create {
		if _, err := c.CreateVariation(ctx, project, key, in); err != nil {
			diags.Append(diagFromError(err, "Creating variation "+in.Key+" of flag "+key))
			return diags
		}
	}
	for _, u := range vd.Update {
		if _, err := c.UpdateVariation(ctx, project, key, u.Key, u.Update); err != nil {
			diags.Append(diagFromError(err, "Updating variation "+u.Key+" of flag "+key))
			return diags
		}
	}
	in, d := flagUpdateFromModel(ctx, plan)
	diags.Append(d...)
	if diags.HasError() {
		return diags
	}
	if _, err := c.UpdateFlag(ctx, project, key, in); err != nil {
		diags.Append(diagFromError(err, "Updating flag "+key))
		return diags
	}
	for _, v := range vd.Delete {
		if err := c.DeleteVariation(ctx, project, key, v); err != nil && !client.IsNotFound(err) {
			diags.Append(diagFromError(err, "Deleting variation "+v+" of flag "+key))
			return diags
		}
	}
	if permanent := plan.Permanent.ValueBool(); !plan.Permanent.IsUnknown() && permanent != (current.Lifecycle == "permanent") {
		if _, err := c.SetLifecycle(ctx, project, key, permanent); err != nil {
			diags.Append(diagFromError(err, "Setting the lifecycle of flag "+key))
		}
	}
	return diags
}

type flagResource struct {
	client *client.Client
}

func newFlagResource() resource.Resource { return &flagResource{} }

func (r *flagResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_flag"
}

func (r *flagResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	replace := []planmodifier.String{stringplanmodifier.RequiresReplace()}
	resp.Schema = schema.Schema{
		Description: "A feature flag and its variations. Its targeting per environment is megooflags_flag_environment.",
		Attributes: map[string]schema.Attribute{
			"project": schema.StringAttribute{
				Description:   "Project key. Changing it replaces the flag.",
				Required:      true,
				PlanModifiers: replace,
			},
			"key": schema.StringAttribute{
				Description:   "Flag key. Changing it replaces the flag.",
				Required:      true,
				PlanModifiers: replace,
			},
			"type": schema.StringAttribute{
				Description:   "Value type: boolean, string, number or json. Changing it replaces the flag.",
				Required:      true,
				PlanModifiers: replace,
				Validators:    []validator.String{stringvalidator.OneOf(flagTypes...)},
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
			"tags": schema.SetAttribute{
				Description: "Tags.",
				ElementType: types.StringType,
				Optional:    true,
				Computed:    true,
				Default:     setdefault.StaticValue(types.SetValueMust(types.StringType, []attr.Value{})),
			},
			"owner": schema.StringAttribute{
				Description: "Owner, for example a team or an email address.",
				Optional:    true,
			},
			"variations": schema.ListNestedAttribute{
				Description: "Values the flag can serve, matched by key. When omitted the server creates the defaults for the type (true and false for boolean flags).",
				Optional:    true,
				Computed:    true,
				PlanModifiers: []planmodifier.List{
					listplanmodifier.UseStateForUnknown(),
				},
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"key": schema.StringAttribute{
							Description: "Variation key.",
							Required:    true,
						},
						"name": schema.StringAttribute{
							Description: "Display name.",
							Optional:    true,
							Computed:    true,
							Default:     stringdefault.StaticString(""),
						},
						"description": schema.StringAttribute{
							Description: "Description.",
							Optional:    true,
							Computed:    true,
							Default:     stringdefault.StaticString(""),
						},
						"value_json": schema.StringAttribute{
							Description: "The value as JSON, for example jsonencode(true). Compared as JSON, so 1 and 1.0 are the same.",
							Required:    true,
							CustomType:  jsonValueType{},
						},
					},
				},
			},
			"off_variation": schema.StringAttribute{
				Description:   "Key of the variation served when the flag is off in an environment.",
				Optional:      true,
				Computed:      true,
				PlanModifiers: []planmodifier.String{stringplanmodifier.UseStateForUnknown()},
			},
			"permanent": schema.BoolAttribute{
				Description: "A permanent flag is never reported as stale.",
				Optional:    true,
				Computed:    true,
				Default:     booldefault.StaticBool(false),
			},
		},
	}
}

func (r *flagResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = providerClient(req.ProviderData, resp.Diagnostics.AddError)
}

func (r *flagResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan flagModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, key := plan.Project.ValueString(), plan.Key.ValueString()
	desired, diags := variationsFromList(ctx, plan.Variations)
	resp.Diagnostics.Append(diags...)
	in := client.FlagCreate{
		Key:         key,
		Name:        plan.Name.ValueString(),
		Description: plan.Description.ValueString(),
		Type:        plan.Type.ValueString(),
		Owner:       plan.Owner.ValueStringPointer(),
	}
	if !plan.Tags.IsNull() && !plan.Tags.IsUnknown() {
		resp.Diagnostics.Append(plan.Tags.ElementsAs(ctx, &in.Tags, false)...)
	}
	for _, v := range desired {
		value, err := decodeJSON(v.ValueJSON.ValueString())
		if err != nil {
			resp.Diagnostics.AddAttributeError(path.Root("variations"), "Invalid variation",
				fmt.Sprintf("variation %s: value_json: %v", v.Key.ValueString(), err))
			continue
		}
		in.Variations = append(in.Variations, client.VariationIn{
			Key: v.Key.ValueString(), Name: v.Name.ValueString(), Description: v.Description.ValueString(), Value: value,
		})
	}
	if !plan.OffVariation.IsUnknown() && !plan.OffVariation.IsNull() {
		in.OffVariation = new(plan.OffVariation.ValueString())
	}
	if resp.Diagnostics.HasError() {
		return
	}
	f, err := r.client.CreateFlag(ctx, project, in)
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Creating flag "+key))
		return
	}
	// Save the flag first, so a failure below leaves it in state (tainted)
	// instead of orphaned.
	m, diags := flagToModel(ctx, project, f, desired)
	resp.Diagnostics.Append(diags...)
	resp.Diagnostics.Append(resp.State.Set(ctx, m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	if plan.Permanent.ValueBool() {
		if _, err := r.client.SetLifecycle(ctx, project, key, true); err != nil {
			resp.Diagnostics.Append(diagFromError(err, "Setting the lifecycle of flag "+key))
			return
		}
	}
	r.readInto(ctx, project, key, desired, &resp.State, &resp.Diagnostics)
}

func (r *flagResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state flagModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, key := state.Project.ValueString(), state.Key.ValueString()
	prior, diags := variationsFromList(ctx, state.Variations)
	resp.Diagnostics.Append(diags...)
	f, err := r.client.GetFlag(ctx, project, key)
	if client.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Reading flag "+key))
		return
	}
	m, diags := flagToModel(ctx, project, f, prior)
	resp.Diagnostics.Append(diags...)
	resp.Diagnostics.Append(resp.State.Set(ctx, m)...)
}

func (r *flagResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan flagModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, key := plan.Project.ValueString(), plan.Key.ValueString()
	desired, diags := variationsFromList(ctx, plan.Variations)
	resp.Diagnostics.Append(diags...)
	current, err := r.client.GetFlag(ctx, project, key)
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Reading flag "+key))
		return
	}
	resp.Diagnostics.Append(applyFlagUpdate(ctx, r.client, current, plan)...)
	r.readInto(ctx, project, key, desired, &resp.State, &resp.Diagnostics)
}

func (r *flagResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state flagModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, key := state.Project.ValueString(), state.Key.ValueString()
	// The API only deletes archived flags; archiving an archived flag is a
	// no-op.
	if _, err := r.client.ArchiveFlag(ctx, project, key); err != nil {
		if !client.IsNotFound(err) {
			resp.Diagnostics.Append(diagFromError(err, "Archiving flag "+key))
		}
		return
	}
	if err := r.client.DeleteFlag(ctx, project, key); err != nil && !client.IsNotFound(err) {
		resp.Diagnostics.Append(diagFromError(err, "Deleting flag "+key))
	}
}

func (r *flagResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	project, key, err := parseFlagID(req.ID)
	if err != nil {
		resp.Diagnostics.AddError("Invalid import ID", err.Error())
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("project"), project)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("key"), key)...)
}

// readInto reads the flag and stores it in state, so the state matches the
// server even after a failed change.
func (r *flagResource) readInto(ctx context.Context, project, key string, prior []variationModel, state *tfsdk.State, diags *diag.Diagnostics) {
	f, err := r.client.GetFlag(ctx, project, key)
	if err != nil {
		diags.Append(diagFromError(err, "Reading flag "+key))
		return
	}
	m, d := flagToModel(ctx, project, f, prior)
	diags.Append(d...)
	diags.Append(state.Set(ctx, m)...)
}
