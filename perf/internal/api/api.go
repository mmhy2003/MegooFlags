// Package api is a small client for the MegooFlags management API, with the
// request shapes of scripts/propagation_test.py.
package api

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Client calls the management API under <base>/api with an API token.
type Client struct {
	base  string
	token string
	http  *http.Client
}

// New returns a client for the server at base (for example
// http://api:8000). insecure skips TLS certificate checks.
func New(base, token string, insecure bool) *Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: insecure}
	return &Client{
		base:  strings.TrimSuffix(base, "/"),
		token: token,
		http:  &http.Client{Transport: transport, Timeout: 10 * time.Second},
	}
}

// ToggleFlag turns flag on or off in env of project.
func (c *Client) ToggleFlag(ctx context.Context, project, env, flag string, on bool) error {
	path := fmt.Sprintf("/projects/%s/flags/%s/environments/%s/toggle",
		url.PathEscape(project), url.PathEscape(flag), url.PathEscape(env))
	return c.call(ctx, http.MethodPost, path, map[string]bool{"enabled": on})
}

func (c *Client) call(ctx context.Context, method, path string, body any) error {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+"/api"+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("%s %s: %s: %s", method, path, resp.Status, bytes.TrimSpace(detail))
	}
	_, err = io.Copy(io.Discard, resp.Body)
	return err
}
