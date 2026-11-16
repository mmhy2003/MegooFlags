package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// DefaultUserAgent is the User-Agent sent when none is set.
const DefaultUserAgent = "megooflags-go-client"

// DefaultMaxRetries is how many times a GET is retried by default.
const DefaultMaxRetries = 3

// maxErrorBody caps how much of an error response is read.
const maxErrorBody = 1 << 20

// Client calls the MegooFlags management API with an API token.
type Client struct {
	BaseURL, Token, UserAgent string
	HTTP                      *http.Client
	// MaxRetries is how many times a GET is retried on a network error, 429
	// or 5xx. Other methods are never retried.
	MaxRetries int
	// Sleep waits between retries, and returns ctx's error as soon as ctx
	// is done. Tests replace it.
	Sleep func(ctx context.Context, d time.Duration) error
}

// MaxRetryWait caps the wait before a retry, whatever Retry-After says.
const MaxRetryWait = 30 * time.Second

// sleep waits for d or until ctx is done.
func sleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Option configures a Client.
type Option func(*Client)

// WithHTTPClient sets the HTTP client used for requests.
func WithHTTPClient(hc *http.Client) Option { return func(c *Client) { c.HTTP = hc } }

// WithUserAgent sets the User-Agent header.
func WithUserAgent(ua string) Option { return func(c *Client) { c.UserAgent = ua } }

// WithMaxRetries sets how many times a GET is retried.
func WithMaxRetries(n int) Option { return func(c *Client) { c.MaxRetries = n } }

// New returns a client for the API at baseURL that authenticates with token.
func New(baseURL, token string, opts ...Option) *Client {
	c := &Client{
		BaseURL:    strings.TrimRight(baseURL, "/"),
		Token:      token,
		UserAgent:  DefaultUserAgent,
		HTTP:       &http.Client{Timeout: 60 * time.Second},
		MaxRetries: DefaultMaxRetries,
		Sleep:      sleep,
	}
	for _, opt := range opts {
		opt(c)
	}
	return c
}

// Do sends a request to path with query, encoding in as the JSON body when it
// is not nil, and decodes a 2xx JSON response into out when out is not nil
// and the status is not 204. A non-2xx response is returned as *Error.
func (c *Client) Do(ctx context.Context, method, path string, query url.Values, in, out any) error {
	resp, err := c.request(ctx, method, path, query, in, nil)
	if err != nil {
		return err
	}
	return c.handle(resp, out)
}

// request sends a request with the extra header, retrying a GET, and
// returns the final response without checking its status.
func (c *Client) request(ctx context.Context, method, path string, query url.Values, in any, header http.Header) (*http.Response, error) {
	var body []byte
	if in != nil {
		var err error
		if body, err = json.Marshal(in); err != nil {
			return nil, fmt.Errorf("encode request: %w", err)
		}
	}
	target := c.BaseURL + path
	if len(query) > 0 {
		target += "?" + query.Encode()
	}
	retries := 0
	if method == http.MethodGet {
		retries = c.MaxRetries
	}
	for attempt := 0; ; attempt++ {
		resp, err := c.send(ctx, method, target, body, header)
		if err != nil {
			if ctx.Err() != nil || attempt >= retries {
				return nil, err
			}
			if err := c.Sleep(ctx, backoff(attempt, "")); err != nil {
				return nil, err
			}
			continue
		}
		if retryable(resp.StatusCode) && attempt < retries {
			wait := backoff(attempt, resp.Header.Get("Retry-After"))
			drain(resp)
			if err := c.Sleep(ctx, wait); err != nil {
				return nil, err
			}
			continue
		}
		return resp, nil
	}
}

func (c *Client) send(ctx context.Context, method, target string, body []byte, header http.Header) (*http.Response, error) {
	var r io.Reader
	if body != nil {
		r = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, target, r)
	if err != nil {
		return nil, err
	}
	for k, v := range header {
		req.Header[k] = v
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", c.UserAgent)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	return c.HTTP.Do(req)
}

func (c *Client) handle(resp *http.Response, out any) error {
	defer drain(resp)
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return c.decodeError(resp)
	}
	if out == nil || resp.StatusCode == http.StatusNoContent {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("decode response: %w", err)
	}
	return nil
}

func (c *Client) decodeError(resp *http.Response) error {
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, maxErrorBody))
	e := &Error{Status: resp.StatusCode}
	var payload struct {
		Code    string `json:"code"`
		Message string `json:"message"`
		Details any    `json:"details"`
	}
	if json.Unmarshal(raw, &payload) == nil && payload.Code != "" {
		e.Code, e.Message, e.Details = payload.Code, payload.Message, payload.Details
	} else {
		e.Code = "http_" + strconv.Itoa(resp.StatusCode)
		e.Message = http.StatusText(resp.StatusCode)
	}
	if c.Token != "" {
		e.Message = strings.ReplaceAll(e.Message, c.Token, "***")
	}
	return e
}

func retryable(status int) bool {
	return status == http.StatusTooManyRequests || status >= 500
}

// backoff honours a Retry-After header in seconds, else waits 0.5 s, 1 s,
// 2 s and so on; never longer than MaxRetryWait.
func backoff(attempt int, retryAfter string) time.Duration {
	if s, err := strconv.Atoi(strings.TrimSpace(retryAfter)); err == nil && s >= 0 {
		return min(time.Duration(min(s, int(MaxRetryWait/time.Second)))*time.Second, MaxRetryWait)
	}
	return min(500*time.Millisecond<<min(attempt, 10), MaxRetryWait)
}

func drain(resp *http.Response) {
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, maxErrorBody))
	_ = resp.Body.Close()
}

// page is one page of a cursor-paginated list.
type page[T any] struct {
	Items      []T     `json:"items"`
	NextCursor *string `json:"next_cursor"`
}

// ListAll fetches every item of a cursor-paginated list at path, requesting
// 500 items per page and following next_cursor. query is not modified.
func ListAll[T any](ctx context.Context, c *Client, path string, query url.Values) ([]T, error) {
	q := url.Values{}
	for k, v := range query {
		q[k] = append([]string(nil), v...)
	}
	q.Set("limit", "500")
	var all []T
	for {
		var p page[T]
		if err := c.Do(ctx, http.MethodGet, path, q, nil, &p); err != nil {
			return nil, err
		}
		all = append(all, p.Items...)
		if p.NextCursor == nil || *p.NextCursor == "" {
			return all, nil
		}
		q.Set("cursor", *p.NextCursor)
	}
}
