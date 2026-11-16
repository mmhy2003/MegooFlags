package client

import (
	"context"
	"net/http"
)

// ListEnvironments returns the environments of project p in sort order.
func (c *Client) ListEnvironments(ctx context.Context, p string) ([]Environment, error) {
	var out []Environment
	if err := c.Do(ctx, http.MethodGet, projectPath(p, "environments"), nil, nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// GetEnvironment returns environment e of project p.
func (c *Client) GetEnvironment(ctx context.Context, p, e string) (*Environment, error) {
	var out Environment
	if err := c.Do(ctx, http.MethodGet, projectPath(p, "environments", e), nil, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// CreateEnvironment creates an environment in project p.
func (c *Client) CreateEnvironment(ctx context.Context, p string, in EnvironmentCreate) (*Environment, error) {
	var out Environment
	if err := c.Do(ctx, http.MethodPost, projectPath(p, "environments"), nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// UpdateEnvironment changes environment e of project p.
func (c *Client) UpdateEnvironment(ctx context.Context, p, e string, in EnvironmentUpdate) (*Environment, error) {
	var out Environment
	if err := c.Do(ctx, http.MethodPatch, projectPath(p, "environments", e), nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// DeleteEnvironment deletes environment e of project p.
func (c *Client) DeleteEnvironment(ctx context.Context, p, e string) error {
	return c.Do(ctx, http.MethodDelete, projectPath(p, "environments", e), nil, nil, nil)
}
