package client

import (
	"fmt"
	"testing"
)

func TestErrorString(t *testing.T) {
	e := &Error{Status: 403, Code: "forbidden", Message: "missing permission 'flag.update'"}
	if got := e.Error(); got != "forbidden: missing permission 'flag.update' (HTTP 403)" {
		t.Errorf("Error() = %q", got)
	}
}

func TestIsCodeUnwrapsWrappedErrors(t *testing.T) {
	err := fmt.Errorf("get flag: %w", &Error{Status: 404, Code: "not_found"})
	if !IsCode(err, "not_found") || !IsNotFound(err) {
		t.Error("wrapped API errors must still match")
	}
}
