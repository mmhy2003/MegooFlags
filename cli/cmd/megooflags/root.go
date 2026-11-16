package main

import (
	"context"
	"fmt"
	"io"

	"github.com/spf13/cobra"

	"github.com/mmhy2003/MegooFlags/cli/internal/buildinfo"
)

// IO is the process's environment, injected so tests can run commands.
type IO struct {
	In         io.Reader
	Out, Err   io.Writer
	Getenv     func(string) string
	IsTerminal bool
}

// globalFlags are the persistent flags every command shares. There is no
// --token flag on purpose: a token on the command line ends up in shell
// history and process listings. Use MEGOOFLAGS_API_TOKEN or the config file.
type globalFlags struct {
	url, project, output, config string
}

// run executes the CLI with args (without the program name) and returns the
// exit code.
func run(ctx context.Context, args []string, io IO) int {
	var g globalFlags
	// Errors returned before a command starts running (flag parsing,
	// argument validation, unknown commands) are usage errors.
	started := false

	root := &cobra.Command{
		Use:           "megooflags",
		Short:         "Manage MegooFlags from the command line",
		SilenceUsage:  true,
		SilenceErrors: true,
		PersistentPreRunE: func(cmd *cobra.Command, _ []string) error {
			if err := cmd.ValidateRequiredFlags(); err != nil {
				return usageError(err)
			}
			if err := cmd.ValidateFlagGroups(); err != nil {
				return usageError(err)
			}
			started = true
			return nil
		},
	}
	root.SetArgs(args)
	root.SetIn(io.In)
	root.SetOut(io.Out)
	root.SetErr(io.Err)
	root.SetFlagErrorFunc(func(_ *cobra.Command, err error) error { return usageError(err) })
	root.CompletionOptions.DisableDefaultCmd = true

	pf := root.PersistentFlags()
	pf.StringVar(&g.url, "url", "", "MegooFlags API URL (env MEGOOFLAGS_URL)")
	pf.StringVarP(&g.project, "project", "p", "", "project key (env MEGOOFLAGS_PROJECT)")
	pf.StringVarP(&g.output, "output", "o", "", "output format: table, json or yaml (env MEGOOFLAGS_OUTPUT)")
	pf.StringVar(&g.config, "config", "", "config file (default $XDG_CONFIG_HOME/megooflags/config.yaml)")

	root.AddCommand(newVersionCmd(), newCompletionCmd())

	err := root.ExecuteContext(ctx)
	if err == nil {
		return ExitOK
	}
	if !started {
		err = usageError(err)
	}
	fmt.Fprintln(io.Err, "Error:", err)
	return exitCode(err)
}

func newVersionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print the CLI version",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			_, err := fmt.Fprintln(cmd.OutOrStdout(), "megooflags", buildinfo.Version)
			return err
		},
	}
}

func newCompletionCmd() *cobra.Command {
	return &cobra.Command{
		Use:       "completion bash|zsh|fish|powershell",
		Short:     "Generate a shell completion script",
		ValidArgs: []string{"bash", "zsh", "fish", "powershell"},
		Args:      cobra.MatchAll(cobra.ExactArgs(1), cobra.OnlyValidArgs),
		RunE: func(cmd *cobra.Command, args []string) error {
			root, out := cmd.Root(), cmd.OutOrStdout()
			switch args[0] {
			case "bash":
				return root.GenBashCompletionV2(out, true)
			case "zsh":
				return root.GenZshCompletion(out)
			case "fish":
				return root.GenFishCompletion(out, true)
			default:
				return root.GenPowerShellCompletionWithDesc(out)
			}
		},
	}
}
