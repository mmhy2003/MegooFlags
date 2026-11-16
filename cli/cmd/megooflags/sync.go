package main

import (
	"fmt"
	"io"
	"maps"
	"slices"

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

func newDiffCmd(a *app) *cobra.Command {
	var dir string
	var opts flagsync.Options
	cmd := &cobra.Command{
		Use:   "diff --dir DIR [--env e ...] [--prune]",
		Short: "Show what apply would change on the server",
		Long: "Compare the flags-as-code files in DIR with the server and print the changes\n" +
			"apply would make, in the order it makes them. Exits 3 when there are changes\n" +
			"and 0 when there are none. A flag's config is compared only in the environments\n" +
			"its file lists, and with --env only in those. With --prune, live flags and\n" +
			"segments missing from the files are archived and deleted.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			files, err := flagfiles.Load(dir)
			if err != nil {
				return err
			}
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			server, err := flagsync.Snapshot(cmd.Context(), c, v.Project, opts.Envs)
			if err != nil {
				return err
			}
			if err := validate(cmd.ErrOrStderr(), files, server); err != nil {
				return err
			}
			plan := flagsync.Diff(files, server, opts)
			out := cmd.OutOrStdout()
			if len(plan.Changes) == 0 {
				_, err := fmt.Fprintln(out, "No differences.")
				return err
			}
			plan.Print(out)
			return quietExit(ExitDrift)
		},
	}
	f := cmd.Flags()
	f.StringVar(&dir, "dir", "", "flags-as-code directory")
	f.StringArrayVar(&opts.Envs, "env", nil, "environment to compare flag configs in (repeatable; default all)")
	f.BoolVar(&opts.Prune, "prune", false, "include archiving flags and deleting segments missing from the files")
	_ = cmd.MarkFlagRequired("dir")
	return cmd
}

// validate checks files, letting them refer to the server's segments, and
// writes every problem to w.
func validate(w io.Writer, files, server *flagfiles.Dir) error {
	var serverOnly []string
	for _, key := range slices.Sorted(maps.Keys(server.Segments)) {
		if _, ok := files.Segments[key]; !ok {
			serverOnly = append(serverOnly, key)
		}
	}
	errs := flagfiles.Validate(files, serverOnly)
	if len(errs) == 0 {
		return nil
	}
	for _, err := range errs {
		fmt.Fprintln(w, err)
	}
	return &exitError{code: ExitError, err: fmt.Errorf("the files have %d problems", len(errs))}
}
