package main

import (
	"strings"
	"testing"
)

const (
	envStaging = `{"key":"staging","name":"Staging","description":"","sort_order":1,"approvals_required":0,"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-01T00:00:00Z"}`
	envsPath   = "/api/projects/shop/environments"
	copyStates = `[{"environment":"staging","enabled":true,"version":2,"updated_at":"2026-01-01T00:00:00Z"}]`
	configDev  = `{"environment":"development","enabled":true,"version":7,` +
		`"targets":[{"variation":"true","values":["u1"]}],"rules":[],` +
		`"fallthrough":{"variation":"false"},"prerequisites":[],"updated_at":"2026-03-04T05:06:07Z"}`
	changeRequest = `{"id":"cr-123","title":"Promote new-checkout from development to production","description":"",` +
		`"flag":"new-checkout","environment":"production","kind":"config","status":"open","author":"mona",` +
		`"approvals":0,"approvals_required":1,"base_version":4,"payload":{},"proposed":null,"current":null,` +
		`"error":null,"created_at":"2026-01-01T00:00:00Z","closed_at":null}`
)

func promoteAPI(t *testing.T) *fakeAPI {
	return newFakeAPI(t, map[string]reply{
		"GET " + envsPath:                               {body: `[` + envDev + `,` + envStaging + `,` + envProd + `]`},
		"POST " + flagPath + "/copy":                    {body: copyStates},
		"POST /api/projects/shop/flags/banner/copy":     {body: copyStates},
		"GET " + flagPath + "/environments/development": {body: configDev},
		"GET " + flagPath + "/environments/production":  {body: configProd},
		"POST /api/projects/shop/change-requests":       {status: 201, body: changeRequest},
	})
}

func TestPromoteDirect(t *testing.T) {
	api := promoteAPI(t)
	s := api.session("shop")

	out := ok(t, s, "promote", "new-checkout", "--from", "development", "--to", "staging", "--include-state")
	if out != "new-checkout -> staging: copied\n" {
		t.Fatalf("out = %q", out)
	}
	api.only(
		recorded{Method: "GET", Path: envsPath},
		recorded{Method: "POST", Path: flagPath + "/copy",
			Body: decodeJSON(t, `{"from":"development","to":["staging"],"include_enabled":true}`)},
	)
}

func TestPromoteNeedsApprovalExits4(t *testing.T) {
	api := promoteAPI(t)
	s := api.session("shop")

	code, out, _ := s.run("promote", "new-checkout", "--from", "development", "--to", "staging,production")
	if code != ExitNeedsApproval {
		t.Fatalf("code = %d, out = %q", code, out)
	}
	if out != "new-checkout -> staging: copied\nnew-checkout -> production: needs approval\n" {
		t.Fatalf("out = %q", out)
	}
	api.only(
		recorded{Method: "GET", Path: envsPath},
		recorded{Method: "POST", Path: flagPath + "/copy",
			Body: decodeJSON(t, `{"from":"development","to":["staging"]}`)},
	)

	// Only an approval-required target: no copy at all.
	api.reset()
	code, _, _ = s.run("promote", "new-checkout", "--from", "development", "--to", "production")
	if code != ExitNeedsApproval {
		t.Fatalf("code = %d", code)
	}
	api.only(recorded{Method: "GET", Path: envsPath})
}

func TestPromoteChangeRequests(t *testing.T) {
	api := promoteAPI(t)
	s := api.session("shop")

	out := ok(t, s, "promote", "new-checkout", "--from", "development", "--to", "production", "--change-requests")
	if out != "new-checkout -> production: opened change request cr-123\n" {
		t.Fatalf("out = %q", out)
	}
	// enabled comes from the target without --include-state; version is the target's.
	api.only(
		recorded{Method: "GET", Path: envsPath},
		recorded{Method: "GET", Path: flagPath + "/environments/development"},
		recorded{Method: "GET", Path: flagPath + "/environments/production"},
		recorded{Method: "POST", Path: "/api/projects/shop/change-requests", Body: decodeJSON(t,
			`{"title":"Promote new-checkout from development to production","flag":"new-checkout",`+
				`"environment":"production","kind":"config","payload":{"enabled":true,"version":4,`+
				`"targets":[{"variation":"true","values":["u1"]}],"fallthrough":{"variation":"false"}}}`)},
	)

	// With --include-state, enabled comes from the source.
	api.reset()
	api.routes["GET "+flagPath+"/environments/development"] = reply{body: strings.Replace(configDev, `"enabled":true`, `"enabled":false`, 1)}
	ok(t, s, "promote", "new-checkout", "--from", "development", "--to", "production", "--change-requests", "--include-state")
	payload := api.requests[3].Body.(map[string]any)["payload"].(map[string]any)
	if payload["enabled"] != false || payload["version"] != float64(4) {
		t.Fatalf("payload = %v", payload)
	}
}

func TestPromoteAllPages(t *testing.T) {
	api := promoteAPI(t)
	api.queue = map[string][]reply{"GET /api/projects/shop/flags": {
		{body: `{"items":[` + flagCheckoutSummary + `],"next_cursor":"c1"}`},
		{body: `{"items":[` + flagBannerSummary + `],"next_cursor":null}`},
	}}
	s := api.session("shop")

	out := ok(t, s, "promote", "--all", "--from", "development", "--to", "staging")
	if out != "new-checkout -> staging: copied\nbanner -> staging: copied\n" {
		t.Fatalf("out = %q", out)
	}
	copyBody := decodeJSON(t, `{"from":"development","to":["staging"]}`)
	api.only(
		recorded{Method: "GET", Path: envsPath},
		recorded{Method: "GET", Path: "/api/projects/shop/flags", Query: "limit=500"},
		recorded{Method: "GET", Path: "/api/projects/shop/flags", Query: "cursor=c1&limit=500"},
		recorded{Method: "POST", Path: flagPath + "/copy", Body: copyBody},
		recorded{Method: "POST", Path: "/api/projects/shop/flags/banner/copy", Body: copyBody},
	)
}

func TestPromoteContinuesAfterFailure(t *testing.T) {
	api := promoteAPI(t)
	s := api.session("shop")

	code, out, errOut := s.run("promote", "missing", "new-checkout", "production-less", "--from", "development",
		"--to", "staging,production")
	if code != ExitError {
		t.Fatalf("code = %d, want 1 (a failure wins over needs approval)", code)
	}
	if !strings.Contains(out, "new-checkout -> staging: copied\n") {
		t.Fatalf("out = %q", out)
	}
	if !strings.Contains(errOut, "missing -> staging: not_found: Not found") {
		t.Fatalf("stderr = %q", errOut)
	}
}

func TestPromoteUsageErrors(t *testing.T) {
	api := promoteAPI(t)
	s := api.session("shop")
	for _, args := range [][]string{
		{"promote", "--from", "development", "--to", "staging"},
		{"promote", "new-checkout", "--all", "--from", "development", "--to", "staging"},
		{"promote", "new-checkout", "--to", "staging"},
		{"promote", "new-checkout", "--from", "development"},
	} {
		if code, _, _ := s.run(args...); code != ExitUsage {
			t.Fatalf("%v: code = %d", args, code)
		}
	}
	api.only()
}
