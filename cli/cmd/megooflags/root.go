package main

import (
	"context"
	"errors"
	"fmt"
	"io"

	"github.com/spf13/cobra"

	"github.com/mmhy2003/MegooFlags/cli/client"
	"github.com/mmhy2003/MegooFlags/cli/internal/buildinfo"
	"github.com/mmhy2003/MegooFlags/cli/internal/config"
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

// app is what commands share: the process IO and the global flags.
type app struct {
	io IO
	g  globalFlags
}

// configPath is --config, else the default path.
func (a *app) configPath() (string, error) {
	if a.g.config != "" {
		return a.g.config, nil
	}
	return config.DefaultPath(a.io.Getenv)
}

// loadFile reads the config file and returns it with its path.
func (a *app) loadFile() (config.File, string, error) {
	path, err := a.configPath()
	if err != nil {
		return config.File{}, "", err
	}
	f, err := config.Load(path)
	return f, path, err
}

// values resolves the settings from the flags, the environment and the file.
func (a *app) values() (config.Values, error) {
	f, _, err := a.loadFile()
	if err != nil {
		return config.Values{}, err
	}
	flags := config.Values{URL: a.g.url, Project: a.g.project, Output: a.g.output}
	return config.Resolve(flags, a.io.Getenv, f), nil
}

// client returns an API client and the settings it was made from.
func (a *app) client() (*client.Client, config.Values, error) {
	v, err := a.values()
	if err != nil {
		return nil, v, err
	}
	c, err := newClient(v)
	return c, v, err
}

// run executes the CLI with args (without the program name) and returns the
// exit code.
func run(ctx context.Context, args []string, io IO) int {
	a := &app{io: io}
	g := &a.g
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

	root.AddCommand(newVersionCmd(), newCompletionCmd(), newLoginCmd(a), newLogoutCmd(a), newWhoamiCmd(a),
		newProjectCmd(a), newEnvCmd(a), newSegmentCmd(a), newFlagCmd(a),
		newPromoteCmd(a), newEvaluateCmd(a), newRulesetCmd(a), newExportCmd(a))

	err := root.ExecuteContext(ctx)
	if err == nil {
		return ExitOK
	}
	if !started {
		err = usageError(err)
	}
	if !printAPIError(io.Err, err) {
		fmt.Fprintln(io.Err, "Error:", err)
	}
	if client.IsCode(err, "approval_required") {
		fmt.Fprintln(io.Err, "hint: open a change request in the UI, or use `megooflags apply --change-requests`")
	}
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

// projectClient is client for commands that work inside one project: the
// project comes from -p, MEGOOFLAGS_PROJECT or the config file, and its
// absence is a usage error.
func (a *app) projectClient() (*client.Client, config.Values, error) {
	v, err := a.values()
	if err != nil {
		return nil, v, err
	}
	if v.Project == "" {
		return nil, v, usageError(errors.New("no project: pass -p or set MEGOOFLAGS_PROJECT"))
	}
	c, err := newClient(v)
	return c, v, err
}

// requireYes refuses a delete that was not confirmed with --yes.
func requireYes(yes bool) error {
	if !yes {
		return usageError(errors.New("refusing to delete without --yes"))
	}
	return nil
}

// errNothingToUpdate is returned by update commands given no changes.
var errNothingToUpdate = usageError(errors.New("nothing to update: pass at least one field flag"))

// changedString returns a pointer to the value of flag name when it was set.
func changedString(cmd *cobra.Command, name, value string) *string {
	if !cmd.Flags().Changed(name) {
		return nil
	}
	return &value
}

// changedInt returns a pointer to the value of flag name when it was set.
func changedInt(cmd *cobra.Command, name string, value int) *int {
	if !cmd.Flags().Changed(name) {
		return nil
	}
	return &value
}
