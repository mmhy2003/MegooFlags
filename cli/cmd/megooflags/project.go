package main

import (
	"fmt"
	"strings"
	"text/tabwriter"

	"github.com/spf13/cobra"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

func newProjectCmd(a *app) *cobra.Command {
	cmd := &cobra.Command{Use: "project", Short: "Manage projects"}
	cmd.AddCommand(newProjectListCmd(a), newProjectGetCmd(a), newProjectCreateCmd(a),
		newProjectUpdateCmd(a), newProjectDeleteCmd(a))
	return cmd
}

const projectHeader = "KEY\tNAME\tENVIRONMENTS"

// renderProject prints one project; its environments are counted from the
// project itself.
func renderProject(cmd *cobra.Command, format string, p *client.Project) error {
	return render(cmd.OutOrStdout(), format, p, func(tw *tabwriter.Writer) {
		fmt.Fprintln(tw, projectHeader)
		fmt.Fprintf(tw, "%s\t%s\t%d\n", p.Key, p.Name, len(p.Environments))
	})
}

func newProjectListCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List projects",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, v, err := a.client()
			if err != nil {
				return err
			}
			projects, err := c.ListProjects(cmd.Context())
			if err != nil {
				return err
			}
			if projects == nil {
				projects = []client.Project{}
			}
			// The list has no environments, so the table fetches them to
			// count them. json and yaml print the list as the API gives it.
			counts := make([]int, len(projects))
			if v.Output == "table" {
				for i, p := range projects {
					envs, err := c.ListEnvironments(cmd.Context(), p.Key)
					if err != nil {
						return err
					}
					counts[i] = len(envs)
				}
			}
			return render(cmd.OutOrStdout(), v.Output, projects, func(tw *tabwriter.Writer) {
				fmt.Fprintln(tw, projectHeader)
				for i, p := range projects {
					fmt.Fprintf(tw, "%s\t%s\t%d\n", p.Key, p.Name, counts[i])
				}
			})
		},
	}
}

func newProjectGetCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "get <key>",
		Short: "Show a project and its environments",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.client()
			if err != nil {
				return err
			}
			p, err := c.GetProject(cmd.Context(), args[0])
			if err != nil {
				return err
			}
			return renderProject(cmd, v.Output, p)
		},
	}
}

func newProjectCreateCmd(a *app) *cobra.Command {
	var name, description string
	var envs []string
	cmd := &cobra.Command{
		Use:   "create <key> --name N",
		Short: "Create a project",
		Long: "Create a project. Each --env key:Name adds an environment; without any,\n" +
			"the API creates its default environments.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			in := client.ProjectCreate{Key: args[0], Name: name, Description: description}
			for _, e := range envs {
				key, envName, found := strings.Cut(e, ":")
				if !found || key == "" || envName == "" {
					return usageError(fmt.Errorf("invalid --env %q: use key:Name", e))
				}
				in.Environments = append(in.Environments, client.EnvironmentCreate{Key: key, Name: envName})
			}
			c, v, err := a.client()
			if err != nil {
				return err
			}
			p, err := c.CreateProject(cmd.Context(), in)
			if err != nil {
				return err
			}
			return renderProject(cmd, v.Output, p)
		},
	}
	f := cmd.Flags()
	f.StringVar(&name, "name", "", "project name")
	f.StringVar(&description, "description", "", "project description")
	f.StringArrayVar(&envs, "env", nil, "environment as key:Name (repeatable)")
	_ = cmd.MarkFlagRequired("name")
	return cmd
}

func newProjectUpdateCmd(a *app) *cobra.Command {
	var name, description string
	var staleAfterDays int
	cmd := &cobra.Command{
		Use:   "update <key>",
		Short: "Change a project",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			in := client.ProjectUpdate{
				Name:           changedString(cmd, "name", name),
				Description:    changedString(cmd, "description", description),
				StaleAfterDays: changedInt(cmd, "stale-after-days", staleAfterDays),
			}
			if in == (client.ProjectUpdate{}) {
				return errNothingToUpdate
			}
			c, v, err := a.client()
			if err != nil {
				return err
			}
			p, err := c.UpdateProject(cmd.Context(), args[0], in)
			if err != nil {
				return err
			}
			return renderProject(cmd, v.Output, p)
		},
	}
	f := cmd.Flags()
	f.StringVar(&name, "name", "", "project name")
	f.StringVar(&description, "description", "", "project description")
	f.IntVar(&staleAfterDays, "stale-after-days", 0, "days without evaluations before a flag is stale")
	return cmd
}

func newProjectDeleteCmd(a *app) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <key> --yes",
		Short: "Delete a project and everything in it",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := requireYes(yes); err != nil {
				return err
			}
			c, _, err := a.client()
			if err != nil {
				return err
			}
			if err := c.DeleteProject(cmd.Context(), args[0]); err != nil {
				return err
			}
			_, err = fmt.Fprintln(cmd.OutOrStdout(), "Deleted project", args[0])
			return err
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "confirm the deletion")
	return cmd
}
