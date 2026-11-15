# MegooFlags Android SDK

`io.github.mmhy2003:megooflags-android` is the MegooFlags client SDK for Android, an
[OpenFeature](https://openfeature.dev) provider. It sends the evaluation context with a client key and
receives the values the server evaluated, so no targeting rule ever reaches the device. It follows changes
over SSE with a polling fallback, caches the last values on the device, can start from bootstrap values,
and sends exposures in batches.

Status: 0.1.0, Alpha: the API may still change. Requires Android API 21 and Kotlin 2.1.

## Install

The library is published to Maven Central. In the app module's `build.gradle.kts`:

```kotlin
dependencies {
    implementation("io.github.mmhy2003:megooflags-android:0.1.0")
}
```

It brings the [OpenFeature Kotlin SDK](https://github.com/open-feature/kotlin-sdk) (`dev.openfeature:kotlin-sdk`
0.9.0) and `kotlinx-coroutines-core` with it; it has no other dependency (HTTP is `HttpURLConnection`, JSON is
`org.json`). Before 1.0 a minor release may break the API, so pin the minor version.

## Quick start

```kotlin
import dev.openfeature.kotlin.sdk.ImmutableContext
import dev.openfeature.kotlin.sdk.OpenFeatureAPI
import io.github.mmhy2003.megooflags.MegooFlagsConfig
import io.github.mmhy2003.megooflags.MegooFlagsProvider

val config =
    MegooFlagsConfig
        .Builder(applicationContext, "mf_client_…") // a client key
        .baseUrl("https://flags.example.com")
        .build()

// In a coroutine, for example lifecycleScope.launch { … } or in Application.onCreate.
OpenFeatureAPI.setProviderAndWait(MegooFlagsProvider(config), ImmutableContext(targetingKey = "user-1"))

val client = OpenFeatureAPI.getClient()
val enabled = client.getBooleanValue("new-checkout", false) // synchronous

// When the user changes, the provider asks the server for the new values.
OpenFeatureAPI.setEvaluationContextAndWait(ImmutableContext(targetingKey = "user-2"))
```

The targeting key is the context's `key` attribute in MegooFlags rules, and it is what percentage rollouts
bucket on by default. Reads are synchronous: they serve the values the server last evaluated for the
current context. Create one provider per app (in `Application.onCreate`, for example), not one per screen.

## Configuration

Every option of `MegooFlagsConfig.Builder(context, sdkKey)`. Durations are `kotlin.time.Duration`.

| Option          | Default                     | Notes                                                                                                   |
| --------------- | --------------------------- | ------------------------------------------------------------------------------------------------------- |
| `sdkKey`        | (required)                  | A client key, `mf_client_…`. A server key (`mf_server_…`) or anything else is refused.                  |
| `baseUrl`       | (required unless `offline`) | The MegooFlags URL, `http` or `https`; trailing `/` are removed.                                        |
| `bootstrap`     | none                        | Values to start from, as JSON text shaped like an evaluate response: `{"results": [...]}`.              |
| `disableCache`  | `false`                     | Never read or write the cache.                                                                          |
| `offline`       | `false`                     | No network at all: serve the bootstrap or cached values only. One of them is required.                  |
| `initTimeout`   | `5.seconds`                 | How long initialisation waits for the first values when neither bootstrap nor the cache gave any. > 0.  |
| `disableStream` | `false`                     | Poll only, never open the SSE stream.                                                                   |
| `pollInterval`  | `30.seconds`                | Minimum 15 s; a smaller value is raised to 15 s.                                                        |
| `flushInterval` | `30.seconds`                | How often exposures are sent. Clamped to 10–60 s.                                                       |
| `sendContext`   | `false`                     | Include the evaluation context in exposures.                                                            |
| `logger`        | Logcat, tag `MegooFlags`    | A `MegooFlagsLogger` (`debug`, `info`, `warn`, `error`). The SDK key is never logged.                   |

An invalid configuration makes `build()` throw an `IllegalArgumentException` whose message starts with
`megooflags:`.

## Client keys only

A client key can only ask the server to evaluate flags for a context; it never downloads rules or segments,
so it is safe to ship in an app. A server key must never ship in an app, and `build()` refuses one. The key
travels in the `Authorization` header only, never in a URL. Requests carry
`User-Agent: megooflags-android/<version>`.

## Bootstrap

Pass values you ship with the app or received earlier, so the first screen has flags without waiting for
the network:

```kotlin
MegooFlagsConfig
    .Builder(applicationContext, sdkKey)
    .baseUrl(baseUrl)
    .bootstrap("""{"results":[{"key":"new-checkout","value":true,"variant":"true","reason":"TARGETING_MATCH","error_code":null}]}""")
    .build()
```

Each result needs `key`, `reason`, `value`, `variant` (string or `null`) and `error_code` (string or
`null`); this is the body of `POST /sdk/v1/evaluate`. An invalid bootstrap throws
`megooflags: invalid bootstrap: …`. When bootstrap or the cache gives values, initialisation completes at
once and the network refreshes them in the background.

## Cache and offline

- After every evaluation answer the provider writes the values, their context hash and ETag to
  `files/megooflags/megooflags-<first 16 hex characters of sha256(sdkKey)>.json` in the app's private files
  directory (`Context.getFilesDir()`); the key itself is never stored. At start, an entry for the same
  context replaces bootstrap and is served with reason `CACHED` until the network answers.
- When the targeting key changes, the old values and the cache file are dropped at once, so one user's
  values are never served to another.
- Without the network the provider keeps serving the last values. With `offline()` it makes no network
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

The provider counts the app's started activities through `Application.ActivityLifecycleCallbacks` (pass any
`Context`; the builder uses its application context). When the last activity stops (not for a configuration
change such as a rotation), it sends the waiting exposures at once, and after 30 s in the background closes
the stream and stops polling. When an activity starts again it reconnects with `Last-Event-ID`, so no change
is missed. The SDK needs no service, no `WorkManager` and no background permission.

## Network permission and cleartext

The library's manifest declares `android.permission.INTERNET`, which the manifest merger adds to your app.

Android 9 (API 28) and later block cleartext `http://` traffic by default. Use an `https://` `baseUrl` in
production. For a local server during development (for example `http://10.0.2.2:8080` from the emulator),
allow cleartext for that host in a
[network security configuration](https://developer.android.com/privacy-and-security/security-config) of a
debug build.

## Exposures and privacy

- An exposure is recorded when a read serves a value: one per targeting key, flag, variant and UTC hour
  (deduplicated in memory, 10 000 entries). Reads never wait on the network.
- They are sent to `POST /sdk/v1/events` every `flushInterval`, at 1000 waiting, when the app goes to the
  background, and on `shutdown()` (within 5 s: blocking off the main thread, in the background on it). At
  most 10 000 wait; newer ones are dropped with a warning.
- An exposure carries no context unless `sendContext` is true, and the server stores the context only when
  it runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- 413 splits the batch, 422 drops it, 429 and 5xx keep it and retry with backoff.

## OpenFeature events

Observe them with `OpenFeatureAPI.observe<…>()` or `provider.observe()` (a `Flow`).

| Event                          | When                                                                                         |
| ------------------------------ | -------------------------------------------------------------------------------------------- |
| `ProviderReady`                | Initial values arrived, or the stream reconnected after `ProviderStale`.                     |
| `ProviderConfigurationChanged` | New values arrived; `flagsChanged` lists the flags whose value or variant changed.           |
| `ProviderStale`                | The stream disconnected; the last values keep being served.                                  |
| `ProviderError`                | The key was refused (401, 403 or a `revoked` stream event), with code `PROVIDER_FATAL`.      |

The Kotlin SDK has no reconciling or context-changed provider events: during `setEvaluationContext` the
OpenFeature SDK itself sets the status to `Reconciling`, then `Ready`, or `Error` when the provider throws.
A failed initialisation or context change throws `GeneralError` (the provider keeps retrying and sends
`ProviderReady` when the values arrive); a refused key throws `ProviderFatalError`.

## Errors

- **401, 403 or `revoked`** stops the stream, polling and exposures for that key and sends `ProviderError`
  with `PROVIDER_FATAL`. The OpenFeature Kotlin SDK 0.9 reports this as status `Error` (never `Fatal`), so
  it does not short-circuit reads: the provider keeps serving the last values it had (or your default with
  `PROVIDER_NOT_READY` if it had none). Check `OpenFeatureAPI.getStatus()` if you need to react.
- Before any value is known a read returns the default with `PROVIDER_NOT_READY`; an unknown flag gives
  `FLAG_NOT_FOUND`, a value of the wrong type `TYPE_MISMATCH` (an integer read accepts only whole numbers
  that fit in `Int`, a long read in `Long`). Errors the server evaluated (`TYPE_MISMATCH`, `PARSE_ERROR`,
  `INVALID_CONTEXT`, `GENERAL`) are passed through. Every error returns your default with reason `ERROR`.

## Rate limit

The client endpoints allow 600 requests per minute per key and IP address. Many users behind one address
(an office, a carrier NAT) share that budget; keep `pollInterval` at its default and do not create a
provider per screen.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it starts at
0.1.0. Releases are tagged `sdk-android/vX.Y.Z` in this repository, and only maintainers push those tags.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/android/`, with JDK 17 and the Android SDK (`ANDROID_HOME` or `local.properties`):

```sh
./gradlew spotlessCheck lint testDebugUnitTest assembleRelease   # unit tests and the shared cases in ../../spec/client
```

The end-to-end test runs the provider on the JVM against a running stack and needs an API token (see the
`sdk-android-e2e` job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
./gradlew testDebugUnitTest -Pe2e
```

Without both variables it is skipped. It keeps its cache in a temporary directory.
