// Package events aggregates evaluation counts and deduplicated exposures and
// sends them to POST /sdk/v1/events in the background (spec/delivery.md §7).
package events

import (
	"container/list"
	"time"
)

// maxCount is the largest count the server accepts in one Count item.
const maxCount = 1_000_000_000

const timestampFormat = "2006-01-02T15:04:05.000Z07:00"

// Evaluation is one flag evaluation as recorded by the provider.
type Evaluation struct {
	FlagKey    string
	Variant    string
	Served     bool // false when the default was returned (error, not ready)
	Reason     string
	ContextKey string
	Context    map[string]any
	At         time.Time
}

// Exposure records that a context saw a variant.
type Exposure struct {
	FlagKey   string         `json:"flag_key"`
	Variant   *string        `json:"variant"`
	Reason    string         `json:"reason"`
	Timestamp string         `json:"timestamp"`
	Context   map[string]any `json:"context,omitempty"`
}

// Count is the number of evaluations of one flag and variant in one UTC hour.
type Count struct {
	FlagKey string  `json:"flag_key"`
	Variant *string `json:"variant"`
	Hour    string  `json:"hour"`
	Count   int64   `json:"count"`
}

// Batch is the body of POST /sdk/v1/events.
type Batch struct {
	Exposures []Exposure `json:"exposures"`
	Counts    []Count    `json:"counts"`
}

// dedupKey identifies one exposure. A context without a string key has
// ctxKey "", so anonymous contexts share one exposure per flag, variant and
// hour.
type dedupKey struct {
	ctxKey, flag, variant string
	hour                  int64
}

type countKey struct {
	flag, variant string
	served        bool
	hour          int64
}

// lru is a fixed-size set that forgets the least recently seen key.
type lru struct {
	cap   int
	ll    *list.List
	items map[dedupKey]*list.Element
}

func newLRU(capacity int) *lru {
	return &lru{cap: max(capacity, 1), ll: list.New(), items: make(map[dedupKey]*list.Element)}
}

// seen reports whether k is present and marks it as the most recent. A new
// key is inserted, evicting the oldest one above the capacity.
func (l *lru) seen(k dedupKey) bool {
	if e, ok := l.items[k]; ok {
		l.ll.MoveToFront(e)
		return true
	}
	l.items[k] = l.ll.PushFront(k)
	if l.ll.Len() > l.cap {
		oldest := l.ll.Back()
		l.ll.Remove(oldest)
		delete(l.items, oldest.Value.(dedupKey))
	}
	return false
}

// aggregator buffers exposures and hour-bucketed counts. It is not safe for
// concurrent use; the batcher owns it from one goroutine.
type aggregator struct {
	dedup       *lru
	counts      map[countKey]int64
	countOrder  []countKey
	exposures   []Exposure
	sendContext bool
	maxPending  int
	dropped     uint64
}

func newAggregator(dedupSize, maxPending int, sendContext bool) *aggregator {
	return &aggregator{
		dedup:       newLRU(dedupSize),
		counts:      make(map[countKey]int64),
		sendContext: sendContext,
		maxPending:  maxPending,
	}
}

func hourOf(t time.Time) int64 { return t.UTC().Truncate(time.Hour).Unix() }

// add counts ev and, the first time a served variant is seen for its context
// key, flag and hour, queues an exposure. An exposure beyond maxPending is
// dropped and counted in dropped; counts are never dropped.
func (a *aggregator) add(ev Evaluation) {
	hour := hourOf(ev.At)
	ck := countKey{flag: ev.FlagKey, served: ev.Served, hour: hour}
	if ev.Served {
		ck.variant = ev.Variant
	}
	a.addCount(ck, 1)

	if !ev.Served || a.dedup.seen(dedupKey{ctxKey: ev.ContextKey, flag: ev.FlagKey, variant: ev.Variant, hour: hour}) {
		return
	}
	if len(a.exposures) >= a.maxPending {
		a.dropped++
		return
	}
	variant := ev.Variant
	e := Exposure{FlagKey: ev.FlagKey, Variant: &variant, Reason: ev.Reason, Timestamp: ev.At.UTC().Format(timestampFormat)}
	if a.sendContext {
		e.Context = ev.Context
	}
	a.exposures = append(a.exposures, e)
}

// addCount adds n to bucket k, appending a new bucket to countOrder.
func (a *aggregator) addCount(k countKey, n int64) {
	if _, ok := a.counts[k]; !ok {
		a.countOrder = append(a.countOrder, k)
	}
	a.counts[k] += n
}

func (a *aggregator) pending() (exposures, counts int) {
	return len(a.exposures), len(a.countOrder)
}

// take removes and returns up to limit exposures (oldest first) and up to
// limit count items (in the order the buckets were first seen). A bucket above
// maxCount becomes several items; what does not fit stays for the next batch.
func (a *aggregator) take(limit int) Batch {
	n := min(limit, len(a.exposures))
	b := Batch{Exposures: append([]Exposure{}, a.exposures[:n]...), Counts: []Count{}}
	a.exposures = a.exposures[n:]

	done := 0
	for done < len(a.countOrder) && len(b.Counts) < limit {
		k := a.countOrder[done]
		c := Count{FlagKey: k.flag, Hour: time.Unix(k.hour, 0).UTC().Format(time.RFC3339)}
		if k.served {
			variant := k.variant
			c.Variant = &variant
		}
		for a.counts[k] > 0 && len(b.Counts) < limit {
			c.Count = min(a.counts[k], maxCount)
			a.counts[k] -= c.Count
			b.Counts = append(b.Counts, c)
		}
		if a.counts[k] > 0 {
			break
		}
		delete(a.counts, k)
		done++
	}
	a.countOrder = a.countOrder[done:]
	return b
}

// restore puts an unsent batch back in front of what is pending, so a retry
// sends it first. Its counts add to any newer count for the same bucket.
func (a *aggregator) restore(b Batch) {
	a.exposures = append(append([]Exposure(nil), b.Exposures...), a.exposures...)
	var order []countKey
	restored := make(map[countKey]bool)
	for _, c := range b.Counts {
		hour, err := time.Parse(time.RFC3339, c.Hour)
		if err != nil {
			continue // take always writes RFC 3339
		}
		k := countKey{flag: c.FlagKey, served: c.Variant != nil, hour: hour.Unix()}
		if c.Variant != nil {
			k.variant = *c.Variant
		}
		if !restored[k] {
			restored[k] = true
			order = append(order, k)
		}
		a.counts[k] += c.Count
	}
	for _, k := range a.countOrder {
		if !restored[k] {
			order = append(order, k)
		}
	}
	a.countOrder = order
}
