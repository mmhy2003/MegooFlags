package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"github.com/spf13/cobra"

	"github.com/mmhy2003/MegooFlags/cli/internal/flagfiles"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers/flagsmith"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers/launchdarkly"
	"github.com/mmhy2003/MegooFlags/cli/internal/importers/unleash"
)

// importTool is one tool import reads from.
type importTool struct {
	label, tokenEnv string
	// needsURL is true when the tool has no cloud host to default to.
	needsURL bool
	// sourceProject returns the default --source-project for project, ""
	// when the API needs one given.
	sourceProject func(project string) string
	// load fetches the export and converts it; the export is returned so
	// --save can write it.
	load func(context.Context, importers.Source, importers.Options) (any, *flagfiles.Dir, importers.Report, error)
}

// loader is a tool's Fetch followed by its Convert.
func loader[E any](
	fetch func(context.Context, importers.Source) (*E, error),
	convert func(*E, importers.Options) (*flagfiles.Dir, importers.Report),
) func(context.Context, importers.Source, importers.Options) (any, *flagfiles.Dir, importers.Report, error) {
	return func(ctx context.Context, src importers.Source, opts importers.Options) (any, *flagfiles.Dir, importers.Report, error) {
		e, err := fetch(ctx, src)
		if err != nil {
			return nil, nil, importers.Report{}, err
		}
		d, r := convert(e, opts)
		return e, d, r, nil
	}
}

var importTools = map[string]importTool{
	"unleash": {
		label: "Unleash", tokenEnv: "UNLEASH_API_TOKEN", needsURL: true,
		sourceProject: func(string) string { return "default" },
		load:          loader(unleash.Fetch, unleash.Convert),
	},
	"flagsmith": {
		label: "Flagsmith", tokenEnv: "FLAGSMITH_API_KEY",
		sourceProject: func(string) string { return "" },
		load:          loader(flagsmith.Fetch, flagsmith.Convert),
	},
	"launchdarkly": {
		label: "LaunchDarkly", tokenEnv: "LAUNCHDARKLY_API_TOKEN",
		sourceProject: func(project string) string { return project },
		load:          loader(launchdarkly.Fetch, launchdarkly.Convert),
	},
}

func newImportCmd(a *app) *cobra.Command {
	var url, file, out, tokenEnv, sourceProject, save string
	var envMap []string
	var strict bool
	cmd := &cobra.Command{
		Use:   "import unleash|flagsmith|launchdarkly (--url U | --file F) --project P --out DIR",
		Short: "Convert an Unleash, Flagsmith or LaunchDarkly project to flags-as-code files",
		Long: "Read a project from the tool's API, or from a file saved with --save, and write it\n" +
			"to DIR as flags-as-code files for project P, with import-report.md listing what\n" +
			"was not translated as it was. Nothing is sent to MegooFlags: review the files,\n" +
			"then run apply. The tool's token is read from the environment variable named by\n" +
			"--token-env (default UNLEASH_API_TOKEN, FLAGSMITH_API_KEY or LAUNCHDARKLY_API_TOKEN).\n" +
			"--url defaults to the Flagsmith and LaunchDarkly cloud; Unleash needs it.\n" +
			"--source-project is the Unleash project id (default \"default\"), the Flagsmith\n" +
			"project id (required with the API) or the LaunchDarkly project key (default P).\n" +
			"With --strict, exits 1 when the report is not empty.",
		ValidArgs: []string{"unleash", "flagsmith", "launchdarkly"},
		Args:      cobra.MatchAll(cobra.ExactArgs(1), cobra.OnlyValidArgs),
		RunE: func(cmd *cobra.Command, args []string) error {
			tool := importTools[args[0]]
			project := a.g.project
			if project == "" {
				return usageError(errors.New("no project: pass --project"))
			}
			m, err := importers.ParseEnvMap(envMap)
			if err != nil {
				return usageError(err)
			}
			src := importers.Source{URL: url, File: file, Project: sourceProject}
			if file == "" {
				if tool.needsURL && url == "" {
					return usageError(fmt.Errorf("pass --url or --file for %s", tool.label))
				}
				if src.Project == "" {
					src.Project = tool.sourceProject(project)
				}
				if src.Project == "" {
					return usageError(fmt.Errorf("pass --source-project with the %s project id", tool.label))
				}
				if tokenEnv == "" {
					tokenEnv = tool.tokenEnv
				}
				if src.Token = a.io.Getenv(tokenEnv); src.Token == "" {
					return fmt.Errorf("set %s to the %s API token", tokenEnv, tool.label)
				}
			}

			export, d, report, err := tool.load(cmd.Context(), src, importers.Options{Project: project, EnvMap: m})
			if err != nil {
				return err
			}
			if save != "" {
				b, err := json.MarshalIndent(export, "", "  ")
				if err != nil {
					return err
				}
				if err := os.WriteFile(save, append(b, '\n'), 0o644); err != nil {
					return err
				}
			}
			d.Project.Project.Key = project
			if d.Project.Project.Name == "" {
				d.Project.Project.Name = project
			}
			if err := flagfiles.Write(out, d); err != nil {
				return err
			}
			if err := os.WriteFile(filepath.Join(out, "import-report.md"), []byte(report.Markdown()), 0o644); err != nil {
				return err
			}
			w := cmd.OutOrStdout()
			fmt.Fprintf(w, "%d flags, %d segments; %d items in import-report.md\n",
				len(d.Flags), len(d.Segments), len(report.Lines))
			if _, err := fmt.Fprintln(w, "Review the files, then run: megooflags apply --dir", out); err != nil {
				return err
			}
			if strict && !report.Empty() {
				return fmt.Errorf("--strict: import-report.md has %d items", len(report.Lines))
			}
			return nil
		},
	}
	f := cmd.Flags()
	// --url shadows the global MegooFlags URL: import never calls MegooFlags.
	f.StringVar(&url, "url", "", "the tool's API URL")
	f.StringVar(&file, "file", "", "read a file saved with --save instead of the API")
	f.StringVar(&out, "out", "", "flags-as-code directory to write")
	f.StringVar(&tokenEnv, "token-env", "", "environment variable holding the tool's API token")
	f.StringVar(&sourceProject, "source-project", "", "the tool's project to read")
	f.StringArrayVar(&envMap, "env-map", nil, "rename an environment, as <source>=<target> (repeatable)")
	f.BoolVar(&strict, "strict", false, "exit 1 when the report is not empty")
	f.StringVar(&save, "save", "", "also write what was read to this file, for --file")
	cmd.MarkFlagsMutuallyExclusive("url", "file")
	_ = cmd.MarkFlagRequired("out")
	return cmd
}
