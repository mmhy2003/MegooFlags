// Package delivery keeps the current ruleset and fills it from the bootstrap
// file, the on-disk cache, the SSE stream and the poller.
package delivery

import (
	"sync/atomic"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/engine"
)

// Snapshot is one parsed ruleset with the raw bytes and ETag it came from.
type Snapshot struct {
	Ruleset *engine.Ruleset
	ETag    string
	Raw     []byte
	Source  string // "bootstrap", "cache", "stream" or "poll"
}

// Store holds the current snapshot. Readers never lock.
type Store struct {
	p atomic.Pointer[Snapshot]
}

// Load returns the current snapshot, or nil before the first Swap.
func (s *Store) Load() *Snapshot { return s.p.Load() }

// Swap installs n and returns the snapshot it replaced.
func (s *Store) Swap(n *Snapshot) (old *Snapshot) { return s.p.Swap(n) }
