# MegooFlags Go SDK

The MegooFlags server SDK for Go is an [OpenFeature](https://openfeature.dev) provider. It downloads
an environment's ruleset and evaluates flags in-process: there is no network call per evaluation,
and p99 is under 1 ms on a 10 000-flag ruleset. It follows changes over SSE with a polling fallback,
keeps serving when the server is unreachable, and sends evaluation counts and exposures in the
background.

Status: 0.1.0, Beta.

## Install

```sh
go get github.com/mmhy2003/MegooFlags/sdks/go
```

The package name is `megooflags`. It needs Go 1.26 or later.

## Quick start

```go
package main

import (
	"context"
	"log"
	"os"

	"github.com/open-feature/go-sdk/openfeature"

	megooflags "github.com/mmhy2003/MegooFlags/sdks/go"
)

func main() {
	err := openfeature.SetProviderAndWait(megooflags.NewProvider(megooflags.Config{
		SDKKey:  os.Getenv("MEGOOFLAGS_SDK_KEY"), // a server key, mf_server_…
		BaseURL: "https://flags.example.com",
	}))
	if err != nil {
		log.Fatal(err)
	}
	defer openfeature.Shutdown() // flushes the buffered events

	client := openfeature.NewDefaultClient()
	evalCtx := openfeature.NewEvaluationContext("user-123", map[string]any{"plan": "pro"})
	enabled, err := client.BooleanValue(context.Background(), "new-checkout", false, evalCtx)
	if err != nil {
		log.Print(err) // enabled is the default value
	}
	log.Println("new-checkout:", enabled)
}
```

The targeting key (`user-123` above) is the context's `key` attribute in MegooFlags rules, and it is
what percentage rollouts bucket on by default.

## Configuration

Every field of `megooflags.Config`:

| Field | Default | Notes |
|---|---|---|
| `SDKKey` | (required) | A server key, `mf_server_…`. A client key is refused. |
| `BaseURL` | (required unless `Offline`) | The MegooFlags URL, `http` or `https`, e.g. `https://flags.example.com`. |
| `BootstrapFile` | none | A ruleset JSON file to start from. |
| `CacheDir` | none | A directory for the last-known ruleset: read at start, written on each change. |
| `Offline` | `false` | No network at all: serve from `BootstrapFile` or `CacheDir` only. One of them is required. |
| `InitTimeout` | 5s | How long `Init` waits for the first ruleset when neither the bootstrap file nor the cache gave one. |
| `DisableStream` | `false` | Poll only, never open the SSE stream. |
| `PollInterval` | 30s | Minimum 15s; a smaller value is raised to 15s. |
| `FlushInterval` | 30s | How often events are sent. Clamped to 10s–60s. |
| `SendContext` | `false` | Include the evaluation context in exposures. |
| `HTTPClient` | `&http.Client{}` | Must not set `Timeout`: the stream is long-lived. Each request carries its own deadline. |
| `Logger` | `slog.Default()` | Log output. The SDK key is never logged. |

An invalid config (no key, a client key, a bad `BaseURL`, `Offline` with neither a bootstrap file nor
a cache directory) is logged. `Init` then fails with `PROVIDER_FATAL`, and every evaluation returns
the default value with `PROVIDER_FATAL`.

## Server keys only

This SDK downloads the whole ruleset, rules and segments included, so it needs a **server** key. A
client key (`mf_client_…`) is refused when the provider is created. Never ship a server key to
browsers or mobile apps: they use client keys, which only return evaluated values.

## How updates arrive

- The provider keeps an SSE stream open (`GET /sdk/v1/stream`). The server sends the full ruleset
  when the stream connects, and again after every change, including the kill switch.
- A dropped stream reconnects with exponential backoff and jitter (3s to 60s, or the server's
  `retry:`). A connection with no data or ping for 45s is treated as dead.
- After the stream fails twice in a row, the provider also polls the ruleset every `PollInterval`
  (`GET /sdk/v1/ruleset` with `If-None-Match`) until the stream is back. With `DisableStream` it only polls.
- A ruleset that does not parse is logged and ignored; the previous one keeps being served.
- **401, 403 or a `revoked` event** stops streaming, polling and event sending for that key. The
  provider emits `PROVIDER_ERROR` with the fatal error code and keeps serving the last ruleset.
- 5xx and network errors back off and retry; 429 waits for `Retry-After`. In both cases the last
  ruleset keeps being served.

## Offline and bootstrap

At start-up the provider looks for a ruleset in this order:

1. `BootstrapFile`;
2. the cache in `CacheDir`;
3. the network.

When the bootstrap file or the cache gives a valid ruleset, `Init` returns at once and the network
refreshes it in the background. Otherwise `Init` waits up to `InitTimeout` for the first ruleset.
After a timeout the provider is in the error state, keeps retrying, and emits `PROVIDER_READY` when a
ruleset arrives. Until then every evaluation returns the default value with `PROVIDER_NOT_READY`.

With `Offline: true` the provider makes no network calls and sends no events. It serves the bootstrap
file or the cache, and `Init` fails if neither gives a valid ruleset.

The cache file is `<CacheDir>/megooflags-<first 16 hex characters of sha256(SDKKey)>.json`, so several
environments can share a directory and the key itself is never written to disk. It is written
atomically (a temp file, then a rename) with mode 0600, each time a new ruleset arrives from the
stream or a poll.

A bootstrap file is a ruleset as returned by `GET /sdk/v1/ruleset`:

```sh
curl -H "Authorization: Bearer $MEGOOFLAGS_SDK_KEY" https://flags.example.com/sdk/v1/ruleset > ruleset.json
```

## Events

Unless `Offline`, the provider sends events to `POST /sdk/v1/events` in the background, every
`FlushInterval`, when 1000 items are waiting, and on `Shutdown`.

- **Evaluation counts** are aggregated per flag, variant and UTC hour. They never carry a context.
- **Exposures**: one per context key, flag, variant and UTC hour (deduplicated in memory, 10 000
  entries).
- An exposure carries no context unless `SendContext` is true, and the server stores it only when it
  runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- Evaluation never blocks on events. If the buffer is full, events are dropped and counted.
- 429 and 5xx keep the batch and retry with backoff; 413 splits it; 422 drops it.
- `Shutdown` (or `openfeature.Shutdown()`) stops the sync and sends the buffered events within 5 s.

## OpenFeature events

| Event | When |
|---|---|
| `PROVIDER_READY` | `Init` succeeded (sent by OpenFeature), or the first ruleset arrived after `Init` timed out, or the stream reconnected after `PROVIDER_STALE`. |
| `PROVIDER_CONFIGURATION_CHANGED` | A new ruleset was applied. `FlagChanges` lists the flags added, removed or with a new version. |
| `PROVIDER_STALE` | The stream disconnected; the last ruleset keeps being served. |
| `PROVIDER_ERROR` | The key was refused (401, 403 or `revoked`), with the fatal error code. |

## Results

- `Reason` is one of `STATIC`, `DEFAULT`, `TARGETING_MATCH`, `SPLIT`, `DISABLED` or `ERROR`, plus the
  custom reason `PREREQUISITE_FAILED` when a prerequisite flag did not serve the required variation.
- `Variant` is the key of the served variation.
- `FlagMetadata` has `rule_index` (the 0-based index of the matching rule) and `prerequisite` (the key
  of the prerequisite that failed), when they apply.
- `IntValue` on a number flag whose value is not an integer returns the default with `TYPE_MISMATCH`.
  Use `FloatValue` for such flags.

## Context attributes

- Rules match top-level context attributes only. A nested object is not looked into.
- Dates for `before` and `after` are RFC 3339 strings or epoch milliseconds.
- Regular expressions behave like Python's `re` with `re.ASCII`, so a pattern evaluates the same in
  every MegooFlags SDK. One difference remains: under `(?i)`, a **non-ASCII** letter still matches its
  other case in Go (`(?i)é` matches `É`), which it does not in Python. ASCII letters, `\s` (which
  includes `\v`) and `$` before a final newline match exactly as in Python. A pattern that does not
  compile never matches.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdks/go/vX.Y.Z`, and only maintainers push those tags.
`megooflags.Version` holds the version, and requests send `User-Agent: megooflags-go/<Version>`.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/go/`:

```sh
gofmt -l .                     # prints nothing
go vet ./...
go test -race ./...            # unit tests, the conformance suite and the p99 guard
go test -run 'TestConformance|TestEvaluateP99' -v ./...
```

The conformance runner reads `../../spec/conformance` and reports each case as `<file>::<name>`.

The end-to-end test needs a running stack (see the `sdk-go-e2e` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
MEGOOFLAGS_E2E_URL=http://localhost:8080 go test -tags e2e -count=1 -v ./e2e
```

Without `MEGOOFLAGS_E2E_URL` it is skipped.
