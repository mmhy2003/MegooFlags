package provider

import (
	"context"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

var _ datasource.DataSourceWithConfigure = (*projectDataSource)(nil)

type projectDataSource struct {
	client *client.Client
}

func newProjectDataSource() datasource.DataSource { return &projectDataSource{} }

func (d *projectDataSource) Metadata(_ context.Context, req datasource.MetadataRequest, resp *datasource.MetadataResponse) {
	resp.TypeName = req.ProviderTypeName + "_project"
}

func (d *projectDataSource) Schema(_ context.Context, _ datasource.SchemaRequest, resp *datasource.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Reads a project and its environments.",
		Attributes: map[string]schema.Attribute{
			"key":              schema.StringAttribute{Description: "Project key.", Required: true},
			"name":             schema.StringAttribute{Description: "Display name.", Computed: true},
			"description":      schema.StringAttribute{Description: "Description.", Computed: true},
			"stale_after_days": schema.Int64Attribute{Description: "Days without evaluations after which a flag is stale.", Computed: true},
			"environments": schema.ListNestedAttribute{
				Description: "Environments in display order.",
				Computed:    true,
				NestedObject: schema.NestedAttributeObject{
					Attributes: map[string]schema.Attribute{
						"key":                schema.StringAttribute{Description: "Environment key.", Computed: true},
						"name":               schema.StringAttribute{Description: "Display name.", Computed: true},
						"description":        schema.StringAttribute{Description: "Description.", Computed: true},
						"approvals_required": schema.Int64Attribute{Description: "Approvals a change request needs here.", Computed: true},
					},
				},
			},
		},
	}
}

func (d *projectDataSource) Configure(_ context.Context, req datasource.ConfigureRequest, resp *datasource.ConfigureResponse) {
	d.client = providerClient(req.ProviderData, resp.Diagnostics.AddError)
}

func (d *projectDataSource) Read(ctx context.Context, req datasource.ReadRequest, resp *datasource.ReadResponse) {
	var cfg projectModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &cfg)...)
	if resp.Diagnostics.HasError() {
		return
	}
	key := cfg.Key.ValueString()
	p, err := d.client.GetProject(ctx, key)
	if err != nil {
		resp.Diagnostics.Append(diagFromError(err, "Reading project "+key))
		return
	}
	resp.Diagnostics.Append(resp.State.Set(ctx, projectToModel(p))...)
}
