# MegooFlags Node.js SDK

The MegooFlags server SDK for Node.js is an [OpenFeature](https://openfeature.dev) provider. It
downloads an environment's ruleset and evaluates flags in-process: there is no network call per
evaluation, and p99 is under 1 ms on a 10 000-flag ruleset. It follows changes over SSE with a
polling fallback, keeps serving when the server is unreachable, and sends evaluation counts and
exposures in the background.

Status: 0.1.0, Alpha.

## Install

```sh
pnpm add @megooflags/node @openfeature/server-sdk
```

The package is ESM only and needs Node.js 22 or later. `require()` of this package from CommonJS
needs Node.js 22.12 or later; on earlier 22.x releases use `import`. `@openfeature/server-sdk` is a
peer dependency (`^1.23.0`).

## Quick start

```js
import { OpenFeature } from "@openfeature/server-sdk";
import { MegooFlagsProvider } from "@megooflags/node";

await OpenFeature.setProviderAndWait(
  new MegooFlagsProvider({
    sdkKey: process.env.MEGOOFLAGS_SDK_KEY, // a server key, mf_server_…
    baseUrl: "https://flags.example.com",
  }),
);

const client = OpenFeature.getClient();
const enabled = await client.getBooleanValue("new-checkout", false, {
  targetingKey: "user-123",
  plan: "pro",
});
console.log("new-checkout:", enabled);

await OpenFeature.close(); // closes the stream and flushes the buffered events
```

The targeting key (`user-123` above) is the context's `key` attribute in MegooFlags rules, and it is
what percentage rollouts bucket on by default.

The open stream keeps the process alive until `OpenFeature.close()` is called (the SDK's timers do
not). Call it when your service shuts down, for example on `SIGTERM`.

## Configuration

Every option of `new MegooFlagsProvider({ … })` (the `MegooFlagsConfig` type). Durations are in
milliseconds.

| Option            | Default                     | Notes                                                                                                                 |
| ----------------- | --------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `sdkKey`          | (required)                  | A server key, `mf_server_…`. A client key is refused.                                                                 |
| `baseUrl`         | (required unless `offline`) | The MegooFlags URL, `http` or `https`, e.g. `https://flags.example.com`.                                              |
| `bootstrapFile`   | none                        | A ruleset JSON file to start from.                                                                                    |
| `cacheDir`        | none                        | A directory for the last-known ruleset: read at start, written on each change.                                        |
| `offline`         | `false`                     | No network at all: serve from `bootstrapFile` or `cacheDir` only. One of them is required.                            |
| `initTimeoutMs`   | `5000`                      | How long `initialize` waits for the first ruleset when neither the bootstrap file nor the cache gave one.             |
| `disableStream`   | `false`                     | Poll only, never open the SSE stream.                                                                                 |
| `pollIntervalMs`  | `30000`                     | Minimum 15 000; a smaller value is raised to 15 000.                                                                  |
| `flushIntervalMs` | `30000`                     | How often events are sent. Clamped to 10 000–60 000.                                                                  |
| `sendContext`     | `false`                     | Include the evaluation context in exposures.                                                                          |
| `logger`          | `console`                   | An object with `debug`, `info`, `warn` and `error`. The SDK key is never logged.                                      |
| `fetch`           | `globalThis.fetch`          | The `fetch` used for every request, for example one that goes through a proxy. Each request carries its own deadline. |

An invalid config (no key, a client key, a bad `baseUrl`, `offline` with neither a bootstrap file
nor a cache directory) is logged. `initialize` then rejects with `ProviderFatalError`, and every
evaluation returns the default value with `PROVIDER_FATAL`.

## Server keys only

This SDK downloads the whole ruleset, rules and segments included, so it needs a **server** key. A
client key (`mf_client_…`) is refused when the provider is created. Never ship a server key to
browsers or mobile apps: they use client keys, which only return evaluated values.

## How updates arrive

- The provider keeps an SSE stream open (`GET /sdk/v1/stream`). The server sends the full ruleset
  when the stream connects, and again after every change, including the kill switch.
- A dropped stream reconnects with exponential backoff and jitter (3 s to 60 s, or the server's
  `retry:`). A connection with no data or ping for 45 s is treated as dead.
- After the stream fails twice in a row, the provider also polls the ruleset every `pollIntervalMs`
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

When the bootstrap file or the cache gives a valid ruleset, `initialize` resolves at once and the
network refreshes it in the background. Otherwise `initialize` waits up to `initTimeoutMs` for the
first ruleset. After a timeout the provider is in the error state, keeps retrying, and emits
`PROVIDER_READY` when a ruleset arrives. Until then every evaluation returns the default value with
`PROVIDER_NOT_READY`.

With `offline: true` the provider makes no network calls and sends no events. It serves the
bootstrap file or the cache, and `initialize` rejects if neither gives a valid ruleset.

The cache file is `<cacheDir>/megooflags-<first 16 hex characters of sha256(sdkKey)>.json`, so
several environments can share a directory and the key itself is never written to disk. It is
written atomically (a temp file, then a rename) with mode 0600, each time a new ruleset arrives from
the stream or a poll.

A bootstrap file is a ruleset as returned by `GET /sdk/v1/ruleset`:

```sh
curl -H "Authorization: Bearer $MEGOOFLAGS_SDK_KEY" https://flags.example.com/sdk/v1/ruleset > ruleset.json
```

## Events

Unless `offline`, the provider sends events to `POST /sdk/v1/events` in the background, every
`flushIntervalMs`, when 1000 items are waiting, and on close.

- **Evaluation counts** are aggregated per flag, variant and UTC hour. They never carry a context.
- **Exposures**: one per context key, flag, variant and UTC hour (deduplicated in memory, 10 000
  entries).
- An exposure carries no context unless `sendContext` is true, and the server stores it only when it
  runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- Evaluation never waits on events. When 10 000 exposures are waiting, new exposures are dropped
  and counted; evaluation counts are never dropped.
- 429 and 5xx keep the batch and retry with backoff; 413 splits it; 422 drops it.
- `OpenFeature.close()` stops the sync and sends the buffered events within 5 s.

## OpenFeature events

| Event                            | When                                                                                                                                                       |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PROVIDER_READY`                 | `initialize` succeeded (sent by OpenFeature), or the first ruleset arrived after `initialize` timed out, or the stream reconnected after `PROVIDER_STALE`. |
| `PROVIDER_CONFIGURATION_CHANGED` | A new ruleset was applied. `flagsChanged` lists the flags added, removed or with a new version.                                                            |
| `PROVIDER_STALE`                 | The stream disconnected; the last ruleset keeps being served.                                                                                              |
| `PROVIDER_ERROR`                 | The key was refused (401, 403 or `revoked`), with error code `PROVIDER_FATAL`.                                                                             |

## Results

- `reason` is one of `STATIC`, `DEFAULT`, `TARGETING_MATCH`, `SPLIT`, `DISABLED` or `ERROR`, plus the
  custom reason `PREREQUISITE_FAILED` when a prerequisite flag did not serve the required variation.
- `variant` is the key of the served variation.
- `flagMetadata` has `rule_index` (the 0-based index of the matching rule) and `prerequisite` (the
  key of the prerequisite that failed), when they apply.
- On an error, `value` is your default and `errorCode` and `errorMessage` say why.
- JavaScript has one number type, so `getNumberValue` serves integers and decimals alike.

## Context attributes

- Rules match top-level context attributes only. A nested object is not looked into.
- Dates for `before` and `after` are RFC 3339 strings or epoch milliseconds. A `Date` object in the
  context matches nothing; pass `date.toISOString()` or `date.getTime()`.
- Regular expressions behave like Python's `re` with `re.ASCII`, so a pattern evaluates the same in
  every MegooFlags SDK: `(?i)` folds ASCII letters only, `\d`, `\w` and `\s` are ASCII (`\s` includes
  `\v`), and `$` also matches before a final newline. Patterns outside the portable subset (for
  example lookarounds, backreferences or `\p{…}`) do not compile, and a pattern that does not compile
  never matches.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdk-node/vX.Y.Z`, and only maintainers push those tags. The
`VERSION` export holds the version, and requests send `User-Agent: megooflags-node/<VERSION>`.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/node/`:

```sh
pnpm install --frozen-lockfile
pnpm format:check && pnpm lint && pnpm typecheck
pnpm test        # unit tests and the conformance suite
pnpm test:p99    # the p99 guard, on its own
pnpm build
```

The conformance runner reads `../../spec/conformance` and reports each case as `<file>::<name>`.

The end-to-end test needs a running stack and an API token (see the `sdk-node-e2e` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
pnpm e2e
```

Without both variables it is skipped.
