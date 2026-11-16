package provider

import (
	"reflect"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

func TestSdkKeyFromCreated(t *testing.T) {
	got := sdkKeyFromCreated("shop", "prod", &client.SdkKeyCreated{
		ID: "k1", Name: "Backend", Kind: "server", Prefix: "mf_server_ab", Key: "mf_server_abcdef", CreatedAt: "2026-11-16T10:00:00Z",
	})
	want := sdkKeyModel{
		Project:     types.StringValue("shop"),
		Environment: types.StringValue("prod"),
		Name:        types.StringValue("Backend"),
		Kind:        types.StringValue("server"),
		ID:          types.StringValue("k1"),
		Prefix:      types.StringValue("mf_server_ab"),
		CreatedAt:   types.StringValue("2026-11-16T10:00:00Z"),
		Secret:      types.StringValue("mf_server_abcdef"),
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v\nwant %+v", got, want)
	}
}

func TestSdkKeyToModelKeepsSecret(t *testing.T) {
	key := client.SdkKey{ID: "k1", Name: "Backend", Kind: "client", Prefix: "mf_client_ab", CreatedAt: "2026-11-16T10:00:00Z"}
	prior := sdkKeyModel{
		Project:     types.StringValue("shop"),
		Environment: types.StringValue("prod"),
		ID:          types.StringValue("k1"),
		Secret:      types.StringValue("mf_client_abcdef"),
	}
	got := sdkKeyToModel(prior, key)
	if got.Secret.ValueString() != "mf_client_abcdef" || got.Name.ValueString() != "Backend" ||
		got.Kind.ValueString() != "client" || got.Prefix.ValueString() != "mf_client_ab" || got.Project.ValueString() != "shop" {
		t.Errorf("got %+v", got)
	}

	// After import the secret is unknown to Terraform and stays null.
	prior.Secret = types.StringNull()
	if got := sdkKeyToModel(prior, key); !got.Secret.IsNull() {
		t.Errorf("imported secret = %v, want null", got.Secret)
	}
}

func TestFindActiveSdkKey(t *testing.T) {
	revoked := "2026-11-16T11:00:00Z"
	keys := []client.SdkKey{
		{ID: "k1", Name: "Old"},
		{ID: "k2", Name: "Revoked", RevokedAt: &revoked},
	}
	if k := findActiveSdkKey(keys, "k1"); k == nil || k.Name != "Old" {
		t.Errorf("k1 = %+v, want the active key", k)
	}
	if k := findActiveSdkKey(keys, "k2"); k != nil {
		t.Errorf("revoked key = %+v, want nil (gone)", k)
	}
	if k := findActiveSdkKey(keys, "k3"); k != nil {
		t.Errorf("missing key = %+v, want nil", k)
	}
}

func TestParseSdkKeyImportID(t *testing.T) {
	parts, err := parseImportID("shop/prod/0b7c", "project/environment/id")
	if err != nil || !reflect.DeepEqual(parts, []string{"shop", "prod", "0b7c"}) {
		t.Errorf("parts = %v, err = %v", parts, err)
	}
	if _, err := parseImportID("shop/0b7c", "project/environment/id"); err == nil {
		t.Error("two parts accepted, want an error")
	}
}
