# MegooFlags JavaScript SDK

`@megooflags/js` is the MegooFlags client SDK for browsers, an [OpenFeature](https://openfeature.dev)
web provider. It sends the evaluation context with a client key and receives the values the server
evaluated, so no targeting rule ever reaches the browser. It follows changes over SSE with a polling
fallback, caches the last values in `localStorage`, can start from bootstrap values, and sends
exposures in batches.

Status: 0.1.0, Alpha: the API may still change.

## Install

```sh
npm i @megooflags/js @openfeature/web-sdk
```

`@openfeature/web-sdk` (`^1.10.0`) is a peer dependency. npm and pnpm install it for you; Yarn users
add it by hand, as in the command above. The package is ESM only and targets evergreen browsers
(ES2020).

## Quick start

```js
import { OpenFeature } from "@openfeature/web-sdk";
import { MegooFlagsWebProvider } from "@megooflags/js";

await OpenFeature.setProviderAndWait(
  new MegooFlagsWebProvider({
    sdkKey: "mf_client_…", // a client key
    baseUrl: "https://flags.example.com",
  }),
  { targetingKey: "user-123", plan: "pro" },
);

const client = OpenFeature.getClient();
const enabled = client.getBooleanValue("new-checkout", false); // synchronous

// When the user changes, the provider asks the server for the new values.
await OpenFeature.setContext({ targetingKey: "user-456" });
```

The targeting key is the context's `key` attribute in MegooFlags rules, and it is what percentage
rollouts bucket on by default. Reads are synchronous: they serve the values the server last
evaluated for the current context.

## Configuration

Every option of `new MegooFlagsWebProvider({ … })` (the `MegooFlagsOptions` type). Durations are in
milliseconds.

| Option            | Default                     | Notes                                                                                                   |
| ----------------- | --------------------------- | ------------------------------------------------------------------------------------------------------- |
| `sdkKey`          | (required)                  | A client key, `mf_client_…`. A server key (`mf_server_…`) or anything else is refused.                  |
| `baseUrl`         | (required unless `offline`) | The MegooFlags URL, `http` or `https`; a trailing `/` is removed.                                       |
| `bootstrap`       | none                        | Values to start from, shaped like an evaluate response: `{ results: [...] }`.                           |
| `disableCache`    | `false`                     | Never read or write the cache.                                                                          |
| `offline`         | `false`                     | No network at all: serve the bootstrap or cached values only. One of them is required.                  |
| `initTimeoutMs`   | `5000`                      | How long initialisation waits for the first values when neither bootstrap nor the cache gave any. > 0.  |
| `disableStream`   | `false`                     | Poll only, never open the SSE stream.                                                                   |
| `pollIntervalMs`  | `30000`                     | Minimum 15 000; a smaller value is raised to 15 000.                                                    |
| `flushIntervalMs` | `30000`                     | How often exposures are sent. Clamped to 10 000–60 000.                                                 |
| `sendContext`     | `false`                     | Include the evaluation context in exposures.                                                            |
| `logger`          | console (warn and error)    | An object with `debug`, `info`, `warn` and `error`. The SDK key is never logged.                        |
| `fetch`           | `globalThis.fetch`          | The `fetch` used for every request, the stream included.                                                |
| `storage`         | `localStorage`              | Where the last values are cached: an object with `getItem`, `setItem` and `removeItem` (sync or async). |

An invalid option throws from the constructor, with a message starting with `megooflags:`.

## Client keys only

A client key can only ask the server to evaluate flags for a context; it never downloads rules or
segments, so it is safe to ship in a web page. A server key must never ship in an app, and the
constructor refuses one. The key travels in the `Authorization` header only, never in a URL, and
requests are sent with `credentials: "omit"`.

## Bootstrap

Pass values rendered by your server so the first paint has flags without waiting for the network:

```js
new MegooFlagsWebProvider({
  sdkKey,
  baseUrl,
  bootstrap: {
    results: [
      {
        key: "new-checkout",
        value: true,
        variant: "true",
        reason: "TARGETING_MATCH",
        error_code: null,
      },
    ],
  },
});
```

Each result needs `key`, `reason`, `value`, `variant` (string or `null`) and `error_code` (string or
`null`); this is the body of `POST /sdk/v1/evaluate`. An invalid bootstrap throws
`megooflags: invalid bootstrap: …`. When bootstrap or the cache gives values, initialisation
resolves at once and the network refreshes them in the background.

## Cache and offline

- After every evaluation answer the provider writes the values, their context hash and ETag to the
  storage key `megooflags-<first 16 hex characters of sha256(sdkKey)>`; the key itself is never
  stored. At start, an entry for the same context replaces bootstrap and is served with reason
  `CACHED` until the network answers.
- When the targeting key changes, the old values and the cache entry are dropped at once, so one
  user's values are never served to another.
- Without the network the provider keeps serving the last values. With `offline: true` it makes no
  network calls and sends no exposures; initialisation fails with
  `megooflags: offline mode needs bootstrap values or a cache entry` when there is nothing to serve.
- When `localStorage` is unavailable (private modes, blocked storage) the cache is off. Write
  failures are logged as warnings and never fail an evaluation.

## How updates arrive

- The provider keeps an SSE stream open (`GET /sdk/v1/stream`). A `changed` event makes it ask for
  the new values (`POST /sdk/v1/evaluate` with `If-None-Match`).
- A dropped stream reconnects with backoff and jitter (3 s, or the server's `retry:`, up to 60 s).
  A connection with no data for 45 s is treated as dead. After two failures in a row the provider
  also polls every `pollIntervalMs` until the stream is back.
- 5xx and network errors back off and keep serving the last values; 429 waits for `Retry-After`.

## Background tabs

When the page is hidden the provider sends the waiting exposures at once, and after 30 s closes the
stream and stops polling. When the page is visible again it reconnects (with `Last-Event-ID`, so no
change is missed). On `pagehide` it sends the exposures with `keepalive` and closes the stream
straight away. It never listens to `unload` or `beforeunload`, so pages stay eligible for the
back/forward cache; `pageshow` brings it back.

## Exposures and privacy

- An exposure is recorded when a read serves a value: one per targeting key, flag, variant and UTC
  hour (deduplicated in memory, 10 000 entries). Reads never wait on the network.
- They are sent to `POST /sdk/v1/events` every `flushIntervalMs`, at 1000 waiting, when the page is
  hidden, and on close (within 5 s). At most 10 000 wait; newer ones are dropped with a warning.
- An exposure carries no context unless `sendContext` is true, and the server stores the context
  only when it runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- 413 splits the batch, 422 drops it, 429 and 5xx keep it and retry with backoff.

## OpenFeature events

| Event                            | When                                                                                    |
| -------------------------------- | --------------------------------------------------------------------------------------- |
| `PROVIDER_READY`                 | Initial values arrived, or the stream reconnected after `PROVIDER_STALE`.               |
| `PROVIDER_CONFIGURATION_CHANGED` | New values arrived; `flagsChanged` lists the flags whose value or variant changed.      |
| `PROVIDER_STALE`                 | The stream disconnected; the last values keep being served.                             |
| `PROVIDER_RECONCILING`           | Sent by the web SDK while the values for a new context are fetched.                     |
| `PROVIDER_CONTEXT_CHANGED`       | Sent by the web SDK when the values for the new context are in.                         |
| `PROVIDER_ERROR`                 | The key was refused (401, 403 or a `revoked` stream event), with code `PROVIDER_FATAL`. |

## Errors

- **401, 403 or `revoked`** stops the stream, polling and exposures for that key and emits
  `PROVIDER_ERROR` with `PROVIDER_FATAL`. In this fatal state the OpenFeature web SDK returns the
  caller's default value for every read.
- Before any value is known a read returns the default with `PROVIDER_NOT_READY`; an unknown flag
  gives `FLAG_NOT_FOUND`, a value of the wrong type `TYPE_MISMATCH`. Errors the server evaluated
  (`TYPE_MISMATCH`, `PARSE_ERROR`, `INVALID_CONTEXT`, `GENERAL`) are passed through. Every error
  returns your default with reason `ERROR`.
- JavaScript has one number type, so `getNumberValue` serves integers and decimals alike.

## Rate limit

The client endpoints allow 600 requests per minute per key and IP address. Many users behind one
address (an office, a carrier NAT) share that budget; keep `pollIntervalMs` at its default and do
not create a provider per component.

## CORS

`/sdk/v1/evaluate`, `/sdk/v1/stream` and `/sdk/v1/events` answer any origin, without credentials,
and expose `ETag` and `Retry-After`. If a reverse proxy sits in front of MegooFlags, let it pass the
`OPTIONS` preflight and these headers through.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdk-js/vX.Y.Z`, and only maintainers push those tags. The
`VERSION` export holds the version. `@megooflags/js/platform` is internal (it is what
`@megooflags/react-native` builds on) and may change in any release.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/js/`:

```sh
pnpm install --frozen-lockfile
pnpm format:check && pnpm lint && pnpm typecheck
pnpm test        # unit tests and the shared cases in ../../spec/client
pnpm build && pnpm pack
```

The end-to-end test runs the provider in Chromium, on a page served from `http://127.0.0.1:4173`
(another origin than the API). It needs a running stack and an API token (see the `sdk-js-e2e` job
in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
pnpm exec playwright install chromium
pnpm e2e
```

Without both variables it is skipped.
