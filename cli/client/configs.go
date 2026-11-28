package client

import (
	"context"
	"net/http"
)

// ListFlagStates returns the state of flag f in every environment.
func (c *Client) ListFlagStates(ctx context.Context, p, f string) ([]FlagEnvironmentState, error) {
	var out []FlagEnvironmentState
	if err := c.Do(ctx, http.MethodGet, projectPath(p, "flags", f, "environments"), nil, nil, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// GetFlagConfig returns the targeting of flag f in environment e.
func (c *Client) GetFlagConfig(ctx context.Context, p, f, e string) (*FlagConfig, error) {
	var out FlagConfig
	if err := c.Do(ctx, http.MethodGet, projectPath(p, "flags", f, "environments", e), nil, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// PutFlagConfig replaces the targeting of flag f in environment e.
func (c *Client) PutFlagConfig(ctx context.Context, p, f, e string, in FlagConfigIn) (*FlagConfig, error) {
	var out FlagConfig
	if err := c.Do(ctx, http.MethodPut, projectPath(p, "flags", f, "environments", e), nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Toggle turns flag f on or off in environment e. With a version, the API
// answers version_conflict when the config has moved on.
func (c *Client) Toggle(ctx context.Context, p, f, e string, enabled bool, version *int) (*FlagEnvironmentState, error) {
	in := struct {
		Enabled bool `json:"enabled"`
		Version *int `json:"version,omitempty"`
	}{enabled, version}
	return c.state(ctx, projectPath(p, "flags", f, "environments", e, "toggle"), in)
}

// Kill turns flag f off in environment e, bypassing approvals.
func (c *Client) Kill(ctx context.Context, p, f, e string) (*FlagEnvironmentState, error) {
	return c.state(ctx, projectPath(p, "flags", f, "environments", e, "kill"), nil)
}

// CopyFlag copies the config of flag f between environments and returns the
// states of the target environments.
func (c *Client) CopyFlag(ctx context.Context, p, f string, in CopyIn) ([]FlagEnvironmentState, error) {
	var out []FlagEnvironmentState
	if err := c.Do(ctx, http.MethodPost, projectPath(p, "flags", f, "copy"), nil, in, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// CreateChangeRequest proposes a change for approval in project p.
func (c *Client) CreateChangeRequest(ctx context.Context, p string, in ChangeRequestCreate) (*ChangeRequest, error) {
	var out ChangeRequest
	if err := c.Do(ctx, http.MethodPost, projectPath(p, "change-requests"), nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *Client) state(ctx context.Context, path string, in any) (*FlagEnvironmentState, error) {
	var out FlagEnvironmentState
	if err := c.Do(ctx, http.MethodPost, path, nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}
