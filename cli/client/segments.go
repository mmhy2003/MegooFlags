package client

import (
	"context"
	"net/http"
)

// ListSegments returns the segments of project p, without their configs.
func (c *Client) ListSegments(ctx context.Context, p string) ([]Segment, error) {
	return ListAll[Segment](ctx, c, projectPath(p, "segments"), nil)
}

// GetSegment returns segment s of project p with its config.
func (c *Client) GetSegment(ctx context.Context, p, s string) (*Segment, error) {
	return c.segment(ctx, http.MethodGet, projectPath(p, "segments", s), nil)
}

// CreateSegment creates an empty segment in project p.
func (c *Client) CreateSegment(ctx context.Context, p string, in SegmentCreate) (*Segment, error) {
	return c.segment(ctx, http.MethodPost, projectPath(p, "segments"), in)
}

// UpdateSegment changes the name and description of segment s.
func (c *Client) UpdateSegment(ctx context.Context, p, s string, in SegmentUpdate) (*Segment, error) {
	return c.segment(ctx, http.MethodPatch, projectPath(p, "segments", s), in)
}

// PutSegmentConfig replaces the included, excluded and rules of segment s.
func (c *Client) PutSegmentConfig(ctx context.Context, p, s string, in SegmentConfig) (*Segment, error) {
	return c.segment(ctx, http.MethodPut, projectPath(p, "segments", s), in)
}

// DeleteSegment deletes segment s of project p.
func (c *Client) DeleteSegment(ctx context.Context, p, s string) error {
	return c.Do(ctx, http.MethodDelete, projectPath(p, "segments", s), nil, nil, nil)
}

func (c *Client) segment(ctx context.Context, method, path string, in any) (*Segment, error) {
	var out Segment
	if err := c.Do(ctx, method, path, nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}
