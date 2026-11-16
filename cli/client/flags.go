package client

import (
	"context"
	"net/http"
	"net/url"
)

// ListFlagsOptions filters ListFlags.
type ListFlagsOptions struct {
	// Archived lists archived flags instead of active ones.
	Archived bool
	// Query matches the key or name.
	Query string
	// Tags keeps flags that have any of the tags.
	Tags []string
}

// ListFlags returns the flags of project p with their state per environment.
func (c *Client) ListFlags(ctx context.Context, p string, opts ListFlagsOptions) ([]FlagSummary, error) {
	q := url.Values{}
	if opts.Archived {
		q.Set("archived", "true")
	}
	if opts.Query != "" {
		q.Set("q", opts.Query)
	}
	for _, t := range opts.Tags {
		q.Add("tag", t)
	}
	return ListAll[FlagSummary](ctx, c, projectPath(p, "flags"), q)
}

// GetFlag returns flag f of project p with its variations.
func (c *Client) GetFlag(ctx context.Context, p, f string) (*Flag, error) {
	return c.flag(ctx, http.MethodGet, projectPath(p, "flags", f), nil)
}

// CreateFlag creates a flag in project p.
func (c *Client) CreateFlag(ctx context.Context, p string, in FlagCreate) (*Flag, error) {
	return c.flag(ctx, http.MethodPost, projectPath(p, "flags"), in)
}

// UpdateFlag changes the details of flag f.
func (c *Client) UpdateFlag(ctx context.Context, p, f string, in FlagUpdate) (*Flag, error) {
	return c.flag(ctx, http.MethodPatch, projectPath(p, "flags", f), in)
}

// SetLifecycle marks flag f permanent, so it is never stale, or clears the
// mark.
func (c *Client) SetLifecycle(ctx context.Context, p, f string, permanent bool) (*Flag, error) {
	in := struct {
		Permanent bool `json:"permanent"`
	}{permanent}
	return c.flag(ctx, http.MethodPut, projectPath(p, "flags", f, "lifecycle"), in)
}

// ArchiveFlag archives flag f.
func (c *Client) ArchiveFlag(ctx context.Context, p, f string) (*Flag, error) {
	return c.flag(ctx, http.MethodPost, projectPath(p, "flags", f, "archive"), nil)
}

// RestoreFlag restores archived flag f.
func (c *Client) RestoreFlag(ctx context.Context, p, f string) (*Flag, error) {
	return c.flag(ctx, http.MethodPost, projectPath(p, "flags", f, "restore"), nil)
}

// DeleteFlag deletes flag f; the API requires it to be archived first.
func (c *Client) DeleteFlag(ctx context.Context, p, f string) error {
	return c.Do(ctx, http.MethodDelete, projectPath(p, "flags", f), nil, nil, nil)
}

// CreateVariation adds a variation to flag f.
func (c *Client) CreateVariation(ctx context.Context, p, f string, in VariationIn) (*Variation, error) {
	return c.variation(ctx, http.MethodPost, projectPath(p, "flags", f, "variations"), in)
}

// UpdateVariation changes variation v of flag f.
func (c *Client) UpdateVariation(ctx context.Context, p, f, v string, in VariationUpdate) (*Variation, error) {
	return c.variation(ctx, http.MethodPatch, projectPath(p, "flags", f, "variations", v), in)
}

// DeleteVariation removes variation v from flag f.
func (c *Client) DeleteVariation(ctx context.Context, p, f, v string) error {
	return c.Do(ctx, http.MethodDelete, projectPath(p, "flags", f, "variations", v), nil, nil, nil)
}

func (c *Client) flag(ctx context.Context, method, path string, in any) (*Flag, error) {
	var out Flag
	if err := c.Do(ctx, method, path, nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *Client) variation(ctx context.Context, method, path string, in any) (*Variation, error) {
	var out Variation
	if err := c.Do(ctx, method, path, nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}
