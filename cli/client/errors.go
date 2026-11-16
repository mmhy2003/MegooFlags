package client

import (
	"errors"
	"fmt"
	"net/http"
)

// Error is a non-2xx response from the API, decoded from its
// {"code", "message", "details"} body.
type Error struct {
	Status  int
	Code    string
	Message string
	Details any
}

// Error returns "<code>: <message> (HTTP <status>)".
func (e *Error) Error() string {
	return fmt.Sprintf("%s: %s (HTTP %d)", e.Code, e.Message, e.Status)
}

// IsCode reports whether err is, or wraps, an API error with the given code.
func IsCode(err error, code string) bool {
	var e *Error
	return errors.As(err, &e) && e.Code == code
}

// IsNotFound reports whether err is, or wraps, an API error with code
// not_found or status 404.
func IsNotFound(err error) bool {
	var e *Error
	return errors.As(err, &e) && (e.Code == "not_found" || e.Status == http.StatusNotFound)
}
