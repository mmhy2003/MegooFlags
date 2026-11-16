package main

import "testing"

const (
	evalPath   = "/api/projects/shop/environments/development/evaluate"
	evalResult = `{"results":[` +
		`{"key":"new-checkout","value":true,"variant":"true","reason":"TARGETING_MATCH","error_code":null,"error_message":null,"metadata":{}},` +
		`{"key":"banner","value":{"text":"hi"},"variant":null,"reason":"ERROR","error_code":"FLAG_NOT_FOUND","error_message":"no flag","metadata":{}}]}`
)

func TestEvaluate(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"POST " + evalPath: {body: evalResult}})
	s := api.session("shop")

	wantRows(t, ok(t, s, "evaluate", "new-checkout", "banner", "--env", "development"),
		"FLAG VALUE VARIANT REASON ERROR",
		"new-checkout true true TARGETING_MATCH -",
		`banner {"text":"hi"} - ERROR FLAG_NOT_FOUND`,
	)
	sameJSON(t, decodeJSON(t, ok(t, s, "evaluate", "--env", "development", "-o", "json")), evalResult)
	api.only(
		recorded{Method: "POST", Path: evalPath, Body: decodeJSON(t, `{"context":{"key":"cli"},"flags":["new-checkout","banner"]}`)},
		recorded{Method: "POST", Path: evalPath, Body: decodeJSON(t, `{"context":{"key":"cli"}}`)},
	)
}

func TestEvaluateContextMerging(t *testing.T) {
	api := newFakeAPI(t, map[string]reply{"POST " + evalPath: {body: evalResult}})
	s := api.session("shop")
	yamlCtx := writeFile(t, "ctx.yaml", "key: u1\ncountry: DE\nplan: {tier: gold}\n")
	jsonCtx := writeFile(t, "ctx.json", `{"country":"FR","age":30}`)

	ok(t, s, "evaluate", "--env", "development", "--context", yamlCtx,
		"-c", "beta=true", "-c", "country=US", "-c", "n=42", "-c", `tags=["a"]`, "-c", "name=Mona Lisa", "-c", "eq=a=b")
	ok(t, s, "evaluate", "--env", "development", "--context", jsonCtx)
	api.only(
		recorded{Method: "POST", Path: evalPath, Body: decodeJSON(t,
			`{"context":{"key":"u1","country":"US","plan":{"tier":"gold"},"beta":true,"n":42,"tags":["a"],"name":"Mona Lisa","eq":"a=b"}}`)},
		recorded{Method: "POST", Path: evalPath, Body: decodeJSON(t, `{"context":{"key":"cli","country":"FR","age":30}}`)},
	)
}

func TestEvaluateUsageErrors(t *testing.T) {
	api := newFakeAPI(t, nil)
	s := api.session("shop")
	notObject := writeFile(t, "list.json", `[1,2]`)
	for _, args := range [][]string{
		{"evaluate"},
		{"evaluate", "--env", "development", "-c", "novalue"},
		{"evaluate", "--env", "development", "-c", "=x"},
		{"evaluate", "--env", "development", "--context", notObject},
		{"evaluate", "--env", "development", "--context", "/does/not/exist.json"},
	} {
		if code, _, _ := s.run(args...); code != ExitUsage {
			t.Fatalf("%v: code = %d", args, code)
		}
	}
	api.only()
}
