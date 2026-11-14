package transport

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"
)

func TestNewRequestSetsHeaders(t *testing.T) {
	req, err := NewRequest(context.Background(), http.MethodGet, "https://flags.example.com/sdk/v1/ruleset", "srv-secret", "megooflags-go/1.0", nil)
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}
	if got := req.Header.Get("Authorization"); got != "Bearer srv-secret" {
		t.Errorf("Authorization = %q", got)
	}
	if got := req.Header.Get("User-Agent"); got != "megooflags-go/1.0" {
		t.Errorf("User-Agent = %q", got)
	}
	if got := req.Header.Get("Accept"); got != "application/json" {
		t.Errorf("Accept = %q", got)
	}
	if got := req.Header.Get("Content-Type"); got != "" {
		t.Errorf("Content-Type = %q without a body", got)
	}
	if req.URL.Query().Has("key") {
		t.Errorf("URL carries the key: %s", req.URL)
	}
	if req.Context() == nil {
		t.Error("request has no context")
	}
}

func TestNewRequestWithBody(t *testing.T) {
	req, err := NewRequest(context.Background(), http.MethodPost, "https://flags.example.com/sdk/v1/events", "k", "ua", []byte(`{"a":1}`))
	if err != nil {
		t.Fatalf("NewRequest: %v", err)
	}
	if got := req.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q", got)
	}
	if req.ContentLength != 7 {
		t.Errorf("ContentLength = %d", req.ContentLength)
	}
}

func TestNewRequestBadURL(t *testing.T) {
	if _, err := NewRequest(context.Background(), http.MethodGet, "://bad", "k", "ua", nil); err == nil {
		t.Fatal("expected an error for a bad URL")
	}
}

func TestStatusError(t *testing.T) {
	now := time.Date(2026, 11, 14, 12, 0, 0, 0, time.UTC)
	cases := []struct {
		status     int
		retryAfter string
		check      func(t *testing.T, err error)
	}{
		{200, "", isNil},
		{304, "", isNil},
		{401, "", isFatal(401)},
		{403, "", isFatal(403)},
		{404, "", isHTTP(404)},
		{413, "", isHTTP(413)},
		{422, "", isHTTP(422)},
		{429, "7", isRetryAfter(7 * time.Second)},
		{429, "", isRetryAfter(0)},
		{500, "", isHTTP(500)},
		{503, "", isHTTP(503)},
	}
	for _, c := range cases {
		resp := &http.Response{StatusCode: c.status, Header: http.Header{}}
		if c.retryAfter != "" {
			resp.Header.Set("Retry-After", c.retryAfter)
		}
		t.Run(http.StatusText(c.status), func(t *testing.T) {
			c.check(t, StatusError(resp, now))
		})
	}
}

func isNil(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Errorf("err = %v, want nil", err)
	}
}

func isFatal(status int) func(*testing.T, error) {
	return func(t *testing.T, err error) {
		t.Helper()
		var fe *FatalError
		if !errors.As(err, &fe) || fe.Status != status {
			t.Errorf("err = %#v, want *FatalError{Status: %d}", err, status)
		}
	}
}

func isHTTP(status int) func(*testing.T, error) {
	return func(t *testing.T, err error) {
		t.Helper()
		var he *HTTPError
		if !errors.As(err, &he) || he.Status != status {
			t.Errorf("err = %#v, want *HTTPError{Status: %d}", err, status)
		}
	}
}

func isRetryAfter(after time.Duration) func(*testing.T, error) {
	return func(t *testing.T, err error) {
		t.Helper()
		var re *RetryAfterError
		if !errors.As(err, &re) || re.After != after {
			t.Errorf("err = %#v, want *RetryAfterError{After: %v}", err, after)
		}
	}
}

func TestErrorMessages(t *testing.T) {
	cases := map[string]error{
		"megooflags: SDK key refused (401)":        &FatalError{Status: 401},
		"megooflags: SDK key refused (revoked)":    &FatalError{Reason: "revoked"},
		"megooflags: rate limited, retry after 7s": &RetryAfterError{After: 7 * time.Second},
		"megooflags: HTTP 500":                     &HTTPError{Status: 500},
	}
	for want, err := range cases {
		if got := err.Error(); got != want {
			t.Errorf("Error() = %q, want %q", got, want)
		}
	}
}

func TestRetryAfter(t *testing.T) {
	now := time.Date(2026, 11, 14, 12, 0, 0, 0, time.UTC)
	cases := []struct {
		value  string
		want   time.Duration
		wantOK bool
	}{
		{"7", 7 * time.Second, true},
		{"0", 0, true},
		{"-3", 0, true},
		{now.Add(30 * time.Second).Format(http.TimeFormat), 30 * time.Second, true},
		{now.Add(-time.Hour).Format(http.TimeFormat), 0, true},
		{"soon", 0, false},
		{"", 0, false},
	}
	for _, c := range cases {
		h := http.Header{}
		if c.value != "" {
			h.Set("Retry-After", c.value)
		}
		got, ok := RetryAfter(h, now)
		if got != c.want || ok != c.wantOK {
			t.Errorf("RetryAfter(%q) = (%v, %v), want (%v, %v)", c.value, got, ok, c.want, c.wantOK)
		}
	}
}
