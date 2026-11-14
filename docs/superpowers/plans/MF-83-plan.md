# MF-83 (part 1) implementation plan: Go server SDK as an OpenFeature provider

**Goal:** ship `github.com/mmhy2003/MegooFlags/sdks/go` (package `megooflags`, version 0.1.0), an OpenFeature provider that evaluates the downloaded ruleset in-process (p99 < 1 ms, no network per evaluation), stays current over SSE with a polling fallback, serves offline from a bootstrap file or on-disk cache, sends counts and deduplicated exposures in batches, and passes 100 % of `spec/conformance` plus an e2e test in CI.

**Architecture:** `internal/engine` is a pure port of `api/src/megooflags_api/evaluation/` (parser, operators, portable regex, bucketing, evaluation). `internal/transport` holds shared HTTP helpers (auth headers, status → error mapping, `Retry-After`, backoff). `internal/delivery` keeps the current ruleset in an atomic `Store` and fills it from the bootstrap file, the `Cache`, the SSE `Streamer` and the `Poller`, which a `Sync` coordinator runs together. `internal/events` aggregates counts and deduplicated exposures and posts them in the background. The root `megooflags` package's `Provider` ties these together behind `openfeature.FeatureProvider`, `StateHandler` and `EventHandler`.

## Conventions for every step
- Branch: the current `megoo/MF-83-server-sdks-as-openfeature-providers-go`. One commit per step. Each message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.megoocolab/`. Never create or push tags.
- Commit subjects: `test(spec): … (MF-83)`, `feat(sdk-go): … (MF-83)`, `test(sdk-go): … (MF-83)`, `ci: … (MF-83)`, `docs(sdk-go): … (MF-83)`.
- Go commands run from `sdks/go/` unless stated otherwise. After every Go step, run `gofmt -l .` (it must print nothing), `go vet ./...` and `go test -race ./...`, and all three must pass.
- Write the failing test first, run it and see it fail (a compile error counts as the expected failure for a new symbol), then implement and run it again.
- Standard library only, plus `github.com/open-feature/go-sdk` (v1.20.0, added in step 17). `go.mod` says `go 1.26` (go-sdk's own `go` directive is 1.26.0).
- Python checks for step 1 run from `api/`: `uv run pytest tests/test_conformance.py -q`. They need no database.

## File map (`sdks/go/`)
| File | Responsibility |
|---|---|
| `go.mod`, `go.sum` | Module `github.com/mmhy2003/MegooFlags/sdks/go` |
| `LICENSE` | A copy of the repository's Apache 2.0 `LICENSE`, so the module zip carries it |
| `doc.go` | Package doc with a usage example |
| `version.go` | `const Version = "0.1.0"`. The User-Agent is `megooflags-go/<Version>` |
| `config.go` | `Config`, `withDefaults()` and `validate()` |
| `provider.go` | `Provider`: `NewProvider`, `Metadata`, `Hooks`, the five typed evaluations, context mapping, event recording |
| `lifecycle.go` | `Init`, `Shutdown`, `EventChannel`, status → OpenFeature events, start-up order |
| `conformance_test.go` | The conformance runner |
| `provider_test.go`, `lifecycle_test.go`, `config_test.go` | Provider unit tests (offline bootstrap, `httptest`) |
| `internal/engine/value.go` | Number normalisation, JSON equality, scalars, array elements, plain conversion |
| `internal/engine/bucketing.go` | `BucketText`, `Bucket`, `pickVariation` |
| `internal/engine/semver.go`, `date.go`, `regex.go` | Strict SemVer, RFC 3339 → ms, portable regex |
| `internal/engine/model.go`, `parse.go` | Ruleset types and the §11 loader with prepared clause values |
| `internal/engine/evaluate.go` | `Evaluate`, `Result`, `Reason`, `ErrorCode` |
| `internal/engine/bench_test.go` | 10 000-flag generator, `BenchmarkEvaluate`, `TestEvaluateP99` |
| `internal/engine/race_test.go`, `norace_test.go` | `raceEnabled` constant (build tags `race` and `!race`) |
| `internal/transport/transport.go`, `backoff.go` | Requests, `FatalError`, `RetryAfterError`, `HTTPError`, `RetryAfter`, `Backoff`, `Sleep` |
| `internal/delivery/store.go`, `cache.go` | `Snapshot`, `Store`, `Cache` |
| `internal/delivery/sse.go`, `streamer.go`, `poller.go`, `sync.go` | SSE line reader, stream client, poller, coordinator |
| `internal/events/aggregate.go`, `batcher.go` | LRU dedup and hour buckets; the background sender |
| `e2e/e2e_test.go` | `//go:build e2e`: the test against the compose stack |
| `README.md` | Usage, config, offline and bootstrap |

Other files: `spec/conformance/cases/operators_regex.json` (new cases), `.github/workflows/ci.yml` (jobs `sdk-go` and `sdk-go-e2e`), `.github/workflows/release.yml` (the `sdks/go/v*` trigger), `sdks/README.md`, `CHANGELOG.md`.

## Contracts (the names every step uses)

### C1 `internal/engine`
```go
const MaxPrerequisiteDepth = 32
const Buckets = 100000
const maxSafeInteger = 1<<53 - 1

type Reason string // ReasonStatic "STATIC", ReasonDefault "DEFAULT", ReasonTargetingMatch "TARGETING_MATCH",
                   // ReasonSplit "SPLIT", ReasonDisabled "DISABLED", ReasonPrerequisiteFailed "PREREQUISITE_FAILED", ReasonError "ERROR"
type ErrorCode string // ErrNone "", ErrFlagNotFound "FLAG_NOT_FOUND", ErrTypeMismatch "TYPE_MISMATCH",
                      // ErrParse "PARSE_ERROR", ErrInvalidContext "INVALID_CONTEXT", ErrGeneral "GENERAL"

// value.go
type number struct { f float64; i int64; isInt bool }
func toNumber(v any) (number, bool)   // int, int8..int64, uint..uint64 (above MaxInt64 → float), float32/64, json.Number; bool → false
func (a number) cmp(b number) int     // both isInt: compare as int64; otherwise as float64
func jsonEqual(a, b any) bool         // bool only == bool; numbers numerically (int vs int exactly); string == string; anything else false
func isScalar(v any) bool             // string, bool or a number
func elements(v any) (elems []any, isArray bool) // []any as is; []string/[]int/[]int64/[]float64/[]bool converted; other slice/array kinds via reflect
func plain(v any) any                 // json.Number → float64, recursively through []any and map[string]any

// bucketing.go
func BucketText(v any) (string, bool) // string as is; integral number with |n| <= 2^53-1 → strconv.FormatInt (42.0 → "42", -0.0 → "0"); else false
func Bucket(flagKey, text string) int // binary.BigEndian.Uint64(sha256(flagKey+"."+text)[:8]) % 100000
func pickVariation(ws []Weight, bucket int) string

// semver.go
type Semver struct { core [3]string; pre []preID } // numeric strings without leading zeros
type preID struct { numeric bool; s string }
func ParseSemver(s string) (Semver, bool)
func CompareSemver(a, b Semver) int  // numeric parts compare by (len, then bytes), so there is no overflow

// date.go
func ParseRFC3339Millis(s string) (int64, bool)
func toMillis(v any) (int64, bool)   // a number is floored (non-finite → false; clamped to the int64 range); a string goes through ParseRFC3339Millis

// regex.go
type Regex struct { re *regexp.Regexp; dollar bool }
func CompilePortable(pattern string) (*Regex, error)
func (r *Regex) MatchString(s string) bool

// model.go
type Ruleset struct { Project, Environment string; Flags map[string]*Flag; Segments map[string]*Segment }
type Flag struct {
    Key, Type string; Enabled bool; Version int64
    Variations map[string]*Variation; OffVariation string; HasOffVariation bool
    Prerequisites []Prerequisite; Targets []Target; Rules []Rule; Fallthrough Serve
}
type Variation struct { Key string; Value any; Int int64; IsInt bool } // Value is plain(); Int/IsInt are set for integral numbers that fit int64
type Prerequisite struct { Flag, Variation string }
type Target struct { Variation string; Values map[string]struct{} }
type Rule struct { Description string; Clauses []Clause; Serve Serve }
type Serve struct { Variation string; Rollout *Rollout }
type Rollout struct { BucketBy string; Weights []Weight }
type Weight struct { Variation string; Weight int }
type Segment struct { Included, Excluded map[string]struct{}; Rules [][]Clause }
type Clause struct {
    Attribute, Operator string; Negate bool; Values []any
    // prepared, per operator; unusable values are left out, which is the same as "never matches"
    equal []any; strs []string; nums []number; regexes []*Regex; semvers []Semver; times []int64; segments []string
}
type ParseError struct { Path, Msg string } // Error(): "PARSE_ERROR: <path>: <msg>"
func Parse(data []byte) (*Ruleset, error)

// evaluate.go
type Result struct {
    Value any; Variant string; Served bool; Reason Reason
    ErrorCode ErrorCode; ErrorMessage string
    RuleIndex int        // -1 when unset
    Prerequisite string  // "" when unset
    Int int64; IsInt bool // copied from the served Variation
}
func (r Result) Metadata() map[string]any // {"rule_index": n} when n >= 0, {"prerequisite": k} when set; {} otherwise
func Evaluate(rs *Ruleset, flagKey string, ctx any, def any, expectedType string) Result // expectedType "" means no check
```

### C2 `internal/transport`
```go
type FatalError struct { Status int; Reason string } // 401, 403, or Status 0 with Reason "revoked". Error(): "megooflags: SDK key refused (<status|revoked>)"
type RetryAfterError struct { After time.Duration }  // 429
type HTTPError struct { Status int }                 // any other non-2xx
func NewRequest(ctx context.Context, method, url, key, userAgent string, body []byte) (*http.Request, error)
    // sets Authorization: Bearer <key> and User-Agent, and Content-Type: application/json when body != nil
func StatusError(resp *http.Response, now time.Time) error // nil for 2xx and 304
func RetryAfter(h http.Header, now time.Time) (time.Duration, bool) // delta-seconds or an HTTP-date; negative → 0
type Backoff struct { Base, Max time.Duration; Rand func() float64; n int }
func (b *Backoff) Next() time.Duration // n == 0: Base; then d = min(Max, Base<<n) and the wait is d*(0.8+0.2*Rand()). Rand nil → math/rand/v2.Float64
func (b *Backoff) Reset()
func Sleep(ctx context.Context, d time.Duration) error
```

### C3 `internal/delivery`
```go
type Snapshot struct { Ruleset *engine.Ruleset; ETag string; Raw []byte; Source string } // Source: "bootstrap", "cache", "stream" or "poll"
type Store struct { p atomic.Pointer[Snapshot] }
func (s *Store) Load() *Snapshot
func (s *Store) Swap(n *Snapshot) (old *Snapshot)

type Cache struct { path string }
func NewCache(dir, sdkKey string) *Cache // nil when dir == ""; file <dir>/megooflags-<first 16 hex of sha256(sdkKey)>.json
func (c *Cache) Load() (raw []byte, etag string, err error) // a nil *Cache returns os.ErrNotExist
func (c *Cache) Save(raw []byte, etag string) error         // {"etag": ..., "ruleset": <raw>}; temp file in dir, then os.Rename; mode 0600

type sseEvent struct { name, data, id string }
func readSSE(r io.Reader, onLine func(), onRetry func(time.Duration), onEvent func(sseEvent) error) error

type StreamerConfig struct {
    URL, Key, UserAgent string; Client *http.Client; Logger *slog.Logger
    LastEventID func() string
    OnPut func(raw []byte, id string) bool // false: invalid ruleset (the previous one stays)
    OnConnected func(); OnDisconnected func(err error, failures int)
    DeadTimeout time.Duration // 45s
    StableAfter time.Duration // 60s
    Backoff *transport.Backoff // Base 3s (replaced by the server's `retry:`), Max 60s
}
func NewStreamer(cfg StreamerConfig) *Streamer
func (s *Streamer) Run(ctx context.Context) error // returns a *transport.FatalError, or ctx.Err()

type PollerConfig struct { URL, Key, UserAgent string; Client *http.Client; Timeout time.Duration; ETag func() string; OnRuleset func(raw []byte, etag string) bool }
func NewPoller(cfg PollerConfig) *Poller
func (p *Poller) Poll(ctx context.Context) error // 200 → OnRuleset; 304 → nil; otherwise transport.StatusError or the network error

type Status int // StatusReady, StatusStale, StatusFatal
type SyncConfig struct {
    BaseURL, Key, UserAgent string; Client *http.Client; Logger *slog.Logger
    DisableStream bool; PollInterval time.Duration; PollAfterFailures int // default 2
    Store *Store; Cache *Cache
    OnChange func(old, new *Snapshot); OnStatus func(Status, error)
    DeadTimeout, StableAfter, RetryBase, RetryMax time.Duration // zero → 45s, 60s, 3s, 60s; tests shorten them
}
func NewSync(cfg SyncConfig) *Sync
func (s *Sync) Apply(raw []byte, etag, source string) error // Parse, Swap, Cache.Save for "stream" and "poll", then OnChange
func (s *Sync) Start()
func (s *Sync) Stop() // cancels and waits for the goroutines; idempotent
```

### C4 `internal/events`
```go
type Evaluation struct { FlagKey, Variant string; Served bool; Reason string; ContextKey string; Context map[string]any; At time.Time }
type Exposure struct { FlagKey string `json:"flag_key"`; Variant *string `json:"variant"`; Reason string `json:"reason"`; Timestamp string `json:"timestamp"`; Context map[string]any `json:"context,omitempty"` }
type Count struct { FlagKey string `json:"flag_key"`; Variant *string `json:"variant"`; Hour string `json:"hour"`; Count int64 `json:"count"` }
type Batch struct { Exposures []Exposure `json:"exposures"`; Counts []Count `json:"counts"` }
type Config struct {
    URL, Key, UserAgent string; Client *http.Client; Logger *slog.Logger
    FlushInterval time.Duration; QueueSize int /*10000*/; DedupSize int /*10000*/; MaxPending int /*10000*/; MaxItems int /*1000*/
    SendContext bool; OnFatal func(error); Now func() time.Time; RetryBase, RetryMax time.Duration /*2s, 60s*/
}
func New(cfg Config) *Batcher
func (b *Batcher) Start()
func (b *Batcher) Record(ev Evaluation) // never blocks: select on the channel with default; a drop increments the counter
func (b *Batcher) Dropped() uint64
func (b *Batcher) Shutdown(ctx context.Context) error // final flush until ctx ends; idempotent
func (b *Batcher) Stop() // fatal: stop without sending; idempotent
```

### C5 root package
```go
type Config struct { /* exactly the design's fields */ }
func NewProvider(cfg Config) *Provider
```
`NewProvider` returns a single value, so it works as `openfeature.SetProviderAndWait(megooflags.NewProvider(cfg))`. It validates the config and never touches the network. An invalid config (for example a client key) is logged, then `Init` returns `&openfeature.ProviderInitError{ErrorCode: openfeature.ProviderFatalCode, Message: …}` and every evaluation returns the default with `PROVIDER_FATAL`.

## Steps
(The steps below match the `steps` array one to one.)

### 1. Spec: regex conformance cases for ASCII folding, `\s`/`\v` and `$` before a final newline
See step detail.

### 2. Go module scaffold and engine value helpers with bucketing
### 3. Engine: strict SemVer and RFC 3339 dates
### 4. Engine: portable regex compiler
### 5. Engine: ruleset model and loader (evaluation.md §11)
### 6. Engine: Evaluate (§3–§9)
### 7. Conformance runner at 100 %
### 8. Benchmark and p99 guard on a 10 000-flag ruleset
### 9. Transport helpers: requests, status errors, Retry-After, backoff
### 10. Delivery: Store and on-disk Cache
### 11. Delivery: SSE line reader
### 12. Delivery: Streamer (connect, reconnect, dead timeout, stop rules)
### 13. Delivery: Poller with ETags
### 14. Delivery: Sync coordinator (stream first, poll while it keeps failing)
### 15. Events: exposure dedup LRU and hourly count aggregation
### 16. Events: background Batcher (flush triggers, 413 split, 422 drop, 429/5xx retry)
### 17. Provider: Config, NewProvider and the typed evaluations
### 18. Provider lifecycle: Init start-up order, OpenFeature events, Shutdown, event recording
### 19. End-to-end test against the compose stack
### 20. CI job, e2e job and the `sdks/go/v*` release trigger
### 21. Docs: SDK README, sdks/README status, CHANGELOG

The full detail of each step is in the `steps` entries. They are self-contained and use the names in Contracts C1–C5.

## Known residual (noted, not a design change)
Go's `regexp/syntax` folds a case-insensitive pattern while it parses, and it stores literal runes as their minimum fold rune. Step 4 therefore restores Python `re.ASCII` behaviour exactly for every ASCII letter: it splits literals into `[Xx]` classes and strips U+212A and U+017F from classes. It cannot recover the original case of a **non-ASCII** letter under `(?i)`, so `(?i)é` still matches `É` in Go and not in Python. This is documented in `regex.go` and the README. The conformance suite has no such case, and the approved design asks only for the ASCII cases.

## Final verification
```bash
# from api/
uv run pytest tests/test_conformance.py -q
# from sdks/go/
test -z "$(gofmt -l .)" && go vet ./... && go vet -tags e2e ./...
go test -race -count=1 ./...
go test -count=1 -run 'TestConformance|TestEvaluateP99' -v ./...
go test -run '^$' -bench BenchmarkEvaluate -benchmem ./internal/engine
# full stack (as in the sdk-go-e2e CI job)
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web
MEGOOFLAGS_SITE_ADDRESS= MEGOOFLAGS_SECRET_KEY=$(openssl rand -hex 32) docker compose -p megooflags-sdk-go \
  -f deploy/docker-compose.yml -f deploy/compose.ci.yml up -d --wait --wait-timeout 180 caddy
(cd sdks/go && MEGOOFLAGS_E2E_URL=http://localhost:8080 go test -tags e2e -count=1 -v ./e2e)
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```
Expected: all green; `TestConformance` reports every `<stem>::<name>` as PASS; the p99 printed is under 1 ms.
