// Package transport holds the HTTP helpers shared by the ruleset download, the
// stream and the event sender: authenticated requests, status mapping,
// Retry-After parsing and jittered backoff.
package transport

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// FatalError reports that the server refused the SDK key (401, 403, or Status
// 0 with Reason "revoked"). Retrying with the same key cannot succeed.
type FatalError struct {
	Status int
	Reason string
}

func (e *FatalError) Error() string {
	cause := e.Reason
	if e.Status != 0 {
		cause = strconv.Itoa(e.Status)
	}
	return "megooflags: SDK key refused (" + cause + ")"
}

// RetryAfterError reports a 429. After is the server's Retry-After, or 0.
type RetryAfterError struct {
	After time.Duration
}

func (e *RetryAfterError) Error() string {
	return fmt.Sprintf("megooflags: rate limited, retry after %v", e.After)
}

// HTTPError reports any other non-2xx status.
type HTTPError struct {
	Status int
}

func (e *HTTPError) Error() string {
	return "megooflags: HTTP " + strconv.Itoa(e.Status)
}

// NewRequest builds a request carrying the SDK key in the Authorization header
// (never in the URL). Callers override Accept for SSE.
func NewRequest(ctx context.Context, method, url, key, userAgent string, body []byte) (*http.Request, error) {
	var r io.Reader
	if body != nil {
		r = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, r)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	return req, nil
}

// StatusError maps a response status to an error: nil for 2xx and 304. It
// neither reads nor closes the body; the caller owns it.
func StatusError(resp *http.Response, now time.Time) error {
	switch s := resp.StatusCode; {
	case s >= 200 && s < 300, s == http.StatusNotModified:
		return nil
	case s == http.StatusUnauthorized, s == http.StatusForbidden:
		return &FatalError{Status: s}
	case s == http.StatusTooManyRequests:
		after, _ := RetryAfter(resp.Header, now)
		return &RetryAfterError{After: after}
	default:
		return &HTTPError{Status: s}
	}
}

// RetryAfter parses a Retry-After header given as delta-seconds or an
// HTTP-date. A value in the past is 0; a missing or invalid one is (0, false).
func RetryAfter(h http.Header, now time.Time) (time.Duration, bool) {
	v := strings.TrimSpace(h.Get("Retry-After"))
	if v == "" {
		return 0, false
	}
	if n, err := strconv.ParseInt(v, 10, 64); err == nil {
		if n < 0 {
			return 0, true
		}
		return time.Duration(n) * time.Second, true
	}
	t, err := http.ParseTime(v)
	if err != nil {
		return 0, false
	}
	return max(t.Sub(now), 0), true
}
