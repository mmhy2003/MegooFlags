package provider

import (
	"context"
	"fmt"
	"maps"
	"slices"
	"strings"

	"github.com/hashicorp/terraform-plugin-framework-validators/stringvalidator"
	"github.com/hashicorp/terraform-plugin-framework/attr"
	"github.com/hashicorp/terraform-plugin-framework/diag"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/booldefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/listdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/planmodifier"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringdefault"
	"github.com/hashicorp/terraform-plugin-framework/resource/schema/stringplanmodifier"
	"github.com/hashicorp/terraform-plugin-framework/schema/validator"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

var (
	_ resource.ResourceWithConfigure      = (*flagEnvironmentResource)(nil)
	_ resource.ResourceWithImportState    = (*flagEnvironmentResource)(nil)
	_ resource.ResourceWithValidateConfig = (*flagEnvironmentResource)(nil)
)

// operators are the clause operators the API accepts.
var operators = []string{
	"in", "not_in", "contains", "starts_with", "ends_with", "matches",
	"lt", "lte", "gt", "gte", "semver_eq", "semver_lt", "semver_gt",
	"before", "after", "in_segment",
}

// rolloutTotal is the sum of a rollout's weights (100%).
const rolloutTotal = 100000

// flagEnvironmentModel is the megooflags_flag_environment resource.
type flagEnvironmentModel struct {
	Project       types.String        `tfsdk:"project"`
	Flag          types.String        `tfsdk:"flag"`
	Environment   types.String        `tfsdk:"environment"`
	Enabled       types.Bool          `tfsdk:"enabled"`
	Targets       []targetModel       `tfsdk:"targets"`
	Rules         []ruleModel         `tfsdk:"rules"`
	Fallthrough   *serveModel         `tfsdk:"fallthrough"`
	Prerequisites []prerequisiteModel `tfsdk:"prerequisites"`
	Version       types.Int64         `tfsdk:"version"`
}

type targetModel struct {
	Variation types.String `tfsdk:"variation"`
	Values    []string     `tfsdk:"values"`
}

type ruleModel struct {
	Description types.String  `tfsdk:"description"`
	Clauses     []clauseModel `tfsdk:"clauses"`
	Variation   types.String  `tfsdk:"variation"`
	Rollout     *rolloutModel `tfsdk:"rollout"`
}

type clauseModel struct {
	Attribute  types.String `tfsdk:"attribute"`
	Operator   types.String `tfsdk:"operator"`
	ValuesJSON jsonValue    `tfsdk:"values_json"`
	Negate     types.Bool   `tfsdk:"negate"`
}

type serveModel struct {
	Variation types.String  `tfsdk:"variation"`
	Rollout   *rolloutModel `tfsdk:"rollout"`
}

type rolloutModel struct {
	BucketBy types.String  `tfsdk:"bucket_by"`
	Weights  []weightModel `tfsdk:"weights"`
}

type weightModel struct {
	Variation types.String `tfsdk:"variation"`
	Weight    types.Int64  `tfsdk:"weight"`
}

type prerequisiteModel struct {
	Flag      types.String `tfsdk:"flag"`
	Variation types.String `tfsdk:"variation"`
}

var (
	rolloutObjectType = types.ObjectType{AttrTypes: map[string]attr.Type{
		"bucket_by": types.StringType,
		"weights": types.ListType{ElemType: types.ObjectType{AttrTypes: map[string]attr.Type{
			"variation": types.StringType,
			"weight":    types.Int64Type,
		}}},
	}}
	targetObjectType = types.ObjectType{AttrTypes: map[string]attr.Type{
		"variation": types.StringType,
		"values":    types.ListType{ElemType: types.StringType},
	}}
	clauseObjectType = types.ObjectType{AttrTypes: map[string]attr.Type{
		"attribute":   types.StringType,
		"operator":    types.StringType,
		"values_json": jsonValueType{},
		"negate":      types.BoolType,
	}}
	ruleObjectType = types.ObjectType{AttrTypes: map[string]attr.Type{
		"description": types.StringType,
		"clauses":     types.ListType{ElemType: clauseObjectType},
		"variation":   types.StringType,
		"rollout":     rolloutObjectType,
	}}
	prerequisiteObjectType = types.ObjectType{AttrTypes: map[string]attr.Type{
		"flag":      types.StringType,
		"variation": types.StringType,
	}}
)

// parseFlagEnvironmentID splits an import ID "project/flag/environment".
func parseFlagEnvironmentID(id string) (project, flag, environment string, err error) {
	parts := strings.Split(id, "/")
	if len(parts) != 3 || slices.Contains(parts, "") {
		return "", "", "", fmt.Errorf("expected an import ID of the form project/flag/environment, got %q", id)
	}
	return parts[0], parts[1], parts[2], nil
}

// clauseValues decodes values_json, which must be a JSON array. Numbers
// stay json.Number, so they reach the API unchanged.
func clauseValues(s string) ([]any, error) {
	v, err := decodeJSON(s)
	if err != nil {
		return nil, err
	}
	values, ok := v.([]any)
	if !ok {
		return nil, fmt.Errorf("values_json must be a JSON array, for example jsonencode([\"a\", 1])")
	}
	return values, nil
}

func rolloutFromModel(r *rolloutModel) *client.Rollout {
	if r == nil {
		return nil
	}
	out := &client.Rollout{BucketBy: r.BucketBy.ValueString(), Weights: []client.Weight{}}
	for _, w := range r.Weights {
		out.Weights = append(out.Weights, client.Weight{Variation: w.Variation.ValueString(), Weight: int(w.Weight.ValueInt64())})
	}
	return out
}

func serveFromModel(s *serveModel) client.Serve {
	if s == nil {
		return client.Serve{}
	}
	return client.Serve{Variation: s.Variation.ValueStringPointer(), Rollout: rolloutFromModel(s.Rollout)}
}

// clausesFromModel decodes each clause's values_json. An error names the
// clause, as in "clauses[1]: ...".
func clausesFromModel(cs []clauseModel) ([]client.Clause, error) {
	var out []client.Clause
	for j, c := range cs {
		values, err := clauseValues(c.ValuesJSON.ValueString())
		if err != nil {
			return nil, fmt.Errorf("clauses[%d]: %w", j, err)
		}
		out = append(out, client.Clause{
			Attribute: c.Attribute.ValueString(),
			Operator:  c.Operator.ValueString(),
			Values:    values,
			Negate:    c.Negate.ValueBool(),
		})
	}
	return out, nil
}

// flagConfigInFromModel builds the PUT body; the caller sets the version.
func flagConfigInFromModel(m flagEnvironmentModel) (client.FlagConfigIn, error) {
	in := client.FlagConfigIn{
		Enabled:     m.Enabled.ValueBool(),
		Fallthrough: serveFromModel(m.Fallthrough),
	}
	for _, t := range m.Targets {
		in.Targets = append(in.Targets, client.Target{Variation: t.Variation.ValueString(), Values: t.Values})
	}
	for i, r := range m.Rules {
		rule := client.Rule{
			Description: r.Description.ValueString(),
			Variation:   r.Variation.ValueStringPointer(),
			Rollout:     rolloutFromModel(r.Rollout),
		}
		clauses, err := clausesFromModel(r.Clauses)
		if err != nil {
			return in, fmt.Errorf("rules[%d].%w", i, err)
		}
		rule.Clauses = clauses
		in.Rules = append(in.Rules, rule)
	}
	for _, p := range m.Prerequisites {
		in.Prerequisites = append(in.Prerequisites, client.Prerequisite{Flag: p.Flag.ValueString(), Variation: p.Variation.ValueString()})
	}
	return in, nil
}

// rolloutToModel maps an API rollout to the model. The API puts weights in
// variation order; prior's order (may be nil) is kept when it has the same
// weights.
func rolloutToModel(r *client.Rollout, prior *rolloutModel) *rolloutModel {
	if r == nil {
		return nil
	}
	bucketBy := r.BucketBy
	if bucketBy == "" {
		bucketBy = "key"
	}
	out := &rolloutModel{BucketBy: types.StringValue(bucketBy), Weights: []weightModel{}}
	for _, w := range r.Weights {
		out.Weights = append(out.Weights, weightModel{Variation: types.StringValue(w.Variation), Weight: types.Int64Value(int64(w.Weight))})
	}
	if prior != nil && sameWeights(prior.Weights, out.Weights) {
		out.Weights = slices.Clone(prior.Weights)
	}
	return out
}

// sameWeights reports whether a and b give the same variations the same
// weights, in any order.
func sameWeights(a, b []weightModel) bool {
	if len(a) != len(b) {
		return false
	}
	shares := map[string]int64{}
	for _, w := range a {
		if w.Variation.IsUnknown() || w.Weight.IsUnknown() {
			return false
		}
		shares[w.Variation.ValueString()] = w.Weight.ValueInt64()
	}
	for _, w := range b {
		if s, ok := shares[w.Variation.ValueString()]; !ok || s != w.Weight.ValueInt64() {
			return false
		}
	}
	return len(shares) == len(b)
}

// targetsToModel maps API targets to the model. The API keeps one list per
// variation, in variation order, with sorted values; prior's targets (may be
// nil) are kept when they hold the same values for each variation.
func targetsToModel(ts []client.Target, prior []targetModel) []targetModel {
	out := []targetModel{}
	for _, t := range ts {
		values := t.Values
		if values == nil {
			values = []string{}
		}
		out = append(out, targetModel{Variation: types.StringValue(t.Variation), Values: values})
	}
	if prior != nil && maps.EqualFunc(targetSets(prior), targetSets(out), slices.Equal) {
		return slices.Clone(prior)
	}
	return out
}

// targetSets is each variation's sorted values; unknown variations count as
// different from everything.
func targetSets(ts []targetModel) map[string][]string {
	out := map[string][]string{}
	for i, t := range ts {
		key := t.Variation.ValueString()
		if t.Variation.IsUnknown() {
			key = fmt.Sprintf("\x00unknown-%d", i)
		}
		out[key] = append(out[key], t.Values...)
	}
	for k, v := range out {
		if len(v) == 0 {
			delete(out, k)
		} else {
			slices.Sort(v)
		}
	}
	return out
}

// clauseText is the values_json of an API clause. It keeps prior's text
// when the values are the same, and for in_segment, whose keys the API
// sorts, when they are the same set.
func clauseText(c client.Clause, prior *clauseModel) string {
	values := c.Values
	if values == nil {
		values = []any{}
	}
	text, err := encodeJSON(values)
	if err != nil || prior == nil || prior.Operator.ValueString() != c.Operator {
		return text
	}
	p := prior.ValuesJSON.ValueString()
	if same, err := jsonEqual(p, text); err == nil && same {
		return p
	}
	if c.Operator == "in_segment" && sameKeySet(p, values) {
		return p
	}
	return text
}

// sameKeySet reports whether the JSON array text holds the same strings
// as values, in any order.
func sameKeySet(text string, values []any) bool {
	priorValues, err := clauseValues(text)
	if err != nil || len(priorValues) != len(values) {
		return false
	}
	keys := func(vs []any) []string {
		out := make([]string, 0, len(vs))
		for _, v := range vs {
			s, ok := v.(string)
			if !ok {
				return nil
			}
			out = append(out, s)
		}
		slices.Sort(out)
		return out
	}
	a, b := keys(priorValues), keys(values)
	return a != nil && b != nil && slices.Equal(a, b)
}

// clausesToModel maps API clauses to the model; prior (may be shorter or
// nil) supplies the values text to keep, clause by clause.
func clausesToModel(cs []client.Clause, prior []clauseModel) []clauseModel {
	out := []clauseModel{}
	for j, c := range cs {
		var p *clauseModel
		if j < len(prior) {
			p = &prior[j]
		}
		out = append(out, clauseModel{
			Attribute:  types.StringValue(c.Attribute),
			Operator:   types.StringValue(c.Operator),
			ValuesJSON: newJSONValue(clauseText(c, p)),
			Negate:     types.BoolValue(c.Negate),
		})
	}
	return out
}

// flagConfigToModel maps an API config to the model. prior (the plan or
// state, may be nil) supplies the clause values text and the order of
// targets and weights to keep.
func flagConfigToModel(project, flag string, c *client.FlagConfig, prior *flagEnvironmentModel) flagEnvironmentModel {
	m := flagEnvironmentModel{
		Project:     types.StringValue(project),
		Flag:        types.StringValue(flag),
		Environment: types.StringValue(c.Environment),
		Enabled:     types.BoolValue(c.Enabled),
		Targets:     []targetModel{},
		Rules:       []ruleModel{},
		Fallthrough: &serveModel{
			Variation: types.StringPointerValue(c.Fallthrough.Variation),
		},
		Prerequisites: []prerequisiteModel{},
		Version:       types.Int64Value(int64(c.Version)),
	}
	var priorTargets []targetModel
	var priorFallthrough *rolloutModel
	if prior != nil {
		priorTargets = prior.Targets
		if prior.Fallthrough != nil {
			priorFallthrough = prior.Fallthrough.Rollout
		}
	}
	m.Targets = targetsToModel(c.Targets, priorTargets)
	m.Fallthrough.Rollout = rolloutToModel(c.Fallthrough.Rollout, priorFallthrough)
	for i, r := range c.Rules {
		var priorClauses []clauseModel
		var priorRollout *rolloutModel
		if prior != nil && i < len(prior.Rules) {
			priorClauses = prior.Rules[i].Clauses
			priorRollout = prior.Rules[i].Rollout
		}
		m.Rules = append(m.Rules, ruleModel{
			Description: types.StringValue(r.Description),
			Clauses:     clausesToModel(r.Clauses, priorClauses),
			Variation:   types.StringPointerValue(r.Variation),
			Rollout:     rolloutToModel(r.Rollout, priorRollout),
		})
	}
	for _, p := range c.Prerequisites {
		m.Prerequisites = append(m.Prerequisites, prerequisiteModel{Flag: types.StringValue(p.Flag), Variation: types.StringValue(p.Variation)})
	}
	return m
}

// validateServe checks that s serves exactly one of a variation and a
// rollout, and that the rollout's weights add up to 100%. Unknown values
// are not checked.
func validateServe(p path.Path, variation types.String, rollout *rolloutModel, diags *diag.Diagnostics) {
	if variation.IsUnknown() {
		return
	}
	if variation.IsNull() == (rollout == nil) {
		diags.AddAttributeError(p, "Invalid serve", "Set exactly one of variation and rollout.")
		return
	}
	if rollout == nil {
		return
	}
	total := int64(0)
	for _, w := range rollout.Weights {
		if w.Weight.IsUnknown() {
			return
		}
		total += w.Weight.ValueInt64()
	}
	if total != rolloutTotal {
		diags.AddAttributeError(p.AtName("rollout").AtName("weights"), "Invalid rollout",
			fmt.Sprintf("The weights add up to %d; they must add up to %d (100%%, in thousandths of a percent).", total, rolloutTotal))
	}
}

type flagEnvironmentResource struct {
	client *client.Client
}

func newFlagEnvironmentResource() resource.Resource { return &flagEnvironmentResource{} }

func (r *flagEnvironmentResource) Metadata(_ context.Context, req resource.MetadataRequest, resp *resource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_flag_environment"
}

func rolloutAttribute(description string) schema.SingleNestedAttribute {
	return schema.SingleNestedAttribute{
		Description: description,
		Optional:    true,
		Attributes: map[string]schema.Attribute{
			"bucket_by": schema.StringAttribute{
				Description: "Context attribute hashed to pick a variation.",
				Optional:    true,
				Computed:    true,
				Default:     stringdefault.StaticString("key"),
			},
			"weights": schema.ListNestedAttribute{
				Description: "Each variation's share in thousandths of a percent; they add up to 100000.",
				Required:    true,
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"variation": schema.StringAttribute{Description: "Variation key.", Required: true},
						"weight":    schema.Int64Attribute{Description: "Share, 1000 = 1%.", Required: true},
					},
				},
			},
		},
	}
}

// clausesAttribute is the clauses of a flag rule or a segment rule.
func clausesAttribute() schema.ListNestedAttribute {
	return schema.ListNestedAttribute{
		Description: "Conditions on the context.",
		Required:    true,
		NestedObject: schema.NestedAttributeObject{
			Attributes: map[string]schema.Attribute{
				"attribute": schema.StringAttribute{
					Description: "Context attribute.",
					Optional:    true,
					Computed:    true,
					Default:     stringdefault.StaticString("key"),
				},
				"operator": schema.StringAttribute{
					Description: "Operator: " + strings.Join(operators, ", ") + ".",
					Required:    true,
					Validators:  []validator.String{stringvalidator.OneOf(operators...)},
				},
				"values_json": schema.StringAttribute{
					Description: "The values as a JSON array, for example jsonencode([\"gold\", 10]), so numbers and strings keep their types. For in_segment, the segment keys.",
					Required:    true,
					CustomType:  jsonValueType{},
				},
				"negate": schema.BoolAttribute{
					Description: "Invert the result.",
					Optional:    true,
					Computed:    true,
					Default:     booldefault.StaticBool(false),
				},
			},
		},
	}
}

func (r *flagEnvironmentResource) Schema(_ context.Context, _ resource.SchemaRequest, resp *resource.SchemaResponse) {
	replace := []planmodifier.String{stringplanmodifier.RequiresReplace()}
	resp.Schema = schema.Schema{
		Description: "A flag's targeting in one environment. Destroying it turns the flag off there and clears its targeting.",
		Attributes: map[string]schema.Attribute{
			"project": schema.StringAttribute{
				Description:   "Project key. Changing it replaces the resource.",
				Required:      true,
				PlanModifiers: replace,
			},
			"flag": schema.StringAttribute{
				Description:   "Flag key. Changing it replaces the resource.",
				Required:      true,
				PlanModifiers: replace,
			},
			"environment": schema.StringAttribute{
				Description:   "Environment key. Changing it replaces the resource.",
				Required:      true,
				PlanModifiers: replace,
			},
			"enabled": schema.BoolAttribute{
				Description: "Whether the flag is on in the environment.",
				Required:    true,
			},
			"targets": schema.ListNestedAttribute{
				Description: "Context keys served a variation directly.",
				Optional:    true,
				Computed:    true,
				Default:     listdefault.StaticValue(types.ListValueMust(targetObjectType, nil)),
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"variation": schema.StringAttribute{Description: "Variation key.", Required: true},
						"values": schema.ListAttribute{
							Description: "Context keys.",
							ElementType: types.StringType,
							Required:    true,
						},
					},
				},
			},
			"rules": schema.ListNestedAttribute{
				Description: "Rules, checked in order. A rule matches when all of its clauses match.",
				Optional:    true,
				Computed:    true,
				Default:     listdefault.StaticValue(types.ListValueMust(ruleObjectType, nil)),
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"description": schema.StringAttribute{
							Description: "Description.",
							Optional:    true,
							Computed:    true,
							Default:     stringdefault.StaticString(""),
						},
						"clauses": clausesAttribute(),
						"variation": schema.StringAttribute{
							Description: "Variation served when the rule matches. Set this or rollout.",
							Optional:    true,
						},
						"rollout": rolloutAttribute("Percentage rollout served when the rule matches. Set this or variation."),
					},
				},
			},
			"fallthrough": schema.SingleNestedAttribute{
				Description: "What is served when no target or rule matches.",
				Required:    true,
				Attributes: map[string]schema.Attribute{
					"variation": schema.StringAttribute{
						Description: "Variation served. Set this or rollout.",
						Optional:    true,
					},
					"rollout": rolloutAttribute("Percentage rollout. Set this or variation."),
				},
			},
			"prerequisites": schema.ListNestedAttribute{
				Description: "Flags that must serve a variation before this flag is evaluated.",
				Optional:    true,
				Computed:    true,
				Default:     listdefault.StaticValue(types.ListValueMust(prerequisiteObjectType, nil)),
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"flag":      schema.StringAttribute{Description: "Prerequisite flag key.", Required: true},
						"variation": schema.StringAttribute{Description: "Variation it must serve.", Required: true},
					},
				},
			},
			"version": schema.Int64Attribute{
				Description: "Version of the configuration, raised on every change.",
				Computed:    true,
			},
		},
	}
}

func (r *flagEnvironmentResource) Configure(_ context.Context, req resource.ConfigureRequest, resp *resource.ConfigureResponse) {
	r.client = providerClient(req.ProviderData, resp.Diagnostics.AddError)
}

func (r *flagEnvironmentResource) ValidateConfig(ctx context.Context, req resource.ValidateConfigRequest, resp *resource.ValidateConfigResponse) {
	var cfg flagEnvironmentModel
	diags := req.Config.Get(ctx, &cfg)
	if diags.HasError() {
		// Whole lists or objects that are still unknown cannot be read
		// into the model; they are checked once they are known.
		if req.Config.Raw.IsFullyKnown() {
			resp.Diagnostics.Append(diags...)
		}
		return
	}
	for i, rule := range cfg.Rules {
		p := path.Root("rules").AtListIndex(i)
		validateServe(p, rule.Variation, rule.Rollout, &resp.Diagnostics)
		for j, c := range rule.Clauses {
			if c.ValuesJSON.IsNull() || c.ValuesJSON.IsUnknown() {
				continue
			}
			if _, err := clauseValues(c.ValuesJSON.ValueString()); err != nil {
				resp.Diagnostics.AddAttributeError(p.AtName("clauses").AtListIndex(j).AtName("values_json"),
					"Invalid clause values", err.Error())
			}
		}
	}
	if cfg.Fallthrough != nil {
		validateServe(path.Root("fallthrough"), cfg.Fallthrough.Variation, cfg.Fallthrough.Rollout, &resp.Diagnostics)
	}
}

// putFlagConfig reads the current config for its version, then PUTs what
// build makes of it. On version_conflict it reads again and retries once.
func putFlagConfig(ctx context.Context, c *client.Client, project, flag, env string, build func(*client.FlagConfig) client.FlagConfigIn) (*client.FlagConfig, error) {
	for attempt := 0; ; attempt++ {
		current, err := c.GetFlagConfig(ctx, project, flag, env)
		if err != nil {
			return nil, err
		}
		in := build(current)
		in.Version = current.Version
		out, err := c.PutFlagConfig(ctx, project, flag, env, in)
		if attempt == 0 && client.IsCode(err, "version_conflict") {
			continue
		}
		return out, err
	}
}

// apply PUTs plan and stores the result in state.
func (r *flagEnvironmentResource) apply(ctx context.Context, plan flagEnvironmentModel, setState func(any) diag.Diagnostics) diag.Diagnostics {
	var diags diag.Diagnostics
	in, err := flagConfigInFromModel(plan)
	if err != nil {
		diags.AddAttributeError(path.Root("rules"), "Invalid clause values", err.Error())
		return diags
	}
	project, flag := plan.Project.ValueString(), plan.Flag.ValueString()
	out, err := putFlagConfig(ctx, r.client, project, flag, plan.Environment.ValueString(),
		func(*client.FlagConfig) client.FlagConfigIn { return in })
	if err != nil {
		diags.Append(diagFromError(err, "Updating the flag configuration"))
		return diags
	}
	return setState(flagConfigToModel(project, flag, out, &plan))
}

func (r *flagEnvironmentResource) Create(ctx context.Context, req resource.CreateRequest, resp *resource.CreateResponse) {
	var plan flagEnvironmentModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	resp.Diagnostics.Append(r.apply(ctx, plan, func(m any) diag.Diagnostics { return resp.State.Set(ctx, m) })...)
}

func (r *flagEnvironmentResource) Read(ctx context.Context, req resource.ReadRequest, resp *resource.ReadResponse) {
	var state flagEnvironmentModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, flag := state.Project.ValueString(), state.Flag.ValueString()
	c, err := r.client.GetFlagConfig(ctx, project, flag, state.Environment.ValueString())
	if client.IsNotFound(err) {
		resp.State.RemoveResource(ctx)
		return
	}
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Reading the flag configuration"))
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, flagConfigToModel(project, flag, c, &state))...)
}

func (r *flagEnvironmentResource) Update(ctx context.Context, req resource.UpdateRequest, resp *resource.UpdateResponse) {
	var plan flagEnvironmentModel
	resp.Diagnostics.Append(req.Plan.Get(ctx, &plan)...)
	if resp.Diagnostics.HasError() {
		return
	}
	resp.Diagnostics.Append(r.apply(ctx, plan, func(m any) diag.Diagnostics { return resp.State.Set(ctx, m) })...)
}

// Delete turns the flag off and clears its targeting, keeping the current
// fallthrough.
func (r *flagEnvironmentResource) Delete(ctx context.Context, req resource.DeleteRequest, resp *resource.DeleteResponse) {
	var state flagEnvironmentModel
	resp.Diagnostics.Append(req.State.Get(ctx, &state)...)
	if resp.Diagnostics.HasError() {
		return
	}
	_, err := putFlagConfig(ctx, r.client, state.Project.ValueString(), state.Flag.ValueString(), state.Environment.ValueString(),
		func(current *client.FlagConfig) client.FlagConfigIn {
			return client.FlagConfigIn{Enabled: false, Fallthrough: current.Fallthrough}
		})
	if err != nil && !client.IsNotFound(err) {
		resp.Diagnostics.Append(diagFromError(err, "Updating the flag configuration"))
	}
}

func (r *flagEnvironmentResource) ImportState(ctx context.Context, req resource.ImportStateRequest, resp *resource.ImportStateResponse) {
	project, flag, env, err := parseFlagEnvironmentID(req.ID)
	if err != nil {
		resp.Diagnostics.AddError("Invalid import ID", err.Error())
		return
	}
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("project"), project)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("flag"), flag)...)
	resp.Diagnostics.Append(resp.State.SetAttribute(ctx, path.Root("environment"), env)...)
}
