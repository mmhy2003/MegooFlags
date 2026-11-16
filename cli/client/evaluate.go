package client

import (
	"context"
	"fmt"
	"io"
	"net/http"
)

// Evaluate evaluates flags in environment e of project p for a context.
func (c *Client) Evaluate(ctx context.Context, p, e string, in EvaluateIn) (*EvaluateOut, error) {
	var out EvaluateOut
	if err := c.Do(ctx, http.MethodPost, projectPath(p, "environments", e, "evaluate"), nil, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Ruleset downloads the ruleset of environment e, byte for byte what the
// SDKs receive. When ifNoneMatch is set it is sent as If-None-Match, and a
// 304 answer returns notModified true with no body.
func (c *Client) Ruleset(ctx context.Context, p, e, ifNoneMatch string) (body []byte, etag string, notModified bool, err error) {
	var header http.Header
	if ifNoneMatch != "" {
		header = http.Header{"If-None-Match": {ifNoneMatch}}
	}
	resp, err := c.request(ctx, http.MethodGet, projectPath(p, "environments", e, "ruleset"), nil, nil, header)
	if err != nil {
		return nil, "", false, err
	}
	defer drain(resp)
	etag = resp.Header.Get("ETag")
	switch {
	case resp.StatusCode == http.StatusNotModified:
		return nil, etag, true, nil
	case resp.StatusCode < 200 || resp.StatusCode > 299:
		return nil, "", false, c.decodeError(resp)
	}
	if body, err = io.ReadAll(resp.Body); err != nil {
		return nil, "", false, fmt.Errorf("read ruleset: %w", err)
	}
	return body, etag, false, nil
}
