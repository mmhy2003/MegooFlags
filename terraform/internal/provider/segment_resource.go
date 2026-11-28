package provider

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/listdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/setdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

var (
	_ resource.ResourceWithConfigure      = (*segmentResource)(nil)
	_ resource.ResourceWithImportState    = (*segmentResource)(nil)
	_ resource.ResourceWithValidateConfig = (*segmentResource)(nil)
)

// segmentModel is the megooflags_segment resource.
type segmentModel struct {
	Project     types.String       `tfsdk:"project"`
	Key         types.String       `tfsdk:"key"`
	Name        types.String       `tfsdk:"name"`
	Description types.String       `tfsdk:"description"`
	Included    types.Set          `tfsdk:"included"`
	Excluded    types.Set          `tfsdk:"excluded"`
	Rules       []segmentRuleModel `tfsdk:"rules"`
}

type segmentRuleModel struct {
	Description types.String  `tfsdk:"description"`
	Clauses     []clauseModel `tfsdk:"clauses"`
}

var segmentRuleObjectType = types.ObjectType{AttrTypes: map[string]attr.Type{
	"description": types.StringType,
	"clauses":     types.ListType{ElemType: clauseObjectType},
}}

// parseImportID splits an import ID of the given form, such as
// "project/key", into that many non-empty parts.
func parseImportID(id, form string) ([]string, error) {
	parts := strings.Split(id, "/")
	if len(parts) != strings.Count(form, "/")+1 || slices.Contains(parts, "") {
		return nil, fmt.Errorf("expected an import ID of the form %s, got %q", form, id)
	}
	return parts, nil
}

// segmentToModel maps an API segment to the model. prior (the plan or
// state, may be nil) supplies the clause values text to keep.
func segmentToModel(ctx context.Context, project string, s *client.Segment, prior *segmentModel) (segmentModel, diag.Diagnostics) {
	var diags diag.Diagnostics
	set := func(values []string) types.Set {
		if values == nil {
			values = []string{}
		}
		v, d := types.SetValueFrom(ctx, types.StringType, values)
		diags.Append(d...)
		return v
	}
	m := segmentModel{
		Project:     types.StringValue(project),
		Key:         types.StringValue(s.Key),
		Name:        types.StringValue(s.Name),
		Description: types.StringValue(s.Description),
		Included:    set(s.Included),
		Excluded:    set(s.Excluded),
		Rules:       []segmentRuleModel{},
	}
	for i, r := range s.Rules {
		var priorClauses []clauseModel
		if prior != nil && i < len(prior.Rules) {
			priorClauses = prior.Rules[i].Clauses
		}
		m.Rules = append(m.Rules, segmentRuleModel{
			Description: types.StringValue(r.Description),
			Clauses:     clausesToModel(r.Clauses, priorClauses),
		})
	}
	return m, diags
}

// segmentConfigFromModel builds the config PUT body.
func segmentConfigFromModel(ctx context.Context, m segmentModel) (client.SegmentConfig, diag.Diagnostics) {
	var diags diag.Diagnostics
	in := client.SegmentConfig{Included: []string{}, Excluded: []string{}, Rules: []client.SegmentRule{}}
	diags.Append(m.Included.ElementsAs(ctx, &in.Included, false)...)
	diags.Append(m.Excluded.ElementsAs(ctx, &in.Excluded, false)...)
	for i, r := range m.Rules {
		clauses, err := clausesFromModel(r.Clauses)
		if err != nil {
			diags.AddAttributeError(path.Root("rules"), "Invalid clause values", fmt.Sprintf("rules[%d].%s", i, err))
			return in, diags
		}
		in.Rules = append(in.Rules, client.SegmentRule{Description: r.Description.ValueString(), Clauses: clauses})
	}
	return in, diags
}

// segmentUpdateFromModel builds the details patch, and reports whether
// the name or description differ from current.
func segmentUpdateFromModel(current *client.Segment, m segmentModel) (client.SegmentUpdate, bool) {
	var in client.SegmentUpdate
	if name := m.Name.ValueString(); name != current.Name {
		in.Name = new(name)
	}
	if desc := m.Description.ValueString(); desc != current.Description {
		in.Description = new(desc)
	}
	return in, in.Name != nil || in.Description != nil
}

// segmentDeleteError is the diagnostic for a failed delete. An in_use
// error lists the flags, with their environments, whose rules use it.
func segmentDeleteError(err error, key string) diag.Diagnostic {
	var flags []string
	envs := map[string][]string{}
	var e *client.Error
	if errors.As(err, &e) && e.Code == "in_use" {
		details, _ := e.Details.(map[string]any)
		usedBy, _ := details["used_by"].([]any)
		for _, u := range usedBy {
			entry, _ := u.(map[string]any)
			flag, _ := entry["flag"].(string)
			env, _ := entry["environment"].(string)
			if flag == "" {
				continue
			}
			if _, seen := envs[flag]; !seen {
				flags = append(flags, flag)
			}
			envs[flag] = append(envs[flag], env)
		}
	}
	if len(flags) == 0 {
		return diagFromError(err, "Deleting segment "+key)
	}
	uses := make([]string, 0, len(flags))
	for _, f := range flags {
		uses = append(uses, f+" ("+strings.Join(envs[f], ", ")+")")
	}
	return diag.NewErrorDiagnostic("Deleting segment "+key+" failed",
		"Segment "+key+" is used by the rules of "+strings.Join(uses, ", ")+". Remove it from those rules first.")
}

type segmentResource struct {
	client *client.Client
}

func newSegmentResource() resource.Resource { return &segmentResource{} }

func (r *segmentResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_segment"
}

func (r *segmentResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	replace := []planmodifier.String{stringplanmodifier.RequiresReplace()}
	emptySet := setdefault.StaticValue(types.SetValueMust(types.StringType, []attr.Value{}))
	resp.Schema = schema.Schema{
		Description: "A segment: a reusable group of contexts that flag rules target with the in_segment operator. " +
			"A context is in the segment when it is included, or matches a rule and is not excluded.",
		Attributes: map[string]schema.Attribute{
			"project": schema.StringAttribute{
				Description:   "Project key. Changing it replaces the resource.",
				Required:      true,
				PlanModifiers: replace,
			},
			"key": schema.StringAttribute{
				Description:   "Segment key. Changing it replaces the resource.",
				Required:      true,
				PlanModifiers: replace,
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
			"included": schema.SetAttribute{
				Description: "Context keys always in the segment.",
				ElementType: types.StringType,
				Optional:    true,
				Computed:    true,
				Default:     emptySet,
			},
			"excluded": schema.SetAttribute{
				Description: "Context keys never in the segment, even when a rule matches.",
				ElementType: types.StringType,
				Optional:    true,
				Computed:    true,
				Default:     emptySet,
			},
			"rules": schema.ListNestedAttribute{
				Description: "Rules. A context matching any rule is in the segment; a rule matches when all of its clauses match.",
				Optional:    true,
				Computed:    true,
				Default:     listdefault.StaticValue(types.ListValueMust(segmentRuleObjectType, nil)),
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"description": schema.StringAttribute{
							Description: "Description.",
							Optional:    true,
							Computed:    true,
							Default:     stringdefault.StaticString(""),
						},
						"clauses": clausesAttribute(),
					},
				},
			},
		},
	}
}

func (r *segmentResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = providerClient(req.ProviderData, resp.Diagnostics.AddError)
}

func (r *segmentResource) ValidateConfig(ctx context.Context, req resource.ValidateConfigRequest, resp *resource.ValidateConfigResponse) {
	var cfg segmentModel
	diags := req.Config.Get(ctx, &cfg)
	if diags.HasError() {
		// Whole lists that are still unknown cannot be read into the
		// model; they are checked once they are known.
		if req.Config.Raw.IsFullyKnown() {
			resp.Diagnostics.Append(diags...)
		}
		return
	}
	for i, rule := range cfg.Rules {
		for j, c := range rule.Clauses {
			if c.ValuesJSON.IsNull() || c.ValuesJSON.IsUnknown() {
				continue
			}
			if _, err := clauseValues(c.ValuesJSON.ValueString()); err != nil {
				resp.Diagnostics.AddAttributeError(path.Root("rules").AtListIndex(i).AtName("clauses").AtListIndex(j).AtName("values_json"),
					"Invalid clause values", err.Error())
			}
		}
	}
}

// putConfig PUTs plan's config and stores the result in state.
func (r *segmentResource) putConfig(ctx context.Context, plan segmentModel, state *tfsdk.State, diags *diag.Diagnostics) {
	project, key := plan.Project.ValueString(), plan.Key.ValueString()
	in, d := segmentConfigFromModel(ctx, plan)
	diags.Append(d...)
	if diags.HasError() {
		return
	}
	s, err := r.client.PutSegmentConfig(ctx, project, key, in)
	if err != nil {
		diags.Append(diagFromError(err, "Updating the config of segment "+key))
		return
	}
	m, d := segmentToModel(ctx, project, s, &plan)
	diags.Append(d...)
	diags.Append(state.Set(ctx, m)...)
}

func (r *segmentResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan segmentModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, key := plan.Project.ValueString(), plan.Key.ValueString()
	s, err := r.client.CreateSegment(ctx, project, client.SegmentCreate{
		Key:         key,
		Name:        plan.Name.ValueString(),
		Description: plan.Description.ValueString(),
	})
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Creating segment "+key))
		return
	}
	// Store the empty segment first, so a failed config is updated on the
	// next apply instead of orphaned.
	m, diags := segmentToModel(ctx, project, s, &plan)
	resp.Diagnostics.Append(diags...)
	resp.Diagnostics.Append(resp.State.Set(ctx, m)...)
	if resp.Diagnostics.HasError() {
		return
	}
	r.putConfig(ctx, plan, &resp.State, &resp.Diagnostics)
}

func (r *segmentResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state segmentModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, key := state.Project.ValueString(), state.Key.ValueString()
	s, err := r.client.GetSegment(ctx, project, key)
	if client.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Reading segment "+key))
		return
	}
	m, diags := segmentToModel(ctx, project, s, &state)
	resp.Diagnostics.Append(diags...)
	resp.Diagnostics.Append(resp.State.Set(ctx, m)...)
}

func (r *segmentResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan, state segmentModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, key := plan.Project.ValueString(), plan.Key.ValueString()
	current := &client.Segment{Name: state.Name.ValueString(), Description: state.Description.ValueString()}
	if in, changed := segmentUpdateFromModel(current, plan); changed {
		if _, err := r.client.UpdateSegment(ctx, project, key, in); err != nil {
			resp.Diagnostics.Append(diagFromError(err, "Updating segment "+key))
			return
		}
	}
	r.putConfig(ctx, plan, &resp.State, &resp.Diagnostics)
}

func (r *segmentResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state segmentModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	key := state.Key.ValueString()
	if err := r.client.DeleteSegment(ctx, state.Project.ValueString(), key); err != nil && !client.IsNotFound(err) {
		resp.Diagnostics.Append(segmentDeleteError(err, key))
	}
}

func (r *segmentResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	parts, err := parseImportID(req.ID, "project/key")
	if err != nil {
		resp.Diagnostics.AddError("Invalid import ID", err.Error())
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("project"), parts[0])...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("key"), parts[1])...)
}
