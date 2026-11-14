# MF-83 (part 1): Go server SDK as an OpenFeature provider

## Goal
A Go back-end service can call `openfeature.SetProviderAndWait(megooflags.NewProvider(cfg))` with a server SDK key and evaluate flags in-process, with p99 under 1 ms and no network call per evaluation. The SDK:
- downloads the environment ruleset and keeps it current over SSE, falling back to polling;
- keeps serving when the API is down, and starts from a bootstrap file or the last-known ruleset on disk;
- sends evaluation counts and deduplicated exposures in background batches.

It passes 100 % of `spec/conformance` and an end-to-end test against the running stack in CI. It ships under Apache 2.0 as the Go module tagged `sdks/go/vX.Y.Z`.

## Decisions
- **Scope: Go only, one PR (your answer).** Node.js, Python, Java, .NET, Ruby and PHP each get their own design later, using this SDK as the template. *Rejected:* Go and Node together; a shared-groundwork step first.
- **Standard library only, plus `github.com/open-feature/go-sdk` (your answer).** The SSE reader, polling loop, strict SemVer 2.0.0 parser and RFC 3339 parser are all written by hand on `net/http`, `crypto/sha256`, `regexp/syntax` and `encoding/json`. This gives full control over the stop, retry and backoff rules in `delivery.md` §3 and §6. *Rejected:* third-party SSE and semver libraries, whose parsing is too lenient and whose reconnect logic is their own; a split core module plus provider module, which means two tags.
- **Exposures (your answer):** one exposure per (context `key`, flag, variant, UTC hour), deduplicated by a bounded LRU (10 000 entries by default). Evaluation counts are always aggregated per flag, variant and UTC hour. *Rejected:* an exposure on every evaluation; exposures off by default.
- **Module path** is `github.com/mmhy2003/MegooFlags/sdks/go`, which matches the GitHub repository's exact case, and the package name is `megooflags`. The version starts at `0.1.0` (Alpha, `0.Y.Z` rules in AGENTS.md §4). Agents never push tags.
- **Context privacy:** exposures carry no context unless `Config.SendContext` is true (`delivery.md` §7).

## Architecture (`sdks/go/`)
| Package | Purpose |
|---|---|
| `internal/engine` | `Parse([]byte) (*Ruleset, error)` validates the ruleset as in `evaluation.md` §11 and prepares clause values (compiled regexes, parsed semvers, epoch milliseconds, segment sets). `Evaluate(rs, flag, ctx, default, expectedType) Result` implements §3–§9: targets, rules, operators, segments, SHA-256 bucketing, prerequisites with depth guard 32, and expected-type checks. It is pure, with no I/O |
| `internal/engine/regex.go` | Compiles portable patterns so Go behaves like Python `re.ASCII`. It parses with `regexp/syntax` and rewrites the tree so that case-insensitive literals and classes fold ASCII only (Go folds `K`/U+212A and `s`/U+017F) and `\s` includes `\v`. It also reproduces Python's `$` matching before a final `\n` by retrying on the string without its trailing newline when the pattern ends in `$`. A pattern Go cannot compile never matches (rule 6) |
| `internal/delivery` | `Store` is an `atomic.Pointer[*engine.Ruleset]` plus the ETag. `Streamer` is the SSE client for `GET /sdk/v1/stream` (`retry`, `: ping`, `put`, `revoked`, unknown events ignored, `Last-Event-ID`, a 45 s dead-connection timeout, exponential backoff with jitter from 3 s to 60 s, reset after 60 s connected). `Poller` handles `GET /sdk/v1/ruleset` with `If-None-Match` every 30 s (never under 15 s), only while the stream keeps failing. `Cache` persists the last valid ruleset and its ETag atomically (temp file and rename) to `Config.CacheDir` |
| `internal/events` | The batcher has an LRU dedup for exposures, an hour-bucket map for counts, and a bounded buffer. It flushes every 30 s (configurable from 10 to 60 s), when a list reaches 1000 items, and on `Shutdown`. On 413 it splits the batch, on 422 it drops it, and on 429 or 5xx it keeps the batch and retries with backoff (respecting `Retry-After`). Evaluation only enqueues to a channel without blocking; on overflow it drops and counts the drop |
| `provider.go` | `Provider` implements `openfeature.FeatureProvider`, `StateHandler` (`Init` and `Shutdown`) and `EventHandler` (it emits `PROVIDER_CONFIGURATION_CHANGED` on a new ruleset, `PROVIDER_STALE` while disconnected, `PROVIDER_READY` when reconnected and `PROVIDER_ERROR` with fatal code on 401/403/`revoked`). It maps `targetingKey` to `key` and fills `FlagMetadata` with `rule_index` and `prerequisite`. `PREREQUISITE_FAILED` is passed through as a custom reason. `IntEvaluation` returns `TYPE_MISMATCH` for a non-integral number |
| `conformance_test.go` | Runs every `../../spec/conformance/cases/*.json` and `bucketing.json` file with the runner algorithm of `spec/conformance/README.md`. It refuses any other `schema_version` and reports `<stem>::<name>` |

## Data and interfaces
```go
type Config struct {
    SDKKey        string        // mf_server_…; a client key is refused at NewProvider
    BaseURL       string        // e.g. https://flags.example.com
    BootstrapFile string        // optional ruleset JSON file
    CacheDir      string        // optional; last-known ruleset is read at start, written on change
    Offline       bool          // no network: bootstrap/cache only
    InitTimeout   time.Duration // default 5s
    DisableStream bool          // poll only
    PollInterval  time.Duration // default 30s, clamped to >= 15s
    FlushInterval time.Duration // default 30s, clamped 10–60s
    SendContext   bool          // include context in exposures
    HTTPClient    *http.Client
    Logger        *slog.Logger
}
```
- **Start-up order:** bootstrap file, then cache, then network. If the bootstrap file or cache gives a valid ruleset, `Init` returns ready at once and the network refreshes it in the background. Otherwise `Init` waits up to `InitTimeout` for the first `put`/200, and then returns an error, leaving the provider in the error state while it keeps retrying.
- **No server changes.** The SDK uses only `/sdk/v1/ruleset`, `/sdk/v1/stream` and `/sdk/v1/events` as specified in `spec/delivery.md`.

## Error handling and edge cases
- An invalid ruleset (from `put`, a poll, the bootstrap file or the cache) keeps the previous ruleset, is logged and never panics. With no ruleset at all, every evaluation returns the default with `PROVIDER_NOT_READY`.
- 401, 403 or `revoked` stops streaming, polling and event sending for that key. The SDK keeps serving the last ruleset and reports a fatal error.
- 5xx and network errors back off and keep serving. 429 waits `Retry-After`.
- A non-object context cannot happen through OpenFeature. Engine tests cover it directly.
- `Shutdown` stops the stream and the poller, flushes events within a 5 s deadline, and is idempotent.
- The evaluation path is lock-free (an atomic load) and does no I/O. A benchmark guards the p99.

## Testing (TDD)
- **Unit tests** for the parser, each operator, the regex rewrite (ASCII case folding, `\s`/`\v`, `$` before `\n`), semver, dates, bucketing, the SSE line parser, backoff and stop rules, poller ETags, cache write and read, and batcher dedup, split, drop and retry. The HTTP tests use `httptest`.
- **Conformance:** 100 % of the cases. This PR also adds regex cases for ASCII case folding, `\s` with `\v`, and `$` before a final newline to `spec/conformance/cases/operators_regex.json`, and the Python reference engine must still pass them.
- **Race and benchmark:** `go test -race ./...`, and a `BenchmarkEvaluate` on a 10 000-flag ruleset that asserts p99 under 1 ms.
- **End to end** (`sdks/go/e2e`, build tag `e2e`): against the compose stack, the test signs in or creates a service token through `/api/setup` and `/api/tokens`. It creates a project, a non-approval environment, a flag and a server key, then starts the provider and checks four things: the evaluated value, a toggle arriving over SSE within 5 s, events being accepted, and offline serving from the cache after the API stops.
- **CI:** a new `sdk-go` job runs `gofmt -l`, `go vet`, `go test -race` and the conformance tests. A job using the existing compose stack runs the e2e test. `release.yml` gains an `sdks/go/v*` trigger that re-runs the tests and warms `proxy.golang.org`.
- **Docs:** `sdks/go/README.md` (usage, config, offline and bootstrap), the `sdks/README.md` status, and a CHANGELOG `[Unreleased]` entry.

## Out of scope
The Node.js, Python, Java, .NET, Ruby and PHP SDKs (separate designs); client and browser SDKs; any API change; the edge relay; a non-OpenFeature public client API.
