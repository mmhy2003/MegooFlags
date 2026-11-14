# MegooFlags Java SDK

The MegooFlags server SDK for Java is an [OpenFeature](https://openfeature.dev) provider. It
downloads an environment's ruleset and evaluates flags in-process: there is no network call per
evaluation, and p99 is under 1 ms on a 10 000-flag ruleset. It follows changes over SSE with a
polling fallback, keeps serving when the server is unreachable, and sends evaluation counts and
exposures in the background.

Status: 0.1.0, Alpha.

## Install

Maven:

```xml
<dependency>
  <groupId>io.github.mmhy2003</groupId>
  <artifactId>megooflags</artifactId>
  <version>0.1.0</version>
</dependency>
```

Gradle:

```kotlin
implementation("io.github.mmhy2003:megooflags:0.1.0")
```

The SDK needs Java 11 or later. Its only dependency is the OpenFeature Java SDK
(`dev.openfeature:sdk` 1.x), which it brings in; it uses `java.net.http.HttpClient` and has a small
internal JSON reader and writer.

## Quick start

```java
import dev.openfeature.sdk.Client;
import dev.openfeature.sdk.ImmutableContext;
import dev.openfeature.sdk.OpenFeatureAPI;
import dev.openfeature.sdk.Value;
import io.github.mmhy2003.megooflags.MegooFlagsConfig;
import io.github.mmhy2003.megooflags.MegooFlagsProvider;
import java.util.Map;

public class Main {
    public static void main(String[] args) {
        OpenFeatureAPI api = OpenFeatureAPI.getInstance();
        api.setProviderAndWait(new MegooFlagsProvider(MegooFlagsConfig.builder()
                .sdkKey(System.getenv("MEGOOFLAGS_SDK_KEY")) // a server key, mf_server_…
                .baseUrl("https://flags.example.com")
                .build()));

        Client client = api.getClient();
        boolean enabled = client.getBooleanValue(
                "new-checkout", false, new ImmutableContext("user-123", Map.of("plan", new Value("pro"))));
        System.out.println("new-checkout: " + enabled);

        api.shutdown(); // closes the stream and flushes the buffered events
    }
}
```

The targeting key (`user-123` above) is the context's `key` attribute in MegooFlags rules, and it is
what percentage rollouts bucket on by default.

The SDK's threads are daemon threads, so they do not keep the JVM alive. Call
`OpenFeatureAPI.getInstance().shutdown()` when your service stops, so the buffered events are sent.

## Configuration

Every option of `MegooFlagsConfig.builder()`; `build()` returns an immutable `MegooFlagsConfig`.
Durations are `java.time.Duration`; null, zero or negative means the default.

| Option          | Default                                             | Notes                                                                                                     |
| --------------- | --------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `sdkKey`        | (required)                                          | A server key, `mf_server_…`. A client key is refused.                                                     |
| `baseUrl`       | (required unless `offline`)                         | The MegooFlags URL, `http` or `https`, e.g. `https://flags.example.com`.                                  |
| `bootstrapFile` | `null`                                              | A ruleset JSON file to start from (`java.nio.file.Path`).                                                 |
| `cacheDir`      | `null`                                              | A directory for the last-known ruleset: read at start, written on each change.                            |
| `offline`       | `false`                                             | No network at all: serve from `bootstrapFile` or `cacheDir` only. One of them is required.                |
| `initTimeout`   | 5 s                                                 | How long `initialize` waits for the first ruleset when neither the bootstrap file nor the cache gave one. |
| `disableStream` | `false`                                             | Poll only, never open the SSE stream.                                                                     |
| `pollInterval`  | 30 s                                                | Minimum 15 s; a smaller value is raised to 15 s.                                                          |
| `flushInterval` | 30 s                                                | How often events are sent. Clamped to 10–60 s.                                                            |
| `sendContext`   | `false`                                             | Include the evaluation context in exposures.                                                              |
| `logger`        | `System.getLogger("io.github.mmhy2003.megooflags")` | A `System.Logger`. The SDK key is never logged.                                                           |
| `httpClient`    | an HTTP/1.1 `HttpClient`                            | The client for every request. It must not set a request timeout (the stream is long-lived).               |

An invalid config (no key, a client key, a bad `baseUrl`, `offline` with neither a bootstrap file nor
a cache directory) is logged. `initialize` then throws `FatalError`, and every evaluation returns
the default value with `PROVIDER_FATAL`.

If you pass your own `httpClient` and talk to MegooFlags over plain `http`, build it with
`.version(HttpClient.Version.HTTP_1_1)`: the JDK's default asks for an `h2c` upgrade, which the
MegooFlags API refuses on requests with a body, so events would not be sent.

### Logging

The SDK logs through `java.lang.System.Logger` (`System.getLogger("io.github.mmhy2003.megooflags")`
by default). Without a bridge that goes to `java.util.logging`. To send it to SLF4J, Logback or
Log4j 2, add the bridge your logging framework ships for the platform logger, for example
`org.slf4j:slf4j-jdk-platform-logging` or `org.apache.logging.log4j:log4j-jpl`, or pass your own
`System.Logger` with `.logger(…)`. The OpenFeature SDK itself logs through SLF4J.

## Server keys only

This SDK downloads the whole ruleset, rules and segments included, so it needs a **server** key. A
client key (`mf_client_…`) is refused when the provider is created. Never ship a server key to
browsers or mobile apps: they use client keys, which only return evaluated values.

## How updates arrive

- The provider keeps an SSE stream open (`GET /sdk/v1/stream`). The server sends the full ruleset
  when the stream connects, and again after every change, including the kill switch.
- A dropped stream reconnects with exponential backoff and jitter (3 s to 60 s, or the server's
  `retry:`). A connection with no data or ping for 45 s is treated as dead.
- After the stream fails twice in a row, the provider also polls the ruleset every `pollInterval`
  (`GET /sdk/v1/ruleset` with `If-None-Match`) until the stream is back. With `disableStream` it
  only polls.
- A ruleset that does not parse is logged and ignored; the previous one keeps being served.
- **401, 403 or a `revoked` event** stops streaming, polling and event sending for that key. The
  provider emits `PROVIDER_ERROR` with the fatal error code and keeps serving the last ruleset.
- 5xx and network errors back off and retry; 429 waits for `Retry-After`. In both cases the last
  ruleset keeps being served.

## Offline and bootstrap

At start-up the provider looks for a ruleset in this order:

1. `bootstrapFile`;
2. the cache in `cacheDir`;
3. the network.

When the bootstrap file or the cache gives a valid ruleset, `initialize` returns at once and the
network refreshes it in the background. Otherwise `initialize` waits up to `initTimeout` for the
first ruleset. After a timeout the provider is in the error state, keeps retrying, and emits
`PROVIDER_READY` when a ruleset arrives. Until then every evaluation returns the default value with
`PROVIDER_NOT_READY`.

With `offline(true)` the provider makes no network calls and sends no events. It serves the
bootstrap file or the cache, and `initialize` throws if neither gives a valid ruleset.

The cache file is `<cacheDir>/megooflags-<first 16 hex characters of sha256(sdkKey)>.json`, so
several environments can share a directory and the key itself is never written to disk. It is
written atomically (a temp file, then a rename) with mode 0600, each time a new ruleset arrives from the stream or a poll.

A bootstrap file is a ruleset as returned by `GET /sdk/v1/ruleset`:

```sh
curl -H "Authorization: Bearer $MEGOOFLAGS_SDK_KEY" https://flags.example.com/sdk/v1/ruleset > ruleset.json
```

## Events

Unless `offline`, the provider sends events to `POST /sdk/v1/events` from a background thread,
every `flushInterval`, when 1000 items are waiting, and on shutdown.

- **Evaluation counts** are aggregated per flag, variant and UTC hour. They never carry a context.
- **Exposures**: one per context key, flag, variant and UTC hour (deduplicated in memory, 10 000
  entries).
- An exposure carries no context unless `sendContext` is true, and the server stores it only when it
  runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- Evaluation never waits on events. If the buffer is full, events are dropped and counted.
- 429 and 5xx keep the batch and retry with backoff; 413 splits it; 422 drops it.
- `OpenFeatureAPI.getInstance().shutdown()` (or `provider.shutdown()`) stops the sync and sends the
  buffered events within 5 s.

## OpenFeature events

| Event                            | When                                                                                                                                                       |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PROVIDER_READY`                 | `initialize` succeeded (sent by OpenFeature), or the first ruleset arrived after `initialize` timed out, or the stream reconnected after `PROVIDER_STALE`. |
| `PROVIDER_CONFIGURATION_CHANGED` | A new ruleset was applied. `getFlagsChanged()` lists the flags added, removed or with a new version.                                                       |
| `PROVIDER_STALE`                 | The stream disconnected; the last ruleset keeps being served.                                                                                              |
| `PROVIDER_ERROR`                 | The key was refused (401, 403 or `revoked`), with error code `PROVIDER_FATAL`.                                                                             |

Subscribe with `client.onProviderConfigurationChanged(…)`, `onProviderStale`, `onProviderReady` and
`onProviderError`, or the same methods on `OpenFeatureAPI`.

## Results

- `reason` is one of `STATIC`, `DEFAULT`, `TARGETING_MATCH`, `SPLIT`, `DISABLED` or `ERROR`, plus the
  custom reason `PREREQUISITE_FAILED` when a prerequisite flag did not serve the required variation.
- `variant` is the key of the served variation.
- The flag metadata has `rule_index` (an integer: the 0-based index of the matching rule) and
  `prerequisite` (a string: the key of the prerequisite that failed), when they apply.
- On an error, the value is your default and the error code and message say why.
- `getIntegerValue` returns an `Integer` and refuses, with `TYPE_MISMATCH`, a number flag whose value
  is not integral or does not fit in an `int`; `getDoubleValue` returns a `Double` for any number
  flag. `getObjectValue` returns a `Value` tree; integers in it are `Integer` when they fit, else
  `Long`.

## Context attributes

- Rules match top-level context attributes only. A nested structure is not looked into.
- Dates for `before` and `after` are RFC 3339 strings or epoch milliseconds. An `Instant` in the
  context matches nothing; pass `instant.toString()` or `instant.toEpochMilli()`.
- Regular expressions behave like Python's `re` with `re.ASCII`, the semantics every MegooFlags SDK
  reproduces, so a pattern evaluates the same everywhere: `(?i)` folds ASCII letters only, `\d`,
  `\w` and `\s` are ASCII, and `$` also matches before a final newline. The SDK translates each
  pattern to `java.util.regex` with those semantics. Patterns outside the portable subset (for
  example lookarounds, backreferences or `\p{…}`) never match; a ruleset with such a pattern is
  loaded and a warning is logged.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdk-java/vX.Y.Z`, and only maintainers push those tags.
`MegooFlagsProvider.VERSION` holds the version, and requests send
`User-Agent: megooflags-java/<VERSION>`.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/java/`:

```sh
mvn -B -ntp verify        # compile with -Xlint:all -Werror, unit tests, conformance, jar; format check on JDK 17+
mvn -B -ntp test -Pp99    # the p99 guard, on its own
mvn spotless:apply        # fix the formatting (JDK 17+)
```

The conformance runner reads `../../spec/conformance` and reports each case as `<file>::<name>`.

The end-to-end test needs a running stack and an API token (see the `sdk-java-e2e` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
mvn -B -ntp test -Pe2e
```

Without both variables it is skipped.
