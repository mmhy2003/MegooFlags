// Command mfload is the MegooFlags load test tool. scripts/perf.sh runs it.
package main

import (
	"flag"
	"fmt"
	"io"
	"maps"
	"os"
	"slices"
)

const version = "dev"

type command struct {
	summary string
	run     func(args []string, stdout io.Writer) error
}

var commands = map[string]command{
	"version": {"print the mfload version", runVersion},
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		usage(stderr)
		return 2
	}
	cmd, ok := commands[args[0]]
	if !ok {
		fmt.Fprintf(stderr, "mfload: unknown command %q\n", args[0])
		usage(stderr)
		return 2
	}
	if err := cmd.run(args[1:], stdout); err != nil {
		fmt.Fprintf(stderr, "mfload %s: %v\n", args[0], err)
		return 1
	}
	return 0
}

func usage(w io.Writer) {
	fmt.Fprintln(w, "usage: mfload <command> [flags]\n\ncommands:")
	for _, name := range slices.Sorted(maps.Keys(commands)) {
		fmt.Fprintf(w, "  %-10s %s\n", name, commands[name].summary)
	}
}

func runVersion(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("version", flag.ContinueOnError)
	if err := fs.Parse(args); err != nil {
		return err
	}
	_, err := fmt.Fprintf(stdout, "mfload %s\n", version)
	return err
}
