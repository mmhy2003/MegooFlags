package main

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
	"text/tabwriter"

	"github.com/spf13/cobra"
	"golang.org/x/term"

	"github.com/mmhy2003/MegooFlags/cli/internal/config"
)

func newLoginCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "login --url URL",
		Short: "Check an API token and save it to the config file",
		Long: "Check an API token and save it with the URL to the config file (mode 0600).\n" +
			"The token comes from MEGOOFLAGS_API_TOKEN, else a prompt on a terminal, else one line of stdin.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			v, err := a.values()
			if err != nil {
				return err
			}
			if v.URL == "" {
				return usageError(errors.New("no URL: pass --url or set MEGOOFLAGS_URL"))
			}
			token := a.io.Getenv("MEGOOFLAGS_API_TOKEN")
			if token == "" {
				if token, err = readToken(a.io); err != nil {
					return err
				}
			}
			if token == "" {
				return usageError(errors.New("no API token given"))
			}
			c, err := newClient(config.Values{URL: v.URL, Token: token})
			if err != nil {
				return err
			}
			me, err := c.Me(cmd.Context())
			if err != nil {
				return err
			}
			f, path, err := a.loadFile()
			if err != nil {
				return err
			}
			f.URL, f.Token = v.URL, token
			if err := config.Save(path, f); err != nil {
				return err
			}
			_, err = fmt.Fprintf(cmd.OutOrStdout(), "Logged in to %s as %s\n", v.URL, me.Label)
			return err
		},
	}
}

// readToken prompts without echo when stdin is a terminal, else reads one
// line of stdin.
func readToken(pio IO) (string, error) {
	if f, ok := pio.In.(*os.File); ok && term.IsTerminal(int(f.Fd())) {
		fmt.Fprint(pio.Err, "API token: ")
		raw, err := term.ReadPassword(int(f.Fd()))
		fmt.Fprintln(pio.Err)
		if err != nil {
			return "", fmt.Errorf("read token: %w", err)
		}
		return strings.TrimSpace(string(raw)), nil
	}
	line, err := bufio.NewReader(pio.In).ReadString('\n')
	if err != nil && !errors.Is(err, io.EOF) {
		return "", fmt.Errorf("read token: %w", err)
	}
	return strings.TrimSpace(line), nil
}

func newLogoutCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "logout",
		Short: "Remove the API token from the config file",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			f, path, err := a.loadFile()
			if err != nil {
				return err
			}
			if f.Token != "" {
				f.Token = ""
				if err := config.Save(path, f); err != nil {
					return err
				}
			}
			_, err = fmt.Fprintln(cmd.OutOrStdout(), "Logged out")
			return err
		},
	}
}

// identity is what whoami prints. It never includes the token.
type identity struct {
	Kind  string `json:"kind"`
	Label string `json:"label"`
	URL   string `json:"url"`
}

func newWhoamiCmd(a *app) *cobra.Command {
	return &cobra.Command{
		Use:   "whoami",
		Short: "Show who the API token belongs to",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			c, v, err := a.client()
			if err != nil {
				return err
			}
			me, err := c.Me(cmd.Context())
			if err != nil {
				return err
			}
			id := identity{Kind: me.Kind, Label: me.Label, URL: v.URL}
			return render(cmd.OutOrStdout(), v.Output, id, func(tw *tabwriter.Writer) {
				fmt.Fprintln(tw, "KIND\tLABEL\tURL")
				fmt.Fprintf(tw, "%s\t%s\t%s\n", id.Kind, id.Label, id.URL)
			})
		},
	}
}
