package main

import (
	"errors"

	"github.com/mmhy2003/MegooFlags/cli/client"
)

// Exit codes. Scripts and CI rely on them, so they never change.
const (
	ExitOK            = 0
	ExitError         = 1
	ExitUsage         = 2 // bad flags, arguments or command
	ExitDrift         = 3 // diff found changes
	ExitNeedsApproval = 4 // apply stopped at an approval-required environment
)

// exitError carries a specific exit code with its error.
type exitError struct {
	code int
	err  error
}

func (e *exitError) Error() string { return e.err.Error() }
func (e *exitError) Unwrap() error { return e.err }

func usageError(err error) error { return &exitError{code: ExitUsage, err: err} }

// errQuiet ends a command that has already said everything, such as diff
// finding changes, with an exit code but no "Error:" line.
var errQuiet = errors.New("quiet exit")

func quietExit(code int) error { return &exitError{code: code, err: errQuiet} }

// exitCode maps an error from a command to the process exit code.
func exitCode(err error) int {
	if err == nil {
		return ExitOK
	}
	var ee *exitError
	if errors.As(err, &ee) {
		return ee.code
	}
	if client.IsCode(err, "approval_required") {
		return ExitNeedsApproval
	}
	return ExitError
}
