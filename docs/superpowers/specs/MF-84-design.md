# MF-84: JS/Web, React, React Native, iOS, Android and Flutter client SDKs as OpenFeature providers

## Goal
A browser or mobile app registers a MegooFlags provider with its platform's official OpenFeature SDK, gives it a **client** SDK key, and reads flags synchronously from values the server evaluated for its context. No rule ever reaches the device. Values follow changes over SSE with a polling fallback, survive restarts and offline periods through a local cache, can start from bootstrap values, and exposures are sent in background batches.

Mockup v1 shows the blueprint, the flows and the six packages.

## Decisions
- **Scope: all six SDKs in this task, one PR into `develop` (your answer).** Built in order: shared spec and cases, JS/Web, React, React Native, iOS, Android, Flutter. Each SDK is its own series of commits and must be green (unit, shared cases, e2e, CI jobs) before the next starts. *Rejected:* JS/Web and React only (my recommendation); the three TypeScript SDKs only; JS/Web only. *Consequence:* a very large PR, reviewed SDK by SDK.
- **React and React Native: one component plus re-exported hooks (your answer).** *Rejected:* provider classes only; our own hooks.
- **Dependencies: platform libraries and the OpenFeature SDK only, plus the forced exceptions (your answer):** AsyncStorage on React Native (optional peer), `http` and `shared_preferences` on Flutter, `@openfeature/react-sdk` for React. SSE readers, backoff, cache, SHA-256 (JS and Dart have none built in that works everywhere) and batching are written by hand. Test and build tools are not restricted. *Rejected:* OkHttp, kotlinx-serialization, `react-native-sse` and similar.
- **Names and minimums (your answer):**

| SDK | Directory | Package | Minimum | Tag |
|---|---|---|---|---|
| JS/Web | `sdks/js` | npm `@megooflags/js` | Evergreen browsers (ES2020) | `sdk-js/vX.Y.Z` |
| React | `sdks/react` | npm `@megooflags/react` | React 18 | `sdk-react/vX.Y.Z` |
| React Native | `sdks/react-native` | npm `@megooflags/react-native` | React Native 0.76 | `sdk-react-native/vX.Y.Z` |
| iOS | `sdks/ios` | SwiftPM `MegooFlags` | iOS 15, macOS 12, Swift 6.1 | `sdk-ios/vX.Y.Z` |
| Android | `sdks/android` | Maven `io.github.mmhy2003:megooflags-android` | Android API 21, Kotlin 2.1 | `sdk-android/vX.Y.Z` |
| Flutter | `sdks/flutter` | pub.dev `megooflags` | Dart 3.10 (Flutter 3.38) | `sdk-flutter/vX.Y.Z` |

- **iOS: SwiftPM only, from a split repository (your answer).** CocoaPods is dropped: the OpenFeature Swift SDK removed its pod in 0.7.0 and CocoaPods trunk becomes read-only on 2 December 2026. This changes the task's "SwiftPM/CocoaPods". *Rejected:* a root `Package.swift`; pinning OpenFeature Swift 0.6.
- **E2E on each language's runtime, no emulators (your answer).** *Rejected:* device runs; iOS e2e outside CI.
- **One blueprint ported by hand, with shared test cases (your answer).** *Rejected:* no shared cases; wrapping OFREP providers.
- **The stream uses a header, not `?key=`.** `EventSource` cannot show a status code, so the 401, 403 and 429 rules of `spec/delivery.md` could not be followed. Every SDK reads the stream through its HTTP client with `Authorization: Bearer`; the key never appears in a URL.
- **Releases follow MF-256:** a tag verifies and builds; it publishes only when `SDK_PUBLISH_<LANG>` is `true`; `-rc` tags never publish. Agents never push tags, so "published" in the task is a maintainer step after merge.
- Every SDK starts at `0.1.0` (Alpha), Apache 2.0 with its own `LICENSE`. The pre-1.0 OpenFeature SDKs (Swift 0.7, Kotlin 0.9, Dart client 0.0.1) are pinned to their minor version.

## Architecture

### The blueprint
The same internal layers in every SDK (the TypeScript packages share one implementation in `@megooflags/js`):

| Layer | Responsibility |
|---|---|
| provider | The OpenFeature adapter: config validation, lifecycle, provider events, mapping stored results to resolution details. |
| store | The results for the current context, with their evaluate ETag, in memory, swapped atomically. Flag reads are synchronous and do no I/O. |
| sync | `POST /sdk/v1/evaluate` with the context (all flags) and `If-None-Match`. Stream reader for `GET /sdk/v1/stream`: `retry`, `: ping`, `changed`, `revoked`, unknown events ignored, `Last-Event-ID`, 45 s dead-connection timer, backoff from 3 s to 60 s with jitter, reset after 60 s connected. Poller: every 30 s (never under 15 s) after the stream fails twice in a row, stopped when it reconnects. |
| cache | The last results on the device, one entry per SDK key: `megooflags-<first 16 hex of sha256(key)>` holding `{"v", "context_hash", "etag", "results", "saved_at"}`. `context_hash` is the SHA-256 of the canonical context JSON, so the raw context is never stored. |
| events | Exposures only (remote evaluations are counted by the server; `delivery.md` §7 forbids client counts). One exposure per context key, flag, variant and UTC hour (LRU of 10 000), bounded buffer, never on the read path. Flush every 30 s (10 to 60 s), at 1000 items, when the app is hidden and on shutdown. 413 splits, 422 drops, 429 and 5xx retry with backoff, 401/403 stops. |
| transport | Request building, status mapping (401/403 fatal, 429 with `Retry-After`), backoff. Native SDKs send `User-Agent: megooflags-<lang>/<version>`; browsers cannot. |
| platform adapters | Stream reader, storage and the foreground/background signal (table below). |

Rules that apply everywhere:
- **Client keys only.** A key not starting with `mf_client_` is refused at construction; a server key gets a message saying it must never ship in an app. Initialisation fails with `PROVIDER_FATAL`.
- **Start-up:** bootstrap values, then the cache entry if its `context_hash` matches the initial context (it replaces the bootstrap), then the network. With bootstrap or cache values, initialisation returns at once and the first evaluate runs in the background. Otherwise it waits up to `initTimeout` (5 s), then fails while it keeps retrying, and emits ready when values arrive. Then the stream opens; its first `changed` costs one conditional evaluate that returns 304.
- **Change:** `changed` triggers one evaluate (at most one in flight and one queued). New results are swapped in, written to the cache, and a configuration-changed event lists the flags whose value or variant differs.
- **Context change:** if the targeting key differs, the old values and the cache entry are dropped at once, so one user's values are never served to another. If only attributes differ, the old values are served while the new ones are fetched. The ETag is discarded, and an answer for an older context is ignored. If the fetch fails, the provider reports the error and keeps retrying.
- **Background:** when the app is hidden, exposures are flushed at once. After 30 s hidden (immediately on a web `pagehide`), the stream closes and polling stops. On return, the stream reopens with `Last-Event-ID`.
- **Result mapping:**
  - `targetingKey` is sent as the context attribute `key`.
  - A stored result with an `error_code` returns the default with that code.
  - `variant: null` without an error (a missing off variation) returns the default with the server's reason.
  - A served value of the wrong type is `TYPE_MISMATCH`; an integer read of a non-integral or out-of-range number too.
  - A flag missing from the results is `FLAG_NOT_FOUND`; no results at all is `PROVIDER_NOT_READY`.
  - Values loaded from the cache report the reason `CACHED`; otherwise the server's reason, `PREREQUISITE_FAILED` included. There is no flag metadata (client keys get none).
- **Provider events:** configuration changed; stale when disconnected; ready when reconnected; error with the fatal code on 401, 403 or `revoked`. Reconciling and context-changed are emitted by whichever side each OpenFeature SDK assigns them to.

### Per SDK

| SDK | OpenFeature integration | HTTP and stream | Cache | Background signal |
|---|---|---|---|---|
| **JS/Web** | `@openfeature/web-sdk` ^1.10 (peer). `MegooFlagsWebProvider` implements `Provider` (`runsOn: "client"`, `initialize`, `onContextChange`, `onClose`, an `OpenFeatureEventEmitter`). | `fetch` with `credentials: "omit"` and a streaming body reader. Flush on hide uses `keepalive` with bodies under 60 KiB. | `localStorage` | Page visibility, `pagehide`, `pageshow` |
| **React** | `@openfeature/react-sdk` ^1.4 and `react` (peers). `<MegooFlagsProvider sdkKey baseUrl context options domain>` registers the provider in an effect (nothing runs during server rendering), calls `setContext` when `context` changes, wraps `OpenFeatureProvider`, and closes the provider on unmount (safe under StrictMode). Re-exports the OpenFeature hooks and `OpenFeatureTestProvider`. | from `@megooflags/js` | from `@megooflags/js` | from `@megooflags/js` |
| **React Native** | The React component with the adapters set. `react-native` is a peer. | A stream reader on `XMLHttpRequest` progress events (React Native's `fetch` cannot stream). | AsyncStorage when installed; otherwise memory only, with one warning | `AppState` |
| **iOS** | OpenFeature Swift 0.7. `MegooFlagsProvider` implements `FeatureProvider` (Combine futures, `observe()`, status through `ProviderStatusTracker`). | `URLSession` with a data delegate | A file in Application Support | App foreground/background notifications |
| **Android** | `dev.openfeature:kotlin-sdk` 0.9, an Android library (AAR). `MegooFlagsProvider` implements `FeatureProvider` (suspend `initialize` and `onContextSet`, `observe(): Flow`). `MegooFlagsConfig.Builder(context)` sets the cache directory and the lifecycle hook. | `HttpURLConnection` on coroutines (they come with the OpenFeature SDK); JSON through `org.json`. | A file in the app's files directory | Activity lifecycle callbacks |
| **Flutter** | `openfeature_dart_client_sdk` 0.0.x. `MegooFlagsProvider` implements `FeatureProvider` plus the initialise, context-reconciliation, shutdown and event-source interfaces. | `package:http` `Client.send` (streams on mobile, desktop and web) | `shared_preferences` | `AppLifecycleListener` |

TypeScript packaging: three ESM-only packages with type declarations, each with pnpm and its own lockfile, ESLint, Prettier, `tsc`, Vitest. `@megooflags/react` depends on `@megooflags/js`, and `@megooflags/react-native` on `@megooflags/react`, as regular dependencies linked locally through a pnpm override. npm and pnpm install the OpenFeature peers automatically; Yarn users add them by hand (documented).

### Shared files this PR touches
- `spec/delivery.md`: a new section "Client SDKs" with the rules above. No endpoint changes.
- `spec/client/` (new): `resolution.json` (stored result or none, source `network` or `cache`, requested type and default → expected value, variant, reason, error code), `context.json` (OpenFeature context → request body), a JSON Schema and a README. An API test validates every stored result against `SdkEvaluationResult`, so the cases stay true to the server.
- `.github/workflows/ci.yml`: `sdk-<name>` and `sdk-<name>-e2e` jobs for the six SDKs.
- `.github/workflows/release.yml`: tag triggers and release jobs.
- `scripts/check_sdk_version.py` and its tests: the six new manifests.
- `sdks/README.md`, `sdks/RELEASING.md`, `AGENTS.md` (§1 package managers, §9 checks, §11 tags), `CHANGELOG.md` (one "Added" line per SDK), and a README per SDK.

## Data and interfaces
Configuration, in each language's naming:

| Option | Default | Notes |
|---|---|---|
| `sdkKey` | required | `mf_client_…` only |
| `baseUrl` | required unless offline | `http` or `https` |
| `bootstrap` | none | The body of an evaluate response (`{"results": [...]}`): an object in TypeScript, JSON text elsewhere |
| `disableCache` | false | No persistent cache |
| `offline` | false | No network and no events; needs bootstrap or a cache |
| `initTimeout` | 5 s | |
| `disableStream` | false | Poll only |
| `pollInterval` | 30 s | Raised to 15 s when smaller |
| `flushInterval` | 30 s | Clamped to 10 to 60 s |
| `sendContext` | false | Include the context in exposures |
| `logger` | platform default | The key is never logged |
| HTTP hook | platform default | `fetch` (TypeScript), `URLSessionConfiguration` (iOS), `http.Client` (Flutter) |
| `storage` | platform default | TypeScript only: `getItem`, `setItem`, `removeItem`, sync or async |

Public surface per SDK: one provider class, one config type, a version constant; React and React Native add the component and the re-exports.

Release jobs:

| SDK | Publishing | You set up once |
|---|---|---|
| JS/Web, React, React Native | `npm publish` with OIDC, as `@megooflags/node`. Release order: js, react, react-native | A first manual publish and a trusted publisher per package |
| iOS | `git subtree split` of `sdks/ios`, pushed with the tag `X.Y.Z` to a read-only repository | The repository (default `mmhy2003/megooflags-swift`, variable `SDK_IOS_SPLIT_REPO`) and the secret `SDK_IOS_SPLIT_TOKEN` |
| Android | Central Publisher Portal with a GPG signature | Nothing new: the Java SDK's `CENTRAL_*` and `GPG_*` secrets |
| Flutter | pub.dev automated publishing with OIDC | A first manual publish; the tag pattern `sdk-flutter/v{{version}}` on pub.dev |

## Error handling and edge cases
- 401, 403 or `revoked` stops the stream, the poller and the event sender for that key. The provider keeps its values and reports a fatal error. In OpenFeature SDKs that short-circuit in the fatal state (the JS SDK does), evaluations then return the caller's default; the READMEs say so.
- 5xx and network errors back off and keep serving; 429 waits `Retry-After`. The READMEs explain the client rate limit (600 requests per minute per key and IP) for users behind one address.
- A corrupt, foreign-version or wrong-context cache entry is ignored. A failed cache write is logged and never fails an evaluation.
- Invalid bootstrap data is refused at construction with a clear message.
- Shutdown stops the sync, flushes exposures within 5 s and is idempotent.
- Web: the stream closes on `pagehide` so the page stays eligible for the back/forward cache. Each visible tab holds one stream.
- React: nothing runs during server rendering; children render with defaults until the client registers the provider.
- React Native without AsyncStorage runs without a persistent cache.

## Testing (TDD)
- **Unit tests** per SDK, against a local fake server or fake adapters: stream parser, backoff and stop rules, ETag handling, context change and late answers, cache read and write, exposure dedup and batch split, config validation, lifecycle and provider events, background pause and resume. React: the component with Testing Library. React Native: the `XMLHttpRequest` reader, AsyncStorage and `AppState` adapters with fakes.
- **Shared cases:** a runner per SDK reads `spec/client`, refuses an unknown schema version and must pass 100 %.
- **End to end** (`sdk-<name>-e2e`, skipped without `MEGOOFLAGS_E2E_URL` and `MEGOOFLAGS_API_TOKEN`): create a project, an environment, a boolean flag with an individual target and a client key through the management API, then check:
  1. the evaluated value;
  2. a toggle arriving over SSE within 5 s, with a configuration-changed event;
  3. a different value after the context changes to the targeted user;
  4. an exposure batch accepted at shutdown, and the server counting the evaluation;
  5. a new provider pointed at a closed port serving the cached values.
- **Where e2e runs:**
  - JS/Web and React: headless Chromium (Playwright), on a page served from another origin than the API, so CORS is exercised.
  - React Native: Node, with AsyncStorage and `AppState` faked and `XMLHttpRequest` as a test shim over Node's HTTP client.
  - Android: the JVM, as Gradle unit tests.
  - Flutter: the Dart VM, with `flutter test`.
  - iOS: `swift test` on a macOS runner. macOS runners have no Docker, so the job starts Postgres, Valkey and the API natively, as `web-e2e` does.
- **CI:** each `sdk-<name>` job runs the format check, lint, type check or analyser, unit tests, shared cases and a package build. React runs on React 18 and 19; Flutter on 3.38 and stable. `actionlint` covers the workflow changes.

## Risks
- Building the API on macOS (lxml and xmlsec from source) may be unreliable. If so, I will ask before changing the iOS e2e approach.
- OpenFeature does not list React Native as supported; it works in practice. The Dart client SDK is at 0.0.1. Interface details are re-checked against the pinned versions during planning.

## Out of scope
- Any API change, the relay, local evaluation on clients, evaluation counts from clients, `track()`.
- CocoaPods, Swift on Linux, Kotlin Multiplatform targets other than Android, a script-tag (UMD) build.
- Server-side rendering helpers (Next.js server components, per-request bootstrap), Vue, Angular or Svelte packages, SwiftUI, Compose or Flutter widget helpers.
- Emulator, simulator and device tests.
- Creating registry accounts, the split repository, secrets and tags; publishing `-rc` versions.
- Client SDK snippets in the web UI.
