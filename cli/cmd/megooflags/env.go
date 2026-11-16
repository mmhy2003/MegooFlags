package main

import (
	"fmt"
	"text/tabwriter"

	"github.com/spf13/cobra"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

func newEnvCmd(a *app) *cobra.Command {
	cmd := &cobra.Command{Use: "env", Short: "Manage the environments of a project"}
	cmd.AddCommand(newEnvListCmd(a), newEnvGetCmd(a), newEnvCreateCmd(a),
		newEnvUpdateCmd(a), newEnvDeleteCmd(a))
	return cmd
}

func envTable(tw *tabwriter.Writer, envs ...client.Environment) {
	fmt.Fprintln(tw, "KEY\tNAME\tAPPROVALS")
	for _, e := range envs {
		fmt.Fprintf(tw, "%s\t%s\t%d\n", e.Key, e.Name, e.ApprovalsRequired)
	}
}

func renderEnv(cmd *cobra.Command, format string, e *client.Environment) error {
	return render(cmd.OutOrStdout(), format, e, func(tw *tabwriter.Writer) { envTable(tw, *e) })
}

func newEnvListCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List environments in sort order",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			envs, err := c.ListEnvironments(cmd.Context(), v.Project)
			if err != nil {
				return err
			}
			if envs == nil {
				envs = []client.Environment{}
			}
			return render(cmd.OutOrStdout(), v.Output, envs, func(tw *tabwriter.Writer) { envTable(tw, envs...) })
		},
	}
}

func newEnvGetCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "get <key>",
		Short: "Show an environment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			e, err := c.GetEnvironment(cmd.Context(), v.Project, args[0])
			if err != nil {
				return err
			}
			return renderEnv(cmd, v.Output, e)
		},
	}
}

func newEnvCreateCmd(a *app) *cobra.Command {
	var name, description string
	var sortOrder int
	cmd := &cobra.Command{
		Use:   "create <key> --name N",
		Short: "Create an environment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			in := client.EnvironmentCreate{
				Key:         args[0],
				Name:        name,
				Description: description,
				SortOrder:   changedInt(cmd, "sort-order", sortOrder),
			}
			e, err := c.CreateEnvironment(cmd.Context(), v.Project, in)
			if err != nil {
				return err
			}
			return renderEnv(cmd, v.Output, e)
		},
	}
	f := cmd.Flags()
	f.StringVar(&name, "name", "", "environment name")
	f.StringVar(&description, "description", "", "environment description")
	f.IntVar(&sortOrder, "sort-order", 0, "position in the environment list")
	_ = cmd.MarkFlagRequired("name")
	return cmd
}

func newEnvUpdateCmd(a *app) *cobra.Command {
	var name, description string
	var sortOrder, approvals int
	cmd := &cobra.Command{
		Use:   "update <key>",
		Short: "Change an environment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			in := client.EnvironmentUpdate{
				Name:              changedString(cmd, "name", name),
				Description:       changedString(cmd, "description", description),
				SortOrder:         changedInt(cmd, "sort-order", sortOrder),
				ApprovalsRequired: changedInt(cmd, "approvals-required", approvals),
			}
			if in == (client.EnvironmentUpdate{}) {
				return errNothingToUpdate
			}
			e, err := c.UpdateEnvironment(cmd.Context(), v.Project, args[0], in)
			if err != nil {
				return err
			}
			return renderEnv(cmd, v.Output, e)
		},
	}
	f := cmd.Flags()
	f.StringVar(&name, "name", "", "environment name")
	f.StringVar(&description, "description", "", "environment description")
	f.IntVar(&sortOrder, "sort-order", 0, "position in the environment list")
	f.IntVar(&approvals, "approvals-required", 0, "approvals a change request needs")
	return cmd
}

func newEnvDeleteCmd(a *app) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <key> --yes",
		Short: "Delete an environment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			if err := requireYes(yes); err != nil {
				return err
			}
			if err := c.DeleteEnvironment(cmd.Context(), v.Project, args[0]); err != nil {
				return err
			}
			_, err = fmt.Fprintln(cmd.OutOrStdout(), "Deleted environment", args[0])
			return err
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "confirm the deletion")
	return cmd
}
