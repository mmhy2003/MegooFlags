package main

import (
	"bytes"
	"errors"
	"fmt"
	"strings"
	"testing"
	"text/tabwriter"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/buildinfo"
	"github.com/mmhy2003/MegooFlags/cli/internal/config"
)

type sample struct {
	Key     string  `json:"key"`
	Enabled bool    `json:"enabled"`
	Value   string  `json:"value"`
	Owner   *string `json:"owner"`
	Count   int     `json:"count"`
}

func sampleTable(tw *tabwriter.Writer) {
	fmt.Fprintln(tw, "KEY\tCOUNT")
	fmt.Fprintln(tw, "checkout\t3")
}

func TestRenderJSON(t *testing.T) {
	var b bytes.Buffer
	if err := render(&b, "json", sample{Key: "checkout", Value: "true", Count: 3}, sampleTable); err != nil {
		t.Fatal(err)
	}
	want := "{\n  \"key\": \"checkout\",\n  \"enabled\": false,\n  \"value\": \"true\",\n  \"owner\": null,\n  \"count\": 3\n}\n"
	if b.String() != want {
		t.Fatalf("got %q, want %q", b.String(), want)
	}
}

func TestRenderYAMLUsesJSONNames(t *testing.T) {
	var b bytes.Buffer
	if err := render(&b, "yaml", sample{Key: "checkout", Value: "true", Count: 3}, sampleTable); err != nil {
		t.Fatal(err)
	}
	want := "key: checkout\nenabled: false\nvalue: \"true\"\nowner: null\ncount: 3\n"
	if b.String() != want {
		t.Fatalf("got %q, want %q", b.String(), want)
	}
}

func TestRenderTable(t *testing.T) {
	var b bytes.Buffer
	if err := render(&b, "table", sample{}, sampleTable); err != nil {
		t.Fatal(err)
	}
	want := "KEY       COUNT\ncheckout  3\n"
	if b.String() != want {
		t.Fatalf("got %q, want %q", b.String(), want)
	}
}

func TestRenderUnknownFormatIsUsageError(t *testing.T) {
	err := render(&bytes.Buffer{}, "xml", sample{}, sampleTable)
	if exitCode(err) != ExitUsage {
		t.Fatalf("err = %v, code = %d", err, exitCode(err))
	}
}

func TestPrintAPIError(t *testing.T) {
	var b bytes.Buffer
	err := fmt.Errorf("get flag: %w", &client.Error{Status: 403, Code: "forbidden", Message: "missing permission 'flag:write'"})
	if !printAPIError(&b, err) {
		t.Fatal("not printed")
	}
	if want := "error: forbidden: missing permission 'flag:write'\n"; b.String() != want {
		t.Fatalf("got %q, want %q", b.String(), want)
	}
	if printAPIError(&bytes.Buffer{}, errors.New("plain")) {
		t.Fatal("printed a non-API error")
	}
}

func TestAPIErrorExitCodes(t *testing.T) {
	if got := exitCode(&client.Error{Status: 409, Code: "approval_required"}); got != ExitNeedsApproval {
		t.Fatalf("approval_required: got %d", got)
	}
	if got := exitCode(&client.Error{Status: 404, Code: "not_found"}); got != ExitError {
		t.Fatalf("not_found: got %d", got)
	}
}

func TestNewClient(t *testing.T) {
	_, err := newClient(config.Values{URL: "https://flags.example"})
	if err == nil || !strings.Contains(err.Error(), "no API token: run `megooflags login` or set MEGOOFLAGS_API_TOKEN") {
		t.Fatalf("no token: err = %v", err)
	}
	if exitCode(err) != ExitError {
		t.Fatalf("no token: code = %d", exitCode(err))
	}
	if _, err := newClient(config.Values{Token: "tok"}); err == nil || !strings.HasPrefix(err.Error(), "no URL:") {
		t.Fatalf("no URL: err = %v", err)
	}
	c, err := newClient(config.Values{URL: "https://flags.example/", Token: "tok"})
	if err != nil {
		t.Fatal(err)
	}
	if want := "megooflags-cli/" + buildinfo.Version; c.UserAgent != want {
		t.Fatalf("User-Agent = %q, want %q", c.UserAgent, want)
	}
	if c.BaseURL != "https://flags.example" || c.Token != "tok" {
		t.Fatalf("client = %+v", c)
	}
}
