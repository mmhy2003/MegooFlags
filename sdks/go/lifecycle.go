package megooflags

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"time"

	"github.com/open-feature/go-sdk/openfeature"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/delivery"
)

// shutdownTimeout bounds the final events flush.
const shutdownTimeout = 5 * time.Second

// Init starts the event sender and, unless Offline, the ruleset sync. With a
// ruleset from the bootstrap file or the cache it returns at once and the
// network refreshes it in the background. Otherwise it waits up to
// InitTimeout for the first ruleset; on timeout the provider is in the error
// state, keeps retrying and emits PROVIDER_READY when a ruleset arrives.
func (p *Provider) Init(openfeature.EvaluationContext) error {
	if p.cfgErr != nil {
		return &openfeature.ProviderInitError{ErrorCode: openfeature.ProviderFatalCode, Message: p.cfgErr.Error()}
	}
	select {
	case <-p.done:
		return errors.New("megooflags: the provider was shut down")
	default:
	}
	p.startOnce.Do(func() {
		if p.batcher != nil {
			p.batcher.Start()
		}
		if !p.cfg.Offline {
			p.sync.Start()
		}
	})
	if p.store.Load() != nil {
		return nil
	}
	if p.cfg.Offline {
		return errors.New("megooflags: offline, and neither the bootstrap file nor the cache gave a valid ruleset")
	}
	timer := time.NewTimer(p.cfg.InitTimeout)
	defer timer.Stop()
	select {
	case <-p.firstReady:
		return nil
	case <-p.fatalCh:
		return &openfeature.ProviderInitError{ErrorCode: openfeature.ProviderFatalCode, Message: p.fatalErr.Error()}
	case <-p.done:
		return errors.New("megooflags: the provider was shut down")
	case <-timer.C:
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	select {
	case <-p.firstReady: // arrived together with the timeout
		return nil
	default:
	}
	p.initTimedOut = true
	return fmt.Errorf("megooflags: no ruleset within %s", p.cfg.InitTimeout)
}

// Shutdown stops the sync and sends the buffered events within 5 s. It is
// idempotent. The last ruleset keeps being served; the event channel is never
// closed.
func (p *Provider) Shutdown() {
	p.shutdownOnce.Do(func() {
		close(p.done)
		if p.sync != nil {
			p.sync.Stop()
		}
		if p.batcher != nil {
			ctx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
			defer cancel()
			if err := p.batcher.Shutdown(ctx); err != nil {
				p.cfg.Logger.Warn("megooflags: could not send all events before shutdown", "error", err)
			}
		}
	})
}

// EventChannel returns the channel of OpenFeature provider events.
func (p *Provider) EventChannel() <-chan openfeature.Event { return p.events }

// onChange runs after each Apply. The first ruleset makes the provider ready;
// a later one is a configuration change.
func (p *Provider) onChange(old, n *delivery.Snapshot) {
	if old == nil {
		p.mu.Lock()
		p.readyOnce.Do(func() { close(p.firstReady) })
		timedOut := p.initTimedOut
		p.mu.Unlock()
		if timedOut {
			p.emit(openfeature.ProviderReady, openfeature.ProviderEventDetails{Message: "megooflags: ruleset loaded"})
		}
		return
	}
	p.emit(openfeature.ProviderConfigChange, openfeature.ProviderEventDetails{
		Message:     "megooflags: ruleset updated",
		FlagChanges: flagChanges(old, n),
	})
}

// onStatus maps Sync's delivery status to OpenFeature events: stale while
// disconnected after being ready, and ready again on reconnect.
func (p *Provider) onStatus(st delivery.Status, err error) {
	switch st {
	case delivery.StatusStale:
		p.mu.Lock()
		emit := !p.stale && p.store.Load() != nil && !p.fatal.Load()
		p.stale = p.stale || emit
		p.mu.Unlock()
		if emit {
			msg := "megooflags: disconnected; serving the last ruleset"
			if err != nil {
				msg += ": " + err.Error()
			}
			p.emit(openfeature.ProviderStale, openfeature.ProviderEventDetails{Message: msg})
		}
	case delivery.StatusReady:
		p.mu.Lock()
		emit := p.stale
		p.stale = false
		p.mu.Unlock()
		if emit {
			p.emit(openfeature.ProviderReady, openfeature.ProviderEventDetails{Message: "megooflags: reconnected"})
		}
	case delivery.StatusFatal:
		p.onFatal(err)
	}
}

// onFatal handles a refused SDK key once: it stops event sending and the
// sync and emits PROVIDER_ERROR with the fatal code. The last ruleset keeps
// being served.
func (p *Provider) onFatal(err error) {
	p.fatalOnce.Do(func() {
		if err == nil {
			err = errors.New("megooflags: the SDK key was refused")
		}
		p.fatalErr = err
		p.fatal.Store(true)
		close(p.fatalCh)
		p.cfg.Logger.Error("megooflags: the server refused the SDK key; serving the last ruleset", "error", err)
		if p.batcher != nil {
			p.batcher.Stop()
		}
		go p.sync.Stop() // onFatal may run on Sync's goroutine, which Stop waits for
		p.emit(openfeature.ProviderError, openfeature.ProviderEventDetails{
			Message:   err.Error(),
			ErrorCode: openfeature.ProviderFatalCode,
		})
	})
}

// emit sends an event; it never blocks after Shutdown.
func (p *Provider) emit(t openfeature.EventType, d openfeature.ProviderEventDetails) {
	ev := openfeature.Event{ProviderName: p.Metadata().Name, EventType: t, ProviderEventDetails: d}
	select {
	case <-p.done:
		return
	default:
	}
	select {
	case p.events <- ev:
	case <-p.done:
	}
}

// flagChanges returns the sorted keys of the flags that were added, removed,
// or have a different version.
func flagChanges(old, n *delivery.Snapshot) []string {
	var keys []string
	for k, f := range n.Ruleset.Flags {
		if of, ok := old.Ruleset.Flags[k]; !ok || of.Version != f.Version {
			keys = append(keys, k)
		}
	}
	for k := range old.Ruleset.Flags {
		if _, ok := n.Ruleset.Flags[k]; !ok {
			keys = append(keys, k)
		}
	}
	slices.Sort(keys)
	return keys
}
