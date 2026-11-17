// Package api is a small client for the MegooFlags management API, with the
// request shapes of scripts/propagation_test.py.
package api

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Client calls the management API under <base>/api with an API token.
type Client struct {
	base     string
	token    string
	http     *http.Client
	attempts int
	backoff  time.Duration
}

// New returns a client for the server at base (for example
// http://api:8000). insecure skips TLS certificate checks.
func New(base, token string, insecure bool) *Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: insecure}
	return &Client{
		base:     strings.TrimSuffix(base, "/"),
		token:    token,
		http:     &http.Client{Transport: transport, Timeout: 10 * time.Second},
		attempts: 1,
	}
}

// WithRetry makes c try a call up to attempts times while the answer is 429
// or 5xx, waiting backoff before the first retry and twice as long before
// each next one. It returns c.
func (c *Client) WithRetry(attempts int, backoff time.Duration) *Client {
	c.attempts, c.backoff = max(1, attempts), backoff
	return c
}

// ToggleFlag turns flag on or off in env of project.
func (c *Client) ToggleFlag(ctx context.Context, project, env, flag string, on bool) error {
	path := fmt.Sprintf("/projects/%s/flags/%s/environments/%s/toggle",
		url.PathEscape(project), url.PathEscape(flag), url.PathEscape(env))
	return c.call(ctx, http.MethodPost, path, map[string]bool{"enabled": on}, nil)
}

// FlagState is a flag's state in one environment.
type FlagState struct {
	Environment string `json:"environment"`
	Enabled     bool   `json:"enabled"`
	Version     int    `json:"version"`
}

// CreateProject creates project key with the default environments and
// returns their keys in order.
func (c *Client) CreateProject(ctx context.Context, key string) ([]string, error) {
	var out struct {
		Environments []struct {
			Key string `json:"key"`
		} `json:"environments"`
	}
	if err := c.call(ctx, http.MethodPost, "/projects", map[string]string{"key": key, "name": key}, &out); err != nil {
		return nil, err
	}
	envs := make([]string, len(out.Environments))
	for i, e := range out.Environments {
		envs[i] = e.Key
	}
	return envs, nil
}

// CreateBooleanFlag creates a boolean flag and returns its state per environment.
func (c *Client) CreateBooleanFlag(ctx context.Context, project, key string) ([]FlagState, error) {
	var out struct {
		Environments []FlagState `json:"environments"`
	}
	body := map[string]string{"key": key, "name": key, "type": "boolean"}
	err := c.call(ctx, http.MethodPost, "/projects/"+url.PathEscape(project)+"/flags", body, &out)
	return out.Environments, err
}

// ReadFlag returns a flag's state per environment.
func (c *Client) ReadFlag(ctx context.Context, project, key string) ([]FlagState, error) {
	var out struct {
		Environments []FlagState `json:"environments"`
	}
	err := c.call(ctx, http.MethodGet, "/projects/"+url.PathEscape(project)+"/flags/"+url.PathEscape(key), nil, &out)
	return out.Environments, err
}

// ReplaceFlagConfig replaces a flag's config in env (PUT, FlagConfigIn).
func (c *Client) ReplaceFlagConfig(ctx context.Context, project, flag, env string, config any) error {
	path := fmt.Sprintf("/projects/%s/flags/%s/environments/%s",
		url.PathEscape(project), url.PathEscape(flag), url.PathEscape(env))
	return c.call(ctx, http.MethodPut, path, config, nil)
}

// CreateSDKKey creates an SDK key of kind (server or client) and returns its secret.
func (c *Client) CreateSDKKey(ctx context.Context, project, env, name, kind string) (string, error) {
	path := fmt.Sprintf("/projects/%s/environments/%s/sdk-keys", url.PathEscape(project), url.PathEscape(env))
	var out struct {
		Key string `json:"key"`
	}
	err := c.call(ctx, http.MethodPost, path, map[string]string{"name": name, "kind": kind}, &out)
	return out.Key, err
}

// ListFlags reads the first page of limit flags.
func (c *Client) ListFlags(ctx context.Context, project string, limit int) error {
	path := fmt.Sprintf("/projects/%s/flags?limit=%d", url.PathEscape(project), limit)
	return c.call(ctx, http.MethodGet, path, nil, nil)
}

// Ruleset downloads the ruleset of a server SDK key from <base>/sdk/v1/ruleset.
func (c *Client) Ruleset(ctx context.Context, key string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/sdk/v1/ruleset", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+key)
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("GET /sdk/v1/ruleset: %s", resp.Status)
	}
	return data, nil
}

// StatusError is a non-2xx answer.
type StatusError struct {
	Method, Path string
	Code         int
	Status       string
	Detail       string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("%s %s: %s: %s", e.Method, e.Path, e.Status, e.Detail)
}

// retryable reports whether an answer with this status is worth retrying.
func retryable(code int) bool {
	return code == http.StatusTooManyRequests || code >= 500
}

func (c *Client) call(ctx context.Context, method, path string, body, out any) error {
	var data []byte
	if body != nil {
		var err error
		if data, err = json.Marshal(body); err != nil {
			return err
		}
	}
	backoff := c.backoff
	for attempt := 1; ; attempt++ {
		err := c.once(ctx, method, path, data, body != nil, out)
		var status *StatusError
		if attempt >= c.attempts || !errors.As(err, &status) || !retryable(status.Code) {
			return err
		}
		select {
		case <-ctx.Done():
			return err
		case <-time.After(backoff):
		}
		backoff *= 2
	}
}

func (c *Client) once(ctx context.Context, method, path string, data []byte, hasBody bool, out any) error {
	var reader io.Reader
	if hasBody {
		reader = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+"/api"+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	if hasBody {
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
		return &StatusError{Method: method, Path: path, Code: resp.StatusCode, Status: resp.Status, Detail: string(bytes.TrimSpace(detail))}
	}
	if out != nil {
		return json.NewDecoder(resp.Body).Decode(out)
	}
	_, err = io.Copy(io.Discard, resp.Body)
	return err
}
