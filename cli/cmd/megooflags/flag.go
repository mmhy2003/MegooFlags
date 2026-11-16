package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"text/tabwriter"

	"github.com/spf13/cobra"
	"go.yaml.in/yaml/v3"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

func newFlagCmd(a *app) *cobra.Command {
	cmd := &cobra.Command{Use: "flag", Short: "Manage the flags of a project"}
	cmd.AddCommand(newFlagListCmd(a), newFlagGetCmd(a), newFlagCreateCmd(a), newFlagUpdateCmd(a),
		newFlagArchiveCmd(a, true), newFlagArchiveCmd(a, false), newFlagDeleteCmd(a),
		newFlagVariationCmd(a), newFlagConfigCmd(a),
		newFlagToggleCmd(a, true), newFlagToggleCmd(a, false), newFlagKillCmd(a))
	return cmd
}

func onOff(enabled bool) string {
	if enabled {
		return "on"
	}
	return "off"
}

func dashIfEmpty(s string) string {
	if s == "" {
		return "-"
	}
	return s
}

// flagTable writes KEY, TYPE, NAME and one on/off column per environment,
// in the order the environments first appear.
func flagTable(tw *tabwriter.Writer, flags ...client.FlagSummary) {
	var envs []string
	seen := map[string]bool{}
	for _, f := range flags {
		for _, s := range f.Environments {
			if !seen[s.Environment] {
				seen[s.Environment] = true
				envs = append(envs, s.Environment)
			}
		}
	}
	fmt.Fprintln(tw, strings.Join(append([]string{"KEY", "TYPE", "NAME"}, envs...), "\t"))
	for _, f := range flags {
		state := map[string]string{}
		for _, s := range f.Environments {
			state[s.Environment] = onOff(s.Enabled)
		}
		cells := []string{f.Key, f.Type, f.Name}
		for _, e := range envs {
			cells = append(cells, dashIfEmpty(state[e]))
		}
		fmt.Fprintln(tw, strings.Join(cells, "\t"))
	}
}

func variationTable(tw *tabwriter.Writer, variations ...client.Variation) {
	fmt.Fprintln(tw, "VARIATION\tNAME\tVALUE")
	for _, v := range variations {
		value, _ := json.Marshal(v.Value)
		fmt.Fprintf(tw, "%s\t%s\t%s\n", v.Key, dashIfEmpty(v.Name), value)
	}
}

// renderFlag prints a flag's details and variations and, when cfg is set,
// its config in one environment. In json and yaml the config is the
// "config" field.
func renderFlag(cmd *cobra.Command, format string, f *client.Flag, cfg *client.FlagConfig) error {
	var v any = f
	if cfg != nil {
		v = struct {
			*client.Flag
			Config *client.FlagConfig `json:"config"`
		}{f, cfg}
	}
	return render(cmd.OutOrStdout(), format, v, func(tw *tabwriter.Writer) {
		flagTable(tw, f.FlagSummary)
		fmt.Fprintln(tw)
		owner := ""
		if f.Owner != nil {
			owner = *f.Owner
		}
		fmt.Fprintf(tw, "DESCRIPTION\t%s\n", dashIfEmpty(f.Description))
		fmt.Fprintf(tw, "TAGS\t%s\n", joinOrDash(f.Tags))
		fmt.Fprintf(tw, "OWNER\t%s\n", dashIfEmpty(owner))
		fmt.Fprintf(tw, "OFF VARIATION\t%s\n", dashIfEmpty(f.OffVariation))
		fmt.Fprintf(tw, "LIFECYCLE\t%s\n", f.Lifecycle)
		fmt.Fprintf(tw, "ARCHIVED\t%t\n", f.Archived)
		fmt.Fprintln(tw)
		variationTable(tw, f.Variations...)
		if cfg != nil {
			fmt.Fprintln(tw)
			configTable(tw, cfg)
		}
	})
}

func configTable(tw *tabwriter.Writer, c *client.FlagConfig) {
	targets := make([]string, len(c.Targets))
	for i, t := range c.Targets {
		targets[i] = t.Variation + ": " + strings.Join(t.Values, ", ")
	}
	prereqs := make([]string, len(c.Prerequisites))
	for i, p := range c.Prerequisites {
		prereqs[i] = p.Flag + " = " + p.Variation
	}
	fmt.Fprintf(tw, "ENVIRONMENT\t%s\n", c.Environment)
	fmt.Fprintf(tw, "ENABLED\t%t\n", c.Enabled)
	fmt.Fprintf(tw, "VERSION\t%d\n", c.Version)
	fmt.Fprintf(tw, "TARGETS\t%s\n", dashIfEmpty(strings.Join(targets, "; ")))
	fmt.Fprintf(tw, "RULES\t%d\n", len(c.Rules))
	for i, r := range c.Rules {
		rule := describeRule(i, client.SegmentRule{Description: r.Description, Clauses: r.Clauses})
		fmt.Fprintf(tw, "  %s → %s\n", rule, describeServe(client.Serve{Variation: r.Variation, Rollout: r.Rollout}))
	}
	fmt.Fprintf(tw, "FALLTHROUGH\t%s\n", describeServe(c.Fallthrough))
	fmt.Fprintf(tw, "PREREQUISITES\t%s\n", joinOrDash(prereqs))
}

// describeServe renders a variation as its key and a rollout as
// "rollout by key: true 40%, false 60%".
func describeServe(s client.Serve) string {
	switch {
	case s.Variation != nil:
		return *s.Variation
	case s.Rollout != nil:
		weights := make([]string, len(s.Rollout.Weights))
		for i, w := range s.Rollout.Weights {
			weights[i] = w.Variation + " " + strconv.FormatFloat(float64(w.Weight)/1000, 'f', -1, 64) + "%"
		}
		by := ""
		if s.Rollout.BucketBy != "" {
			by = " by " + s.Rollout.BucketBy
		}
		return "rollout" + by + ": " + strings.Join(weights, ", ")
	default:
		return "-"
	}
}

func newFlagListCmd(a *app) *cobra.Command {
	var opts client.ListFlagsOptions
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List flags with their state in each environment",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			flags, err := c.ListFlags(cmd.Context(), v.Project, opts)
			if err != nil {
				return err
			}
			if flags == nil {
				flags = []client.FlagSummary{}
			}
			return render(cmd.OutOrStdout(), v.Output, flags, func(tw *tabwriter.Writer) { flagTable(tw, flags...) })
		},
	}
	f := cmd.Flags()
	f.BoolVar(&opts.Archived, "archived", false, "list archived flags instead of active ones")
	f.StringArrayVar(&opts.Tags, "tag", nil, "keep flags with this tag (repeatable)")
	f.StringVarP(&opts.Query, "query", "q", "", "match the key or name")
	return cmd
}

func newFlagGetCmd(a *app) *cobra.Command {
	var env string
	cmd := &cobra.Command{
		Use:   "get <key> [--env e]",
		Short: "Show a flag and its variations, and with --env its config",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			f, err := c.GetFlag(cmd.Context(), v.Project, args[0])
			if err != nil {
				return err
			}
			var cfg *client.FlagConfig
			if env != "" {
				if cfg, err = c.GetFlagConfig(cmd.Context(), v.Project, args[0], env); err != nil {
					return err
				}
			}
			return renderFlag(cmd, v.Output, f, cfg)
		},
	}
	cmd.Flags().StringVar(&env, "env", "", "also show the config in this environment")
	return cmd
}

// parseJSONValue parses a flag value given on the command line.
func parseJSONValue(s string) (any, error) {
	var v any
	if err := json.Unmarshal([]byte(s), &v); err != nil {
		return nil, fmt.Errorf("value is not JSON (quote strings, e.g. '\"text\"'): %w", err)
	}
	return v, nil
}

// parseVariation parses --variation key=<json value>.
func parseVariation(s string) (client.VariationIn, error) {
	key, raw, found := strings.Cut(s, "=")
	if !found || key == "" {
		return client.VariationIn{}, fmt.Errorf("invalid --variation %q: use key=<json value>", s)
	}
	value, err := parseJSONValue(raw)
	if err != nil {
		return client.VariationIn{}, fmt.Errorf("variation %q: %w", key, err)
	}
	return client.VariationIn{Key: key, Value: value}, nil
}

var flagTypes = map[string]bool{"boolean": true, "string": true, "number": true, "json": true}

func newFlagCreateCmd(a *app) *cobra.Command {
	var name, description, typ, owner, offVariation string
	var tags, variations []string
	cmd := &cobra.Command{
		Use:   "create <key> --name N --type boolean|string|number|json",
		Short: "Create a flag",
		Long: "Create a flag. Without --variation the API creates the default variations for the type.\n" +
			"Each --variation is key=<json value>, e.g. --variation on=true or --variation blue='\"#00f\"'.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			if !flagTypes[typ] {
				return usageError(fmt.Errorf("invalid --type %q: use boolean, string, number or json", typ))
			}
			in := client.FlagCreate{
				Key:          args[0],
				Name:         name,
				Description:  description,
				Type:         typ,
				Tags:         tags,
				Owner:        changedString(cmd, "owner", owner),
				OffVariation: changedString(cmd, "off-variation", offVariation),
			}
			for _, s := range variations {
				vi, err := parseVariation(s)
				if err != nil {
					return usageError(err)
				}
				in.Variations = append(in.Variations, vi)
			}
			f, err := c.CreateFlag(cmd.Context(), v.Project, in)
			if err != nil {
				return err
			}
			return renderFlag(cmd, v.Output, f, nil)
		},
	}
	f := cmd.Flags()
	f.StringVar(&name, "name", "", "flag name")
	f.StringVar(&typ, "type", "", "flag type: boolean, string, number or json")
	f.StringVar(&description, "description", "", "flag description")
	f.StringArrayVar(&tags, "tag", nil, "tag (repeatable)")
	f.StringVar(&owner, "owner", "", "owner, e.g. an email address")
	f.StringArrayVar(&variations, "variation", nil, "variation as key=<json value> (repeatable)")
	f.StringVar(&offVariation, "off-variation", "", "variation served when the flag is off")
	_ = cmd.MarkFlagRequired("name")
	_ = cmd.MarkFlagRequired("type")
	return cmd
}

func newFlagUpdateCmd(a *app) *cobra.Command {
	var name, description, owner, offVariation string
	var tags []string
	var clearOwner bool
	cmd := &cobra.Command{
		Use:   "update <key>",
		Short: "Change a flag's details",
		Long:  "Change a flag's details. --tag replaces all the tags; --tag \"\" removes them.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			in := client.FlagUpdate{
				Name:         changedString(cmd, "name", name),
				Description:  changedString(cmd, "description", description),
				Owner:        changedString(cmd, "owner", owner),
				ClearOwner:   clearOwner,
				OffVariation: changedString(cmd, "off-variation", offVariation),
			}
			if cmd.Flags().Changed("tag") {
				kept := []string{}
				for _, t := range tags {
					if t != "" {
						kept = append(kept, t)
					}
				}
				in.Tags = &kept
			}
			if in == (client.FlagUpdate{}) {
				return errNothingToUpdate
			}
			f, err := c.UpdateFlag(cmd.Context(), v.Project, args[0], in)
			if err != nil {
				return err
			}
			return renderFlag(cmd, v.Output, f, nil)
		},
	}
	f := cmd.Flags()
	f.StringVar(&name, "name", "", "flag name")
	f.StringVar(&description, "description", "", "flag description")
	f.StringArrayVar(&tags, "tag", nil, "tag (repeatable); replaces the tags")
	f.StringVar(&owner, "owner", "", "owner, e.g. an email address")
	f.BoolVar(&clearOwner, "clear-owner", false, "remove the owner")
	f.StringVar(&offVariation, "off-variation", "", "variation served when the flag is off")
	cmd.MarkFlagsMutuallyExclusive("owner", "clear-owner")
	return cmd
}

// newFlagArchiveCmd returns `flag archive`, or `flag restore` when archive
// is false.
func newFlagArchiveCmd(a *app, archive bool) *cobra.Command {
	use, short := "archive", "Archive a flag"
	if !archive {
		use, short = "restore", "Restore an archived flag"
	}
	return &cobra.Command{
		Use:   use + " <key>",
		Short: short,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			var f *client.Flag
			if archive {
				f, err = c.ArchiveFlag(cmd.Context(), v.Project, args[0])
			} else {
				f, err = c.RestoreFlag(cmd.Context(), v.Project, args[0])
			}
			if err != nil {
				return err
			}
			return renderFlag(cmd, v.Output, f, nil)
		},
	}
}

func newFlagDeleteCmd(a *app) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <key> --yes",
		Short: "Delete an archived flag",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			if err := requireYes(yes); err != nil {
				return err
			}
			if err := c.DeleteFlag(cmd.Context(), v.Project, args[0]); err != nil {
				return err
			}
			_, err = fmt.Fprintln(cmd.OutOrStdout(), "Deleted flag", args[0])
			return err
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "confirm the deletion")
	return cmd
}

func newFlagVariationCmd(a *app) *cobra.Command {
	cmd := &cobra.Command{Use: "variation", Short: "Manage the variations of a flag"}
	cmd.AddCommand(newFlagVariationSetCmd(a), newFlagVariationRemoveCmd(a))
	return cmd
}

func newFlagVariationSetCmd(a *app) *cobra.Command {
	var value, name, description string
	cmd := &cobra.Command{
		Use:   "set <flag> <key> --value JSON",
		Short: "Create a variation, or update it when it exists",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			val, err := parseJSONValue(value)
			if err != nil {
				return usageError(fmt.Errorf("--value: %w", err))
			}
			flag, key := args[0], args[1]
			f, err := c.GetFlag(cmd.Context(), v.Project, flag)
			if err != nil {
				return err
			}
			exists := false
			for _, existing := range f.Variations {
				exists = exists || existing.Key == key
			}
			var out *client.Variation
			if exists {
				out, err = c.UpdateVariation(cmd.Context(), v.Project, flag, key, client.VariationUpdate{
					Name:        changedString(cmd, "name", name),
					Description: changedString(cmd, "description", description),
					Value:       &val,
				})
			} else {
				out, err = c.CreateVariation(cmd.Context(), v.Project, flag, client.VariationIn{
					Key: key, Name: name, Description: description, Value: val,
				})
			}
			if err != nil {
				return err
			}
			return render(cmd.OutOrStdout(), v.Output, out, func(tw *tabwriter.Writer) { variationTable(tw, *out) })
		},
	}
	f := cmd.Flags()
	f.StringVar(&value, "value", "", "variation value as JSON, e.g. true, 1, '\"text\"' or '{\"a\":1}'")
	f.StringVar(&name, "name", "", "variation name")
	f.StringVar(&description, "description", "", "variation description")
	_ = cmd.MarkFlagRequired("value")
	return cmd
}

func newFlagVariationRemoveCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "remove <flag> <key>",
		Short: "Remove a variation from a flag",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			if err := c.DeleteVariation(cmd.Context(), v.Project, args[0], args[1]); err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "Removed variation %s from flag %s\n", args[1], args[0])
			return err
		},
	}
}

func newFlagConfigCmd(a *app) *cobra.Command {
	cmd := &cobra.Command{Use: "config", Short: "Show or replace a flag's config in an environment"}
	cmd.AddCommand(newFlagConfigGetCmd(a), newFlagConfigSetCmd(a))
	return cmd
}

// renderConfig prints a config; a table would lose its structure, so the
// table format prints YAML.
func renderConfig(cmd *cobra.Command, format string, cfg *client.FlagConfig) error {
	if format == "table" {
		format = "yaml"
	}
	return render(cmd.OutOrStdout(), format, cfg, nil)
}

func newFlagConfigGetCmd(a *app) *cobra.Command {
	var env string
	cmd := &cobra.Command{
		Use:   "get <flag> --env e",
		Short: "Show a flag's config in an environment (YAML unless -o json)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			cfg, err := c.GetFlagConfig(cmd.Context(), v.Project, args[0], env)
			if err != nil {
				return err
			}
			return renderConfig(cmd, v.Output, cfg)
		},
	}
	cmd.Flags().StringVar(&env, "env", "", "environment key")
	_ = cmd.MarkFlagRequired("env")
	return cmd
}

// envConfig is a flag's config in one environment as written in a
// flags-as-code file.
type envConfig struct {
	Enabled       bool                  `yaml:"enabled"`
	Targets       []client.Target       `yaml:"targets"`
	Rules         []client.Rule         `yaml:"rules"`
	Fallthrough   client.Serve          `yaml:"fallthrough"`
	Prerequisites []client.Prerequisite `yaml:"prerequisites"`
}

// readEnvConfig reads a YAML or JSON env config, rejecting unknown fields so
// that a typo does not clear part of the config.
func readEnvConfig(path string) (*envConfig, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	dec := yaml.NewDecoder(bytes.NewReader(raw))
	dec.KnownFields(true)
	var cfg envConfig
	if err := dec.Decode(&cfg); err != nil {
		if errors.Is(err, io.EOF) {
			return nil, fmt.Errorf("%s: empty file", path)
		}
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return &cfg, nil
}

func newFlagConfigSetCmd(a *app) *cobra.Command {
	var env, file string
	cmd := &cobra.Command{
		Use:   "set <flag> --env e -f file",
		Short: "Replace a flag's config in an environment",
		Long: "Replace a flag's config in an environment with the file's, a YAML or JSON object with\n" +
			"enabled, targets, rules, fallthrough and prerequisites; a missing list is cleared.",
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			cfg, err := readEnvConfig(file)
			if err != nil {
				return usageError(err)
			}
			in := client.FlagConfigIn{
				Enabled:       cfg.Enabled,
				Targets:       cfg.Targets,
				Rules:         cfg.Rules,
				Fallthrough:   cfg.Fallthrough,
				Prerequisites: cfg.Prerequisites,
			}
			// Someone else may change the config between the GET and the
			// PUT; then fetch the new version and try once more.
			for attempt := 0; ; attempt++ {
				cur, err := c.GetFlagConfig(cmd.Context(), v.Project, args[0], env)
				if err != nil {
					return err
				}
				in.Version = cur.Version
				out, err := c.PutFlagConfig(cmd.Context(), v.Project, args[0], env, in)
				if err == nil {
					return renderConfig(cmd, v.Output, out)
				}
				if attempt > 0 || !client.IsCode(err, "version_conflict") {
					return err
				}
			}
		},
	}
	f := cmd.Flags()
	f.StringVar(&env, "env", "", "environment key")
	f.StringVarP(&file, "file", "f", "", "YAML or JSON file with the config")
	_ = cmd.MarkFlagRequired("env")
	_ = cmd.MarkFlagRequired("file")
	return cmd
}

func renderState(cmd *cobra.Command, format string, s *client.FlagEnvironmentState) error {
	return render(cmd.OutOrStdout(), format, s, func(tw *tabwriter.Writer) {
		fmt.Fprintln(tw, "ENVIRONMENT\tSTATE\tVERSION")
		fmt.Fprintf(tw, "%s\t%s\t%d\n", s.Environment, onOff(s.Enabled), s.Version)
	})
}

// newFlagToggleCmd returns `flag on`, or `flag off` when enabled is false.
func newFlagToggleCmd(a *app, enabled bool) *cobra.Command {
	var env string
	cmd := &cobra.Command{
		Use:   onOff(enabled) + " <flag> --env e",
		Short: "Turn a flag " + onOff(enabled) + " in an environment",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			s, err := c.Toggle(cmd.Context(), v.Project, args[0], env, enabled, nil)
			if err != nil {
				return err
			}
			return renderState(cmd, v.Output, s)
		},
	}
	cmd.Flags().StringVar(&env, "env", "", "environment key")
	_ = cmd.MarkFlagRequired("env")
	return cmd
}

func newFlagKillCmd(a *app) *cobra.Command {
	var env string
	cmd := &cobra.Command{
		Use:   "kill <flag> --env e",
		Short: "Turn a flag off in an environment at once, bypassing approvals",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			s, err := c.Kill(cmd.Context(), v.Project, args[0], env)
			if err != nil {
				return err
			}
			return renderState(cmd, v.Output, s)
		},
	}
	cmd.Flags().StringVar(&env, "env", "", "environment key")
	_ = cmd.MarkFlagRequired("env")
	return cmd
}
