package client

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"testing"
)

// apiCase is one client call against a recording server.
type apiCase struct {
	name   string
	call   func(ctx context.Context, c *Client) (any, error)
	method string
	path   string // escaped path
	query  string // encoded query, compared as url.Values
	body   string // request JSON, compared semantically; "" means no body
	status int    // response status; 0 means 200
	resp   string // response JSON fixture
	want   any    // decoded result; nil for calls that return only an error
}

func runCases(t *testing.T, cases []apiCase) {
	t.Helper()
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var method, path string
			var query url.Values
			var body []byte
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				method, path, query = r.Method, r.URL.EscapedPath(), r.URL.Query()
				body, _ = io.ReadAll(r.Body)
				status := tc.status
				if status == 0 {
					status = http.StatusOK
				}
				if tc.resp != "" {
					w.Header().Set("Content-Type", "application/json")
				}
				w.WriteHeader(status)
				_, _ = io.WriteString(w, tc.resp)
			}))
			defer srv.Close()

			got, err := tc.call(context.Background(), New(srv.URL, testToken))
			if err != nil {
				t.Fatalf("call: %v", err)
			}
			if method != tc.method {
				t.Errorf("method = %s, want %s", method, tc.method)
			}
			if path != tc.path {
				t.Errorf("path = %s, want %s", path, tc.path)
			}
			wantQuery, _ := url.ParseQuery(tc.query)
			if len(query) != 0 || len(wantQuery) != 0 {
				if !reflect.DeepEqual(query, wantQuery) {
					t.Errorf("query = %v, want %v", query, wantQuery)
				}
			}
			assertJSON(t, body, tc.body)
			if tc.want != nil && !reflect.DeepEqual(got, tc.want) {
				t.Errorf("result =\n%#v\nwant\n%#v", got, tc.want)
			}
		})
	}
}

func assertJSON(t *testing.T, got []byte, want string) {
	t.Helper()
	if want == "" {
		if len(got) != 0 {
			t.Errorf("body = %s, want none", got)
		}
		return
	}
	var g, w any
	if err := json.Unmarshal(got, &g); err != nil {
		t.Fatalf("request body %q: %v", got, err)
	}
	if err := json.Unmarshal([]byte(want), &w); err != nil {
		t.Fatalf("bad want %q: %v", want, err)
	}
	if !reflect.DeepEqual(g, w) {
		t.Errorf("body = %s, want %s", got, want)
	}
}

func ptr[T any](v T) *T { return &v }

const (
	envFixture = `{"key":"production","name":"Production","description":"Live","sort_order":1,
		"approvals_required":2,"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z"}`
	projectFixture = `{"key":"shop","name":"Shop","description":"d","stale_after_days":30,
		"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z","environments":[` + envFixture + `]}`
	segmentFixture = `{"key":"beta","name":"Beta","description":"","included":["u1"],"excluded":["u2"],
		"rules":[{"description":"staff","clauses":[{"attribute":"email","operator":"ends_with","values":["@x.com"],"negate":false}]}],
		"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z"}`
	stateFixture = `{"environment":"production","enabled":true,"version":3,"updated_at":"2026-01-02T00:00:00Z"}`
	flagFixture  = `{"key":"new-checkout","name":"New checkout","description":"","type":"boolean","tags":["checkout"],
		"owner":"mona@example.com","archived":false,"archived_at":null,"lifecycle":"active","last_evaluated_at":null,
		"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z","environments":[` + stateFixture + `],
		"off_variation":"false","variations":[{"key":"true","name":"On","description":"","value":true},{"key":"false","name":"Off","description":"","value":false}]}`
	configFixture = `{"environment":"production","enabled":true,"version":3,"updated_at":"2026-01-02T00:00:00Z",
		"targets":[{"variation":"true","values":["u1"]}],
		"rules":[{"description":"","clauses":[{"attribute":"key","operator":"in_segment","values":["beta"],"negate":false}],
			"rollout":{"bucket_by":"key","weights":[{"variation":"true","weight":40000},{"variation":"false","weight":60000}]}}],
		"fallthrough":{"variation":"false"},"prerequisites":[{"flag":"other","variation":"true"}]}`
)

var (
	wantEnv = Environment{
		Key: "production", Name: "Production", Description: "Live", SortOrder: 1, ApprovalsRequired: 2,
		CreatedAt: "2026-01-01T00:00:00Z", UpdatedAt: "2026-01-02T00:00:00Z",
	}
	wantProject = &Project{
		Key: "shop", Name: "Shop", Description: "d", StaleAfterDays: 30,
		CreatedAt: "2026-01-01T00:00:00Z", UpdatedAt: "2026-01-02T00:00:00Z", Environments: []Environment{wantEnv},
	}
	wantSegment = &Segment{
		Key: "beta", Name: "Beta", Included: []string{"u1"}, Excluded: []string{"u2"},
		Rules: []SegmentRule{{Description: "staff", Clauses: []Clause{
			{Attribute: "email", Operator: "ends_with", Values: []any{"@x.com"}},
		}}},
		CreatedAt: "2026-01-01T00:00:00Z", UpdatedAt: "2026-01-02T00:00:00Z",
	}
	wantState = FlagEnvironmentState{Environment: "production", Enabled: true, Version: 3, UpdatedAt: "2026-01-02T00:00:00Z"}
	wantFlag  = &Flag{
		FlagSummary: FlagSummary{
			Key: "new-checkout", Name: "New checkout", Type: "boolean", Tags: []string{"checkout"},
			Owner: ptr("mona@example.com"), Lifecycle: "active",
			CreatedAt: "2026-01-01T00:00:00Z", UpdatedAt: "2026-01-02T00:00:00Z",
			Environments: []FlagEnvironmentState{wantState},
		},
		OffVariation: "false",
		Variations: []Variation{
			{Key: "true", Name: "On", Value: true},
			{Key: "false", Name: "Off", Value: false},
		},
	}
	wantConfig = &FlagConfig{
		Environment: "production", Enabled: true, Version: 3, UpdatedAt: "2026-01-02T00:00:00Z",
		Targets: []Target{{Variation: "true", Values: []string{"u1"}}},
		Rules: []Rule{{
			Clauses: []Clause{{Attribute: "key", Operator: "in_segment", Values: []any{"beta"}}},
			Rollout: &Rollout{BucketBy: "key", Weights: []Weight{{Variation: "true", Weight: 40000}, {Variation: "false", Weight: 60000}}},
		}},
		Fallthrough:   Serve{Variation: ptr("false")},
		Prerequisites: []Prerequisite{{Flag: "other", Variation: "true"}},
	}
)

func page1(items string) string { return `{"items":[` + items + `],"next_cursor":null}` }

func TestProjects(t *testing.T) {
	runCases(t, []apiCase{
		{
			name: "ListProjects", method: "GET", path: "/api/projects", query: "limit=500",
			resp: page1(`{"key":"shop","name":"Shop","description":"","stale_after_days":30,"created_at":"c","updated_at":"u"}`),
			call: func(ctx context.Context, c *Client) (any, error) { return c.ListProjects(ctx) },
			want: []Project{{Key: "shop", Name: "Shop", StaleAfterDays: 30, CreatedAt: "c", UpdatedAt: "u"}},
		},
		{
			name: "GetProject escapes", method: "GET", path: "/api/projects/a%2Fb%20c", resp: projectFixture,
			call: func(ctx context.Context, c *Client) (any, error) { return c.GetProject(ctx, "a/b c") },
			want: wantProject,
		},
		{
			name: "CreateProject", method: "POST", path: "/api/projects", status: 201, resp: projectFixture,
			body: `{"key":"shop","name":"Shop","description":"d","environments":[{"key":"production","name":"Production"}]}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.CreateProject(ctx, ProjectCreate{Key: "shop", Name: "Shop", Description: "d",
					Environments: []EnvironmentCreate{{Key: "production", Name: "Production"}}})
			},
			want: wantProject,
		},
		{
			name: "UpdateProject", method: "PATCH", path: "/api/projects/shop", resp: projectFixture,
			body: `{"name":"Shop","stale_after_days":30}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.UpdateProject(ctx, "shop", ProjectUpdate{Name: ptr("Shop"), StaleAfterDays: ptr(30)})
			},
			want: wantProject,
		},
		{
			name: "DeleteProject", method: "DELETE", path: "/api/projects/shop", status: 204,
			call: func(ctx context.Context, c *Client) (any, error) { return nil, c.DeleteProject(ctx, "shop") },
		},
	})
}

func TestEnvironments(t *testing.T) {
	base := "/api/projects/shop/environments"
	runCases(t, []apiCase{
		{
			name: "ListEnvironments", method: "GET", path: base, resp: `[` + envFixture + `]`,
			call: func(ctx context.Context, c *Client) (any, error) { return c.ListEnvironments(ctx, "shop") },
			want: []Environment{wantEnv},
		},
		{
			name: "GetEnvironment", method: "GET", path: base + "/production", resp: envFixture,
			call: func(ctx context.Context, c *Client) (any, error) { return c.GetEnvironment(ctx, "shop", "production") },
			want: &wantEnv,
		},
		{
			name: "CreateEnvironment", method: "POST", path: base, status: 201, resp: envFixture,
			body: `{"key":"production","name":"Production","description":"Live","sort_order":1}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.CreateEnvironment(ctx, "shop", EnvironmentCreate{Key: "production", Name: "Production", Description: "Live", SortOrder: ptr(1)})
			},
			want: &wantEnv,
		},
		{
			name: "UpdateEnvironment", method: "PATCH", path: base + "/production", resp: envFixture,
			body: `{"approvals_required":0}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.UpdateEnvironment(ctx, "shop", "production", EnvironmentUpdate{ApprovalsRequired: ptr(0)})
			},
			want: &wantEnv,
		},
		{
			name: "DeleteEnvironment", method: "DELETE", path: base + "/production", status: 204,
			call: func(ctx context.Context, c *Client) (any, error) {
				return nil, c.DeleteEnvironment(ctx, "shop", "production")
			},
		},
	})
}

func TestSegments(t *testing.T) {
	base := "/api/projects/shop/segments"
	runCases(t, []apiCase{
		{
			name: "ListSegments", method: "GET", path: base, query: "limit=500",
			resp: page1(`{"key":"beta","name":"Beta","description":"","created_at":"c","updated_at":"u"}`),
			call: func(ctx context.Context, c *Client) (any, error) { return c.ListSegments(ctx, "shop") },
			want: []Segment{{Key: "beta", Name: "Beta", CreatedAt: "c", UpdatedAt: "u"}},
		},
		{
			name: "GetSegment", method: "GET", path: base + "/beta", resp: segmentFixture,
			call: func(ctx context.Context, c *Client) (any, error) { return c.GetSegment(ctx, "shop", "beta") },
			want: wantSegment,
		},
		{
			name: "CreateSegment", method: "POST", path: base, status: 201, resp: segmentFixture,
			body: `{"key":"beta","name":"Beta"}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.CreateSegment(ctx, "shop", SegmentCreate{Key: "beta", Name: "Beta"})
			},
			want: wantSegment,
		},
		{
			name: "UpdateSegment", method: "PATCH", path: base + "/beta", resp: segmentFixture,
			body: `{"description":"new"}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.UpdateSegment(ctx, "shop", "beta", SegmentUpdate{Description: ptr("new")})
			},
			want: wantSegment,
		},
		{
			name: "PutSegmentConfig", method: "PUT", path: base + "/beta", resp: segmentFixture,
			body: `{"included":["u1"],"rules":[{"description":"staff","clauses":[{"attribute":"email","operator":"ends_with","values":["@x.com"],"negate":false}]}]}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.PutSegmentConfig(ctx, "shop", "beta", SegmentConfig{
					Included: []string{"u1"},
					Rules:    wantSegment.Rules,
				})
			},
			want: wantSegment,
		},
		{
			name: "DeleteSegment", method: "DELETE", path: base + "/beta", status: 204,
			call: func(ctx context.Context, c *Client) (any, error) { return nil, c.DeleteSegment(ctx, "shop", "beta") },
		},
	})
}

func TestFlags(t *testing.T) {
	base := "/api/projects/shop/flags"
	runCases(t, []apiCase{
		{
			name: "ListFlags", method: "GET", path: base, query: "limit=500",
			resp: page1(`{"key":"f","name":"F","description":"","type":"string","tags":[],"owner":null,"archived":false,
				"archived_at":null,"lifecycle":"new","last_evaluated_at":null,"created_at":"c","updated_at":"u","environments":[` + stateFixture + `]}`),
			call: func(ctx context.Context, c *Client) (any, error) { return c.ListFlags(ctx, "shop", ListFlagsOptions{}) },
			want: []FlagSummary{{Key: "f", Name: "F", Type: "string", Tags: []string{}, Lifecycle: "new",
				CreatedAt: "c", UpdatedAt: "u", Environments: []FlagEnvironmentState{wantState}}},
		},
		{
			name: "ListFlags options", method: "GET", path: base,
			query: "limit=500&archived=true&q=check&tag=a&tag=b", resp: page1(``),
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.ListFlags(ctx, "shop", ListFlagsOptions{Archived: true, Query: "check", Tags: []string{"a", "b"}})
			},
		},
		{
			name: "GetFlag", method: "GET", path: base + "/new-checkout", resp: flagFixture,
			call: func(ctx context.Context, c *Client) (any, error) { return c.GetFlag(ctx, "shop", "new-checkout") },
			want: wantFlag,
		},
		{
			name: "CreateFlag", method: "POST", path: base, status: 201, resp: flagFixture,
			body: `{"key":"new-checkout","name":"New checkout","type":"boolean","tags":["checkout"],"owner":"mona@example.com",
				"variations":[{"key":"true","value":true},{"key":"false","name":"Off","value":false}],"off_variation":"false"}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.CreateFlag(ctx, "shop", FlagCreate{
					Key: "new-checkout", Name: "New checkout", Type: "boolean", Tags: []string{"checkout"},
					Owner:        ptr("mona@example.com"),
					Variations:   []VariationIn{{Key: "true", Value: true}, {Key: "false", Name: "Off", Value: false}},
					OffVariation: ptr("false"),
				})
			},
			want: wantFlag,
		},
		{
			name: "UpdateFlag sets fields", method: "PATCH", path: base + "/new-checkout", resp: flagFixture,
			body: `{"name":"N","tags":[],"owner":"o","off_variation":"true"}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.UpdateFlag(ctx, "shop", "new-checkout", FlagUpdate{
					Name: ptr("N"), Tags: ptr([]string{}), Owner: ptr("o"), OffVariation: ptr("true"),
				})
			},
			want: wantFlag,
		},
		{
			name: "UpdateFlag clears owner", method: "PATCH", path: base + "/new-checkout", resp: flagFixture,
			body: `{"description":"d","owner":null}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.UpdateFlag(ctx, "shop", "new-checkout", FlagUpdate{Description: ptr("d"), ClearOwner: true})
			},
		},
		{
			name: "SetLifecycle", method: "PUT", path: base + "/new-checkout/lifecycle", resp: flagFixture,
			body: `{"permanent":false}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.SetLifecycle(ctx, "shop", "new-checkout", false)
			},
			want: wantFlag,
		},
		{
			name: "ArchiveFlag", method: "POST", path: base + "/new-checkout/archive", resp: flagFixture,
			call: func(ctx context.Context, c *Client) (any, error) { return c.ArchiveFlag(ctx, "shop", "new-checkout") },
			want: wantFlag,
		},
		{
			name: "RestoreFlag", method: "POST", path: base + "/new-checkout/restore", resp: flagFixture,
			call: func(ctx context.Context, c *Client) (any, error) { return c.RestoreFlag(ctx, "shop", "new-checkout") },
			want: wantFlag,
		},
		{
			name: "DeleteFlag", method: "DELETE", path: base + "/new-checkout", status: 204,
			call: func(ctx context.Context, c *Client) (any, error) {
				return nil, c.DeleteFlag(ctx, "shop", "new-checkout")
			},
		},
		{
			name: "CreateVariation", method: "POST", path: base + "/f/variations", status: 201,
			body: `{"key":"blue","name":"Blue","description":"d","value":{"c":"#00f"}}`,
			resp: `{"key":"blue","name":"Blue","description":"d","value":{"c":"#00f"}}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.CreateVariation(ctx, "shop", "f", VariationIn{Key: "blue", Name: "Blue", Description: "d", Value: map[string]any{"c": "#00f"}})
			},
			want: &Variation{Key: "blue", Name: "Blue", Description: "d", Value: map[string]any{"c": "#00f"}},
		},
		{
			name: "UpdateVariation", method: "PATCH", path: base + "/f/variations/blue",
			body: `{"name":"B","value":null}`, resp: `{"key":"blue","name":"B","description":"","value":null}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.UpdateVariation(ctx, "shop", "f", "blue", VariationUpdate{Name: ptr("B"), Value: ptr[any](nil)})
			},
			want: &Variation{Key: "blue", Name: "B"},
		},
		{
			name: "UpdateVariation without value", method: "PATCH", path: base + "/f/variations/blue",
			body: `{"description":"x"}`, resp: `{"key":"blue","name":"B","description":"x","value":1}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.UpdateVariation(ctx, "shop", "f", "blue", VariationUpdate{Description: ptr("x")})
			},
			want: &Variation{Key: "blue", Name: "B", Description: "x", Value: float64(1)},
		},
		{
			name: "DeleteVariation", method: "DELETE", path: base + "/f/variations/blue", status: 204,
			call: func(ctx context.Context, c *Client) (any, error) {
				return nil, c.DeleteVariation(ctx, "shop", "f", "blue")
			},
		},
	})
}

func TestConfigs(t *testing.T) {
	base := "/api/projects/shop/flags/f"
	runCases(t, []apiCase{
		{
			name: "ListFlagStates", method: "GET", path: base + "/environments", resp: `[` + stateFixture + `]`,
			call: func(ctx context.Context, c *Client) (any, error) { return c.ListFlagStates(ctx, "shop", "f") },
			want: []FlagEnvironmentState{wantState},
		},
		{
			name: "GetFlagConfig", method: "GET", path: base + "/environments/production", resp: configFixture,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.GetFlagConfig(ctx, "shop", "f", "production")
			},
			want: wantConfig,
		},
		{
			name: "PutFlagConfig", method: "PUT", path: base + "/environments/production", resp: configFixture,
			body: `{"enabled":true,"version":2,"targets":[{"variation":"true","values":["u1"]}],
				"fallthrough":{"variation":"false"},"prerequisites":[{"flag":"other","variation":"true"}]}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.PutFlagConfig(ctx, "shop", "f", "production", FlagConfigIn{
					Enabled: true, Version: 2,
					Targets:       wantConfig.Targets,
					Fallthrough:   Serve{Variation: ptr("false")},
					Prerequisites: wantConfig.Prerequisites,
				})
			},
			want: wantConfig,
		},
		{
			name: "Toggle", method: "POST", path: base + "/environments/production/toggle", resp: stateFixture,
			body: `{"enabled":true}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.Toggle(ctx, "shop", "f", "production", true, nil)
			},
			want: &wantState,
		},
		{
			name: "Toggle with version", method: "POST", path: base + "/environments/production/toggle", resp: stateFixture,
			body: `{"enabled":false,"version":3}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.Toggle(ctx, "shop", "f", "production", false, ptr(3))
			},
			want: &wantState,
		},
		{
			name: "Kill", method: "POST", path: base + "/environments/production/kill", resp: stateFixture,
			call: func(ctx context.Context, c *Client) (any, error) { return c.Kill(ctx, "shop", "f", "production") },
			want: &wantState,
		},
		{
			name: "CopyFlag", method: "POST", path: base + "/copy", resp: `[` + stateFixture + `]`,
			body: `{"from":"staging","to":["production"],"include_enabled":true}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.CopyFlag(ctx, "shop", "f", CopyIn{From: "staging", To: []string{"production"}, IncludeEnabled: true})
			},
			want: []FlagEnvironmentState{wantState},
		},
		{
			name: "CreateChangeRequest", method: "POST", path: "/api/projects/shop/change-requests", status: 201,
			body: `{"title":"t","flag":"f","environment":"production","kind":"toggle","payload":{"enabled":true}}`,
			resp: `{"id":"cr1","title":"t","description":"","flag":"f","environment":"production","kind":"toggle",
				"status":"open","author":"a","approvals":0,"approvals_required":1,"base_version":3,"payload":{"enabled":true},
				"proposed":null,"current":null,"error":null,"comments":[],"reviews":[],"created_at":"c","closed_at":null}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.CreateChangeRequest(ctx, "shop", ChangeRequestCreate{
					Title: "t", Flag: "f", Environment: "production", Kind: "toggle", Payload: map[string]any{"enabled": true},
				})
			},
			want: &ChangeRequest{ID: "cr1", Title: "t", Flag: "f", Environment: "production", Kind: "toggle",
				Status: "open", Author: "a", ApprovalsRequired: 1, BaseVersion: 3,
				Payload: map[string]any{"enabled": true}, CreatedAt: "c"},
		},
	})
}

func TestEvaluateAndKeys(t *testing.T) {
	base := "/api/projects/shop/environments/production"
	runCases(t, []apiCase{
		{
			name: "Evaluate", method: "POST", path: base + "/evaluate",
			body: `{"context":{"key":"u1"},"flags":["f"]}`,
			resp: `{"results":[{"key":"f","value":true,"variant":"true","reason":"TARGETING_MATCH","error_code":null,
				"error_message":null,"metadata":{"rule":0}}]}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.Evaluate(ctx, "shop", "production", EvaluateIn{Context: map[string]any{"key": "u1"}, Flags: []string{"f"}})
			},
			want: &EvaluateOut{Results: []EvaluationResult{{Key: "f", Value: true, Variant: ptr("true"),
				Reason: "TARGETING_MATCH", Metadata: map[string]any{"rule": float64(0)}}}},
		},
		{
			name: "ListSdkKeys", method: "GET", path: base + "/sdk-keys",
			resp: `[{"id":"k1","name":"srv","kind":"server","prefix":"mf_server_ab","created_at":"c","expires_at":null,
				"revoked_at":null,"rotated_from_id":null}]`,
			call: func(ctx context.Context, c *Client) (any, error) { return c.ListSdkKeys(ctx, "shop", "production") },
			want: []SdkKey{{ID: "k1", Name: "srv", Kind: "server", Prefix: "mf_server_ab", CreatedAt: "c"}},
		},
		{
			name: "CreateSdkKey", method: "POST", path: base + "/sdk-keys", status: 201,
			body: `{"name":"srv","kind":"server"}`,
			resp: `{"id":"k1","name":"srv","kind":"server","prefix":"mf_server_ab","key":"mf_server_abcdef","created_at":"c",
				"expires_at":null,"rotated_from_id":null}`,
			call: func(ctx context.Context, c *Client) (any, error) {
				return c.CreateSdkKey(ctx, "shop", "production", SdkKeyCreate{Name: "srv", Kind: "server"})
			},
			want: &SdkKeyCreated{ID: "k1", Name: "srv", Kind: "server", Prefix: "mf_server_ab", Key: "mf_server_abcdef", CreatedAt: "c"},
		},
		{
			name: "RevokeSdkKey", method: "DELETE", path: base + "/sdk-keys/k1", status: 204,
			call: func(ctx context.Context, c *Client) (any, error) {
				return nil, c.RevokeSdkKey(ctx, "shop", "production", "k1")
			},
		},
		{
			name: "Me", method: "GET", path: "/api/auth/me",
			resp: `{"id":"t1","kind":"service_token","label":"ci","name":null,"email":null,"auth_method":"api_token",
				"require_mfa":false,"mfa":null,"preferences":null,"session_state":null}`,
			call: func(ctx context.Context, c *Client) (any, error) { return c.Me(ctx) },
			want: &Me{ID: "t1", Kind: "service_token", Label: "ci", AuthMethod: "api_token"},
		},
	})
}

func TestRuleset(t *testing.T) {
	const body = `{"version":1,"flags":{}}`
	var gotINM, gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotINM = r.URL.EscapedPath(), r.Header.Get("If-None-Match")
		w.Header().Set("ETag", `"abc"`)
		if gotINM == `"abc"` {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, body)
	}))
	defer srv.Close()
	c := New(srv.URL, testToken)
	ctx := context.Background()

	raw, etag, notModified, err := c.Ruleset(ctx, "shop", "production", "")
	if err != nil {
		t.Fatal(err)
	}
	if gotPath != "/api/projects/shop/environments/production/ruleset" {
		t.Errorf("path = %s", gotPath)
	}
	if gotINM != "" {
		t.Errorf("If-None-Match sent without a value: %q", gotINM)
	}
	if string(raw) != body || etag != `"abc"` || notModified {
		t.Errorf("200: body %q, etag %q, notModified %v", raw, etag, notModified)
	}

	raw, etag, notModified, err = c.Ruleset(ctx, "shop", "production", `"abc"`)
	if err != nil {
		t.Fatal(err)
	}
	if gotINM != `"abc"` {
		t.Errorf("If-None-Match = %q", gotINM)
	}
	if len(raw) != 0 || etag != `"abc"` || !notModified {
		t.Errorf("304: body %q, etag %q, notModified %v", raw, etag, notModified)
	}
}

func TestRulesetError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"code":"not_found","message":"no env"}`)
	}))
	defer srv.Close()
	_, _, _, err := New(srv.URL, testToken).Ruleset(context.Background(), "shop", "nope", "")
	if !IsNotFound(err) {
		t.Errorf("err = %v, want not_found", err)
	}
}

func TestFlagConfigJSONRoundTrip(t *testing.T) {
	cases := map[string]string{
		"variation": `{"environment":"production","enabled":true,"version":1,"updated_at":"u",
			"targets":[{"variation":"on","values":["a","b"]}],
			"rules":[{"description":"r","clauses":[{"attribute":"age","operator":"gte","values":[18,"x",true],"negate":true}],"variation":"on"}],
			"fallthrough":{"variation":"off"},"prerequisites":[]}`,
		"rollout": `{"environment":"production","enabled":false,"version":2,"updated_at":"u","targets":[],"rules":[],
			"fallthrough":{"rollout":{"bucket_by":"org","weights":[{"variation":"on","weight":40000},{"variation":"off","weight":60000}]}},
			"prerequisites":[{"flag":"p","variation":"on"}]}`,
	}
	for name, in := range cases {
		t.Run(name, func(t *testing.T) {
			var cfg FlagConfig
			if err := json.Unmarshal([]byte(in), &cfg); err != nil {
				t.Fatal(err)
			}
			out, err := json.Marshal(cfg)
			if err != nil {
				t.Fatal(err)
			}
			assertJSON(t, out, in)
		})
	}
}
