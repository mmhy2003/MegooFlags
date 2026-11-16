package provider

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

func varModel(key, name, desc, value string) variationModel {
	return variationModel{
		Key:         types.StringValue(key),
		Name:        types.StringValue(name),
		Description: types.StringValue(desc),
		ValueJSON:   newJSONValue(value),
	}
}

func variationList(t *testing.T, vs ...variationModel) types.List {
	t.Helper()
	l, diags := variationsToList(context.Background(), vs)
	if diags.HasError() {
		t.Fatal(diags)
	}
	return l
}

func TestParseFlagID(t *testing.T) {
	project, key, err := parseFlagID("shop/new-checkout")
	if err != nil || project != "shop" || key != "new-checkout" {
		t.Errorf(`parseFlagID("shop/new-checkout") = %q, %q, %v`, project, key, err)
	}
	for _, id := range []string{"a", "a/b/c", "/b", "a/", ""} {
		if _, _, err := parseFlagID(id); err == nil {
			t.Errorf("parseFlagID(%q) succeeded, want an error", id)
		}
	}
}

func TestJSONValueSemanticEquals(t *testing.T) {
	ctx := context.Background()
	cases := []struct {
		a, b string
		want bool
	}{
		{"1", "1.0", true},
		{"1", "1e0", true},
		{`{"a":1,"b":[true,null]}`, `{ "b": [true, null], "a": 1.0 }`, true},
		{"12345678901234567890", "12345678901234567891", false},
		{"1", `"1"`, false},
		{"[1,2]", "[2,1]", false},
		{`{"a":1}`, `{"a":1,"b":2}`, false},
		{`"x"`, `"x"`, true},
	}
	for _, c := range cases {
		got, diags := newJSONValue(c.a).StringSemanticEquals(ctx, newJSONValue(c.b))
		if diags.HasError() {
			t.Errorf("%s against %s: %v", c.a, c.b, diags)
		}
		if got != c.want {
			t.Errorf("%s against %s = %v, want %v", c.a, c.b, got, c.want)
		}
	}
}

func TestJSONValueValidate(t *testing.T) {
	if _, err := decodeJSON("{"); err == nil {
		t.Error("decodeJSON accepted invalid JSON")
	}
	if _, err := decodeJSON("1 2"); err == nil {
		t.Error("decodeJSON accepted two values")
	}
}

func TestFlagToModelKeepsPriorVariationOrder(t *testing.T) {
	owner := "team-a"
	f := &client.Flag{
		FlagSummary: client.FlagSummary{
			Key: "checkout", Name: "Checkout", Type: "string", Tags: []string{"web"},
			Owner: &owner, Lifecycle: "permanent",
		},
		Variations: []client.Variation{
			{Key: "a", Name: "A", Value: "a"},
			{Key: "b", Value: map[string]any{"x": 1.0}},
			{Key: "c", Value: nil},
		},
		OffVariation: "a",
	}
	prior := []variationModel{varModel("c", "", "", "null"), varModel("b", "", "", "{}")}
	ctx := context.Background()
	m, diags := flagToModel(ctx, "shop", f, prior)
	if diags.HasError() {
		t.Fatal(diags)
	}
	vars, diags := variationsFromList(ctx, m.Variations)
	if diags.HasError() {
		t.Fatal(diags)
	}
	var keys []string
	for _, v := range vars {
		keys = append(keys, v.Key.ValueString())
	}
	if !reflect.DeepEqual(keys, []string{"c", "b", "a"}) {
		t.Errorf("variation order = %v, want [c b a]", keys)
	}
	if got := vars[1].ValueJSON.ValueString(); got != `{"x":1}` {
		t.Errorf("value_json = %s", got)
	}
	if !m.Permanent.ValueBool() || m.Owner.ValueString() != "team-a" ||
		m.OffVariation.ValueString() != "a" || m.Project.ValueString() != "shop" {
		t.Errorf("flagToModel = %+v", m)
	}
	f.Owner, f.Lifecycle = nil, "active"
	if m, _ := flagToModel(ctx, "shop", f, nil); !m.Owner.IsNull() || m.Permanent.ValueBool() {
		t.Errorf("owner = %v, permanent = %v, want null and false", m.Owner, m.Permanent)
	}
}

func TestDiffVariations(t *testing.T) {
	current := []client.Variation{
		{Key: "on", Name: "On", Value: true},
		{Key: "num", Value: 1.0},
		{Key: "gone", Value: "x"},
	}
	desired := []variationModel{
		varModel("on", "On", "", "true"),
		varModel("num", "Number", "", "1.0"),
		varModel("new", "", "Fresh", `{"a":[1]}`),
	}
	got, err := diffVariations(current, desired)
	if err != nil {
		t.Fatalf("diffVariations: %v", err)
	}
	want := variationDiff{
		Create: []client.VariationIn{{Key: "new", Description: "Fresh", Value: map[string]any{"a": []any{json.Number("1")}}}},
		Update: []variationUpdate{{Key: "num", Update: client.VariationUpdate{Name: new("Number")}}},
		Delete: []string{"gone"},
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("diffVariations =\n %+v\nwant\n %+v", got, want)
	}

	got, err = diffVariations(current[:1], []variationModel{varModel("on", "On", "", "false")})
	if err != nil {
		t.Fatalf("diffVariations: %v", err)
	}
	var v any = false
	if len(got.Update) != 1 || !reflect.DeepEqual(got.Update[0].Update, client.VariationUpdate{Value: &v}) {
		t.Errorf("value change = %+v", got.Update)
	}
}

// TestApplyFlagUpdateOrder checks the request order: new and changed
// variations, then the flag (with the off variation), then deletes, then
// the lifecycle.
func TestApplyFlagUpdateOrder(t *testing.T) {
	var calls []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, r.Method+" "+r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodDelete {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		_, _ = w.Write([]byte(`{"key":"f"}`))
	}))
	defer srv.Close()
	c := client.New(srv.URL, "mf_api_x")

	current := &client.Flag{
		FlagSummary:  client.FlagSummary{Key: "f", Lifecycle: "active"},
		Variations:   []client.Variation{{Key: "old", Value: "o"}, {Key: "keep", Value: "k"}},
		OffVariation: "old",
	}
	plan := flagModel{
		Project:      types.StringValue("p"),
		Key:          types.StringValue("f"),
		Name:         types.StringValue("F"),
		Description:  types.StringValue(""),
		Tags:         types.SetNull(types.StringType),
		Owner:        types.StringNull(),
		Variations:   variationList(t, varModel("keep", "Keep", "", `"k"`), varModel("new", "", "", `"n"`)),
		OffVariation: types.StringValue("new"),
		Permanent:    types.BoolValue(true),
	}
	if diags := applyFlagUpdate(context.Background(), c, current, plan); diags.HasError() {
		t.Fatalf("applyFlagUpdate: %v", diags)
	}
	want := []string{
		"POST /api/projects/p/flags/f/variations",
		"PATCH /api/projects/p/flags/f/variations/keep",
		"PATCH /api/projects/p/flags/f",
		"DELETE /api/projects/p/flags/f/variations/old",
		"PUT /api/projects/p/flags/f/lifecycle",
	}
	if !reflect.DeepEqual(calls, want) {
		t.Errorf("calls =\n %v\nwant\n %v", calls, want)
	}

	// Unchanged variations and lifecycle: only the flag patch.
	calls = nil
	current = &client.Flag{
		FlagSummary: client.FlagSummary{Key: "f", Lifecycle: "permanent"},
		Variations:  []client.Variation{{Key: "keep", Name: "Keep", Value: "k"}, {Key: "new", Value: "n"}},
	}
	if diags := applyFlagUpdate(context.Background(), c, current, plan); diags.HasError() {
		t.Fatalf("applyFlagUpdate: %v", diags)
	}
	if !reflect.DeepEqual(calls, []string{"PATCH /api/projects/p/flags/f"}) {
		t.Errorf("calls = %v, want only the flag patch", calls)
	}
}

func TestFlagUpdateFromModel(t *testing.T) {
	m := flagModel{
		Name:         types.StringValue("F"),
		Description:  types.StringValue("d"),
		Tags:         types.SetValueMust(types.StringType, nil),
		Owner:        types.StringNull(),
		OffVariation: types.StringUnknown(),
	}
	got, diags := flagUpdateFromModel(context.Background(), m)
	if diags.HasError() {
		t.Fatal(diags)
	}
	want := client.FlagUpdate{Name: new("F"), Description: new("d"), Tags: &[]string{}, ClearOwner: true}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("flagUpdateFromModel = %+v, want %+v", got, want)
	}
}
