# MegooFlags .NET SDK

The MegooFlags server SDK for .NET is an [OpenFeature](https://openfeature.dev) provider. It
downloads an environment's ruleset and evaluates flags in-process: there is no network call per
evaluation, and p99 is under 1 ms on a 10 000-flag ruleset. It follows changes over SSE with a
polling fallback, keeps serving when the server is unreachable, and sends evaluation counts and
exposures in the background.

Status: 0.1.0, Beta.

## Install

```sh
dotnet add package MegooFlags
```

The package targets `net8.0` and runs on .NET 8 or later. Its only dependency is the OpenFeature .NET
SDK (`OpenFeature` 2.x), which it brings in; it uses `HttpClient` and `System.Text.Json`.

## Quick start

```csharp
using MegooFlags;
using OpenFeature;
using OpenFeature.Model;

await Api.Instance.SetProviderAsync(new MegooFlagsProvider(new MegooFlagsOptions
{
    SdkKey = Environment.GetEnvironmentVariable("MEGOOFLAGS_SDK_KEY")!, // a server key, mf_server_…
    BaseUrl = "https://flags.example.com",
}));

var client = Api.Instance.GetClient();
var ctx = EvaluationContext.Builder()
    .SetTargetingKey("user-123")
    .Set("plan", "pro")
    .Build();
var enabled = await client.GetBooleanValueAsync("new-checkout", false, ctx);
Console.WriteLine($"new-checkout: {enabled}");

await Api.Instance.ShutdownAsync(); // closes the stream and flushes the buffered events
```

The targeting key (`user-123` above) is the context's `key` attribute in MegooFlags rules, and it is
what percentage rollouts bucket on by default.

`SetProviderAsync` does not throw when initialisation fails: check `client.ProviderStatus`, or
subscribe to `ProviderError`. Call `Api.Instance.ShutdownAsync()` when your service stops, so the
buffered events are sent.

## Configuration

Every property of `MegooFlagsOptions`. Durations are `TimeSpan`; zero or negative means the default,
and each property reads back the value the provider uses.

| Property        | Default                       | Notes                                                                                                          |
| --------------- | ----------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `SdkKey`        | (required)                    | A server key, `mf_server_…`. A client key is refused.                                                          |
| `BaseUrl`       | (required unless `Offline`)   | The MegooFlags URL, `http` or `https`, e.g. `https://flags.example.com`. Trailing slashes are removed.         |
| `BootstrapFile` | `null`                        | A ruleset JSON file to start from.                                                                             |
| `CacheDir`      | `null`                        | A directory for the last-known ruleset: read at start, written on each change.                                 |
| `Offline`       | `false`                       | No network at all: serve from `BootstrapFile` or `CacheDir` only. One of them is required.                     |
| `InitTimeout`   | 5 s                           | How long `InitializeAsync` waits for the first ruleset when neither the bootstrap file nor the cache gave one. |
| `DisableStream` | `false`                       | Poll only, never open the SSE stream.                                                                          |
| `PollInterval`  | 30 s                          | Minimum 15 s; a smaller value is raised to 15 s.                                                               |
| `FlushInterval` | 30 s                          | How often events are sent. Clamped to 10–60 s.                                                                 |
| `SendContext`   | `false`                       | Include the evaluation context in exposures.                                                                   |
| `Logger`        | `NullLogger.Instance`         | A `Microsoft.Extensions.Logging.ILogger`. The SDK key is never logged.                                         |
| `HttpClient`    | clients the provider creates  | The client for every request. It must not set `Timeout`: the stream is long-lived. Each request has its own.  |

An invalid configuration (no key, a client key, a bad `BaseUrl`, `Offline` with neither a bootstrap
file nor a cache directory) is logged. `InitializeAsync` then throws `ProviderFatalException`, the
provider status becomes fatal, and every evaluation returns the default value with
`ErrorType.ProviderFatal`.

### Logging

The SDK logs through `Microsoft.Extensions.Logging`. Nothing is logged unless you pass a logger, for
example one from your host's `ILoggerFactory`:

```csharp
var provider = new MegooFlagsProvider(new MegooFlagsOptions
{
    SdkKey = sdkKey,
    BaseUrl = "https://flags.example.com",
    Logger = loggerFactory.CreateLogger("MegooFlags"),
});
```

## Server keys only

This SDK downloads the whole ruleset, rules and segments included, so it needs a **server** key. A
client key (`mf_client_…`) is refused when the provider is created. Never ship a server key to
browsers or mobile apps: they use client keys, which only return evaluated values.

## How updates arrive

- The provider keeps an SSE stream open (`GET /sdk/v1/stream`). The server sends the full ruleset
  when the stream connects, and again after every change, including the kill switch.
- A dropped stream reconnects with exponential backoff and jitter (3 s to 60 s, or the server's
  `retry:`). A connection with no data or ping for 45 s is treated as dead.
- After the stream fails twice in a row, the provider also polls the ruleset every `PollInterval`
  (`GET /sdk/v1/ruleset` with `If-None-Match`) until the stream is back. With `DisableStream` it
  only polls.
- A ruleset that does not parse is logged and ignored; the previous one keeps being served.
- **401, 403 or a `revoked` event** stops streaming, polling and event sending for that key. The
  provider emits `ProviderError` with `ErrorType.ProviderFatal` and keeps serving the last ruleset.
- 5xx and network errors back off and retry; 429 waits for `Retry-After`. In both cases the last
  ruleset keeps being served.

## Offline and bootstrap

At start-up the provider looks for a ruleset in this order:

1. `BootstrapFile`;
2. the cache in `CacheDir`;
3. the network.

When the bootstrap file or the cache gives a valid ruleset, `InitializeAsync` returns at once and the
network refreshes it in the background. Otherwise `InitializeAsync` waits up to `InitTimeout` for the
first ruleset. After a timeout the provider is in the error state, keeps retrying, and emits
`ProviderReady` when a ruleset arrives. Until then every evaluation returns the default value with
`ErrorType.ProviderNotReady`.

With `Offline = true` the provider makes no network calls and sends no events. It serves the
bootstrap file or the cache, and initialisation fails if neither gives a valid ruleset.

The cache file is `<CacheDir>/megooflags-<first 16 hex characters of sha256(SdkKey)>.json`, so
several environments can share a directory and the key itself is never written to disk. It is
written atomically (a temp file, then a rename), with mode 0600 on Unix, each time a new ruleset
arrives from the stream or a poll.

A bootstrap file is a ruleset as returned by `GET /sdk/v1/ruleset`:

```sh
curl -H "Authorization: Bearer $MEGOOFLAGS_SDK_KEY" https://flags.example.com/sdk/v1/ruleset > ruleset.json
```

## Events

Unless `Offline`, the provider sends events to `POST /sdk/v1/events` from a background task, every
`FlushInterval`, when 1000 items are waiting, and on shutdown.

- **Evaluation counts** are aggregated per flag, variant and UTC hour. They never carry a context.
- **Exposures**: one per context key, flag, variant and UTC hour (deduplicated in memory, 10 000
  entries).
- An exposure carries no context unless `SendContext` is true, and the server stores it only when it
  runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- Evaluation never waits on events. If the buffer is full, events are dropped and counted.
- 429 and 5xx keep the batch and retry with backoff; 413 splits it; 422 drops it.
- `Api.Instance.ShutdownAsync()` (or `provider.ShutdownAsync()`) stops the sync and sends the
  buffered events within 5 s.

## OpenFeature events

| Event                          | When                                                                                                                                                      |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ProviderReady`                | `InitializeAsync` succeeded (sent by OpenFeature), or the first ruleset arrived after `InitializeAsync` timed out, or the stream reconnected after `ProviderStale`. |
| `ProviderConfigurationChanged` | A new ruleset was applied. `FlagsChanged` lists the flags added, removed or with a new version.                                                          |
| `ProviderStale`                | The stream disconnected; the last ruleset keeps being served.                                                                                            |
| `ProviderError`                | The key was refused (401, 403 or `revoked`), with `ErrorType.ProviderFatal`.                                                                             |

Subscribe with `client.AddHandler(ProviderEventTypes.ProviderConfigurationChanged, …)`, or the same
method on `Api.Instance`. Events are delivered from a background task, so a slow handler never
delays the next ruleset.

## Results

- `Reason` is one of `STATIC`, `DEFAULT`, `TARGETING_MATCH`, `SPLIT`, `DISABLED` or `ERROR`, plus the
  custom reason `PREREQUISITE_FAILED` when a prerequisite flag did not serve the required variation.
- `Variant` is the key of the served variation.
- `FlagMetadata` has `rule_index` (an integer: the 0-based index of the matching rule) and
  `prerequisite` (a string: the key of the prerequisite that failed), when they apply.
- On an error the value is always your default, and `ErrorType` and `ErrorMessage` say why. The
  provider returns the default itself, so this holds for the typed `Get*DetailsAsync` calls too.
- `GetIntegerValueAsync` returns an `int` and refuses, with `ErrorType.TypeMismatch`, a number flag
  whose value is not integral or does not fit in an `int`; `GetDoubleValueAsync` returns a `double`
  for any number flag. `GetObjectValueAsync` returns a `Value` tree; numbers in it are doubles.

## Context attributes

- Rules match top-level context attributes only. A nested structure is not looked into.
- Dates for `before` and `after` are RFC 3339 strings or epoch milliseconds. A `DateTime` in the
  context matches nothing; pass `dateTime.ToString("O")` or the epoch milliseconds.
- Regular expressions behave like Python's `re` with `re.ASCII`, the semantics every MegooFlags SDK
  reproduces, so a pattern evaluates the same everywhere: `(?i)` folds ASCII letters only, `\d`,
  `\w` and `\s` are ASCII, and `$` also matches before a final newline. The SDK translates each
  pattern to `System.Text.RegularExpressions` with those semantics. Patterns outside the portable
  subset (for example lookarounds, backreferences or `\p{…}`) never match; a ruleset with such a
  pattern is loaded and a warning is logged.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdk-dotnet/vX.Y.Z`, and only maintainers push those tags.
`MegooFlagsProvider.Version` holds the version, and requests send
`User-Agent: megooflags-dotnet/<Version>`.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/dotnet/`:

```sh
dotnet format --verify-no-changes
dotnet build -c Release
dotnet test -c Release --no-build --filter "Category!=P99&Category!=E2E"   # unit tests and conformance
dotnet test -c Release --no-build --filter "Category=P99"                  # the p99 guard, on its own
dotnet pack src/MegooFlags -c Release --no-build -o out
```

Add `-p:TestTargetFramework=net10.0` to `build` and `test` to run the tests on .NET 10. The
conformance runner reads `../../spec/conformance` and reports each case as `<file>::<name>`.

The end-to-end test needs a running stack and an API token (see the `sdk-dotnet-e2e` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
dotnet test -c Release --filter "Category=E2E"
```

Without both variables it is skipped.
