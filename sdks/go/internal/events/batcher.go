package events

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/transport"
)

// maxBodyBytes is the largest events body the server accepts.
const maxBodyBytes = 1_000_000

// sendTimeout bounds one POST.
const sendTimeout = 10 * time.Second

// Config configures a Batcher. URL is the full events URL
// (<base>/sdk/v1/events). FlushInterval is already clamped by the provider.
// Zero sizes and timings, a nil Client, Logger, OnFatal and Now get defaults.
type Config struct {
	URL, Key, UserAgent string
	Client              *http.Client
	Logger              *slog.Logger
	FlushInterval       time.Duration // 30s
	QueueSize           int           // 10000
	DedupSize           int           // 10000
	MaxPending          int           // 10000
	MaxItems            int           // 1000
	SendContext         bool
	OnFatal             func(error)
	Now                 func() time.Time
	RetryBase, RetryMax time.Duration // 2s, 60s
}

// Batcher aggregates evaluations and posts them to the events endpoint from
// one background goroutine, which alone owns the aggregator.
type Batcher struct {
	cfg     Config
	in      chan Evaluation
	agg     *aggregator
	backoff transport.Backoff

	dropped    atomic.Uint64 // queue overflows and records after Stop or Shutdown
	aggDropped atomic.Uint64 // the aggregator's drops, published by the loop

	ctx    context.Context // cancelled by Stop: aborts a send in flight
	cancel context.CancelFunc

	done        chan struct{} // closed by Stop or Shutdown: Record drops from then on
	doneOnce    sync.Once
	stopCh      chan struct{}
	stopOnce    sync.Once
	shutdownCh  chan struct{}
	shutdownCtx context.Context // written before shutdownCh is closed
	shutOnce    sync.Once
	shutdownErr error // written by the loop before loopDone is closed
	loopDone    chan struct{}

	startOnce sync.Once
	running   bool // set inside startOnce

	fatalOnce   sync.Once
	nextAttempt time.Time // loop only
	stopped     bool      // loop only: a fatal status stopped sending
}

// New applies the defaults to cfg. It does not start the goroutine.
func New(cfg Config) *Batcher {
	if cfg.Client == nil {
		cfg.Client = &http.Client{}
	}
	if cfg.Logger == nil {
		cfg.Logger = slog.New(slog.DiscardHandler)
	}
	if cfg.FlushInterval <= 0 {
		cfg.FlushInterval = 30 * time.Second
	}
	if cfg.QueueSize <= 0 {
		cfg.QueueSize = 10000
	}
	if cfg.DedupSize <= 0 {
		cfg.DedupSize = 10000
	}
	if cfg.MaxPending <= 0 {
		cfg.MaxPending = 10000
	}
	if cfg.MaxItems <= 0 {
		cfg.MaxItems = 1000
	}
	if cfg.OnFatal == nil {
		cfg.OnFatal = func(error) {}
	}
	if cfg.Now == nil {
		cfg.Now = time.Now
	}
	if cfg.RetryBase <= 0 {
		cfg.RetryBase = 2 * time.Second
	}
	if cfg.RetryMax <= 0 {
		cfg.RetryMax = 60 * time.Second
	}
	b := &Batcher{
		cfg:        cfg,
		in:         make(chan Evaluation, cfg.QueueSize),
		agg:        newAggregator(cfg.DedupSize, cfg.MaxPending, cfg.SendContext),
		backoff:    transport.Backoff{Base: cfg.RetryBase, Max: cfg.RetryMax},
		done:       make(chan struct{}),
		stopCh:     make(chan struct{}),
		shutdownCh: make(chan struct{}),
		loopDone:   make(chan struct{}),
	}
	b.ctx, b.cancel = context.WithCancel(context.Background())
	return b
}

// Start runs the background goroutine. It is a no-op when called twice or
// after Stop or Shutdown.
func (b *Batcher) Start() {
	b.startOnce.Do(func() {
		b.running = true
		go b.loop()
	})
}

// Record queues ev without blocking. A full queue, or a Batcher that was
// stopped or shut down, drops ev and counts the drop.
func (b *Batcher) Record(ev Evaluation) {
	select {
	case <-b.done:
		b.dropped.Add(1)
		return
	default:
	}
	select {
	case b.in <- ev:
	default:
		b.dropped.Add(1)
	}
}

// Dropped returns how many evaluations or exposures were dropped.
func (b *Batcher) Dropped() uint64 {
	return b.dropped.Load() + b.aggDropped.Load()
}

// Shutdown sends what is buffered until ctx ends, then stops. It returns
// ctx.Err() when ctx ends first. It is idempotent.
func (b *Batcher) Shutdown(ctx context.Context) error {
	if !b.started() {
		b.closeDone()
		return nil
	}
	b.shutOnce.Do(func() {
		b.closeDone()
		b.shutdownCtx = ctx
		close(b.shutdownCh)
	})
	select {
	case <-b.loopDone:
		return b.shutdownErr
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Stop stops without sending what is buffered and waits for the goroutine.
// It is idempotent.
func (b *Batcher) Stop() {
	b.signalStop()
	if b.started() {
		<-b.loopDone
	}
}

// started reports whether Start ran, and prevents a later Start.
func (b *Batcher) started() bool {
	b.startOnce.Do(func() {})
	return b.running
}

func (b *Batcher) closeDone() { b.doneOnce.Do(func() { close(b.done) }) }

// signalStop is Stop without the wait, so the loop can call it.
func (b *Batcher) signalStop() {
	b.stopOnce.Do(func() {
		b.closeDone()
		b.cancel()
		close(b.stopCh)
	})
}

func (b *Batcher) loop() {
	defer close(b.loopDone)
	ticker := time.NewTicker(b.cfg.FlushInterval)
	defer ticker.Stop()
	for !b.stopped {
		select {
		case ev := <-b.in:
			b.add(ev)
			if e, c := b.agg.pending(); e >= b.cfg.MaxItems || c >= b.cfg.MaxItems {
				b.flush(b.ctx)
			}
		case <-ticker.C:
			b.flush(b.ctx)
		case <-b.shutdownCh:
			b.drain()
			return
		case <-b.stopCh:
			return
		}
	}
}

func (b *Batcher) add(ev Evaluation) {
	b.agg.add(ev)
	b.aggDropped.Store(b.agg.dropped)
}

// drain empties the queue, then sends everything until the shutdown context
// ends, waiting out a retry delay when it fits.
func (b *Batcher) drain() {
	ctx, cancel := context.WithCancel(b.shutdownCtx)
	defer cancel()
	defer context.AfterFunc(b.ctx, cancel)()
queue:
	for {
		select {
		case ev := <-b.in:
			b.add(ev)
		default:
			break queue
		}
	}
	for !b.stopped {
		if e, c := b.agg.pending(); e == 0 && c == 0 {
			return
		}
		wait := b.nextAttempt.Sub(b.cfg.Now())
		if ctx.Err() != nil || (wait > 0 && transport.Sleep(ctx, wait) != nil) {
			b.shutdownErr = b.shutdownCtx.Err()
			return
		}
		b.flush(ctx)
	}
}

// flush sends pending items in batches of at most MaxItems per list. It does
// nothing before nextAttempt and stops at the first failure.
func (b *Batcher) flush(ctx context.Context) {
	if b.cfg.Now().Before(b.nextAttempt) {
		return
	}
	for !b.stopped {
		if e, c := b.agg.pending(); e == 0 && c == 0 {
			return
		}
		unsent, retry := b.send(ctx, b.agg.take(b.cfg.MaxItems))
		if retry {
			b.agg.restore(unsent)
			return
		}
	}
}

// send posts batch, splitting it when it is too large. When it returns
// retry, unsent is the part that was not delivered and must be kept.
func (b *Batcher) send(ctx context.Context, batch Batch) (unsent Batch, retry bool) {
	body, err := json.Marshal(batch)
	if err != nil {
		b.cfg.Logger.Error("megooflags: cannot encode events; dropping the batch", "error", err)
		return Batch{}, false
	}
	if len(body) > maxBodyBytes {
		return b.split(ctx, batch, "the body is over 1 MB")
	}

	reqCtx, cancel := context.WithTimeout(ctx, sendTimeout)
	defer cancel()
	req, err := transport.NewRequest(reqCtx, http.MethodPost, b.cfg.URL, b.cfg.Key, b.cfg.UserAgent, body)
	if err != nil {
		b.cfg.Logger.Error("megooflags: cannot build the events request; dropping the batch", "error", err)
		return Batch{}, false
	}
	resp, err := b.cfg.Client.Do(req)
	if err != nil {
		b.retryIn(b.backoff.Next())
		b.cfg.Logger.Debug("megooflags: sending events failed; will retry", "error", err)
		return batch, true
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
	_ = resp.Body.Close()

	err = transport.StatusError(resp, b.cfg.Now())
	var fatal *transport.FatalError
	var limited *transport.RetryAfterError
	var httpErr *transport.HTTPError
	switch {
	case err == nil:
		b.backoff.Reset()
		return Batch{}, false
	case errors.As(err, &fatal):
		b.stopped = true
		b.fatalOnce.Do(func() { b.cfg.OnFatal(err) })
		b.signalStop()
		return Batch{}, false
	case errors.As(err, &limited):
		b.retryIn(max(limited.After, b.backoff.Next()))
		b.cfg.Logger.Debug("megooflags: events rate limited; will retry", "retry_after", limited.After)
		return batch, true
	case errors.As(err, &httpErr) && httpErr.Status == http.StatusRequestEntityTooLarge:
		return b.split(ctx, batch, "the server refused the body as too large")
	case errors.As(err, &httpErr) && httpErr.Status >= 500:
		b.retryIn(b.backoff.Next())
		b.cfg.Logger.Debug("megooflags: sending events failed; will retry", "status", httpErr.Status)
		return batch, true
	default:
		b.cfg.Logger.Warn("megooflags: the server refused an events batch; dropping it",
			"error", err, "exposures", len(batch.Exposures), "counts", len(batch.Counts))
		return Batch{}, false
	}
}

// split sends batch as two halves. A single item cannot be split and is
// dropped. When the first half must be retried, the second is kept unsent.
func (b *Batcher) split(ctx context.Context, batch Batch, why string) (unsent Batch, retry bool) {
	n := len(batch.Exposures) + len(batch.Counts)
	if n <= 1 {
		b.cfg.Logger.Warn("megooflags: an events item is too large; dropping it", "reason", why)
		return Batch{}, false
	}
	first, second := halve(batch)
	if unsent, retry := b.send(ctx, first); retry {
		return Batch{
			Exposures: append(unsent.Exposures, second.Exposures...),
			Counts:    append(unsent.Counts, second.Counts...),
		}, true
	}
	return b.send(ctx, second)
}

// halve splits batch into two batches with half of its items each, taking
// exposures first. Both halves have non-nil lists.
func halve(batch Batch) (first, second Batch) {
	h := (len(batch.Exposures) + len(batch.Counts)) / 2
	ne := min(h, len(batch.Exposures))
	nc := h - ne
	first = Batch{
		Exposures: append([]Exposure{}, batch.Exposures[:ne]...),
		Counts:    append([]Count{}, batch.Counts[:nc]...),
	}
	second = Batch{
		Exposures: append([]Exposure{}, batch.Exposures[ne:]...),
		Counts:    append([]Count{}, batch.Counts[nc:]...),
	}
	return first, second
}

func (b *Batcher) retryIn(d time.Duration) { b.nextAttempt = b.cfg.Now().Add(d) }
