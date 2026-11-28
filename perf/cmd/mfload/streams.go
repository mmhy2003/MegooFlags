package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/mmhy2003/MegooFlags/perf/internal/api"
	"github.com/mmhy2003/MegooFlags/perf/internal/result"
	"github.com/mmhy2003/MegooFlags/perf/internal/sse"
	"github.com/mmhy2003/MegooFlags/perf/internal/stats"
)

// missAfter is how long a stream has to show a flip before it counts as
// missed. A stream that has not shown a flip by the next one also misses it.
var missAfter = 30 * time.Second

func init() {
	commands["streams"] = command{"hold many SDK streams and measure flag propagation", runStreamsCommand}
}

type stringList []string

func (l *stringList) String() string     { return strings.Join(*l, ",") }
func (l *stringList) Set(v string) error { *l = append(*l, v); return nil }

type streamsConfig struct {
	targets        []string
	key            string
	streams        int
	ramp           time.Duration
	hold           time.Duration
	flipEvery      time.Duration
	flag           string
	insecure       bool
	rampUntilError bool
	max            int
}

func runStreamsCommand(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("streams", flag.ContinueOnError)
	var targets stringList
	fs.Var(&targets, "target", "server URL to open streams on (repeatable; streams go round-robin)")
	key := fs.String("key", "", "server SDK key")
	streams := fs.Int("streams", 1, "streams to open")
	rampSeconds := fs.Float64("ramp-seconds", 10, "seconds over which to open the streams")
	holdSeconds := fs.Float64("hold", 60, "seconds to hold the streams open after the ramp")
	flipEvery := fs.Float64("flip-every", 5, "seconds between flag toggles during the hold (0: no toggles)")
	apiURL := fs.String("api", "", "server URL of the management API")
	token := fs.String("token", "", "management API token")
	project := fs.String("project", "", "project key")
	env := fs.String("env", "", "environment key")
	flagKey := fs.String("flag", "", "flag key to toggle")
	insecure := fs.Bool("insecure", false, "skip TLS certificate checks")
	out := fs.String("out", "", "file to write the scenario result to")
	rampUntilError := fs.Bool("ramp-until-error", false, "open streams until an open fails or --max is reached, and report capacity")
	maxStreams := fs.Int("max", 0, "the most streams to open with --ramp-until-error")
	if err := fs.Parse(args); err != nil {
		return err
	}
	switch {
	case len(targets) == 0 || *key == "" || *out == "":
		return errors.New("--target, --key and --out are required")
	case *rampUntilError && *maxStreams < 1:
		return errors.New("--ramp-until-error needs --max")
	case !*rampUntilError && *streams < 1:
		return errors.New("--streams must be at least 1")
	case *flipEvery > 0 && (*apiURL == "" || *token == "" || *project == "" || *env == "" || *flagKey == ""):
		return errors.New("--flip-every needs --api, --token, --project, --env and --flag")
	}
	cfg := streamsConfig{
		targets: targets, key: *key, streams: *streams,
		ramp: seconds(*rampSeconds), hold: seconds(*holdSeconds), flipEvery: seconds(*flipEvery),
		flag: *flagKey, insecure: *insecure, rampUntilError: *rampUntilError, max: *maxStreams,
	}
	client := api.New(*apiURL, *token, *insecure)
	toggle := func(ctx context.Context, on bool) error {
		return client.ToggleFlag(ctx, *project, *env, *flagKey, on)
	}

	// An interrupt ends the ramp or the hold early; the result is still written.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	s, err := measureStreams(ctx, cfg, toggle)
	if err != nil {
		return err
	}
	if err := result.Write(*out, s); err != nil {
		return err
	}
	m := s.Metrics
	_, err = fmt.Fprintf(stdout, "streams: opened=%.0f dropped=%.0f flips=%.0f p50=%.1fms p95=%.1fms max=%.1fms missed=%.0f errors=%d\n",
		m["streams_opened"], m["streams_dropped"], m["flips"], m["prop_p50_ms"], m["prop_p95_ms"], m["prop_max_ms"], m["missed"], s.Errors)
	return err
}

func seconds(s float64) time.Duration { return time.Duration(s * float64(time.Second)) }

// stream is one open SSE stream. Its fields are guarded by tracker.mu.
type stream struct {
	live  bool // open and not dropped
	ready bool // has received a put
	seen  int  // id of the last flip it showed
}

type flipRecord struct {
	id   int
	want bool
	at   time.Time
	from int // len(tracker.samples) when the flip started
}

type tracker struct {
	mu      sync.Mutex
	streams []*stream
	ending  bool // streams closing now are not drops
	known   bool // state is known, from the first put
	state   bool
	flip    *flipRecord // the last successful or current flip
	lastID  int
	samples []time.Duration
	opened  int
	dropped int
	flips   int
	missed  int
	errors  int
}

func (t *tracker) onPut(s *stream, enabled bool, now time.Time) {
	t.mu.Lock()
	defer t.mu.Unlock()
	s.ready = true
	if !t.known {
		t.known, t.state = true, enabled
	}
	if f := t.flip; f != nil && s.seen < f.id && enabled == f.want {
		s.seen = f.id
		if d := now.Sub(f.at); d <= missAfter {
			t.samples = append(t.samples, d)
		}
	}
}

// finish counts the live streams that did not show f. Callers hold t.mu.
func (t *tracker) finish(f *flipRecord) {
	for _, s := range t.streams {
		if s.live && s.seen < f.id {
			t.missed++
		}
	}
}

// waitFor polls until done reports true under t.mu, deadline passes or ctx is done.
func (t *tracker) waitFor(ctx context.Context, deadline time.Time, done func() bool) {
	for {
		t.mu.Lock()
		ok := done()
		t.mu.Unlock()
		if ok || time.Now().After(deadline) || ctx.Err() != nil {
			return
		}
		select {
		case <-ctx.Done():
		case <-time.After(10 * time.Millisecond):
		}
	}
}

func (t *tracker) allLive(pred func(*stream) bool) bool {
	for _, s := range t.streams {
		if s.live && !pred(s) {
			return false
		}
	}
	return true
}

// flipOnce toggles the flag to the opposite of its known state.
func (t *tracker) flipOnce(ctx context.Context, toggle func(context.Context, bool) error) {
	t.mu.Lock()
	if t.flip != nil {
		t.finish(t.flip)
	}
	t.lastID++
	// Registered before the request, as a put may arrive before the response.
	f := &flipRecord{id: t.lastID, want: !t.state, at: time.Now(), from: len(t.samples)}
	t.flip = f
	t.mu.Unlock()

	err := toggle(ctx, f.want)

	t.mu.Lock()
	defer t.mu.Unlock()
	if err != nil {
		fmt.Fprintf(os.Stderr, "mfload streams: toggle: %v\n", err)
		t.errors++
		t.samples = t.samples[:f.from]
		t.flip = nil
		return
	}
	t.state = f.want
	t.flips++
}

func newStreamClient(insecure bool) *http.Client {
	return &http.Client{Transport: &http.Transport{
		DialContext:           (&net.Dialer{Timeout: 30 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		TLSClientConfig:       &tls.Config{InsecureSkipVerify: insecure},
		TLSHandshakeTimeout:   30 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second,
		MaxConnsPerHost:       0, // no limit: every stream has its own connection
		DisableKeepAlives:     false,
	}}
}

// open opens one stream and reads it in a new goroutine until streamCtx is done.
func (t *tracker) open(streamCtx context.Context, client *http.Client, cfg streamsConfig, target string, readers *sync.WaitGroup) error {
	req, err := http.NewRequestWithContext(streamCtx, http.MethodGet, strings.TrimSuffix(target, "/")+"/sdk/v1/stream", nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "text/event-stream")
	req.Header.Set("Authorization", "Bearer "+cfg.key)
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	if resp.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		resp.Body.Close()
		return fmt.Errorf("open %s: %s", req.URL, resp.Status)
	}
	s := &stream{live: true}
	t.mu.Lock()
	t.streams = append(t.streams, s)
	t.opened++
	t.mu.Unlock()
	readers.Go(func() {
		defer resp.Body.Close()
		err := sse.Read(streamCtx, resp.Body, func(e sse.Event) {
			now := time.Now()
			switch e.Name {
			case "put":
				if enabled, ok := flagEnabled(e.Data, cfg.flag); ok {
					t.onPut(s, enabled, now)
				} else {
					t.mu.Lock()
					s.ready = true
					t.mu.Unlock()
				}
			case "revoked":
				resp.Body.Close() // ends Read: the stream is dropped
			}
		}, nil)
		t.mu.Lock()
		defer t.mu.Unlock()
		if !t.ending {
			if err != nil {
				fmt.Fprintf(os.Stderr, "mfload streams: stream %s: %v\n", req.URL, err)
			}
			s.live = false
			t.dropped++
		}
	})
	return nil
}

// flagEnabled reads one flag's state from a put's ruleset without decoding the others.
func flagEnabled(data, key string) (bool, bool) {
	if key == "" {
		return false, false
	}
	var ruleset struct {
		Flags map[string]json.RawMessage `json:"flags"`
	}
	if err := json.Unmarshal([]byte(data), &ruleset); err != nil {
		return false, false
	}
	raw, ok := ruleset.Flags[key]
	if !ok {
		return false, false
	}
	var f struct {
		Enabled bool `json:"enabled"`
	}
	if err := json.Unmarshal(raw, &f); err != nil {
		return false, false
	}
	return f.Enabled, true
}

// measureStreams opens the streams, holds them while flipping the flag and
// returns the scenario. ctx ends the ramp or the hold early.
func measureStreams(ctx context.Context, cfg streamsConfig, toggle func(context.Context, bool) error) (result.Scenario, error) {
	t := &tracker{}
	client := newStreamClient(cfg.insecure)
	streamCtx, closeStreams := context.WithCancel(context.Background())
	defer closeStreams()
	var readers, opens sync.WaitGroup

	count := cfg.streams
	if cfg.rampUntilError {
		count = cfg.max
	}
	start := time.Now()
ramp:
	for i := range count {
		due := start.Add(time.Duration(float64(cfg.ramp) * float64(i) / float64(count)))
		if !sleepUntil(ctx, due) {
			break
		}
		target := cfg.targets[i%len(cfg.targets)]
		if cfg.rampUntilError {
			if err := t.open(streamCtx, client, cfg, target, &readers); err != nil {
				fmt.Fprintf(os.Stderr, "mfload streams: stopped after %d streams: %v\n", i, err)
				t.mu.Lock()
				t.errors++
				t.mu.Unlock()
				break ramp
			}
			continue
		}
		opens.Go(func() {
			if err := t.open(streamCtx, client, cfg, target, &readers); err != nil {
				fmt.Fprintf(os.Stderr, "mfload streams: %v\n", err)
				t.mu.Lock()
				t.errors++
				t.mu.Unlock()
			}
		})
	}
	opens.Wait()

	if cfg.flipEvery > 0 {
		t.waitFor(ctx, time.Now().Add(missAfter), func() bool { return t.allLive(func(s *stream) bool { return s.ready }) })
		t.mu.Lock()
		known := t.known
		t.mu.Unlock()
		if !known && ctx.Err() == nil {
			return result.Scenario{}, fmt.Errorf("no stream received a put showing flag %q", cfg.flag)
		}
	}

	holdStart := time.Now()
	for k := 0; cfg.flipEvery > 0 && time.Duration(k)*cfg.flipEvery < cfg.hold; k++ {
		if !sleepUntil(ctx, holdStart.Add(time.Duration(k)*cfg.flipEvery)) {
			break
		}
		t.flipOnce(ctx, toggle)
	}
	sleepUntil(ctx, holdStart.Add(cfg.hold))
	t.mu.Lock()
	last := t.flip
	t.mu.Unlock()
	if last != nil {
		t.waitFor(ctx, last.at.Add(missAfter), func() bool { return t.allLive(func(s *stream) bool { return s.seen >= last.id }) })
		t.mu.Lock()
		t.finish(last)
		t.mu.Unlock()
	}

	t.mu.Lock()
	t.ending = true
	t.mu.Unlock()
	closeStreams()
	readers.Wait()

	sum := stats.Summarize(t.samples, 0)
	m := result.Metrics{
		"streams_opened":  float64(t.opened),
		"streams_dropped": float64(t.dropped),
		"flips":           float64(t.flips),
		"prop_p50_ms":     ms(sum.P50),
		"prop_p95_ms":     ms(sum.P95),
		"prop_max_ms":     ms(sum.Max),
		"missed":          float64(t.missed),
	}
	if cfg.rampUntilError {
		m["capacity"] = float64(t.opened)
	}
	return result.Scenario{Metrics: m, Errors: t.errors}, nil
}

// sleepUntil waits for t and reports false if ctx ended first.
func sleepUntil(ctx context.Context, t time.Time) bool {
	wait := time.Until(t)
	if wait <= 0 {
		return ctx.Err() == nil
	}
	timer := time.NewTimer(wait)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func ms(d time.Duration) float64 { return float64(d) / float64(time.Millisecond) }
