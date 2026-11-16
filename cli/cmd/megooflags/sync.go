package main

import (
	"fmt"

	"github.com/spf13/cobra"

	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
	flagsync "github.com/mmhy2003/MegooFlags/cli/internal/sync"
)

func newExportCmd(a *app) *cobra.Command {
	var dir string
	var envs []string
	cmd := &cobra.Command{
		Use:   "export --dir DIR [--env e ...]",
		Short: "Write the project's flags, segments and environments as flags-as-code files",
		Long: "Write the project's server state to DIR as flags-as-code files: megooflags.yaml,\n" +
			"segments/<key>.yaml and flags/<key>.yaml, live and archived flags included.\n" +
			"YAML files in segments/ and flags/ that the project no longer has are removed.\n" +
			"With --env, flags carry the config of those environments only.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			d, err := flagsync.Snapshot(cmd.Context(), c, v.Project, envs)
			if err != nil {
				return err
			}
			if err := flagfiles.Write(dir, d); err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "exported %d flags and %d segments to %s\n",
				len(d.Flags), len(d.Segments), dir)
			return err
		},
	}
	f := cmd.Flags()
	f.StringVar(&dir, "dir", "", "flags-as-code directory")
	f.StringArrayVar(&envs, "env", nil, "environment to export flag configs for (repeatable; default all)")
	_ = cmd.MarkFlagRequired("dir")
	return cmd
}
