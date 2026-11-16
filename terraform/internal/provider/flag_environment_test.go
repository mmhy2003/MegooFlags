package provider

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"github.com/hashicorp/terraform-plugin-framework/resource"
	"github.com/hashicorp/terraform-plugin-framework/tfsdk"
	"github.com/hashicorp/terraform-plugin-framework/types"
	"github.com/mmhy2003/MegooFlags/cli/client"
)

func clause(attribute, operator, values string, negate bool) clauseModel {
	return clauseModel{
		Attribute:  types.StringValue(attribute),
		Operator:   types.StringValue(operator),
		ValuesJSON: newJSONValue(values),
		Negate:     types.BoolValue(negate),
	}
}

func rollout(bucketBy string, weights ...any) *rolloutModel {
	r := &rolloutModel{BucketBy: types.StringValue(bucketBy), Weights: []weightModel{}}
	for i := 0; i < len(weights); i += 2 {
		r.Weights = append(r.Weights, weightModel{
			Variation: types.StringValue(weights[i].(string)),
			Weight:    types.Int64Value(int64(weights[i+1].(int))),
		})
	}
	return r
}

// fullFlagEnvironment uses every attribute: targets, a rule serving a
// variation with number and string values, a rule serving a rollout, a
// rollout fallthrough and a prerequisite.
func fullFlagEnvironment() flagEnvironmentModel {
	return flagEnvironmentModel{
		Project:     types.StringValue("shop"),
		Flag:        types.StringValue("checkout"),
		Environment: types.StringValue("prod"),
		Enabled:     types.BoolValue(true),
		Targets: []targetModel{
			{Variation: types.StringValue("on"), Values: []string{"alice", "bob"}},
		},
		Rules: []ruleModel{
			{
				Description: types.StringValue("Big spenders"),
				Clauses: []clauseModel{
					clause("orders", "gte", `[10,2.5]`, false),
					clause("plan", "in", `["10","gold"]`, true),
				},
				Variation: types.StringValue("on"),
			},
			{
				Description: types.StringValue(""),
				Clauses:     []clauseModel{clause("key", "in_segment", `["beta"]`, false)},
				Variation:   types.StringNull(),
				Rollout:     rollout("org", "on", 25000, "off", 75000),
			},
		},
		Fallthrough: &serveModel{
			Variation: types.StringNull(),
			Rollout:   rollout("key", "on", 10000, "off", 90000),
		},
		Prerequisites: []prerequisiteModel{
			{Flag: types.StringValue("new-cart"), Variation: types.StringValue("on")},
		},
		Version: types.Int64Value(7),
	}
}

// TestFlagEnvironmentRoundTrip sends the model through FlagConfigIn and the
// JSON the API echoes back, and expects the same model.
func TestFlagEnvironmentRoundTrip(t *testing.T) {
	cases := map[string]flagEnvironmentModel{
		"full": fullFlagEnvironment(),
		"empty": {
			Project:       types.StringValue("shop"),
			Flag:          types.StringValue("checkout"),
			Environment:   types.StringValue("dev"),
			Enabled:       types.BoolValue(false),
			Targets:       []targetModel{},
			Rules:         []ruleModel{},
			Fallthrough:   &serveModel{Variation: types.StringValue("off")},
			Prerequisites: []prerequisiteModel{},
			Version:       types.Int64Value(1),
		},
	}
	for name, m := range cases {
		t.Run(name, func(t *testing.T) {
			in, err := flagConfigInFromModel(m)
			if err != nil {
				t.Fatalf("flagConfigInFromModel: %v", err)
			}
			body, err := json.Marshal(in)
			if err != nil {
				t.Fatal(err)
			}
			var out client.FlagConfig
			if err := json.Unmarshal(body, &out); err != nil {
				t.Fatal(err)
			}
			out.Environment, out.Version = m.Environment.ValueString(), int(m.Version.ValueInt64())
			got := flagConfigToModel("shop", "checkout", &out, nil)
			if !reflect.DeepEqual(got, m) {
				t.Errorf("round trip =\n %+v\nwant\n %+v", got, m)
			}
		})
	}
}

func TestFlagConfigInFromModelKeepsValueTypes(t *testing.T) {
	in, err := flagConfigInFromModel(fullFlagEnvironment())
	if err != nil {
		t.Fatal(err)
	}
	want := []any{json.Number("10"), json.Number("2.5")}
	if got := in.Rules[0].Clauses[0].Values; !reflect.DeepEqual(got, want) {
		t.Errorf("number values = %#v, want %#v", got, want)
	}
	if got := in.Rules[0].Clauses[1].Values; !reflect.DeepEqual(got, []any{"10", "gold"}) {
		t.Errorf("string values = %#v", got)
	}
	if in.Fallthrough.Variation != nil || in.Fallthrough.Rollout.Weights[1].Weight != 90000 {
		t.Errorf("fallthrough = %+v", in.Fallthrough)
	}

	m := fullFlagEnvironment()
	m.Rules[0].Clauses[0].ValuesJSON = newJSONValue(`{"a":1}`)
	if _, err := flagConfigInFromModel(m); err == nil {
		t.Error("an object values_json was accepted, want an error")
	}
}

// TestFlagConfigToModelKeepsPriorText keeps the configuration's JSON text
// when the API returns the same values, in_segment keys in any order.
func TestFlagConfigToModelKeepsPriorText(t *testing.T) {
	prior := fullFlagEnvironment()
	prior.Rules[0].Clauses[0].ValuesJSON = newJSONValue(`[ 10.0, 2.5 ]`)
	prior.Rules[1].Clauses[0].ValuesJSON = newJSONValue(`["vip","beta"]`)
	cfg := &client.FlagConfig{
		Environment: "prod",
		Enabled:     true,
		Version:     8,
		Rules: []client.Rule{
			{Clauses: []client.Clause{
				{Attribute: "orders", Operator: "gte", Values: []any{10.0, 2.5}},
				{Attribute: "plan", Operator: "in", Values: []any{"gold", "10"}},
			}, Variation: new("on")},
			{Clauses: []client.Clause{
				{Attribute: "key", Operator: "in_segment", Values: []any{"beta", "vip"}},
			}, Rollout: &client.Rollout{Weights: []client.Weight{{Variation: "on", Weight: 100000}}}},
		},
		Fallthrough: client.Serve{Variation: new("off")},
	}
	m := flagConfigToModel("shop", "checkout", cfg, &prior)
	if got := m.Rules[0].Clauses[0].ValuesJSON.ValueString(); got != `[ 10.0, 2.5 ]` {
		t.Errorf("same numbers: values_json = %s, want the prior text", got)
	}
	if got := m.Rules[0].Clauses[1].ValuesJSON.ValueString(); got != `["gold","10"]` {
		t.Errorf("reordered in values: values_json = %s, want the API order", got)
	}
	if got := m.Rules[1].Clauses[0].ValuesJSON.ValueString(); got != `["vip","beta"]` {
		t.Errorf("sorted segment keys: values_json = %s, want the prior text", got)
	}
	if got := m.Rules[1].Rollout.BucketBy.ValueString(); got != "key" {
		t.Errorf("bucket_by = %q, want the default key", got)
	}
	if m.Targets == nil || m.Prerequisites == nil || m.Version.ValueInt64() != 8 {
		t.Errorf("targets = %#v, prerequisites = %#v, version = %v", m.Targets, m.Prerequisites, m.Version)
	}
}

// validateFlagEnvironment runs the resource's ValidateConfig on m.
func validateFlagEnvironment(t *testing.T, m flagEnvironmentModel) resource.ValidateConfigResponse {
	t.Helper()
	ctx := context.Background()
	r := newFlagEnvironmentResource().(*flagEnvironmentResource)
	var sresp resource.SchemaResponse
	r.Schema(ctx, resource.SchemaRequest{}, &sresp)
	if sresp.Diagnostics.HasError() {
		t.Fatal(sresp.Diagnostics)
	}
	plan := tfsdk.Plan{Schema: sresp.Schema}
	if diags := plan.Set(ctx, m); diags.HasError() {
		t.Fatal(diags)
	}
	var resp resource.ValidateConfigResponse
	r.ValidateConfig(ctx, resource.ValidateConfigRequest{Config: tfsdk.Config{Schema: sresp.Schema, Raw: plan.Raw}}, &resp)
	return resp
}

func TestFlagEnvironmentValidateConfig(t *testing.T) {
	if resp := validateFlagEnvironment(t, fullFlagEnvironment()); resp.Diagnostics.HasError() {
		t.Errorf("valid config: %v", resp.Diagnostics)
	}
	cases := map[string]struct {
		change  func(*flagEnvironmentModel)
		summary string
	}{
		"rule with both": {func(m *flagEnvironmentModel) {
			m.Rules[0].Rollout = rollout("key", "on", 100000)
		}, "Invalid serve"},
		"rule with neither": {func(m *flagEnvironmentModel) {
			m.Rules[1].Rollout = nil
		}, "Invalid serve"},
		"fallthrough with both": {func(m *flagEnvironmentModel) {
			m.Fallthrough.Variation = types.StringValue("on")
		}, "Invalid serve"},
		"fallthrough with neither": {func(m *flagEnvironmentModel) {
			m.Fallthrough.Rollout = nil
		}, "Invalid serve"},
		"rule weights": {func(m *flagEnvironmentModel) {
			m.Rules[1].Rollout = rollout("key", "on", 50000, "off", 40000)
		}, "Invalid rollout"},
		"fallthrough weights": {func(m *flagEnvironmentModel) {
			m.Fallthrough.Rollout = rollout("key", "on", 100000, "off", 1)
		}, "Invalid rollout"},
		"values not an array": {func(m *flagEnvironmentModel) {
			m.Rules[0].Clauses[1].ValuesJSON = newJSONValue(`"gold"`)
		}, "Invalid clause values"},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			m := fullFlagEnvironment()
			c.change(&m)
			resp := validateFlagEnvironment(t, m)
			if resp.Diagnostics.ErrorsCount() != 1 || resp.Diagnostics[0].Summary() != c.summary {
				t.Errorf("diagnostics = %v, want one %q error", resp.Diagnostics, c.summary)
			}
		})
	}
}

func TestFlagEnvironmentSchemaOperators(t *testing.T) {
	if len(operators) != 16 {
		t.Errorf("%d operators, want 16", len(operators))
	}
}

func TestParseFlagEnvironmentID(t *testing.T) {
	p, f, e, err := parseFlagEnvironmentID("shop/checkout/prod")
	if err != nil || p != "shop" || f != "checkout" || e != "prod" {
		t.Errorf(`parseFlagEnvironmentID("shop/checkout/prod") = %q, %q, %q, %v`, p, f, e, err)
	}
	for _, id := range []string{"a", "a/b", "a/b/c/d", "/b/c", "a//c", "a/b/", ""} {
		if _, _, _, err := parseFlagEnvironmentID(id); err == nil {
			t.Errorf("parseFlagEnvironmentID(%q) succeeded, want an error", id)
		}
	}
}

// TestPutFlagConfigRetriesOnce reads the version again after a
// version_conflict and retries once; a second conflict is returned.
func TestPutFlagConfigRetriesOnce(t *testing.T) {
	var calls []string
	conflicts := 1
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodGet {
			calls = append(calls, "GET")
			_, _ = w.Write([]byte(`{"environment":"prod","version":` + strconv.Itoa(len(calls)) + `,"fallthrough":{"variation":"off"}}`))
			return
		}
		b, _ := io.ReadAll(r.Body)
		var in client.FlagConfigIn
		_ = json.Unmarshal(b, &in)
		calls = append(calls, "PUT "+strconv.Itoa(in.Version))
		if conflicts > 0 {
			conflicts--
			w.WriteHeader(http.StatusConflict)
			_, _ = w.Write([]byte(`{"code":"version_conflict","message":"moved on"}`))
			return
		}
		_, _ = w.Write(b)
	}))
	defer srv.Close()
	c := client.New(srv.URL, "mf_api_x")
	build := func(cur *client.FlagConfig) client.FlagConfigIn {
		return client.FlagConfigIn{Enabled: false, Fallthrough: cur.Fallthrough}
	}

	out, err := putFlagConfig(context.Background(), c, "shop", "checkout", "prod", build)
	if err != nil {
		t.Fatalf("putFlagConfig: %v", err)
	}
	if want := []string{"GET", "PUT 1", "GET", "PUT 3"}; !reflect.DeepEqual(calls, want) {
		t.Errorf("calls = %v, want %v", calls, want)
	}
	if out.Fallthrough.Variation == nil || *out.Fallthrough.Variation != "off" {
		t.Errorf("result = %+v", out)
	}

	calls, conflicts = nil, 2
	_, err = putFlagConfig(context.Background(), c, "shop", "checkout", "prod", build)
	if !client.IsCode(err, "version_conflict") || len(calls) != 4 {
		t.Errorf("two conflicts: err = %v, calls = %v, want version_conflict after one retry", err, calls)
	}
	d := diagFromError(err, "Updating the flag configuration")
	if !strings.Contains(d.Detail(), "version_conflict") {
		t.Errorf("diagnostic = %s", d.Detail())
	}
}

// The API groups targets by variation with sorted values and puts weights in
// variation order; the model keeps the prior order when the content is the
// same, so apply gives back what was planned.
func TestFlagConfigToModelKeepsPriorOrder(t *testing.T) {
	weights := func(ws ...weightModel) *rolloutModel {
		return &rolloutModel{BucketBy: types.StringValue("key"), Weights: ws}
	}
	w := func(v string, n int64) weightModel {
		return weightModel{Variation: types.StringValue(v), Weight: types.Int64Value(n)}
	}
	prior := flagEnvironmentModel{
		Targets: []targetModel{
			{Variation: types.StringValue("off"), Values: []string{"carol"}},
			{Variation: types.StringValue("on"), Values: []string{"bob", "alice"}},
		},
		Rules:       []ruleModel{{Clauses: []clauseModel{}, Rollout: weights(w("off", 60000), w("on", 40000))}},
		Fallthrough: &serveModel{Rollout: weights(w("off", 75000), w("on", 25000))},
	}
	api := func(fallthroughOn int) *client.FlagConfig {
		return &client.FlagConfig{
			Environment: "prod",
			Targets:     []client.Target{{Variation: "on", Values: []string{"alice", "bob"}}, {Variation: "off", Values: []string{"carol"}}},
			Rules: []client.Rule{{Clauses: []client.Clause{}, Rollout: &client.Rollout{BucketBy: "key", Weights: []client.Weight{
				{Variation: "on", Weight: 40000}, {Variation: "off", Weight: 60000},
			}}}},
			Fallthrough: client.Serve{Rollout: &client.Rollout{BucketBy: "key", Weights: []client.Weight{
				{Variation: "on", Weight: fallthroughOn}, {Variation: "off", Weight: 100000 - fallthroughOn},
			}}},
		}
	}

	m := flagConfigToModel("shop", "checkout", api(25000), &prior)
	if !reflect.DeepEqual(m.Targets, prior.Targets) {
		t.Errorf("targets = %+v, want the prior order", m.Targets)
	}
	if !reflect.DeepEqual(m.Rules[0].Rollout, prior.Rules[0].Rollout) {
		t.Errorf("rule weights = %+v, want the prior order", m.Rules[0].Rollout)
	}
	if !reflect.DeepEqual(m.Fallthrough.Rollout, prior.Fallthrough.Rollout) {
		t.Errorf("fallthrough weights = %+v, want the prior order", m.Fallthrough.Rollout)
	}

	// Other weights than the prior's come in the API's order.
	m = flagConfigToModel("shop", "checkout", api(30000), &prior)
	if got := m.Fallthrough.Rollout.Weights; got[0].Variation.ValueString() != "on" || got[0].Weight.ValueInt64() != 30000 {
		t.Errorf("fallthrough weights = %+v, want the API's", got)
	}
}
