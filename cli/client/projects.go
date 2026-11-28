package client

import (
	"context"
	"net/http"
	"net/url"
	"strings"
)

// projectPath returns /api/projects/{p}/{rest...} with every segment escaped.
func projectPath(p string, rest ...string) string {
	var b strings.Builder
	b.WriteString("/api/projects/")
	b.WriteString(url.PathEscape(p))
	for _, s := range rest {
		b.WriteByte('/')
		b.WriteString(url.PathEscape(s))
	}
	return b.String()
}

// ListProjects returns every project the token can see.
func (c *Client) ListProjects(ctx context.Context) ([]Project, error) {
	return ListAll[Project](ctx, c, "/api/projects", nil)
}

// GetProject returns project p with its environments.
func (c *Client) GetProject(ctx context.Context, p string) (*Project, error) {
	var out Project
	if err := c.Do(ctx, http.MethodGet, projectPath(p), nil, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// CreateProject creates a project.
func (c *Client) CreateProject(ctx context.Context, in ProjectCreate) (*Project, error) {
	var out Project
	if err := c.Do(ctx, http.MethodPost, "/api/projects", nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// UpdateProject changes project p.
func (c *Client) UpdateProject(ctx context.Context, p string, in ProjectUpdate) (*Project, error) {
	var out Project
	if err := c.Do(ctx, http.MethodPatch, projectPath(p), nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// DeleteProject deletes project p and everything in it.
func (c *Client) DeleteProject(ctx context.Context, p string) error {
	return c.Do(ctx, http.MethodDelete, projectPath(p), nil, nil, nil)
}
