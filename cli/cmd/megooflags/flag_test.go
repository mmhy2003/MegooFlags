package main

import (
	"fmt"
	"strings"
	"testing"
)

const (
	flagStates = `"environments":[` +
		`{"environment":"development","enabled":true,"version":3,"updated_at":"2026-01-01T00:00:00Z"},` +
		`{"environment":"production","enabled":false,"version":1,"updated_at":"2026-01-01T00:00:00Z"}]`
	flagCheckoutSummary = `{"key":"new-checkout","name":"New checkout","description":"","type":"boolean","tags":["checkout"],` +
		`"owner":null,"archived":false,"archived_at":null,"lifecycle":"active","last_evaluated_at":null,` +
		`"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z",` + flagStates + `}`
	flagBannerSummary = `{"key":"banner","name":"Banner","description":"","type":"string","tags":[],` +
		`"owner":"mona@example.com","archived":false,"archived_at":null,"lifecycle":"active","last_evaluated_at":null,` +
		`"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z","environments":[` +
		`{"environment":"development","enabled":false,"version":1,"updated_at":"2026-01-01T00:00:00Z"}]}`
	flagCheckout = `{"key":"new-checkout","name":"New checkout","description":"Faster checkout","type":"boolean","tags":["checkout"],` +
		`"owner":"mona@example.com","archived":false,"archived_at":null,"lifecycle":"active","last_evaluated_at":null,` +
		`"created_at":"2026-01-01T00:00:00Z","updated_at":"2026-01-02T00:00:00Z",` + flagStates + `,` +
		`"variations":[{"key":"true","name":"On","description":"","value":true},{"key":"false","name":"Off","description":"","value":false}],` +
		`"off_variation":"false"}`
	configProd = `{"environment":"production","enabled":true,"version":4,` +
		`"targets":[{"variation":"true","values":["u1","u2"]}],` +
		`"rules":[{"description":"staff","clauses":[{"attribute":"email","operator":"ends_with","values":["@example.com"],"negate":false}],"variation":"true"}],` +
		`"fallthrough":{"rollout":{"bucket_by":"key","weights":[{"variation":"true","weight":40000},{"variation":"false","weight":60000}]}},` +
		`"prerequisites":[{"flag":"banner","variation":"on"}],"updated_at":"2026-03-04T05:06:07Z"}`
	stateProdOn  = `{"environment":"production","enabled":true,"version":5,"updated_at":"2026-03-04T05:06:07Z"}`
	stateProdOff = `{"environment":"production","enabled":false,"version":5,"updated_at":"2026-03-04T05:06:07Z"}`

	approvalRequired = `{"code":"approval_required","message":"Changes in production need an approved change request"}`
	versionConflict  = `{"code":"version_conflict","message":"The config has changed"}`

	flagPath = "/api/projects/shop/flags/new-checkout"
)

func TestFlagList(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"GET /api/projects/shop/flags": {body: `{"items":[` + flagCheckoutSummary + `,` + flagBannerSummary + `],"next_cursor":null}`},
	})
	s := api.session("shop")

	wantRows(t, ok(t, s, "flag", "list"),
		"KEY TYPE NAME development production",
		"new-checkout boolean New checkout on off",
		"banner string Banner off -",
	)
	want := `[` + flagCheckoutSummary + `,` + flagBannerSummary + `]`
	sameJSON(t, decodeJSON(t, ok(t, s, "flag", "list", "-o", "json")), want)
	sameJSON(t, decodeYAML(t, ok(t, s, "flag", "list", "-o", "yaml")), want)
	ok(t, s, "flag", "list", "--archived", "--tag", "checkout", "--tag", "web", "-q", "check out")
	list := recorded{Method: "GET", Path: "/api/projects/shop/flags", Query: "limit=500"}
	api.only(list, list, list, recorded{Method: "GET", Path: "/api/projects/shop/flags",
		Query: "archived=true&limit=500&q=check+out&tag=checkout&tag=web"})
}

func TestFlagListEmpty(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"GET /api/projects/shop/flags": {body: `{"items":[],"next_cursor":null}`}})
	s := api.session("shop")
	wantRows(t, ok(t, s, "flag", "list"), "KEY TYPE NAME")
	sameJSON(t, decodeJSON(t, ok(t, s, "flag", "list", "-o", "json")), `[]`)
}

func TestFlagGet(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"GET " + flagPath: {body: flagCheckout}})
	s := api.session("shop")

	wantRows(t, ok(t, s, "flag", "get", "new-checkout"),
		"KEY TYPE NAME development production",
		"new-checkout boolean New checkout on off",
		"",
		"DESCRIPTION Faster checkout",
		"TAGS checkout",
		"OWNER mona@example.com",
		"OFF VARIATION false",
		"LIFECYCLE active",
		"ARCHIVED false",
		"",
		"VARIATION NAME VALUE",
		"true On true",
		"false Off false",
	)
	sameJSON(t, decodeJSON(t, ok(t, s, "flag", "get", "new-checkout", "-o", "json")), flagCheckout)
	sameJSON(t, decodeYAML(t, ok(t, s, "flag", "get", "new-checkout", "-o", "yaml")), flagCheckout)
	get := recorded{Method: "GET", Path: flagPath}
	api.only(get, get, get)
}

func TestFlagGetWithEnv(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"GET " + flagPath: {body: flagCheckout},
		"GET " + flagPath + "/environments/production": {body: configProd},
	})
	s := api.session("shop")

	out := ok(t, s, "flag", "get", "new-checkout", "--env", "production")
	rows := tableRows(out)
	tail := make([]string, 0, 9)
	for _, r := range rows[len(rows)-9:] {
		tail = append(tail, strings.Join(r, " "))
	}
	wantTail := []string{
		"",
		"ENVIRONMENT production",
		"ENABLED true",
		"VERSION 4",
		"TARGETS true: u1, u2",
		"RULES 1",
		`staff: email ends_with ["@example.com"] → true`,
		"FALLTHROUGH rollout by key: true 40%, false 60%",
		"PREREQUISITES banner = on",
	}
	if strings.Join(tail, "\n") != strings.Join(wantTail, "\n") {
		t.Fatalf("tail = %q\nwant %q\nout = %s", tail, wantTail, out)
	}

	sameJSON(t, decodeJSON(t, ok(t, s, "flag", "get", "new-checkout", "--env", "production", "-o", "json")),
		strings.TrimSuffix(flagCheckout, "}")+`,"config":`+configProd+`}`)
	get := recorded{Method: "GET", Path: flagPath}
	cfg := recorded{Method: "GET", Path: flagPath + "/environments/production"}
	api.only(get, cfg, get, cfg)
}

func TestFlagCreate(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"POST /api/projects/shop/flags": {status: 201, body: flagCheckout}})
	s := api.session("shop")

	out := ok(t, s, "flag", "create", "new-checkout", "--name", "New checkout", "--type", "boolean",
		"--description", "Faster checkout", "--tag", "checkout", "--tag", "web,mobile", "--owner", "mona@example.com",
		"--variation", "true=true", "--variation", "false=false", "--off-variation", "false", "-o", "json")
	sameJSON(t, decodeJSON(t, out), flagCheckout)
	api.only(recorded{Method: "POST", Path: "/api/projects/shop/flags", Body: decodeJSON(t,
		`{"key":"new-checkout","name":"New checkout","description":"Faster checkout","type":"boolean",`+
			`"tags":["checkout","web,mobile"],"owner":"mona@example.com",`+
			`"variations":[{"key":"true","value":true},{"key":"false","value":false}],"off_variation":"false"}`)})

	api.reset()
	ok(t, s, "flag", "create", "new-checkout", "--name", "New checkout", "--type", "boolean")
	api.only(recorded{Method: "POST", Path: "/api/projects/shop/flags", Body: decodeJSON(t,
		`{"key":"new-checkout","name":"New checkout","type":"boolean"}`)})
}

func TestFlagCreateParsesVariationValues(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"POST /api/projects/shop/flags": {status: 201, body: flagCheckout}})
	s := api.session("shop")

	ok(t, s, "flag", "create", "cfg", "--name", "Config", "--type", "json",
		"--variation", "a=true", "--variation", "b=1", "--variation", `c="x"`, "--variation", `d={"a":1}`,
		"--variation", `e="k=v"`)
	api.only(recorded{Method: "POST", Path: "/api/projects/shop/flags", Body: decodeJSON(t,
		`{"key":"cfg","name":"Config","type":"json","variations":[`+
			`{"key":"a","value":true},{"key":"b","value":1},{"key":"c","value":"x"},{"key":"d","value":{"a":1}},`+
			`{"key":"e","value":"k=v"}]}`)})
}

func TestFlagCreateUsageErrors(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("shop")
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"flag", "create", "x", "--type", "boolean"}, "name"},
		{[]string{"flag", "create", "x", "--name", "X"}, "type"},
		{[]string{"flag", "create", "x", "--name", "X", "--type", "bool"}, "boolean, string, number or json"},
		{[]string{"flag", "create", "x", "--name", "X", "--type", "string", "--variation", "a"}, "key=<json value>"},
		{[]string{"flag", "create", "x", "--name", "X", "--type", "string", "--variation", "=1"}, "key=<json value>"},
		{[]string{"flag", "create", "x", "--name", "X", "--type", "string", "--variation", "a=hello"}, `variation "a"`},
		{[]string{"flag", "create", "x", "--name", "X", "--type", "string", "--variation", "a=1 2"}, `variation "a"`},
	} {
		code, _, errOut := s.run(tc.args...)
		if code != ExitUsage || !strings.Contains(errOut, tc.want) {
			t.Fatalf("%v: code = %d, stderr = %q", tc.args, code, errOut)
		}
	}
	api.only()
}

func TestFlagUpdate(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"PATCH " + flagPath: {body: flagCheckout}})
	s := api.session("shop")

	out := ok(t, s, "flag", "update", "new-checkout", "--name", "New checkout", "--description", "",
		"--tag", "checkout", "--tag", "web", "--owner", "mona@example.com", "--off-variation", "false", "-o", "json")
	sameJSON(t, decodeJSON(t, out), flagCheckout)
	api.only(recorded{Method: "PATCH", Path: flagPath, Body: decodeJSON(t,
		`{"name":"New checkout","description":"","tags":["checkout","web"],"owner":"mona@example.com","off_variation":"false"}`)})

	api.reset()
	wantRows(t, strings.SplitN(ok(t, s, "flag", "update", "new-checkout", "--clear-owner"), "\n\n", 2)[0],
		"KEY TYPE NAME development production", "new-checkout boolean New checkout on off")
	ok(t, s, "flag", "update", "new-checkout", "--tag", "")
	api.only(
		recorded{Method: "PATCH", Path: flagPath, Body: decodeJSON(t, `{"owner":null}`)},
		recorded{Method: "PATCH", Path: flagPath, Body: decodeJSON(t, `{"tags":[]}`)},
	)

	api.reset()
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"flag", "update", "new-checkout"}, "nothing to update"},
		{[]string{"flag", "update", "new-checkout", "--owner", "a", "--clear-owner"}, "clear-owner"},
	} {
		code, _, errOut := s.run(tc.args...)
		if code != ExitUsage || !strings.Contains(errOut, tc.want) {
			t.Fatalf("%v: code = %d, stderr = %q", tc.args, code, errOut)
		}
	}
	api.only()
}

func TestFlagArchiveRestore(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"POST " + flagPath + "/archive": {body: flagCheckout},
		"POST " + flagPath + "/restore": {body: flagCheckout},
	})
	s := api.session("shop")

	sameJSON(t, decodeJSON(t, ok(t, s, "flag", "archive", "new-checkout", "-o", "json")), flagCheckout)
	wantRows(t, strings.SplitN(ok(t, s, "flag", "restore", "new-checkout"), "\n\n", 2)[0],
		"KEY TYPE NAME development production", "new-checkout boolean New checkout on off")
	api.only(
		recorded{Method: "POST", Path: flagPath + "/archive"},
		recorded{Method: "POST", Path: flagPath + "/restore"},
	)
}

func TestFlagDelete(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"DELETE " + flagPath:                   {status: 204},
		"DELETE /api/projects/shop/flags/live": {status: 409, body: `{"code":"not_archived","message":"Archive the flag before deleting it"}`},
	})
	s := api.session("shop")

	if code, _, errOut := s.run("flag", "delete", "new-checkout"); code != ExitUsage || !strings.Contains(errOut, "refusing to delete without --yes") {
		t.Fatalf("without --yes: code = %d, stderr = %q", code, errOut)
	}
	api.only()
	if out := ok(t, s, "flag", "delete", "new-checkout", "--yes"); out != "Deleted flag new-checkout\n" {
		t.Fatalf("out = %q", out)
	}
	code, out, errOut := s.run("flag", "delete", "live", "--yes")
	if code != ExitError || out != "" || errOut != "error: not_archived: Archive the flag before deleting it\n" {
		t.Fatalf("not archived: code = %d, out = %q, stderr = %q", code, out, errOut)
	}
	api.only(
		recorded{Method: "DELETE", Path: flagPath},
		recorded{Method: "DELETE", Path: "/api/projects/shop/flags/live"},
	)
}

func TestFlagVariationSetCreates(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"GET " + flagPath:                  {body: flagCheckout},
		"POST " + flagPath + "/variations": {status: 201, body: `{"key":"maybe","name":"Maybe","description":"Half","value":{"a":1}}`},
	})
	s := api.session("shop")

	wantRows(t, ok(t, s, "flag", "variation", "set", "new-checkout", "maybe", "--value", `{"a":1}`, "--name", "Maybe", "--description", "Half"),
		"VARIATION NAME VALUE", `maybe Maybe {"a":1}`)
	api.only(
		recorded{Method: "GET", Path: flagPath},
		recorded{Method: "POST", Path: flagPath + "/variations", Body: decodeJSON(t,
			`{"key":"maybe","name":"Maybe","description":"Half","value":{"a":1}}`)},
	)
}

func TestFlagVariationSetUpdates(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{
		"GET " + flagPath:                        {body: flagCheckout},
		"PATCH " + flagPath + "/variations/true": {body: `{"key":"true","name":"On","description":"","value":true}`},
	})
	s := api.session("shop")

	out := ok(t, s, "flag", "variation", "set", "new-checkout", "true", "--value", "true", "-o", "json")
	sameJSON(t, decodeJSON(t, out), `{"key":"true","name":"On","description":"","value":true}`)
	ok(t, s, "flag", "variation", "set", "new-checkout", "true", "--value", "true", "--name", "Yes", "--description", "")
	api.only(
		recorded{Method: "GET", Path: flagPath},
		recorded{Method: "PATCH", Path: flagPath + "/variations/true", Body: decodeJSON(t, `{"value":true}`)},
		recorded{Method: "GET", Path: flagPath},
		recorded{Method: "PATCH", Path: flagPath + "/variations/true", Body: decodeJSON(t, `{"name":"Yes","description":"","value":true}`)},
	)
}

func TestFlagVariationSetUsageErrors(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("shop")
	for _, args := range [][]string{
		{"flag", "variation", "set", "new-checkout", "maybe"},
		{"flag", "variation", "set", "new-checkout", "maybe", "--value", "maybe"},
	} {
		if code, _, _ := s.run(args...); code != ExitUsage {
			t.Fatalf("%v: code = %d", args, code)
		}
	}
	api.only()
}

func TestFlagVariationRemove(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"DELETE " + flagPath + "/variations/maybe": {status: 204}})
	s := api.session("shop")
	if out := ok(t, s, "flag", "variation", "remove", "new-checkout", "maybe"); out != "Removed variation maybe from flag new-checkout\n" {
		t.Fatalf("out = %q", out)
	}
	api.only(recorded{Method: "DELETE", Path: flagPath + "/variations/maybe"})
}

func TestFlagConfigGet(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"GET " + flagPath + "/environments/production": {body: configProd}})
	s := api.session("shop")

	out := ok(t, s, "flag", "config", "get", "new-checkout", "--env", "production")
	if !strings.HasPrefix(out, "environment: production\n") {
		t.Fatalf("not yaml: %s", out)
	}
	sameJSON(t, decodeYAML(t, out), configProd)
	sameJSON(t, decodeYAML(t, ok(t, s, "flag", "config", "get", "new-checkout", "--env", "production", "-o", "table")), configProd)
	sameJSON(t, decodeJSON(t, ok(t, s, "flag", "config", "get", "new-checkout", "--env", "production", "-o", "json")), configProd)
	get := recorded{Method: "GET", Path: flagPath + "/environments/production"}
	api.only(get, get, get)

	if code, _, _ := s.run("flag", "config", "get", "new-checkout"); code != ExitUsage {
		t.Fatalf("without --env: code = %d", code)
	}
}

const (
	envConfigYAML = `enabled: true
targets: [{variation: "true", values: [u1, u2]}]
rules:
  - description: staff
    clauses: [{attribute: email, operator: ends_with, values: ["@example.com"], negate: false}]
    variation: "true"
fallthrough:
  rollout: {bucket_by: key, weights: [{variation: "true", weight: 40000}, {variation: "false", weight: 60000}]}
prerequisites: [{flag: banner, variation: "on"}]
`
	configPutBody = `{"enabled":true,"version":%d,"targets":[{"variation":"true","values":["u1","u2"]}],` +
		`"rules":[{"description":"staff","clauses":[{"attribute":"email","operator":"ends_with","values":["@example.com"],"negate":false}],"variation":"true"}],` +
		`"fallthrough":{"rollout":{"bucket_by":"key","weights":[{"variation":"true","weight":40000},{"variation":"false","weight":60000}]}},` +
		`"prerequisites":[{"flag":"banner","variation":"on"}]}`
)

func putBody(t *testing.T, version int) any {
	t.Helper()
	return decodeJSON(t, fmt.Sprintf(configPutBody, version))
}

func TestFlagConfigSet(t *testing.T) {
	cfgPath := flagPath + "/environments/production"
	api := newFakeAPI(t, map[string]reply{
		"GET " + cfgPath: {body: configProd},
		"PUT " + cfgPath: {body: configProd},
	})
	s := api.session("shop")
	path := writeFile(t, "production.yaml", envConfigYAML)

	sameJSON(t, decodeYAML(t, ok(t, s, "flag", "config", "set", "new-checkout", "--env", "production", "-f", path)), configProd)
	api.only(
		recorded{Method: "GET", Path: cfgPath},
		recorded{Method: "PUT", Path: cfgPath, Body: putBody(t, 4)},
	)

	api.reset()
	jsonPath := writeFile(t, "production.json", `{"enabled":false,"fallthrough":{"variation":"false"}}`)
	sameJSON(t, decodeJSON(t, ok(t, s, "flag", "config", "set", "new-checkout", "--env", "production", "-f", jsonPath, "-o", "json")), configProd)
	api.only(
		recorded{Method: "GET", Path: cfgPath},
		recorded{Method: "PUT", Path: cfgPath, Body: decodeJSON(t, `{"enabled":false,"version":4,"fallthrough":{"variation":"false"}}`)},
	)
}

func TestFlagConfigSetRetriesOnceOnVersionConflict(t *testing.T) {
	cfgPath := flagPath + "/environments/production"
	api := newFakeAPI(t, map[string]reply{"PUT " + cfgPath: {body: configProd}})
	api.queue = map[string][]reply{
		"GET " + cfgPath: {{body: configProd}, {body: strings.Replace(configProd, `"version":4`, `"version":5`, 1)}},
		"PUT " + cfgPath: {{status: 409, body: versionConflict}},
	}
	s := api.session("shop")
	path := writeFile(t, "production.yaml", envConfigYAML)

	ok(t, s, "flag", "config", "set", "new-checkout", "--env", "production", "-f", path)
	api.only(
		recorded{Method: "GET", Path: cfgPath},
		recorded{Method: "PUT", Path: cfgPath, Body: putBody(t, 4)},
		recorded{Method: "GET", Path: cfgPath},
		recorded{Method: "PUT", Path: cfgPath, Body: putBody(t, 5)},
	)
}

func TestFlagConfigSetGivesUpAfterSecondConflict(t *testing.T) {
	cfgPath := flagPath + "/environments/production"
	api := newFakeAPI(t, map[string]reply{
		"GET " + cfgPath: {body: configProd},
		"PUT " + cfgPath: {status: 409, body: versionConflict},
	})
	s := api.session("shop")
	path := writeFile(t, "production.yaml", envConfigYAML)

	code, _, errOut := s.run("flag", "config", "set", "new-checkout", "--env", "production", "-f", path)
	if code != ExitError || !strings.HasPrefix(errOut, "error: version_conflict:") {
		t.Fatalf("code = %d, stderr = %q", code, errOut)
	}
	puts := 0
	for _, r := range api.requests {
		if r.Method == "PUT" {
			puts++
		}
	}
	if puts != 2 {
		t.Fatalf("PUTs = %d, want 2", puts)
	}
}

func TestFlagConfigSetUsageErrors(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("shop")
	bad := writeFile(t, "bad.yaml", "enabled: true\nversion: 3\n")
	for _, tc := range []struct {
		args []string
		want string
	}{
		{[]string{"flag", "config", "set", "new-checkout", "-f", bad}, "env"},
		{[]string{"flag", "config", "set", "new-checkout", "--env", "production"}, "file"},
		{[]string{"flag", "config", "set", "new-checkout", "--env", "production", "-f", bad}, "version"},
		{[]string{"flag", "config", "set", "new-checkout", "--env", "production", "-f", writeFile(t, "empty.yaml", "")}, "empty file"},
	} {
		code, _, errOut := s.run(tc.args...)
		if code != ExitUsage || !strings.Contains(errOut, tc.want) {
			t.Fatalf("%v: code = %d, stderr = %q", tc.args, code, errOut)
		}
	}
	api.only()
}

func TestFlagOnOffKill(t *testing.T) {
	envPath := flagPath + "/environments/production"
	api := newFakeAPI(t, map[string]reply{
		"POST " + envPath + "/toggle": {body: stateProdOn},
		"POST " + envPath + "/kill":   {body: stateProdOff},
	})
	s := api.session("shop")

	wantRows(t, ok(t, s, "flag", "on", "new-checkout", "--env", "production"), "ENVIRONMENT STATE VERSION", "production on 5")
	ok(t, s, "flag", "off", "new-checkout", "--env", "production")
	wantRows(t, ok(t, s, "flag", "kill", "new-checkout", "--env", "production"), "ENVIRONMENT STATE VERSION", "production off 5")
	sameJSON(t, decodeJSON(t, ok(t, s, "flag", "kill", "new-checkout", "--env", "production", "-o", "json")), stateProdOff)
	api.only(
		recorded{Method: "POST", Path: envPath + "/toggle", Body: decodeJSON(t, `{"enabled":true}`)},
		recorded{Method: "POST", Path: envPath + "/toggle", Body: decodeJSON(t, `{"enabled":false}`)},
		recorded{Method: "POST", Path: envPath + "/kill"},
		recorded{Method: "POST", Path: envPath + "/kill"},
	)

	for _, cmd := range []string{"on", "off", "kill"} {
		if code, _, _ := s.run("flag", cmd, "new-checkout"); code != ExitUsage {
			t.Fatalf("%s without --env: code = %d", cmd, code)
		}
	}
}

func TestFlagApprovalRequiredExits4(t *testing.T) {
	envPath := flagPath + "/environments/production"
	api := newFakeAPI(t, map[string]reply{
		"POST " + envPath + "/toggle": {status: 409, body: approvalRequired},
		"GET " + envPath:              {body: configProd},
		"PUT " + envPath:              {status: 409, body: approvalRequired},
	})
	s := api.session("shop")
	path := writeFile(t, "production.yaml", envConfigYAML)

	want := "error: approval_required: Changes in production need an approved change request\n" +
		"hint: open a change request in the UI, or use `megooflags apply --change-requests`\n"
	for _, args := range [][]string{
		{"flag", "on", "new-checkout", "--env", "production"},
		{"flag", "config", "set", "new-checkout", "--env", "production", "-f", path},
	} {
		code, out, errOut := s.run(args...)
		if code != ExitNeedsApproval || out != "" || errOut != want {
			t.Fatalf("%v: code = %d, out = %q, stderr = %q", args, code, out, errOut)
		}
	}
	api.only(
		recorded{Method: "POST", Path: envPath + "/toggle", Body: decodeJSON(t, `{"enabled":true}`)},
		recorded{Method: "GET", Path: envPath},
		recorded{Method: "PUT", Path: envPath, Body: putBody(t, 4)},
	)
}

func TestFlagCommandsNeedProject(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("")
	for _, args := range [][]string{
		{"flag", "list"},
		{"flag", "get", "x"},
		{"flag", "create", "x", "--name", "X", "--type", "boolean"},
		{"flag", "update", "x", "--name", "X"},
		{"flag", "archive", "x"},
		{"flag", "restore", "x"},
		{"flag", "delete", "x", "--yes"},
		{"flag", "variation", "set", "x", "v", "--value", "1"},
		{"flag", "variation", "remove", "x", "v"},
		{"flag", "config", "get", "x", "--env", "e"},
		{"flag", "on", "x", "--env", "e"},
		{"flag", "off", "x", "--env", "e"},
		{"flag", "kill", "x", "--env", "e"},
	} {
		code, _, errOut := s.run(args...)
		if code != ExitUsage || !strings.Contains(errOut, "no project: pass -p or set MEGOOFLAGS_PROJECT") {
			t.Fatalf("%v: code = %d, stderr = %q", args, code, errOut)
		}
	}
	api.only()
}

func TestFlagNotFound(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("shop")
	code, _, errOut := s.run("flag", "get", "nope")
	if code != ExitError || !strings.HasPrefix(errOut, "error: not_found:") {
		t.Fatalf("code = %d, stderr = %q", code, errOut)
	}
}
