package delivery

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"sync"
	"time"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/engine"
	"github.com/mmhy2003/MegooFlags/sdks/go/internal/transport"
)

// Status is the delivery state Sync reports through OnStatus.
type Status int

const (
	StatusReady Status = iota // the stream is connected, or (DisableStream) the last poll succeeded
	StatusStale               // the stream or the poll failed; the last ruleset is still served
	StatusFatal               // the server refused the key; Sync has stopped
)

func (s Status) String() string {
	switch s {
	case StatusReady:
		return "ready"
	case StatusStale:
		return "stale"
	case StatusFatal:
		return "fatal"
	}
	return "unknown"
}

// SyncConfig configures a Sync. PollInterval is used as given (the provider
// clamps it); zero means 30s. Zero timings, nil Store, Client, Logger and nil
// callbacks get defaults. A nil Cache saves nothing.
type SyncConfig struct {
	BaseURL, Key, UserAgent string
	Client                  *http.Client // must not set a Timeout: the stream is long-lived
	Logger                  *slog.Logger

	DisableStream     bool
	PollInterval      time.Duration
	PollAfterFailures int // stream failures in a row before polling starts; default 2

	Store *Store
	Cache *Cache

	OnChange func(old, new *Snapshot)
	OnStatus func(Status, error)

	DeadTimeout, StableAfter, RetryBase, RetryMax time.Duration // zero → 45s, 60s, 3s, 60s
}

// Sync keeps the Store current: it runs the stream and polls only while the
// stream keeps failing (or always, with DisableStream). The callbacks run on
// Sync's goroutines and must not call Stop.
type Sync struct {
	cfg      SyncConfig
	streamer *Streamer
	poller   *Poller

	ctx    context.Context
	cancel context.CancelFunc
	wg     sync.WaitGroup
	stop   sync.Once

	mu         sync.Mutex
	pollCancel context.CancelFunc // non-nil while the poll loop runs
	nextPoll   time.Time          // the earliest next poll; kept across poll-loop restarts

	statusMu sync.Mutex
	fatal    bool
}

// NewSync applies the defaults to cfg. It does not touch the network.
func NewSync(cfg SyncConfig) *Sync {
	if cfg.Client == nil {
		cfg.Client = &http.Client{}
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.New(slog.DiscardHandler)
	}
	if cfg.PollInterval <= 0 {
		cfg.PollInterval = 30 * time.Second
	}
	if cfg.PollAfterFailures <= 0 {
		cfg.PollAfterFailures = 2
	}
	if cfg.Store == nil {
		cfg.Store = &Store{}
	}
	if cfg.OnChange == nil {
		cfg.OnChange = func(*Snapshot, *Snapshot) {}
	}
	if cfg.OnStatus == nil {
		cfg.OnStatus = func(Status, error) {}
	}
	if cfg.RetryBase <= 0 {
		cfg.RetryBase = 3 * time.Second
	}
	if cfg.RetryMax <= 0 {
		cfg.RetryMax = 60 * time.Second
	}
	s := &Sync{cfg: cfg}
	s.ctx, s.cancel = context.WithCancel(context.Background())
	s.poller = NewPoller(PollerConfig{
		URL:       cfg.BaseURL + "/sdk/v1/ruleset",
		Key:       cfg.Key,
		UserAgent: cfg.UserAgent,
		Client:    cfg.Client,
		ETag:      s.etag,
		OnRuleset: func(raw []byte, etag string) bool { return s.Apply(raw, etag, "poll") == nil },
	})
	s.streamer = NewStreamer(StreamerConfig{
		URL:            cfg.BaseURL + "/sdk/v1/stream",
		Key:            cfg.Key,
		UserAgent:      cfg.UserAgent,
		Client:         cfg.Client,
		Logger:         cfg.Logger,
		LastEventID:    s.etag,
		OnPut:          func(raw []byte, id string) bool { return s.Apply(raw, id, "stream") == nil },
		OnConnected:    s.onConnected,
		OnDisconnected: s.onDisconnected,
		DeadTimeout:    cfg.DeadTimeout,
		StableAfter:    cfg.StableAfter,
		Backoff:        &transport.Backoff{Base: cfg.RetryBase, Max: cfg.RetryMax},
	})
	return s
}

func (s *Sync) etag() string {
	if snap := s.cfg.Store.Load(); snap != nil {
		return snap.ETag
	}
	return ""
}

// Apply parses raw and installs it. An invalid ruleset is logged and
// returned, and the Store keeps the previous one. A ruleset from the stream or
// a poll is also saved to the Cache; a save error is only logged.
func (s *Sync) Apply(raw []byte, etag, source string) error {
	rs, err := engine.Parse(raw)
	if err != nil {
		s.cfg.Logger.Warn("megooflags: invalid ruleset; keeping the previous one", "source", source, "error", err)
		return err
	}
	n := &Snapshot{Ruleset: rs, ETag: etag, Raw: raw, Source: source}
	old := s.cfg.Store.Swap(n)
	if source == "stream" || source == "poll" {
		if err := s.cfg.Cache.Save(raw, etag); err != nil {
			s.cfg.Logger.Warn("megooflags: could not save the ruleset cache", "error", err)
		}
	}
	s.cfg.OnChange(old, n)
	return nil
}

// Start runs the stream, or only the poll loop with DisableStream. Call it
// once.
func (s *Sync) Start() {
	if s.cfg.DisableStream {
		s.startPolling()
		return
	}
	s.wg.Go(func() {
		err := s.streamer.Run(s.ctx)
		var fatal *transport.FatalError
		if errors.As(err, &fatal) {
			s.fail(fatal)
		}
	})
}

// Stop cancels the stream and the poll loop and waits for them. It is
// idempotent and safe before Start.
func (s *Sync) Stop() {
	s.stop.Do(func() {
		s.cancel()
		s.wg.Wait()
	})
}

func (s *Sync) onConnected() {
	s.stopPolling()
	s.status(StatusReady, nil)
}

func (s *Sync) onDisconnected(err error, failures int) {
	s.status(StatusStale, err)
	if failures >= s.cfg.PollAfterFailures {
		s.startPolling()
	}
}

// fail reports a refused key once and stops everything.
func (s *Sync) fail(err error) {
	s.cancel()
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	if s.fatal {
		return
	}
	s.fatal = true
	s.cfg.OnStatus(StatusFatal, err)
}

// status reports st unless Sync is stopping or has failed.
func (s *Sync) status(st Status, err error) {
	s.statusMu.Lock()
	defer s.statusMu.Unlock()
	if s.fatal || s.ctx.Err() != nil {
		return
	}
	s.cfg.OnStatus(st, err)
}

func (s *Sync) startPolling() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.pollCancel != nil || s.ctx.Err() != nil {
		return
	}
	ctx, cancel := context.WithCancel(s.ctx)
	s.pollCancel = cancel
	s.wg.Go(func() { s.pollLoop(ctx) })
}

func (s *Sync) stopPolling() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.pollCancel != nil {
		s.pollCancel()
		s.pollCancel = nil
	}
}

// reservePoll returns 0 and books the next slot when a poll may start now,
// or how long to wait. Booking under the lock keeps polls at least
// PollInterval apart even across poll-loop restarts.
func (s *Sync) reservePoll(ctx context.Context) (wait time.Duration, ok bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if ctx.Err() != nil {
		return 0, false
	}
	now := time.Now()
	if wait := s.nextPoll.Sub(now); wait > 0 {
		return wait, true
	}
	s.nextPoll = now.Add(s.cfg.PollInterval)
	return 0, true
}

func (s *Sync) pollLoop(ctx context.Context) {
	for {
		wait, ok := s.reservePoll(ctx)
		if !ok {
			return
		}
		if wait > 0 {
			if transport.Sleep(ctx, wait) != nil {
				return
			}
			continue
		}
		started := time.Now()
		err := s.poller.Poll(ctx)
		if ctx.Err() != nil {
			return
		}
		var fatal *transport.FatalError
		var ra *transport.RetryAfterError
		switch {
		case errors.As(err, &fatal):
			s.fail(fatal)
			return
		case errors.As(err, &ra):
			s.mu.Lock()
			s.nextPoll = started.Add(max(ra.After, s.cfg.PollInterval))
			s.mu.Unlock()
		}
		if err != nil {
			s.cfg.Logger.Debug("megooflags: poll failed", "error", err)
		}
		if s.cfg.DisableStream {
			if err != nil {
				s.status(StatusStale, err)
			} else {
				s.status(StatusReady, nil)
			}
		}
	}
}
