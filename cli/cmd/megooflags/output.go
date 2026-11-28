package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"text/tabwriter"

	"go.yaml.in/yaml/v3"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/buildinfo"
	"github.com/mmhy2003/MegooFlags/cli/internal/config"
)

// render writes v to w in format. json is indented with 2 spaces and yaml
// uses the same field names as json. table calls table with a tabwriter,
// which writes an upper-case header row and then one row per item.
func render(w io.Writer, format string, v any, table func(*tabwriter.Writer)) error {
	switch format {
	case "json":
		enc := json.NewEncoder(w)
		enc.SetIndent("", "  ")
		return enc.Encode(v)
	case "yaml":
		return renderYAML(w, v)
	case "table":
		tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
		table(tw)
		return tw.Flush()
	default:
		return usageError(fmt.Errorf("unknown output format %q: use table, json or yaml", format))
	}
}

// renderYAML goes through JSON so that the keys and their order match the
// json output: the client types only carry json tags.
func renderYAML(w io.Writer, v any) error {
	raw, err := json.Marshal(v)
	if err != nil {
		return err
	}
	var doc yaml.Node
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		return err
	}
	blockStyle(&doc)
	var b bytes.Buffer
	enc := yaml.NewEncoder(&b)
	enc.SetIndent(2)
	if err := enc.Encode(&doc); err != nil {
		return err
	}
	if err := enc.Close(); err != nil {
		return err
	}
	_, err = w.Write(b.Bytes())
	return err
}

// blockStyle drops the flow and quoting styles that parsing JSON gives, so
// the encoder picks plain block YAML and quotes only where it must.
func blockStyle(n *yaml.Node) {
	n.Style = 0
	for _, c := range n.Content {
		blockStyle(c)
	}
}

// printAPIError prints "error: <code>: <message>" when err is, or wraps, an
// API error, and reports whether it did.
func printAPIError(w io.Writer, err error) bool {
	var e *client.Error
	if !errors.As(err, &e) {
		return false
	}
	fmt.Fprintf(w, "error: %s: %s\n", e.Code, e.Message)
	return true
}

// newClient returns an API client for the resolved settings.
func newClient(v config.Values) (*client.Client, error) {
	if v.URL == "" {
		return nil, errors.New("no URL: run `megooflags login --url URL` or set MEGOOFLAGS_URL")
	}
	if v.Token == "" {
		return nil, errors.New("no API token: run `megooflags login` or set MEGOOFLAGS_API_TOKEN")
	}
	return client.New(v.URL, v.Token, client.WithUserAgent("megooflags-cli/"+buildinfo.Version)), nil
}
