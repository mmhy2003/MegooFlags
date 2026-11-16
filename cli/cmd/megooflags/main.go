// Command megooflags manages MegooFlags through its REST API.
package main

import (
	"context"
	"os"
	"os/signal"

	"golang.org/x/term"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	code := run(ctx, os.Args[1:], IO{
		In:         os.Stdin,
		Out:        os.Stdout,
		Err:        os.Stderr,
		Getenv:     os.Getenv,
		IsTerminal: term.IsTerminal(int(os.Stdout.Fd())),
	})
	stop()
	os.Exit(code)
}
