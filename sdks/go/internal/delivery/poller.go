package delivery

import (
	"context"
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/transport"
)

// maxRulesetBytes caps a downloaded ruleset.
const maxRulesetBytes = 256 << 20

// errInvalidRuleset reports a downloaded ruleset that OnRuleset rejected; the
// previous ruleset stays.
var errInvalidRuleset = errors.New("megooflags: invalid ruleset")

// PollerConfig configures a Poller. A zero Timeout, a nil Client and nil
// callbacks get defaults.
type PollerConfig struct {
	URL, Key, UserAgent string
	Client              *http.Client
	Timeout             time.Duration                      // 10s
	ETag                func() string                      // sent as If-None-Match when non-empty
	OnRuleset           func(raw []byte, etag string) bool // false: invalid ruleset (the previous one stays)
}

// Poller downloads the ruleset from GET /sdk/v1/ruleset. The poll loop and its
// interval belong to Sync.
type Poller struct {
	cfg PollerConfig
}

// NewPoller applies the defaults to cfg.
func NewPoller(cfg PollerConfig) *Poller {
	if cfg.Client == nil {
		cfg.Client = &http.Client{}
	}
	if cfg.Timeout <= 0 {
		cfg.Timeout = 10 * time.Second
	}
	if cfg.ETag == nil {
		cfg.ETag = func() string { return "" }
	}
	if cfg.OnRuleset == nil {
		cfg.OnRuleset = func([]byte, string) bool { return true }
	}
	return &Poller{cfg: cfg}
}

// Poll downloads the ruleset once. A 304 returns nil and keeps what we have; a
// 200 hands the body to OnRuleset. Any other status returns
// transport.StatusError, and a network failure returns its error.
func (p *Poller) Poll(ctx context.Context) error {
	reqCtx, cancel := context.WithTimeout(ctx, p.cfg.Timeout)
	defer cancel()
	req, err := transport.NewRequest(reqCtx, http.MethodGet, p.cfg.URL, p.cfg.Key, p.cfg.UserAgent, nil)
	if err != nil {
		return err
	}
	if etag := p.cfg.ETag(); etag != "" {
		req.Header.Set("If-None-Match", etag)
	}
	resp, err := p.cfg.Client.Do(req)
	if err != nil {
		return err
	}
	defer func() {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, maxRulesetBytes))
		_ = resp.Body.Close()
	}()
	if resp.StatusCode == http.StatusNotModified {
		return nil
	}
	if err := transport.StatusError(resp, time.Now()); err != nil {
		return err
	}
	if resp.StatusCode != http.StatusOK {
		return &transport.HTTPError{Status: resp.StatusCode}
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, maxRulesetBytes))
	if err != nil {
		return err
	}
	if !p.cfg.OnRuleset(raw, resp.Header.Get("ETag")) {
		return errInvalidRuleset
	}
	return nil
}
