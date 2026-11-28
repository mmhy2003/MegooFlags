# MegooFlags Flutter SDK

`megooflags` is the MegooFlags client SDK for Flutter, an [OpenFeature](https://openfeature.dev) provider.
It sends the evaluation context with a client key and receives the values the server evaluated, so no
targeting rule ever reaches the device. It follows changes over SSE with a polling fallback, caches the
last values on the device, can start from bootstrap values, and sends exposures in batches.

Status: 0.1.0, Beta: the API may still change before 1.0.0. Requires Dart 3.10 and Flutter 3.38.

## Install

```sh
flutter pub add megooflags openfeature_dart_client_sdk
```

The package depends on the [OpenFeature Dart SDK](https://pub.dev/packages/openfeature_dart_client_sdk)
(`openfeature_dart_client_sdk`), [`http`](https://pub.dev/packages/http) and
[`shared_preferences`](https://pub.dev/packages/shared_preferences), and on nothing else. The OpenFeature
Dart SDK is at 0.0.x, so its API may change in any release; this package pins it to `>=0.0.1 <0.1.0`.
Before 1.0 a minor release of `megooflags` may break the API too, so pin its minor version
(`megooflags: ^0.1.0`).

## Platforms

Android, iOS, macOS, Windows, Linux and web. Requests and the SSE stream use `package:http` on every
platform. Requests carry `User-Agent: megooflags-flutter/<version>`, except on the web, where the browser
does not let a page set it. On the web an integer read accepts whole numbers below 2^53 only.

## Quick start

```dart
import 'package:flutter/widgets.dart';
import 'package:megooflags/megooflags.dart';
import 'package:openfeature_dart_client_sdk/openfeature_dart_client_sdk.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final api = OpenFeatureAPI.instance;
  await api.setEvaluationContextAndWait(EvaluationContext(targetingKey: 'user-1'));
  await api.setProviderAndWait(
    MegooFlagsProvider(
      MegooFlagsConfig(
        sdkKey: 'mf_client_…', // a client key
        baseUrl: 'https://flags.example.com',
      ),
    ),
  );
  runApp(const MyApp());
}

// Anywhere in the app; reads are synchronous.
bool newCheckout() =>
    OpenFeatureAPI.instance.getClient().getBooleanValue('new-checkout', false);

// When the user changes, the provider asks the server for the new values.
Future<void> signedIn(String userId) =>
    OpenFeatureAPI.instance.setEvaluationContextAndWait(EvaluationContext(targetingKey: userId));
```

The targeting key is the context's `key` attribute in MegooFlags rules, and it is what percentage rollouts
bucket on by default. Reads serve the values the server last evaluated for the current context. Create one
provider per app, not one per screen, and call `OpenFeatureAPI.instance.shutdown()` when you are done with
it.

## Configuration

Every option of `MegooFlagsConfig(...)`. Durations are `Duration`.

| Option          | Default                     | Notes                                                                                                  |
| --------------- | --------------------------- | ------------------------------------------------------------------------------------------------------ |
| `sdkKey`        | (required)                  | A client key, `mf_client_…`. A server key (`mf_server_…`) or anything else is refused.                 |
| `baseUrl`       | (required unless `offline`) | The MegooFlags URL, `http` or `https`; trailing `/` are removed.                                       |
| `bootstrap`     | none                        | Values to start from, as JSON text shaped like an evaluate response: `{"results": [...]}`.             |
| `disableCache`  | `false`                     | Never read or write the cache.                                                                         |
| `offline`       | `false`                     | No network at all: serve the bootstrap or cached values only. One of them is required.                 |
| `initTimeout`   | 5 seconds                   | How long initialisation waits for the first values when neither bootstrap nor the cache gave any. > 0. |
| `disableStream` | `false`                     | Poll only, never open the SSE stream.                                                                  |
| `pollInterval`  | 30 seconds                  | Minimum 15 s; a smaller value is raised to 15 s.                                                       |
| `flushInterval` | 30 seconds                  | How often exposures are sent. Clamped to 10–60 s.                                                      |
| `sendContext`   | `false`                     | Include the evaluation context in exposures.                                                           |
| `logger`        | `dart:developer` `log`      | A `MegooFlagsLogger` function `(level, message)`; the default writes warnings and errors only.         |
| `httpClient`    | a new `http.Client`         | The `package:http` client for every request. The provider closes only the client it created.           |

An invalid configuration makes the constructor throw an `ArgumentError` whose message starts with
`megooflags:`.

## Client keys only

A client key can only ask the server to evaluate flags for a context; it never downloads rules or segments,
so it is safe to ship in an app. A server key must never ship in an app, and the constructor refuses one.
The key travels in the `Authorization` header only, never in a URL, and is never logged.

## Bootstrap

Pass values you ship with the app or received earlier, so the first screen has flags without waiting for
the network:

```dart
MegooFlagsConfig(
  sdkKey: sdkKey,
  baseUrl: baseUrl,
  bootstrap: '{"results":[{"key":"new-checkout","value":true,"variant":"true","reason":"TARGETING_MATCH","error_code":null}]}',
)
```

Each result needs `key`, `reason`, `value`, `variant` (string or `null`) and `error_code` (string or
`null`); this is the body of `POST /sdk/v1/evaluate`. An invalid bootstrap throws
`megooflags: invalid bootstrap: …`. When bootstrap or the cache gives values, initialisation completes at
once and the network refreshes them in the background.

## Cache and offline

- After every evaluation answer the provider writes the values, their context hash and ETag to
  `shared_preferences` (`SharedPreferencesAsync`) under the key
  `megooflags-<first 16 hex characters of sha256(sdkKey)>`; the key itself is never stored. At start, an
  entry for the same context replaces bootstrap and is served with reason `CACHED` until the network
  answers.
- When the targeting key changes, the old values and the cache entry are dropped at once, so one user's
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

The provider follows the app with an `AppLifecycleListener` on the `WidgetsBinding`, so initialise the
binding (`WidgetsFlutterBinding.ensureInitialized()` or `runApp`) before you create it; without a binding
there is no background signal. When the app is hidden (`onHide`) it sends the waiting exposures at once,
and after 30 s hidden it closes the stream and stops polling. When the app is shown again (`onShow`) it
reconnects with `Last-Event-ID`, so no change is missed. The SDK needs no background task or permission.

## Exposures and privacy

- An exposure is recorded when a read serves a value: one per targeting key, flag, variant and UTC hour
  (deduplicated in memory, 10 000 entries). Reads never wait on the network.
- They are sent to `POST /sdk/v1/events` every `flushInterval`, at 1000 waiting, when the app is hidden,
  and on shutdown (within 5 s). At most 10 000 wait; newer ones are dropped with a warning.
- An exposure carries no context unless `sendContext` is true, and the server stores the context only when
  it runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- 413 splits the batch, 422 drops it, 429 and 5xx keep it and retry with backoff.

## OpenFeature events

Listen with `OpenFeatureAPI.instance.addHandler(...)`, a client's `addHandler(...)`, or `provider.events`
(a broadcast `Stream<ProviderEvent>` that closes on shutdown).

| Event                  | When                                                                                                                                                                                    |
| ---------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ready`                | Initial values arrived, or the stream reconnected after `stale`.                                                                                                                        |
| `configurationChanged` | New values arrived; `flagsChanged` lists the flags whose value or variant changed.                                                                                                      |
| `stale`                | The stream disconnected; the last values keep being served.                                                                                                                             |
| `reconciling`          | Sent by the provider while the values for a new context are fetched.                                                                                                                    |
| `contextChanged`       | Sent by the provider when the values for the new context are in.                                                                                                                        |
| `error`                | The key was refused (401, 403 or a `revoked` stream event), with code `providerFatal`; or initialisation or a context change failed, with code `general` (the provider keeps retrying). |

A failed initialisation or context change throws an `OpenFeatureException` with that code from
`setProviderAndWait` or `setEvaluationContextAndWait`; with `general`, `ready` or `contextChanged` follows
when the values arrive.

## Errors

- **401, 403 or `revoked`** stops the stream, polling and exposures for that key, sends `error` with
  `providerFatal` and sets the status to `ProviderStatus.fatal`. The OpenFeature Dart SDK does not
  short-circuit reads in that state, so the provider keeps serving the last values it had (or your default
  with `providerNotReady` if it had none). Check `client.providerStatus` if you need to react.
- Before any value is known a read returns the default with `providerNotReady`; an unknown flag gives
  `flagNotFound`, a value of the wrong type `typeMismatch` (an integer read accepts only whole numbers that
  fit in 64 bits; a structure read accepts JSON objects, never arrays). Errors the server evaluated
  (`TYPE_MISMATCH`, `PARSE_ERROR`, `INVALID_CONTEXT`, `GENERAL`) are passed through. Every error returns
  your default with reason `ERROR`.

## Rate limit

The client endpoints allow 600 requests per minute per key and IP address. Many users behind one address
(an office, a carrier NAT) share that budget; keep `pollInterval` at its default and do not create a
provider per screen.

## `http://` URLs

Use an `https://` `baseUrl` in production. For a local server during development:

- **Android** 9 (API 28) and later block cleartext traffic. Allow it for that host in a
  [network security configuration](https://developer.android.com/privacy-and-security/security-config) of a
  debug build (`http://10.0.2.2:8080` reaches the host from the emulator). Release builds also need
  `android.permission.INTERNET` in the app's manifest.
- **iOS and macOS** block `http://` through App Transport Security. Add `NSAppTransportSecurity` ›
  `NSAllowsLocalNetworking` (or an `NSExceptionDomains` entry for your host) to the app's `Info.plist`.
  Sandboxed macOS apps also need the `com.apple.security.network.client` entitlement.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in
[AGENTS.md](https://github.com/mmhy2003/MegooFlags/blob/main/AGENTS.md); it starts at 0.1.0. Releases are
tagged `sdk-flutter/vX.Y.Z` in the repository, and only maintainers push those tags. `megooFlagsVersion`
holds the version.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/flutter/`, with Flutter 3.38:

```sh
flutter pub get
dart format --output=none --set-exit-if-changed . && flutter analyze
flutter test                     # unit tests and the shared cases in ../../spec/client
flutter pub publish --dry-run
```

The end-to-end test runs the provider on the Dart VM against a running stack and needs an API token (see
the `sdk-flutter-e2e` job in
[`.github/workflows/ci.yml`](https://github.com/mmhy2003/MegooFlags/blob/main/.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
flutter test e2e
```

Without both variables it is skipped. It keeps its cache in an in-memory `shared_preferences` store.
