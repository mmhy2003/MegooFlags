package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"text/tabwriter"

	"github.com/spf13/cobra"
	"go.yaml.in/yaml/v3"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

func newEvaluateCmd(a *app) *cobra.Command {
	var env, file string
	var pairs []string
	cmd := &cobra.Command{
		Use:   "evaluate [flag...] --env e [--context ctx.json] [-c key=value ...]",
		Short: "Evaluate flags for a context, or every flag when none is given",
		Long: "Evaluate flags in an environment for a context. The context is the JSON or YAML\n" +
			"object in --context, with each -c key=value on top. A value that is valid JSON is\n" +
			"used as JSON (-c beta=true gives a bool), anything else as a string. The context\n" +
			"key defaults to \"cli\".",
		RunE: func(cmd *cobra.Command, args []string) error {
			evalCtx, err := buildContext(file, pairs)
			if err != nil {
				return usageError(err)
			}
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			out, err := c.Evaluate(cmd.Context(), v.Project, env, client.EvaluateIn{Context: evalCtx, Flags: args})
			if err != nil {
				return err
			}
			return render(cmd.OutOrStdout(), v.Output, out, func(tw *tabwriter.Writer) { evaluationTable(tw, out.Results) })
		},
	}
	f := cmd.Flags()
	f.StringVar(&env, "env", "", "environment key")
	f.StringVar(&file, "context", "", "JSON or YAML file with the context object")
	f.StringArrayVarP(&pairs, "attribute", "c", nil, "context attribute key=value (repeatable)")
	_ = cmd.MarkFlagRequired("env")
	return cmd
}

// buildContext reads the context file, if any, and sets each key=value pair
// on top of it.
func buildContext(file string, pairs []string) (map[string]any, error) {
	evalCtx := map[string]any{}
	if file != "" {
		raw, err := os.ReadFile(file)
		if err != nil {
			return nil, err
		}
		// YAML is a superset of JSON, so one decoder reads both.
		if err := yaml.NewDecoder(bytes.NewReader(raw)).Decode(&evalCtx); err != nil && !errors.Is(err, io.EOF) {
			return nil, fmt.Errorf("%s: the context must be an object: %w", file, err)
		}
	}
	for _, pair := range pairs {
		k, raw, found := strings.Cut(pair, "=")
		if !found || k == "" {
			return nil, fmt.Errorf("context attribute %q: want key=value", pair)
		}
		var value any
		if err := json.Unmarshal([]byte(raw), &value); err != nil {
			value = raw
		}
		evalCtx[k] = value
	}
	if _, set := evalCtx["key"]; !set {
		evalCtx["key"] = "cli"
	}
	return evalCtx, nil
}

func evaluationTable(tw *tabwriter.Writer, results []client.EvaluationResult) {
	fmt.Fprintln(tw, "FLAG\tVALUE\tVARIANT\tREASON\tERROR")
	for _, r := range results {
		value, _ := json.Marshal(r.Value)
		variant, errCode := "", ""
		if r.Variant != nil {
			variant = *r.Variant
		}
		if r.ErrorCode != nil {
			errCode = *r.ErrorCode
		}
		fmt.Fprintf(tw, "%s\t%s\t%s\t%s\t%s\n", r.Key, value, dashIfEmpty(variant), r.Reason, dashIfEmpty(errCode))
	}
}
