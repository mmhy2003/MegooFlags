package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/mmhy2003/MegooFlags/perf/internal/api"
	"github.com/mmhy2003/MegooFlags/perf/internal/result"
	"github.com/mmhy2003/MegooFlags/perf/internal/seed"
)

func init() {
	commands["seed"] = command{"create a project with many flags and the SDK keys the load tests use", runSeedCommand}
}

// seedFile is what seed writes: the keys the other commands need, plus the
// scenario fields, so the file also serves as the flags_10k result.
type seedFile struct {
	seed.Output
	result.Scenario
}

func runSeedCommand(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("seed", flag.ContinueOnError)
	apiURL := fs.String("api", "", "server URL of the management API")
	token := fs.String("token", "", "management API token")
	project := fs.String("project", "", "key of the project to create")
	flags := fs.Int("flags", 100, "flags to create in the project")
	evalFlags := fs.Int("eval-flags", 100, "flags to create in <project>-eval for /sdk/v1/evaluate")
	insecure := fs.Bool("insecure", false, "skip TLS certificate checks")
	out := fs.String("out", "", "file to write the keys and metrics to")
	if err := fs.Parse(args); err != nil {
		return err
	}
	switch {
	case *apiURL == "" || *token == "" || *project == "" || *out == "":
		return errors.New("--api, --token, --project and --out are required")
	case *flags < 1 || *evalFlags < 1:
		return errors.New("--flags and --eval-flags must be at least 1")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	client := api.New(*apiURL, *token, *insecure).WithRetry(6, 250*time.Millisecond)
	output, metrics, err := seed.Run(ctx, client, seed.Config{Project: *project, Flags: *flags, EvalFlags: *evalFlags, Concurrency: 16})
	if err != nil {
		return err
	}
	if err := writeSeedFile(*out, output, metrics); err != nil {
		return err
	}
	_, err = fmt.Fprintf(stdout, "seed: project=%s flags=%d eval_project=%s ruleset=%.0fB download=%.1fms flag_list=%.1fms\n",
		output.Project, *flags, output.EvalProject, metrics["ruleset_bytes"], metrics["ruleset_download_ms"], metrics["flag_list_ms"])
	return err
}

// writeSeedFile writes output and metrics as indented JSON to path, creating its directory.
func writeSeedFile(path string, output seed.Output, metrics result.Metrics) error {
	data, err := json.MarshalIndent(seedFile{output, result.Scenario{Metrics: metrics}}, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0o644)
}
