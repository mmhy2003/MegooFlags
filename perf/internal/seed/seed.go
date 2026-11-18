// Package seed fills a MegooFlags server with the flags the load tests use.
//
// The main project holds N boolean flags, all on in its first environment;
// its server key gives SDKs the large ruleset. /sdk/v1/evaluate evaluates
// every flag of an environment, on or off, so a 100-flag evaluate case needs
// an environment that holds only 100 flags. Flags belong to a project, not to
// an environment (routers/flag_environments.py only configures a flag per
// environment), so the eval flags live in a second project, <project>-eval,
// with the same flag keys and rules.
package seed

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/mmhy2003/MegooFlags/perf/internal/api"
	"github.com/mmhy2003/MegooFlags/perf/internal/result"
)

// Config says what to create.
type Config struct {
	Project     string
	Flags       int // flags in Project
	EvalFlags   int // flags in the eval project
	Concurrency int // flags created at once
}

// Output is what the load tests need from the seeded server. Both keys are
// server keys: client keys are rate limited per key and address.
type Output struct {
	Project     string `json:"project"`
	Env         string `json:"env"`
	ServerKey   string `json:"server_key"`
	EvalProject string `json:"eval_project"`
	EvalKey     string `json:"eval_key"`
	Flag        string `json:"flag"`
}

// FlagKey is the key of the i-th seeded flag.
func FlagKey(i int) string { return fmt.Sprintf("flag-%05d", i) }

// Plans are the values of the plan attribute the rules and the load match on.
var Plans = []string{"free", "pro", "enterprise"}

// Run seeds the server behind c and measures how long the large project's
// ruleset and first flag page take to load.
func Run(ctx context.Context, c *api.Client, cfg Config) (Output, result.Metrics, error) {
	out := Output{Project: cfg.Project, EvalProject: cfg.Project + "-eval", Flag: FlagKey(0)}
	var err error
	if out.Env, out.ServerKey, err = project(ctx, c, out.Project, cfg.Flags, cfg.Concurrency); err != nil {
		return Output{}, nil, err
	}
	if _, out.EvalKey, err = project(ctx, c, out.EvalProject, cfg.EvalFlags, cfg.Concurrency); err != nil {
		return Output{}, nil, err
	}

	start := time.Now()
	ruleset, err := c.Ruleset(ctx, out.ServerKey)
	if err != nil {
		return Output{}, nil, err
	}
	download := time.Since(start)
	start = time.Now()
	if err := c.ListFlags(ctx, out.Project, 100); err != nil {
		return Output{}, nil, err
	}
	list := time.Since(start)

	return out, result.Metrics{
		"flags":               float64(cfg.Flags),
		"ruleset_bytes":       float64(len(ruleset)),
		"ruleset_download_ms": ms(download),
		"flag_list_ms":        ms(list),
	}, nil
}

// project creates a project with n flags on in its first environment and a
// server key for that environment.
func project(ctx context.Context, c *api.Client, key string, n, concurrency int) (env, sdkKey string, err error) {
	envs, err := c.CreateProject(ctx, key)
	if err != nil {
		return "", "", err
	}
	if len(envs) == 0 {
		return "", "", fmt.Errorf("project %s has no environments", key)
	}
	env = envs[0]

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var next atomic.Int64
	var wg sync.WaitGroup
	var once sync.Once
	for range max(1, concurrency) {
		wg.Go(func() {
			for {
				i := int(next.Add(1) - 1)
				if i >= n || ctx.Err() != nil {
					return
				}
				if e := flag(ctx, c, key, env, FlagKey(i)); e != nil {
					once.Do(func() { err = e; cancel() })
					return
				}
			}
		})
	}
	wg.Wait()
	if err != nil {
		return "", "", err
	}
	sdkKey, err = c.CreateSDKKey(ctx, key, env, "perf", "server")
	return env, sdkKey, err
}

// flag creates one flag and turns it on in env with one rule: users on the
// enterprise plan get a 50 % rollout, everyone else gets false.
func flag(ctx context.Context, c *api.Client, project, env, key string) error {
	states, err := c.CreateBooleanFlag(ctx, project, key)
	var status *api.StatusError
	if errors.As(err, &status) && status.Code == http.StatusConflict {
		// A retried create whose first attempt went through.
		states, err = c.ReadFlag(ctx, project, key)
	}
	if err != nil {
		return err
	}
	version := -1
	for _, s := range states {
		if s.Environment == env {
			version = s.Version
		}
	}
	if version < 0 {
		return fmt.Errorf("flag %s has no state in %s", key, env)
	}
	return c.ReplaceFlagConfig(ctx, project, key, env, map[string]any{
		"version": version,
		"enabled": true,
		"rules": []any{map[string]any{
			"clauses": []any{map[string]any{"attribute": "plan", "operator": "in", "values": []string{Plans[2]}}},
			"rollout": map[string]any{"weights": []any{
				map[string]any{"variation": "true", "weight": 50000},
				map[string]any{"variation": "false", "weight": 50000},
			}},
		}},
		"fallthrough": map[string]any{"variation": "false"},
	})
}

func ms(d time.Duration) float64 { return float64(d) / float64(time.Millisecond) }
