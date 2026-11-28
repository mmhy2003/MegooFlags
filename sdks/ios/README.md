# MegooFlags iOS SDK

`MegooFlags` is the MegooFlags client SDK for iOS and macOS, a Swift package with an
[OpenFeature](https://openfeature.dev) provider. It sends the evaluation context with a client key and
receives the values the server evaluated, so no targeting rule ever reaches the device. It follows
changes over SSE with a polling fallback, caches the last values on the device, can start from
bootstrap values, and sends exposures in batches.

Status: 0.1.0, Beta: the API may still change before 1.0.0. Requires iOS 15 or macOS 12 and Swift 6.1 (Xcode 16.4).

## Install

The package is published to [`mmhy2003/megooflags-swift`](https://github.com/mmhy2003/megooflags-swift), a
read-only mirror of `sdks/ios` whose tags are plain `X.Y.Z`. In Xcode, choose **File › Add Package
Dependencies…** and enter `https://github.com/mmhy2003/megooflags-swift`, or add it to `Package.swift`:

```swift
dependencies: [
    .package(url: "https://github.com/mmhy2003/megooflags-swift", .upToNextMinor(from: "0.1.0")),
],
targets: [
    .target(
        name: "MyApp",
        dependencies: [
            .product(name: "MegooFlags", package: "megooflags-swift"),
            .product(name: "OpenFeature", package: "swift-sdk"),
        ]
    ),
]
```

It brings the [OpenFeature Swift SDK](https://github.com/open-feature/swift-sdk) (`0.7.x`) with it. Use
`.upToNextMinor`: before 1.0 a minor release may break the API.

There is no CocoaPods pod. The OpenFeature Swift SDK removed its own pod in 0.7.0, and the CocoaPods trunk
becomes read-only on 2 December 2026, so a pod could neither depend on OpenFeature nor be updated.

## Quick start

```swift
import MegooFlags
import OpenFeature

let provider = try MegooFlagsProvider(
    config: MegooFlagsConfig(
        sdkKey: "mf_client_…",  // a client key
        baseURL: URL(string: "https://flags.example.com")!))
await OpenFeatureAPI.shared.setProviderAndWait(
    provider: provider,
    initialContext: ImmutableContext(
        targetingKey: "user-123", structure: ImmutableStructure(attributes: ["plan": .string("pro")])))

let client = OpenFeatureAPI.shared.getClient()
let enabled = client.getBooleanValue(key: "new-checkout", defaultValue: false)  // synchronous

// When the user changes, the provider asks the server for the new values.
await OpenFeatureAPI.shared.setEvaluationContextAndWait(
    evaluationContext: ImmutableContext(targetingKey: "user-456"))
```

The targeting key is the context's `key` attribute in MegooFlags rules, and it is what percentage rollouts
bucket on by default. Reads are synchronous: they serve the values the server last evaluated for the
current context. Create one provider per app, not one per screen.

## Configuration

Every parameter of `MegooFlagsConfig(…)`. Durations are `TimeInterval` seconds.

| Parameter              | Default                     | Notes                                                                                                                                |
| ---------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `sdkKey`               | (required)                  | A client key, `mf_client_…`. A server key (`mf_server_…`) or anything else is refused.                                               |
| `baseURL`              | (required unless `offline`) | The MegooFlags URL, `http` or `https`; trailing `/` are removed.                                                                     |
| `bootstrap`            | `nil`                       | Values to start from, as JSON text shaped like an evaluate response: `{"results": [...]}`.                                           |
| `disableCache`         | `false`                     | Never read or write the cache.                                                                                                       |
| `offline`              | `false`                     | No network at all: serve the bootstrap or cached values only. One of them is required.                                               |
| `initTimeout`          | `5`                         | How long initialisation waits for the first values when neither bootstrap nor the cache gave any. > 0.                               |
| `disableStream`        | `false`                     | Poll only, never open the SSE stream.                                                                                                |
| `pollInterval`         | `30`                        | Minimum 15; a smaller value is raised to 15.                                                                                         |
| `flushInterval`        | `30`                        | How often exposures are sent. Clamped to 10–60.                                                                                      |
| `sendContext`          | `false`                     | Include the evaluation context in exposures.                                                                                         |
| `logger`               | `OSLogLogger()`             | A `MegooFlagsLogger`. The default writes to unified logging, subsystem `io.github.mmhy2003.megooflags`. The SDK key is never logged. |
| `sessionConfiguration` | `.default`                  | The `URLSessionConfiguration` for every request (proxies, extra headers, protocol classes).                                          |

An invalid configuration makes `MegooFlagsProvider(config:)` throw a `MegooFlagsConfigError` whose
description starts with `megooflags:`.

## Client keys only

A client key can only ask the server to evaluate flags for a context; it never downloads rules or segments,
so it is safe to ship in an app. A server key must never ship in an app, and the initialiser refuses one.
The key travels in the `Authorization` header only, never in a URL. Requests carry
`User-Agent: megooflags-ios/<version>`.

## Bootstrap

Pass values you ship with the app or received earlier, so the first screen has flags without waiting for
the network:

```swift
MegooFlagsConfig(
    sdkKey: sdkKey, baseURL: baseURL,
    bootstrap: #"{"results":[{"key":"new-checkout","value":true,"variant":"true","reason":"TARGETING_MATCH","error_code":null}]}"#)
```

Each result needs `key`, `reason`, `value`, `variant` (string or `null`) and `error_code` (string or
`null`); this is the body of `POST /sdk/v1/evaluate`. An invalid bootstrap throws
`megooflags: invalid bootstrap: …`. When bootstrap or the cache gives values, initialisation completes at
once and the network refreshes them in the background.

## Cache and offline

- After every evaluation answer the provider writes the values, their context hash and ETag to
  `Application Support/megooflags/megooflags-<first 16 hex characters of sha256(sdkKey)>.json` in the
  app's container; the key itself is never stored. At start, an entry for the same context replaces
  bootstrap and is served with reason `CACHED` until the network answers.
- When the targeting key changes, the old values and the cache file are dropped at once, so one user's
  values are never served to another.
- Without the network the provider keeps serving the last values. With `offline: true` it makes no network
  calls and sends no exposures; initialisation fails with
  `megooflags: offline mode needs bootstrap values or a cache entry` when there is nothing to serve.
- Write failures are logged as warnings and never fail an evaluation.

## How updates arrive

- The provider keeps an SSE stream open (`GET /sdk/v1/stream`). A `changed` event makes it ask for the new
  values (`POST /sdk/v1/evaluate` with `If-None-Match`).
- A dropped stream reconnects with backoff and jitter (3 s, or the server's `retry:`, up to 60 s). A
  connection with no data for 45 s is treated as dead. After two failures in a row the provider also polls
  every `pollInterval` until the stream is back.
- 5xx and network errors back off and keep serving the last values; 429 waits for `Retry-After`.

## Background

On iOS the provider follows `UIApplication.didEnterBackgroundNotification` and
`willEnterForegroundNotification`; on macOS `NSApplication.didHideNotification` and
`didUnhideNotification`. In the background it sends the waiting exposures at once, and after 30 s closes
the stream and stops polling (iOS suspends the app soon after anyway). In the foreground it reconnects
with `Last-Event-ID`, so no change is missed. The SDK needs no background mode.

## Exposures and privacy

- An exposure is recorded when a read serves a value: one per targeting key, flag, variant and UTC hour
  (deduplicated in memory, 10 000 entries). Reads never wait on the network.
- They are sent to `POST /sdk/v1/events` every `flushInterval`, at 1000 waiting, when the app goes to the
  background, and on `shutdown()` (within 5 s). At most 10 000 wait; newer ones are dropped with a
  warning.
- An exposure carries no context unless `sendContext` is true, and the server stores the context only when
  it runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- 413 splits the batch, 422 drops it, 429 and 5xx keep it and retry with backoff.

## OpenFeature events

Observe them with `OpenFeatureAPI.shared.observe()` or `provider.observe()` (Combine).

| Event                  | When                                                                                                                                                                                    |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ready`                | Initial values arrived, or the stream reconnected after `stale`.                                                                                                                        |
| `configurationChanged` | New values arrived; `flagsChanged` lists the flags whose value or variant changed.                                                                                                      |
| `stale`                | The stream disconnected; the last values keep being served.                                                                                                                             |
| `reconciling`          | Sent by the provider while the values for a new context are fetched.                                                                                                                    |
| `contextChanged`       | Sent by the provider when the values for the new context are in.                                                                                                                        |
| `error`                | The key was refused (401, 403 or a `revoked` stream event), with code `providerFatal`; or initialisation or a context change failed, with code `general` (the provider keeps retrying). |

## Errors

- **401, 403 or `revoked`** stops the stream, polling and exposures for that key, sends `error` with
  `providerFatal` and sets the status to `.fatal`. The OpenFeature Swift SDK does not short-circuit reads
  in that state, so the provider keeps serving the last values it had (or your default with
  `PROVIDER_NOT_READY` if it had none). Check `provider.status` if you need to react.
- Before any value is known a read returns the default with `PROVIDER_NOT_READY`; an unknown flag gives
  `FLAG_NOT_FOUND`, a value of the wrong type `TYPE_MISMATCH` (an integer read accepts only whole numbers
  that fit in `Int64`). Errors the server evaluated (`TYPE_MISMATCH`, `PARSE_ERROR`, `INVALID_CONTEXT`,
  `GENERAL`) are passed through. Every error returns your default with reason `ERROR`.

## Rate limit

The client endpoints allow 600 requests per minute per key and IP address. Many users behind one address
(an office, a carrier NAT) share that budget; keep `pollInterval` at its default and do not create a
provider per screen.

## App Transport Security

iOS and macOS apps block `http://` URLs by default. Use an `https://` `baseURL` in production. For a local
server during development, add `NSAppTransportSecurity` › `NSAllowsLocalNetworking` (or an
`NSExceptionDomains` entry for your host) to the app's `Info.plist`.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it starts at
0.1.0. Releases are tagged `sdk-ios/vX.Y.Z` in this repository, and only maintainers push those tags; the
release job publishes the mirror tag `X.Y.Z`. `megooFlagsVersion` holds the version.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/ios/`, on macOS with Xcode 16.4:

```sh
swift format lint --strict --recursive Sources Tests Package.swift
swift build
swift test --filter MegooFlagsTests   # unit tests and the shared cases in ../../spec/client
xcodebuild -scheme MegooFlags -destination 'generic/platform=iOS' build
```

The end-to-end test runs the provider on macOS against a running stack and needs an API token (see the
`sdk-ios-e2e` job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
swift test --filter MegooFlagsE2ETests
```

Without both variables it is skipped. It writes its cache to the user's Application Support folder.
