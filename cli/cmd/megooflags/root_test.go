package main

import (
	"bytes"
	"context"
	"strings"
	"testing"

	"github.com/mmhy2003/MegooFlags/cli/internal/buildinfo"
)

func runCLI(t *testing.T, args ...string) (code int, stdout, stderr string) {
	t.Helper()
	var out, errb bytes.Buffer
	code = run(context.Background(), args, IO{
		In:     strings.NewReader(""),
		Out:    &out,
		Err:    &errb,
		Getenv: func(string) string { return "" },
	})
	return code, out.String(), errb.String()
}

func TestVersion(t *testing.T) {
	code, out, _ := runCLI(t, "version")
	if code != ExitOK {
		t.Fatalf("code = %d", code)
	}
	if want := "megooflags " + buildinfo.Version + "\n"; out != want {
		t.Fatalf("out = %q, want %q", out, want)
	}
}

func TestUnknownCommandIsUsageError(t *testing.T) {
	code, _, errOut := runCLI(t, "frobnicate")
	if code != ExitUsage {
		t.Fatalf("code = %d, want %d", code, ExitUsage)
	}
	if !strings.Contains(errOut, "frobnicate") {
		t.Fatalf("stderr = %q", errOut)
	}
}

func TestUnknownFlagIsUsageError(t *testing.T) {
	code, _, _ := runCLI(t, "version", "--nope")
	if code != ExitUsage {
		t.Fatalf("code = %d, want %d", code, ExitUsage)
	}
}

func TestNoTokenFlag(t *testing.T) {
	code, _, _ := runCLI(t, "--token", "x", "version")
	if code != ExitUsage {
		t.Fatalf("code = %d, want %d", code, ExitUsage)
	}
}

func TestCompletion(t *testing.T) {
	for _, shell := range []string{"bash", "zsh", "fish", "powershell"} {
		code, out, _ := runCLI(t, "completion", shell)
		if code != ExitOK {
			t.Fatalf("%s: code = %d", shell, code)
		}
		if !strings.Contains(out, "megooflags") {
			t.Fatalf("%s: output lacks megooflags", shell)
		}
	}
}

func TestCompletionUnknownShell(t *testing.T) {
	code, _, _ := runCLI(t, "completion", "tcsh")
	if code != ExitUsage {
		t.Fatalf("code = %d, want %d", code, ExitUsage)
	}
}

func TestExitCodeOfExitError(t *testing.T) {
	if got := exitCode(&exitError{code: ExitDrift, err: errString("drift")}); got != ExitDrift {
		t.Fatalf("got %d", got)
	}
	if got := exitCode(errString("boom")); got != ExitError {
		t.Fatalf("got %d", got)
	}
	if got := exitCode(nil); got != ExitOK {
		t.Fatalf("got %d", got)
	}
}

type errString string

func (e errString) Error() string { return string(e) }
