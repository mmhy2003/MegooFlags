package events

import (
	"encoding/json"
	"fmt"
	"reflect"
	"slices"
	"testing"
	"time"
)

var t0 = time.Date(2026, 11, 10, 9, 15, 2, 0, time.UTC)

func served(flag, variant, key string, at time.Time) Evaluation {
	return Evaluation{FlagKey: flag, Variant: variant, Served: true, Reason: "SPLIT", ContextKey: key,
		Context: map[string]any{"key": key}, At: at}
}

func TestLRUEvicts(t *testing.T) {
	l := newLRU(2)
	a, b, c := dedupKey{ctxKey: "a"}, dedupKey{ctxKey: "b"}, dedupKey{ctxKey: "c"}
	if l.seen(a) || l.seen(b) {
		t.Fatal("first sight reported as seen")
	}
	if !l.seen(a) {
		t.Fatal("a not seen after insert")
	}
	// a is now the most recent, so inserting c evicts b.
	if l.seen(c) {
		t.Fatal("c reported as seen")
	}
	if !l.seen(a) {
		t.Error("a was evicted, want b evicted")
	}
	if l.seen(b) {
		t.Error("b still present after eviction")
	}
	if l.ll.Len() != 2 || len(l.items) != 2 {
		t.Errorf("size = %d/%d, want 2", l.ll.Len(), len(l.items))
	}
}

func TestDedupOncePerKeyFlagVariantHour(t *testing.T) {
	a := newAggregator(100, 100, false)
	a.add(served("f", "on", "u1", t0))
	a.add(served("f", "on", "u1", t0.Add(10*time.Minute)))
	if n, _ := a.pending(); n != 1 {
		t.Fatalf("same hour: %d exposures, want 1", n)
	}
	a.add(served("f", "on", "u1", t0.Add(time.Hour)))
	if n, _ := a.pending(); n != 2 {
		t.Fatalf("next hour: %d exposures, want 2", n)
	}

	cases := map[string]Evaluation{
		"another variant": served("f", "off", "u1", t0),
		"another key":     served("f", "on", "u2", t0),
		"another flag":    served("g", "on", "u1", t0),
	}
	for name, ev := range cases {
		a := newAggregator(100, 100, false)
		a.add(served("f", "on", "u1", t0))
		a.add(ev)
		if n, _ := a.pending(); n != 2 {
			t.Errorf("%s: %d exposures, want 2", name, n)
		}
	}
}

func TestAnonymousContextsShareExposure(t *testing.T) {
	a := newAggregator(100, 100, false)
	a.add(served("f", "on", "", t0))
	a.add(served("f", "on", "", t0))
	if n, _ := a.pending(); n != 1 {
		t.Errorf("%d exposures, want 1", n)
	}
}

func TestCountsAggregatePerHour(t *testing.T) {
	a := newAggregator(100, 100, false)
	for i := range 5 {
		a.add(served("f", "on", fmt.Sprint("u", i), t0))
	}
	a.add(served("f", "on", "u1", t0.Add(time.Hour)))
	a.add(served("f", "off", "u1", t0))
	if _, n := a.pending(); n != 3 {
		t.Fatalf("%d count buckets, want 3", n)
	}
	b := a.take(1000)
	on, off := "on", "off"
	want := []Count{
		{FlagKey: "f", Variant: &on, Hour: "2026-11-10T09:00:00Z", Count: 5},
		{FlagKey: "f", Variant: &on, Hour: "2026-11-10T10:00:00Z", Count: 1},
		{FlagKey: "f", Variant: &off, Hour: "2026-11-10T09:00:00Z", Count: 1},
	}
	if !reflect.DeepEqual(b.Counts, want) {
		t.Errorf("counts = %s, want %s", jsonString(t, b.Counts), jsonString(t, want))
	}
	if e, c := a.pending(); e != 0 || c != 0 {
		t.Errorf("pending after take = %d, %d, want 0, 0", e, c)
	}
}

func TestUnservedCountsHaveNullVariant(t *testing.T) {
	a := newAggregator(100, 100, false)
	a.add(Evaluation{FlagKey: "f", Reason: "ERROR", ContextKey: "u1", At: t0})
	a.add(Evaluation{FlagKey: "f", Reason: "ERROR", ContextKey: "u2", At: t0})
	b := a.take(1000)
	if len(b.Exposures) != 0 {
		t.Errorf("unserved evaluation produced %d exposures", len(b.Exposures))
	}
	want := []Count{{FlagKey: "f", Variant: nil, Hour: "2026-11-10T09:00:00Z", Count: 2}}
	if !reflect.DeepEqual(b.Counts, want) {
		t.Errorf("counts = %s, want %s", jsonString(t, b.Counts), jsonString(t, want))
	}
}

func TestContextOnlyWhenSendContext(t *testing.T) {
	for _, send := range []bool{false, true} {
		a := newAggregator(100, 100, send)
		a.add(served("f", "on", "u1", t0))
		b := a.take(1000)
		got := b.Exposures[0].Context
		if send && !reflect.DeepEqual(got, map[string]any{"key": "u1"}) {
			t.Errorf("sendContext=true: context = %v", got)
		}
		if !send && got != nil {
			t.Errorf("sendContext=false: context = %v, want nil", got)
		}
	}
}

func TestExposureFields(t *testing.T) {
	a := newAggregator(100, 100, false)
	local := t0.In(time.FixedZone("X", 3*3600)).Add(123 * time.Millisecond)
	a.add(served("f", "on", "u1", local))
	e := a.take(1000).Exposures[0]
	if e.FlagKey != "f" || e.Variant == nil || *e.Variant != "on" || e.Reason != "SPLIT" {
		t.Errorf("exposure = %+v", e)
	}
	if e.Timestamp != "2026-11-10T09:15:02.123Z" {
		t.Errorf("timestamp = %q, want 2026-11-10T09:15:02.123Z", e.Timestamp)
	}
}

func TestTakeLimitsTo1000(t *testing.T) {
	a := newAggregator(10000, 10000, false)
	for i := range 1500 {
		a.add(served(fmt.Sprint("f", i), "on", "u", t0))
	}
	b := a.take(1000)
	if len(b.Exposures) != 1000 || len(b.Counts) != 1000 {
		t.Fatalf("first take = %d exposures, %d counts, want 1000, 1000", len(b.Exposures), len(b.Counts))
	}
	if b.Exposures[0].FlagKey != "f0" || b.Counts[999].FlagKey != "f999" {
		t.Errorf("first take not FIFO: %s, %s", b.Exposures[0].FlagKey, b.Counts[999].FlagKey)
	}
	b = a.take(1000)
	if len(b.Exposures) != 500 || len(b.Counts) != 500 {
		t.Fatalf("second take = %d exposures, %d counts, want 500, 500", len(b.Exposures), len(b.Counts))
	}
	if b.Exposures[0].FlagKey != "f1000" || b.Counts[0].FlagKey != "f1000" {
		t.Errorf("second take starts at %s, %s, want f1000", b.Exposures[0].FlagKey, b.Counts[0].FlagKey)
	}
	if e, c := a.pending(); e != 0 || c != 0 {
		t.Errorf("pending = %d, %d, want 0, 0", e, c)
	}
}

func TestTakeSplitsLargeCounts(t *testing.T) {
	a := newAggregator(10, 10, false)
	a.add(served("f", "on", "u", t0))
	a.counts[a.countOrder[0]] = 2_500_000_000
	b := a.take(1000)
	var got []int64
	for _, c := range b.Counts {
		got = append(got, c.Count)
	}
	if !slices.Equal(got, []int64{1e9, 1e9, 5e8}) {
		t.Errorf("counts = %v, want [1e9 1e9 5e8]", got)
	}

	// The item limit holds; the remainder stays pending for the next batch.
	a.add(served("f", "on", "u", t0))
	a.counts[a.countOrder[0]] = 2_500_000_000
	b = a.take(2)
	if len(b.Counts) != 2 {
		t.Fatalf("take(2) = %d counts, want 2", len(b.Counts))
	}
	b = a.take(2)
	if len(b.Counts) != 1 || b.Counts[0].Count != 5e8 {
		t.Errorf("remainder = %s, want one count of 5e8", jsonString(t, b.Counts))
	}
}

func TestRestoreMergesCounts(t *testing.T) {
	a := newAggregator(100, 100, false)
	a.add(served("f", "on", "u1", t0))
	a.add(served("f", "on", "u2", t0))
	a.add(Evaluation{FlagKey: "f", At: t0})
	sent := a.take(1000)

	a.add(served("f", "on", "u3", t0))
	a.add(served("g", "on", "u3", t0))
	a.restore(sent)

	b := a.take(1000)
	if len(b.Exposures) != 4 || b.Exposures[0].Timestamp != sent.Exposures[0].Timestamp ||
		b.Exposures[2].FlagKey != "f" || b.Exposures[3].FlagKey != "g" {
		t.Errorf("exposures = %s, want restored ones first", jsonString(t, b.Exposures))
	}
	on := "on"
	want := []Count{
		{FlagKey: "f", Variant: &on, Hour: "2026-11-10T09:00:00Z", Count: 3},
		{FlagKey: "f", Variant: nil, Hour: "2026-11-10T09:00:00Z", Count: 1},
		{FlagKey: "g", Variant: &on, Hour: "2026-11-10T09:00:00Z", Count: 1},
	}
	if !reflect.DeepEqual(b.Counts, want) {
		t.Errorf("counts = %s, want %s", jsonString(t, b.Counts), jsonString(t, want))
	}
}

func TestRestoreJoinsSplitCounts(t *testing.T) {
	a := newAggregator(10, 10, false)
	a.add(served("f", "on", "u", t0))
	a.counts[a.countOrder[0]] = 2_500_000_000
	a.restore(a.take(1000))
	if _, n := a.pending(); n != 1 {
		t.Fatalf("%d buckets after restore, want 1", n)
	}
	if got := a.counts[a.countOrder[0]]; got != 2_500_000_000 {
		t.Errorf("count = %d, want 2500000000", got)
	}
}

func TestMaxPendingDrops(t *testing.T) {
	a := newAggregator(100, 2, false)
	for i := range 5 {
		a.add(served("f", "on", fmt.Sprint("u", i), t0))
	}
	a.add(served("f", "on", "u0", t0)) // a duplicate is not a drop
	if n, _ := a.pending(); n != 2 {
		t.Errorf("%d exposures, want 2", n)
	}
	if a.dropped != 3 {
		t.Errorf("dropped = %d, want 3", a.dropped)
	}
	if got := a.counts[a.countOrder[0]]; got != 6 {
		t.Errorf("count = %d, want 6 (counts are never dropped)", got)
	}
}

func TestHourFormat(t *testing.T) {
	a := newAggregator(10, 10, false)
	a.add(Evaluation{FlagKey: "f", At: time.Date(2026, 11, 10, 11, 59, 59, 999, time.FixedZone("X", 2*3600))})
	if got := a.take(10).Counts[0].Hour; got != "2026-11-10T09:00:00Z" {
		t.Errorf("hour = %q, want 2026-11-10T09:00:00Z", got)
	}
}

func TestJSONShape(t *testing.T) {
	// The example of spec/delivery.md §7.
	example := `{
	  "exposures": [
	    {"flag_key": "new-checkout", "variant": "on", "reason": "SPLIT",
	     "timestamp": "2026-11-10T09:15:02Z", "context": {"key": "user-1"}}
	  ],
	  "counts": [
	    {"flag_key": "new-checkout", "variant": "on", "hour": "2026-11-10T09:00:00Z", "count": 1250}
	  ]
	}`
	a := newAggregator(10, 10, true)
	a.add(served("new-checkout", "on", "user-1", t0))
	a.add(Evaluation{FlagKey: "new-checkout", At: t0})
	raw, err := json.Marshal(a.take(10))
	if err != nil {
		t.Fatal(err)
	}
	var got, want map[string][]map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal([]byte(example), &want); err != nil {
		t.Fatal(err)
	}
	for list, items := range want {
		if len(got[list]) == 0 {
			t.Fatalf("%s missing in %s", list, raw)
		}
		for _, g := range got[list] {
			if !slices.Equal(sortedKeys(g), sortedKeys(items[0])) {
				t.Errorf("%s keys = %v, want %v", list, sortedKeys(g), sortedKeys(items[0]))
			}
		}
	}
	if v, ok := got["counts"][1]["variant"]; !ok || v != nil {
		t.Errorf("unserved count variant = %v (present %v), want null", v, ok)
	}

	// Without a context the key is omitted, and an empty list is [] rather than null.
	a = newAggregator(10, 10, false)
	a.add(served("f", "on", "u", t0))
	b := a.take(10)
	b.Counts = nil
	a.restore(b)
	raw, _ = json.Marshal(a.take(10))
	if string(raw) != `{"exposures":[{"flag_key":"f","variant":"on","reason":"SPLIT","timestamp":"2026-11-10T09:15:02.000Z"}],"counts":[]}` {
		t.Errorf("marshal = %s", raw)
	}
	a = newAggregator(10, 10, false)
	a.add(Evaluation{FlagKey: "f", At: t0})
	raw, _ = json.Marshal(a.take(10))
	if string(raw) != `{"exposures":[],"counts":[{"flag_key":"f","variant":null,"hour":"2026-11-10T09:00:00Z","count":1}]}` {
		t.Errorf("marshal = %s", raw)
	}
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	return keys
}

func jsonString(t *testing.T, v any) string {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}
