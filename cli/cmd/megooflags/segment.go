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

func newSegmentCmd(a *app) *cobra.Command {
	cmd := &cobra.Command{Use: "segment", Short: "Manage the segments of a project"}
	cmd.AddCommand(newSegmentListCmd(a), newSegmentGetCmd(a), newSegmentCreateCmd(a),
		newSegmentUpdateCmd(a), newSegmentDeleteCmd(a))
	return cmd
}

func segmentTable(tw *tabwriter.Writer, segments ...client.Segment) {
	fmt.Fprintln(tw, "KEY\tNAME\tUPDATED")
	for _, s := range segments {
		fmt.Fprintf(tw, "%s\t%s\t%s\n", s.Key, s.Name, s.UpdatedAt)
	}
}

// renderSegment prints a segment's details and, in a table, its config.
func renderSegment(cmd *cobra.Command, format string, s *client.Segment) error {
	return render(cmd.OutOrStdout(), format, s, func(tw *tabwriter.Writer) {
		segmentTable(tw, *s)
		fmt.Fprintln(tw)
		fmt.Fprintf(tw, "INCLUDED\t%s\n", joinOrDash(s.Included))
		fmt.Fprintf(tw, "EXCLUDED\t%s\n", joinOrDash(s.Excluded))
		fmt.Fprintf(tw, "RULES\t%d\n", len(s.Rules))
		for i, r := range s.Rules {
			fmt.Fprintf(tw, "  %s\n", describeRule(i, r))
		}
	})
}

func joinOrDash(values []string) string {
	if len(values) == 0 {
		return "-"
	}
	return strings.Join(values, ", ")
}

// describeRule renders a rule on one line, e.g.
// `staff: email ends_with ["@example.com"] and country not in ["FR"]`.
func describeRule(i int, r client.SegmentRule) string {
	name := r.Description
	if name == "" {
		name = fmt.Sprintf("rule %d", i+1)
	}
	clauses := make([]string, len(r.Clauses))
	for j, c := range r.Clauses {
		values, _ := json.Marshal(c.Values)
		op := c.Operator
		if c.Negate {
			op = "not " + op
		}
		clauses[j] = fmt.Sprintf("%s %s %s", c.Attribute, op, values)
	}
	return name + ": " + strings.Join(clauses, " and ")
}

func newSegmentListCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List segments",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			segments, err := c.ListSegments(cmd.Context(), v.Project)
			if err != nil {
				return err
			}
			if segments == nil {
				segments = []client.Segment{}
			}
			return render(cmd.OutOrStdout(), v.Output, segments, func(tw *tabwriter.Writer) { segmentTable(tw, segments...) })
		},
	}
}

func newSegmentGetCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "get <key>",
		Short: "Show a segment and its config",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			s, err := c.GetSegment(cmd.Context(), v.Project, args[0])
			if err != nil {
				return err
			}
			return renderSegment(cmd, v.Output, s)
		},
	}
}

func newSegmentCreateCmd(a *app) *cobra.Command {
	var name, description string
	cmd := &cobra.Command{
		Use:   "create <key> --name N",
		Short: "Create an empty segment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			in := client.SegmentCreate{Key: args[0], Name: name, Description: description}
			s, err := c.CreateSegment(cmd.Context(), v.Project, in)
			if err != nil {
				return err
			}
			return renderSegment(cmd, v.Output, s)
		},
	}
	f := cmd.Flags()
	f.StringVar(&name, "name", "", "segment name")
	f.StringVar(&description, "description", "", "segment description")
	_ = cmd.MarkFlagRequired("name")
	return cmd
}

func newSegmentUpdateCmd(a *app) *cobra.Command {
	var name, description, file string
	cmd := &cobra.Command{
		Use:   "update <key> [-f config.yaml]",
		Short: "Change a segment's details or replace its config",
		Long: "Change a segment's name and description, and with -f replace its whole config.\n" +
			"The file is YAML or JSON with included, excluded and rules; a missing list is cleared.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			details := client.SegmentUpdate{
				Name:        changedString(cmd, "name", name),
				Description: changedString(cmd, "description", description),
			}
			var cfg *client.SegmentConfig
			if file != "" {
				if cfg, err = readSegmentConfig(file); err != nil {
					return usageError(err)
				}
			}
			if details == (client.SegmentUpdate{}) && cfg == nil {
				return errNothingToUpdate
			}
			var s *client.Segment
			if details != (client.SegmentUpdate{}) {
				if s, err = c.UpdateSegment(cmd.Context(), v.Project, args[0], details); err != nil {
					return err
				}
			}
			if cfg != nil {
				if s, err = c.PutSegmentConfig(cmd.Context(), v.Project, args[0], *cfg); err != nil {
					return err
				}
			}
			return renderSegment(cmd, v.Output, s)
		},
	}
	f := cmd.Flags()
	f.StringVar(&name, "name", "", "segment name")
	f.StringVar(&description, "description", "", "segment description")
	f.StringVarP(&file, "file", "f", "", "YAML or JSON file with the segment config")
	return cmd
}

// readSegmentConfig reads a YAML or JSON segment config, rejecting unknown
// fields so that a typo does not clear part of the config.
func readSegmentConfig(path string) (*client.SegmentConfig, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	dec := yaml.NewDecoder(bytes.NewReader(raw))
	dec.KnownFields(true)
	var cfg client.SegmentConfig
	if err := dec.Decode(&cfg); err != nil {
		if errors.Is(err, io.EOF) {
			return nil, fmt.Errorf("%s: empty file", path)
		}
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return &cfg, nil
}

func newSegmentDeleteCmd(a *app) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <key> --yes",
		Short: "Delete a segment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			if err := requireYes(yes); err != nil {
				return err
			}
			if err := c.DeleteSegment(cmd.Context(), v.Project, args[0]); err != nil {
				return err
			}
			_, err = fmt.Fprintln(cmd.OutOrStdout(), "Deleted segment", args[0])
			return err
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "confirm the deletion")
	return cmd
}
