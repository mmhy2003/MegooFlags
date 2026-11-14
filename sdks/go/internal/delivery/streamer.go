package delivery

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"sync/atomic"
	"time"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/transport"
)

// errDeadConnection reports a stream that sent nothing, not even a ping, for
// DeadTimeout.
var errDeadConnection = errors.New("megooflags: stream sent nothing within the dead-connection timeout")

// StreamerConfig configures a Streamer. Zero durations, a nil Backoff, Client
// or Logger, and nil callbacks get defaults.
type StreamerConfig struct {
	URL, Key, UserAgent string
	Client              *http.Client // must not set a Timeout: the stream is long-lived
	Logger              *slog.Logger
	LastEventID         func() string
	OnPut               func(raw []byte, id string) bool // false: invalid ruleset (the previous one stays)
	OnConnected         func()
	OnDisconnected      func(err error, failures int)
	DeadTimeout         time.Duration      // 45s
	StableAfter         time.Duration      // 60s
	Backoff             *transport.Backoff // Base 3s (replaced by the server's `retry:`), Max 60s
}

// Streamer is the SSE client for GET /sdk/v1/stream. It reconnects with
// backoff until the context ends or the server refuses the key.
type Streamer struct {
	cfg StreamerConfig
}

// NewStreamer applies the defaults to cfg.
func NewStreamer(cfg StreamerConfig) *Streamer {
	if cfg.Client == nil {
		cfg.Client = &http.Client{}
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.New(slog.DiscardHandler)
	}
	if cfg.LastEventID == nil {
		cfg.LastEventID = func() string { return "" }
	}
	if cfg.OnPut == nil {
		cfg.OnPut = func([]byte, string) bool { return true }
	}
	if cfg.OnConnected == nil {
		cfg.OnConnected = func() {}
	}
	if cfg.OnDisconnected == nil {
		cfg.OnDisconnected = func(error, int) {}
	}
	if cfg.DeadTimeout <= 0 {
		cfg.DeadTimeout = 45 * time.Second
	}
	if cfg.StableAfter <= 0 {
		cfg.StableAfter = 60 * time.Second
	}
	if cfg.Backoff == nil {
		cfg.Backoff = &transport.Backoff{Base: 3 * time.Second, Max: 60 * time.Second}
	}
	return &Streamer{cfg: cfg}
}

// Run streams until ctx ends, returning ctx.Err(), or until the server refuses
// the key, returning a *transport.FatalError. It must not be called
// concurrently.
func (s *Streamer) Run(ctx context.Context) error {
	failures := 0
	for {
		connectedAt, err := s.connect(ctx)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		var fatal *transport.FatalError
		if errors.As(err, &fatal) {
			return fatal
		}
		if !connectedAt.IsZero() && time.Since(connectedAt) >= s.cfg.StableAfter {
			s.cfg.Backoff.Reset()
			failures = 0
		}
		failures++
		wait := s.cfg.Backoff.Next()
		var ra *transport.RetryAfterError
		if errors.As(err, &ra) {
			wait = max(ra.After, wait)
		}
		s.cfg.Logger.Debug("megooflags: stream disconnected", "error", err, "failures", failures, "retry_in", wait)
		s.cfg.OnDisconnected(err, failures)
		if err := transport.Sleep(ctx, wait); err != nil {
			return err
		}
	}
}

// connect opens one stream and reads it until it ends. connectedAt is zero when
// the server did not answer 200.
func (s *Streamer) connect(ctx context.Context) (connectedAt time.Time, err error) {
	connCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	req, err := transport.NewRequest(connCtx, http.MethodGet, s.cfg.URL, s.cfg.Key, s.cfg.UserAgent, nil)
	if err != nil {
		return time.Time{}, err
	}
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Cache-Control", "no-cache")
	if id := s.cfg.LastEventID(); id != "" {
		req.Header.Set("Last-Event-ID", id)
	}
	resp, err := s.cfg.Client.Do(req)
	if err != nil {
		return time.Time{}, err
	}
	defer resp.Body.Close()
	if err := transport.StatusError(resp, time.Now()); err != nil {
		return time.Time{}, err
	}
	if resp.StatusCode != http.StatusOK {
		return time.Time{}, &transport.HTTPError{Status: resp.StatusCode}
	}

	connectedAt = time.Now()
	s.cfg.OnConnected()
	var dead atomic.Bool
	t := time.AfterFunc(s.cfg.DeadTimeout, func() {
		dead.Store(true)
		cancel()
	})
	defer t.Stop()

	err = readSSE(resp.Body,
		func() { t.Reset(s.cfg.DeadTimeout) },
		func(d time.Duration) { s.cfg.Backoff.Base = d },
		s.onEvent,
	)
	if dead.Load() && ctx.Err() == nil {
		return connectedAt, errDeadConnection
	}
	return connectedAt, err
}

func (s *Streamer) onEvent(ev sseEvent) error {
	switch ev.name {
	case "put":
		if !s.cfg.OnPut([]byte(ev.data), ev.id) {
			s.cfg.Logger.Warn("megooflags: stream sent an invalid ruleset; keeping the previous one", "event_id", ev.id)
		}
	case "revoked":
		return &transport.FatalError{Reason: "revoked"}
	}
	return nil
}
