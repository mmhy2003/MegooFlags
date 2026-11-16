package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"

	"github.com/spf13/cobra"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

// promoteOptions are the flags of `promote`.
type promoteOptions struct {
	all, includeState, changeRequests bool
	from                              string
	to                                []string
}

// promoter promotes flags of one project and tallies the outcome.
type promoter struct {
	c              *client.Client
	project        string
	opts           promoteOptions
	out, errOut    io.Writer
	failed, needed bool
}

func newPromoteCmd(a *app) *cobra.Command {
	var opts promoteOptions
	cmd := &cobra.Command{
		Use:   "promote <flag...> | --all --from E1 --to E2[,E3]",
		Short: "Copy flag configs from one environment to others",
		Long: "Copy the targeting of flags from one environment to others. A target that requires\n" +
			"approvals is not changed: with --change-requests a config change request is opened\n" +
			"there, otherwise the command reports it and exits 4. A failing flag does not stop\n" +
			"the others but makes the command exit 1.",
		Args: func(cmd *cobra.Command, args []string) error {
			if opts.all == (len(args) > 0) {
				return usageError(errors.New("pass flag keys or --all, not both"))
			}
			return nil
		},
		RunE: func(cmd *cobra.Command, args []string) error {
			c, v, err := a.projectClient()
			if err != nil {
				return err
			}
			p := &promoter{c: c, project: v.Project, opts: opts, out: cmd.OutOrStdout(), errOut: cmd.ErrOrStderr()}
			return p.run(cmd.Context(), args)
		},
	}
	f := cmd.Flags()
	f.BoolVar(&opts.all, "all", false, "promote every flag that is not archived")
	f.StringVar(&opts.from, "from", "", "source environment key")
	f.StringSliceVar(&opts.to, "to", nil, "target environment keys, comma separated")
	f.BoolVar(&opts.includeState, "include-state", false, "also copy whether the flag is on")
	f.BoolVar(&opts.changeRequests, "change-requests", false, "open change requests for targets that require approvals")
	_ = cmd.MarkFlagRequired("from")
	_ = cmd.MarkFlagRequired("to")
	return cmd
}

func (p *promoter) run(ctx context.Context, flags []string) error {
	envs, err := p.c.ListEnvironments(ctx, p.project)
	if err != nil {
		return err
	}
	approval := map[string]bool{}
	for _, e := range envs {
		approval[e.Key] = e.ApprovalsRequired > 0
	}
	var direct, guarded []string
	for _, e := range p.opts.to {
		if approval[e] {
			guarded = append(guarded, e)
		} else {
			direct = append(direct, e)
		}
	}
	if p.opts.all {
		all, err := p.c.ListFlags(ctx, p.project, client.ListFlagsOptions{})
		if err != nil {
			return err
		}
		for _, f := range all {
			flags = append(flags, f.Key)
		}
	}
	for _, f := range flags {
		p.copy(ctx, f, direct)
		for _, e := range guarded {
			p.guarded(ctx, f, e)
		}
	}
	switch {
	case p.failed:
		return &exitError{code: ExitError, err: errors.New("some promotions failed")}
	case p.needed:
		return &exitError{code: ExitNeedsApproval, err: errors.New("some targets need approval: use --change-requests")}
	}
	return nil
}

// copy copies flag f to the targets that need no approval in one call.
func (p *promoter) copy(ctx context.Context, f string, targets []string) {
	if len(targets) == 0 {
		return
	}
	in := client.CopyIn{From: p.opts.from, To: targets, IncludeEnabled: p.opts.includeState}
	if _, err := p.c.CopyFlag(ctx, p.project, f, in); err != nil {
		for _, e := range targets {
			p.fail(f, e, err)
		}
		return
	}
	for _, e := range targets {
		fmt.Fprintf(p.out, "%s -> %s: copied\n", f, e)
	}
}

// guarded handles a target that requires approvals.
func (p *promoter) guarded(ctx context.Context, f, e string) {
	if !p.opts.changeRequests {
		p.needed = true
		fmt.Fprintf(p.out, "%s -> %s: needs approval\n", f, e)
		return
	}
	cr, err := p.changeRequest(ctx, f, e)
	if err != nil {
		p.fail(f, e, err)
		return
	}
	fmt.Fprintf(p.out, "%s -> %s: opened change request %s\n", f, e, cr.ID)
}

// changeRequest opens a config change request that gives target e the
// source's targeting, based on the target's current version.
func (p *promoter) changeRequest(ctx context.Context, f, e string) (*client.ChangeRequest, error) {
	src, err := p.c.GetFlagConfig(ctx, p.project, f, p.opts.from)
	if err != nil {
		return nil, err
	}
	dst, err := p.c.GetFlagConfig(ctx, p.project, f, e)
	if err != nil {
		return nil, err
	}
	in := client.FlagConfigIn{
		Enabled:       dst.Enabled,
		Version:       dst.Version,
		Targets:       src.Targets,
		Rules:         src.Rules,
		Fallthrough:   src.Fallthrough,
		Prerequisites: src.Prerequisites,
	}
	if p.opts.includeState {
		in.Enabled = src.Enabled
	}
	raw, err := json.Marshal(in)
	if err != nil {
		return nil, err
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, err
	}
	return p.c.CreateChangeRequest(ctx, p.project, client.ChangeRequestCreate{
		Title:       fmt.Sprintf("Promote %s from %s to %s", f, p.opts.from, e),
		Flag:        f,
		Environment: e,
		Kind:        "config",
		Payload:     payload,
	})
}

func (p *promoter) fail(f, e string, err error) {
	p.failed = true
	var apiErr *client.Error
	if errors.As(err, &apiErr) {
		fmt.Fprintf(p.errOut, "%s -> %s: %s: %s\n", f, e, apiErr.Code, apiErr.Message)
		return
	}
	fmt.Fprintf(p.errOut, "%s -> %s: %v\n", f, e, err)
}
