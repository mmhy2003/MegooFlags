package provider

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	fwprovider "github.com/hashicorp/terraform-plugin-framework/provider"
	"github.com/hashicorp/terraform-plugin-framework/providerserver"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-go/tfprotov6"
	"github.com/hashicorp/terraform-plugin-go/tftypes"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

func TestProviderSchemaValid(t *testing.T) {
	srv, err := providerserver.NewProtocol6WithError(New("test")())()
	if err != nil {
		t.Fatalf("server: %v", err)
	}
	resp, err := srv.GetProviderSchema(context.Background(), &tfprotov6.GetProviderSchemaRequest{})
	if err != nil {
		t.Fatalf("GetProviderSchema: %v", err)
	}
	for _, d := range resp.Diagnostics {
		t.Errorf("diagnostic: %s: %s", d.Summary, d.Detail)
	}
	for _, name := range []string{"megooflags_project", "megooflags_flag"} {
		if _, ok := resp.ResourceSchemas[name]; !ok {
			t.Errorf("%s resource missing", name)
		}
		if _, ok := resp.DataSourceSchemas[name]; !ok {
			t.Errorf("%s data source missing", name)
		}
	}
}

// configure runs the provider's Configure with url and token (nil is null).
func configure(t *testing.T, url, token *string) fwprovider.ConfigureResponse {
	t.Helper()
	ctx := context.Background()
	p := New("test")()
	var sresp fwprovider.SchemaResponse
	p.Schema(ctx, fwprovider.SchemaRequest{}, &sresp)
	str := func(v *string) tftypes.Value {
		if v == nil {
			return tftypes.NewValue(tftypes.String, nil)
		}
		return tftypes.NewValue(tftypes.String, *v)
	}
	raw := tftypes.NewValue(sresp.Schema.Type().TerraformType(ctx), map[string]tftypes.Value{
		"url":   str(url),
		"token": str(token),
	})
	var resp fwprovider.ConfigureResponse
	p.Configure(ctx, fwprovider.ConfigureRequest{Config: tfsdk.Config{Schema: sresp.Schema, Raw: raw}}, &resp)
	return resp
}

func TestConfigureFromEnvironment(t *testing.T) {
	var auth, agent string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auth, agent = r.Header.Get("Authorization"), r.Header.Get("User-Agent")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"key":"shop","name":"Shop","description":"","stale_after_days":30,"environments":[]}`))
	}))
	defer srv.Close()
	t.Setenv("MEGOOFLAGS_URL", srv.URL)
	t.Setenv("MEGOOFLAGS_API_TOKEN", "mf_api_secret")

	resp := configure(t, nil, nil)
	if resp.Diagnostics.HasError() {
		t.Fatalf("Configure: %v", resp.Diagnostics)
	}
	c, ok := resp.ResourceData.(*client.Client)
	if !ok {
		t.Fatalf("ResourceData = %T, want *client.Client", resp.ResourceData)
	}
	if resp.DataSourceData != resp.ResourceData {
		t.Error("DataSourceData differs from ResourceData")
	}
	if _, err := c.GetProject(context.Background(), "shop"); err != nil {
		t.Fatalf("GetProject: %v", err)
	}
	if auth != "Bearer mf_api_secret" {
		t.Errorf("Authorization = %q", auth)
	}
	if agent != "terraform-provider-megooflags/"+Version {
		t.Errorf("User-Agent = %q", agent)
	}
}

func TestConfigurePrefersAttributes(t *testing.T) {
	t.Setenv("MEGOOFLAGS_URL", "http://env.invalid")
	t.Setenv("MEGOOFLAGS_API_TOKEN", "env-token")
	url, token := "http://attr.invalid/", "attr-token"
	resp := configure(t, &url, &token)
	if resp.Diagnostics.HasError() {
		t.Fatalf("Configure: %v", resp.Diagnostics)
	}
	c := resp.ResourceData.(*client.Client)
	if c.BaseURL != "http://attr.invalid" || c.Token != "attr-token" {
		t.Errorf("client = %s %s, want the attribute values", c.BaseURL, c.Token)
	}
}

func TestConfigureMissingValues(t *testing.T) {
	t.Setenv("MEGOOFLAGS_URL", "")
	t.Setenv("MEGOOFLAGS_API_TOKEN", "")
	resp := configure(t, nil, nil)
	if resp.Diagnostics.ErrorsCount() != 2 {
		t.Fatalf("errors = %d, want 2: %v", resp.Diagnostics.ErrorsCount(), resp.Diagnostics)
	}
	for _, d := range resp.Diagnostics {
		if !strings.Contains(d.Detail(), "MEGOOFLAGS_") {
			t.Errorf("detail %q does not name the environment variable", d.Detail())
		}
	}
}

func TestDiagFromError(t *testing.T) {
	d := diagFromError(&client.Error{Status: 409, Code: "approval_required", Message: "x"}, "Updating environment prod")
	if !strings.HasPrefix(d.Detail(), "Updating environment prod needs approval in this environment. Set approvals_required to 0") {
		t.Errorf("approval detail = %q", d.Detail())
	}
	d = diagFromError(&client.Error{Status: 422, Code: "validation_error", Message: "bad key"}, "Creating project")
	if d.Detail() != "validation_error: bad key" {
		t.Errorf("API error detail = %q", d.Detail())
	}
	if d.Summary() != "Creating project failed" {
		t.Errorf("summary = %q", d.Summary())
	}
	d = diagFromError(errors.New("dial tcp: refused"), "Reading project")
	if d.Detail() != "dial tcp: refused" {
		t.Errorf("other error detail = %q", d.Detail())
	}
}
