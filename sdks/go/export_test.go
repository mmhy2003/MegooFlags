package megooflags

import (
	"time"

	"github.com/mmhy2003/MegooFlags/sdks/go/internal/delivery"
)

// The tests shorten Sync's timings so reconnects take milliseconds.
func init() {
	tuneSync = func(c *delivery.SyncConfig) {
		c.RetryBase = 20 * time.Millisecond
		c.RetryMax = 200 * time.Millisecond
		c.StableAfter = 100 * time.Millisecond
		c.DeadTimeout = 5 * time.Second
	}
}
