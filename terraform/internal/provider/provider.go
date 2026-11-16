// Package provider is the MegooFlags Terraform provider. It manages
// MegooFlags through the management REST API with an API token.
package provider

import (
	"context"
	"os"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	"github.com/hashicorp/terraform-plugin-framework/path"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

var _ provider.Provider = (*megooflagsProvider)(nil)

type megooflagsProvider struct {
	version string
}

type providerModel struct {
	URL   types.String `tfsdk:"url"`
	Token types.String `tfsdk:"token"`
}

// New returns a constructor for the provider at version.
func New(version string) func() provider.Provider {
	return func() provider.Provider { return &megooflagsProvider{version: version} }
}

func (p *megooflagsProvider) Metadata(_ context.Context, _ provider.MetadataRequest, resp *provider.MetadataResponse) {
	resp.TypeName = "megooflags"
	resp.Version = p.version
}

func (p *megooflagsProvider) Schema(_ context.Context, _ provider.SchemaRequest, resp *provider.SchemaResponse) {
	resp.Schema = schema.Schema{
		Description: "Manages MegooFlags projects, flags, targeting, segments and SDK keys through the REST API.",
		Attributes: map[string]schema.Attribute{
			"url": schema.StringAttribute{
				Description: "Base URL of the MegooFlags API, for example https://flags.example.com. Defaults to MEGOOFLAGS_URL.",
				Optional:    true,
			},
			"token": schema.StringAttribute{
				Description: "API token. Defaults to MEGOOFLAGS_API_TOKEN.",
				Optional:    true,
				Sensitive:   true,
			},
		},
	}
}

func (p *megooflagsProvider) Configure(ctx context.Context, req provider.ConfigureRequest, resp *provider.ConfigureResponse) {
	var cfg providerModel
	resp.Diagnostics.Append(req.Config.Get(ctx, &cfg)...)
	if resp.Diagnostics.HasError() {
		return
	}
	url := configValue(cfg.URL, "MEGOOFLAGS_URL")
	token := configValue(cfg.Token, "MEGOOFLAGS_API_TOKEN")
	if url == "" {
		resp.Diagnostics.AddAttributeError(path.Root("url"), "Missing MegooFlags URL",
			"Set the provider's url attribute or the MEGOOFLAGS_URL environment variable.")
	}
	if token == "" {
		resp.Diagnostics.AddAttributeError(path.Root("token"), "Missing MegooFlags API token",
			"Set the provider's token attribute or the MEGOOFLAGS_API_TOKEN environment variable.")
	}
	if resp.Diagnostics.HasError() {
		return
	}
	c := client.New(url, token, client.WithUserAgent("terraform-provider-megooflags/"+Version))
	resp.ResourceData = c
	resp.DataSourceData = c
}

// configValue returns the attribute's value, or the environment variable
// when the attribute is null, unknown or empty.
func configValue(v types.String, env string) string {
	if s := v.ValueString(); s != "" {
		return s
	}
	return os.Getenv(env)
}

func (p *megooflagsProvider) Resources(context.Context) []func() resource.Resource {
	return []func() resource.Resource{
		newProjectResource,
		newFlagResource,
		newFlagEnvironmentResource,
	}
}

func (p *megooflagsProvider) DataSources(context.Context) []func() datasource.DataSource {
	return []func() datasource.DataSource{
		newProjectDataSource,
		newFlagDataSource,
	}
}

// providerClient returns the client the provider shares, or nil with an
// error when providerData is something else. It is nil before Configure.
func providerClient(providerData any, add func(summary, detail string)) *client.Client {
	if providerData == nil {
		return nil
	}
	c, ok := providerData.(*client.Client)
	if !ok {
		add("Unexpected provider data", "Expected *client.Client; this is a bug in the provider.")
	}
	return c
}
