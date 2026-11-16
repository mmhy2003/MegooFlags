package main

import "errors"

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

// exitCode maps an error from a command to the process exit code.
func exitCode(err error) int {
	if err == nil {
		return ExitOK
	}
	var ee *exitError
	if errors.As(err, &ee) {
		return ee.code
	}
	return ExitError
}
