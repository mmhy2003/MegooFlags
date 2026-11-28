package provider

import (
	"context"
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

func stringSet(t *testing.T, values ...string) types.Set {
	t.Helper()
	if values == nil {
		values = []string{}
	}
	s, diags := types.SetValueFrom(context.Background(), types.StringType, values)
	if diags.HasError() {
		t.Fatal(diags)
	}
	return s
}

func fullSegment(t *testing.T) segmentModel {
	return segmentModel{
		Project:     types.StringValue("shop"),
		Key:         types.StringValue("beta"),
		Name:        types.StringValue("Beta users"),
		Description: types.StringValue("Early access"),
		Included:    stringSet(t, "alice", "bob"),
		Excluded:    stringSet(t, "mallory"),
		Rules: []segmentRuleModel{
			{
				Description: types.StringValue("Staff"),
				Clauses: []clauseModel{
					clause("email", "ends_with", `["@example.com"]`, false),
					clause("orders", "gte", `[10]`, true),
				},
			},
			{
				Description: types.StringValue(""),
				Clauses:     []clauseModel{clause("key", "in_segment", `["vip","gold"]`, false)},
			},
		},
	}
}

// serverSegment is what the API returns for in: sorted lists and sorted
// in_segment keys.
func serverSegment(t *testing.T, m segmentModel, in client.SegmentConfig) *client.Segment {
	t.Helper()
	b, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	var out client.Segment
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	out.Key, out.Name, out.Description = m.Key.ValueString(), m.Name.ValueString(), m.Description.ValueString()
	out.Rules[1].Clauses[0].Values = []any{"gold", "vip"}
	return &out
}

func TestSegmentRoundTrip(t *testing.T) {
	ctx := context.Background()
	m := fullSegment(t)
	in, diags := segmentConfigFromModel(ctx, m)
	if diags.HasError() {
		t.Fatal(diags)
	}
	if got := in.Rules[0].Clauses[1].Values[0]; got != json.Number("10") {
		t.Errorf("number value = %#v, want json.Number 10", got)
	}
	got, diags := segmentToModel(ctx, "shop", serverSegment(t, m, in), &m)
	if diags.HasError() {
		t.Fatal(diags)
	}
	if !reflect.DeepEqual(got, m) {
		t.Errorf("round trip:\n got %+v\nwant %+v", got, m)
	}
}

func TestSegmentToModelEmpty(t *testing.T) {
	got, diags := segmentToModel(context.Background(), "shop", &client.Segment{Key: "beta", Name: "Beta"}, nil)
	if diags.HasError() {
		t.Fatal(diags)
	}
	want := segmentModel{
		Project:     types.StringValue("shop"),
		Key:         types.StringValue("beta"),
		Name:        types.StringValue("Beta"),
		Description: types.StringValue(""),
		Included:    stringSet(t),
		Excluded:    stringSet(t),
		Rules:       []segmentRuleModel{},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("got %+v\nwant %+v", got, want)
	}
}

func TestSegmentConfigFromModelInvalidValues(t *testing.T) {
	m := fullSegment(t)
	m.Rules[1].Clauses[0] = clause("key", "in", `"vip"`, false)
	_, diags := segmentConfigFromModel(context.Background(), m)
	if !diags.HasError() || !strings.Contains(diags[0].Detail(), "rules[1].clauses[0]") {
		t.Errorf("diags = %v, want an error naming rules[1].clauses[0]", diags)
	}
}

func TestSegmentUpdateFromModel(t *testing.T) {
	m := fullSegment(t)
	current := &client.Segment{Key: "beta", Name: "Beta users", Description: "Early access"}
	if _, changed := segmentUpdateFromModel(current, m); changed {
		t.Error("unchanged details give a patch")
	}
	current.Name = "Old"
	in, changed := segmentUpdateFromModel(current, m)
	if !changed || in.Name == nil || *in.Name != "Beta users" || in.Description != nil {
		t.Errorf("patch = %+v, %v; want only the name", in, changed)
	}
}

func TestSegmentDeleteError(t *testing.T) {
	err := &client.Error{Status: 409, Code: "in_use", Message: "segment 'beta' is used by flag rules", Details: map[string]any{
		"used_by": []any{
			map[string]any{"flag": "checkout", "environment": "prod"},
			map[string]any{"flag": "checkout", "environment": "staging"},
			map[string]any{"flag": "search", "environment": "prod"},
		},
	}}
	d := segmentDeleteError(err, "beta")
	want := "Segment beta is used by the rules of checkout (prod, staging), search (prod). Remove it from those rules first."
	if d.Detail() != want {
		t.Errorf("detail = %q\nwant %q", d.Detail(), want)
	}
	other := segmentDeleteError(&client.Error{Status: 403, Code: "forbidden", Message: "no"}, "beta")
	if other.Detail() != "forbidden: no" {
		t.Errorf("other error detail = %q", other.Detail())
	}
}

func TestParseImportID(t *testing.T) {
	parts, err := parseImportID("shop/beta", "project/key")
	if err != nil || !reflect.DeepEqual(parts, []string{"shop", "beta"}) {
		t.Errorf(`parseImportID("shop/beta") = %v, %v`, parts, err)
	}
	for _, id := range []string{"shop", "shop/beta/x", "/beta", "shop/", ""} {
		if _, err := parseImportID(id, "project/key"); err == nil || !strings.Contains(err.Error(), "project/key") {
			t.Errorf("parseImportID(%q) error = %v, want one naming the form", id, err)
		}
	}
}
