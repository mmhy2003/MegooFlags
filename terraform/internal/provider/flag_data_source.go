package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

var _ datasource.DataSourceWithConfigure = (*flagDataSource)(nil)

// flagDataModel is the megooflags_flag data source: the resource's
// attributes plus archived.
type flagDataModel struct {
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
	Archived     types.Bool   `tfsdk:"archived"`
}

type flagDataSource struct {
	client *client.Client
}

func newFlagDataSource() datasource.DataSource { return &flagDataSource{} }

func (d *flagDataSource) Metadata(_ context.Context, req datasource.MetadataRequest, resp *datasource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_flag"
}

func (d *flagDataSource) Schema(_ context.Context, _ datasource.SchemaRequest, resp *datasource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Reads a flag and its variations.",
		Attributes: map[string]schema.Attribute{
			"project":     schema.StringAttribute{Description: "Project key.", Required: true},
			"key":         schema.StringAttribute{Description: "Flag key.", Required: true},
			"type":        schema.StringAttribute{Description: "Value type: boolean, string, number or json.", Computed: true},
			"name":        schema.StringAttribute{Description: "Display name.", Computed: true},
			"description": schema.StringAttribute{Description: "Description.", Computed: true},
			"tags":        schema.SetAttribute{Description: "Tags.", ElementType: types.StringType, Computed: true},
			"owner":       schema.StringAttribute{Description: "Owner.", Computed: true},
			"variations": schema.ListNestedAttribute{
				Description: "Values the flag can serve.",
				Computed:    true,
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"key":         schema.StringAttribute{Description: "Variation key.", Computed: true},
						"name":        schema.StringAttribute{Description: "Display name.", Computed: true},
						"description": schema.StringAttribute{Description: "Description.", Computed: true},
						"value_json":  schema.StringAttribute{Description: "The value as JSON; use jsondecode() to read it.", Computed: true, CustomType: jsonValueType{}},
					},
				},
			},
			"off_variation": schema.StringAttribute{Description: "Key of the variation served when the flag is off.", Computed: true},
			"permanent":     schema.BoolAttribute{Description: "Whether the flag is permanent, so never stale.", Computed: true},
			"archived":      schema.BoolAttribute{Description: "Whether the flag is archived.", Computed: true},
		},
	}
}

func (d *flagDataSource) Configure(_ context.Context, req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) {
	d.client = providerClient(req.ProviderData, resp.Diagnostics.AddError)
}

func (d *flagDataSource) Read(ctx context.Context, req datasource.ReadRequest, resp *datasource.ReadResponse) {
	var cfg flagDataModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &cfg)...)
	if resp.Diagnostics.HasError() {
		return
	}
	project, key := cfg.Project.ValueString(), cfg.Key.ValueString()
	f, err := d.client.GetFlag(ctx, project, key)
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Reading flag "+key))
		return
	}
	m, diags := flagToModel(ctx, project, f, nil)
	resp.Diagnostics.Append(diags...)
	if resp.Diagnostics.HasError() {
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, flagDataModel{
		Project:      m.Project,
		Key:          m.Key,
		Type:         m.Type,
		Name:         m.Name,
		Description:  m.Description,
		Tags:         m.Tags,
		Owner:        m.Owner,
		Variations:   m.Variations,
		OffVariation: m.OffVariation,
		Permanent:    m.Permanent,
		Archived:     types.BoolValue(f.Archived),
	})...)
}
