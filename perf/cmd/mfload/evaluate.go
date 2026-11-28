package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/mmhy2003/MegooFlags/perf/internal/rate"
	"github.com/mmhy2003/MegooFlags/perf/internal/result"
	"github.com/mmhy2003/MegooFlags/perf/internal/seed"
	"github.com/mmhy2003/MegooFlags/perf/internal/stats"
)

func init() {
	commands["evaluate"] = command{"remote evaluation at a fixed request rate (OFREP or /sdk/v1/evaluate)", runEvaluateCommand}
}

type evaluateConfig struct {
	target      string
	key         string
	mode        string // ofrep or evaluate
	flag        string
	rps         float64
	duration    time.Duration
	warmup      time.Duration
	users       int
	concurrency int
	insecure    bool
}

func runEvaluateCommand(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("evaluate", flag.ContinueOnError)
	target := fs.String("target", "", "server URL to evaluate on")
	key := fs.String("key", "", "server SDK key")
	mode := fs.String("mode", "ofrep", "ofrep (one flag through /ofrep/v1) or evaluate (every flag through /sdk/v1/evaluate)")
	flagKey := fs.String("flag", seed.FlagKey(0), "flag key for --mode ofrep")
	rps := fs.Float64("rps", 100, "requests per second")
	duration := fs.Float64("duration", 30, "seconds to measure")
	warmup := fs.Float64("warmup", 5, "seconds to run first without measuring")
	users := fs.Int("users", 1_000_000, "distinct targeting keys to pick from")
	concurrency := fs.Int("concurrency", 256, "most requests in flight")
	insecure := fs.Bool("insecure", false, "skip TLS certificate checks")
	out := fs.String("out", "", "file to write the scenario result to")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *target == "" || *key == "" || *out == "" {
		return errors.New("--target, --key and --out are required")
	}
	cfg := evaluateConfig{
		target: *target, key: *key, mode: *mode, flag: *flagKey,
		rps: *rps, duration: seconds(*duration), warmup: seconds(*warmup),
		users: *users, concurrency: *concurrency, insecure: *insecure,
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	s, err := measureEvaluate(ctx, cfg)
	if err != nil {
		return err
	}
	if err := result.Write(*out, s); err != nil {
		return err
	}
	m := s.Metrics
	_, err = fmt.Fprintf(stdout, "evaluate %s: rps=%.0f count=%.0f errors=%d p50=%.1fms p95=%.1fms p99=%.1fms max=%.1fms\n",
		cfg.mode, m["rps"], m["count"], s.Errors, m["p50_ms"], m["p95_ms"], m["p99_ms"], m["max_ms"])
	return err
}

// evaluateRequest returns the URL and body builder of a mode. OFREP takes
// the user as targetingKey; /sdk/v1/evaluate takes it as the key attribute.
func evaluateRequest(cfg evaluateConfig) (string, string, error) {
	base := strings.TrimSuffix(cfg.target, "/")
	switch cfg.mode {
	case "ofrep":
		return base + "/ofrep/v1/evaluate/flags/" + url.PathEscape(cfg.flag), "targetingKey", nil
	case "evaluate":
		return base + "/sdk/v1/evaluate", "key", nil
	}
	return "", "", fmt.Errorf("unknown --mode %q (want ofrep or evaluate)", cfg.mode)
}

// measureEvaluate sends requests at cfg.rps for cfg.warmup, discards them,
// then for cfg.duration, and summarizes the latter. A non-2xx answer is an error.
func measureEvaluate(ctx context.Context, cfg evaluateConfig) (result.Scenario, error) {
	endpoint, userAttr, err := evaluateRequest(cfg)
	if err != nil {
		return result.Scenario{}, err
	}
	if cfg.users < 1 {
		return result.Scenario{}, errors.New("--users must be at least 1")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: cfg.insecure}
	transport.MaxIdleConnsPerHost = cfg.concurrency
	client := &http.Client{Transport: transport, Timeout: 10 * time.Second}
	defer client.CloseIdleConnections()

	do := func(ctx context.Context) error {
		body, err := json.Marshal(map[string]any{"context": map[string]any{
			userAttr: fmt.Sprintf("user-%d", rand.IntN(cfg.users)),
			"plan":   seed.Plans[rand.IntN(len(seed.Plans))],
		}})
		if err != nil {
			return err
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
		if err != nil {
			return err
		}
		req.Header.Set("Authorization", "Bearer "+cfg.key)
		req.Header.Set("Content-Type", "application/json")
		resp, err := client.Do(req)
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		_, _ = io.Copy(io.Discard, resp.Body)
		if resp.StatusCode/100 != 2 {
			return fmt.Errorf("%s", resp.Status)
		}
		return nil
	}

	if cfg.warmup > 0 {
		rate.Run(ctx, cfg.rps, cfg.warmup, cfg.concurrency, do)
	}
	start := time.Now()
	results := rate.Run(ctx, cfg.rps, cfg.duration, cfg.concurrency, do)
	elapsed := time.Since(start)

	var samples []time.Duration
	errs := 0
	for _, r := range results {
		if r.Err != nil {
			errs++
			continue
		}
		samples = append(samples, r.Latency)
	}
	m := result.Metrics(stats.Summarize(samples, errs).Metrics())
	m["rps"] = float64(len(results)) / elapsed.Seconds()
	return result.Scenario{Metrics: m, Errors: errs}, nil
}
