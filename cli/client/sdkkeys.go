package client

import (
	"context"
	"net/http"
)

// ListSdkKeys returns the SDK keys of environment e, without their secrets.
func (c *Client) ListSdkKeys(ctx context.Context, p, e string) ([]SdkKey, error) {
	var out []SdkKey
	if err := c.Do(ctx, http.MethodGet, projectPath(p, "environments", e, "sdk-keys"), nil, nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// CreateSdkKey creates an SDK key in environment e. The secret is in the
// result only.
func (c *Client) CreateSdkKey(ctx context.Context, p, e string, in SdkKeyCreate) (*SdkKeyCreated, error) {
	var out SdkKeyCreated
	if err := c.Do(ctx, http.MethodPost, projectPath(p, "environments", e, "sdk-keys"), nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// RevokeSdkKey revokes SDK key id of environment e.
func (c *Client) RevokeSdkKey(ctx context.Context, p, e, id string) error {
	return c.Do(ctx, http.MethodDelete, projectPath(p, "environments", e, "sdk-keys", id), nil, nil, nil)
}

// Me returns the caller of the token.
func (c *Client) Me(ctx context.Context) (*Me, error) {
	var out Me
	if err := c.Do(ctx, http.MethodGet, "/api/auth/me", nil, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}
