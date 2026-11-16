package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"
)

func newRulesetCmd(a *app) *cobra.Command {
	cmd := &cobra.Command{Use: "ruleset", Short: "Download environment rulesets for local development"}
	cmd.AddCommand(newRulesetPullCmd(a))
	return cmd
}

func newRulesetPullCmd(a *app) *cobra.Command {
	var env, file string
	cmd := &cobra.Command{
		Use:   "pull --env e -o FILE",
		Short: "Write an environment's ruleset to a bootstrap file for the SDKs' offline mode",
		Long: "Write an environment's ruleset, byte for byte what the SDKs receive, to FILE, or to\n" +
			"stdout with -o -. Here -o is the output file: it replaces the global output format flag.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			body, _, _, err := c.Ruleset(cmd.Context(), v.Project, env, "")
			if err != nil {
				return err
			}
			if file == "-" {
				_, err = cmd.OutOrStdout().Write(body)
				return err
			}
			var ruleset struct {
				Flags map[string]json.RawMessage `json:"flags"`
			}
			if err := json.Unmarshal(body, &ruleset); err != nil {
				return fmt.Errorf("the ruleset is not valid JSON: %w", err)
			}
			if err := writeFileAtomic(file, body); err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "wrote %s (%d flags)\n", file, len(ruleset.Flags))
			return err
		},
	}
	f := cmd.Flags()
	f.StringVar(&env, "env", "", "environment key")
	f.StringVarP(&file, "output", "o", "", "file to write, or - for stdout (replaces the global -o)")
	_ = cmd.MarkFlagRequired("env")
	_ = cmd.MarkFlagRequired("output")
	return cmd
}

// writeFileAtomic writes data to a temporary file next to path and renames
// it over path, so readers never see a partial file.
func writeFileAtomic(path string, data []byte) error {
	tmp, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0o644); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}
