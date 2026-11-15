# MF-84 implementation plan: JS/Web, React, React Native, iOS, Android and Flutter client SDKs as OpenFeature providers

**Goal:** ship six 0.1.0 client SDKs (`sdks/js`, `sdks/react`, `sdks/react-native`, `sdks/ios`, `sdks/android`, `sdks/flutter`). Each is an OpenFeature provider that evaluates remotely with a `mf_client_` key, follows changes over SSE with a polling fallback, caches the last values on the device, starts from bootstrap values, sends batched exposures, passes 100 % of the new shared cases in `spec/client/` and has an end-to-end test in CI.

**Architecture:** every SDK has the same layers (blueprint B1 to B13 below): transport, SSE reader, context mapping, store and result mapping, cache, evaluator, streamer, poller, sync coordinator, exposures and batcher, background signal, and the provider. The three TypeScript packages share one implementation: `@megooflags/js` holds every layer and takes a `Platform` (stream opener, storage, background signal), `@megooflags/react` adds one component and re-exports the OpenFeature React hooks, and `@megooflags/react-native` gives that component a React Native platform (`XMLHttpRequest` stream, AsyncStorage, `AppState`). Swift, Kotlin and Dart port the blueprint by hand. Every SDK runs the language-neutral cases in `spec/client/`.

## How to read this plan
- **B1 to B13** are the blueprint: language-neutral contracts. **L1 to L4** are the language rules (TypeScript, Swift, Kotlin, Dart). **R** is the release jobs. When a step says "B7 for Swift", implement B7 following L2.
- Blueprint names are camelCase; use each language's convention (`contextHash` is `contextHash` in Swift/Kotlin/Dart; types are PascalCase everywhere).
- Order: steps 1 and 2 are shared; JS 3 to 15; React 16 and 17; React Native 18 to 20; iOS 21 to 27; Android 28 to 34; Flutter 35 to 41; release jobs 42 and 43; docs 44. Each SDK's last step must be green (unit, shared cases, e2e, CI jobs) before the next SDK starts.
- Every step is one commit: `<type>(<scope>): <summary> (MF-84)` with scopes `spec`, `scripts`, `sdk-js`, `sdk-react`, `sdk-react-native`, `sdk-ios`, `sdk-android`, `sdk-flutter`, `ci`, `docs`, and the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.megoocolab/`, `node_modules/`, `dist/`, `.build/`, `build/`, `.dart_tool/`, `test-results/`, `playwright-report/`.
- TDD in every step: write the named failing test, run it and see it fail, implement, run it and see it pass, then run the SDK's whole check group (L1 to L4) before committing.
- Runtime dependencies are only the platform, the OpenFeature SDK and the forced exceptions: AsyncStorage (optional peer, React Native), `http` and `shared_preferences` (Flutter), `@openfeature/react-sdk` and `react` (React peers). `kotlinx-coroutines-core` is declared explicitly in Android at the version the OpenFeature Kotlin SDK already brings (it is not a new library). Test and build tools are not restricted.

## File map

| Path | Created or changed | Responsibility |
|---|---|---|
| `spec/client/README.md`, `spec/client/client.schema.json`, `spec/client/resolution.json`, `spec/client/context.json` | new | Shared client cases (B2) |
| `spec/delivery.md`, `spec/README.md` | changed | New section 11 "Client SDKs"; a row for `client/` |
| `api/tests/test_spec_client.py` | new | Validates the case files: schema, every stored result against `SdkEvaluationResult`, canonical JSON and hashes |
| `scripts/check_sdk_version.py`, `scripts/tests/test_check_sdk_version.py` | changed | Six new manifests |
| `sdks/js/` | new | `@megooflags/js`: all layers, `MegooFlagsWebProvider`, `Platform`, web platform, Playwright e2e |
| `sdks/react/` | new | `@megooflags/react`: `MegooFlagsProvider` component, `createMegooFlagsComponent`, re-exported hooks, Playwright e2e |
| `sdks/react-native/` | new | `@megooflags/react-native`: XHR stream opener, AsyncStorage and `AppState` adapters, component, Node e2e |
| `sdks/ios/` | new | SwiftPM package `MegooFlags` |
| `sdks/android/` | new | Gradle Android library `io.github.mmhy2003:megooflags-android` |
| `sdks/flutter/` | new | pub package `megooflags` |
| `.github/workflows/ci.yml` | changed | `sdk-js`, `sdk-js-e2e`, `sdk-react`, `sdk-react-e2e`, `sdk-react-native`, `sdk-react-native-e2e`, `sdk-ios`, `sdk-ios-e2e`, `sdk-android`, `sdk-android-e2e`, `sdk-flutter`, `sdk-flutter-e2e` |
| `.github/workflows/release.yml` | changed | Tag triggers and release/publish jobs (R) |
| `sdks/README.md`, `sdks/RELEASING.md`, `AGENTS.md`, `CHANGELOG.md` | changed | Status table, registry setup, package managers, checks, tags, "Added" lines |

## B1. Layers and file names

| Layer | TypeScript (`sdks/js/src/`) | Swift (`sdks/ios/Sources/MegooFlags/`) | Kotlin (`sdks/android/src/main/kotlin/io/github/mmhy2003/megooflags/`) | Dart (`sdks/flutter/lib/src/`) |
|---|---|---|---|---|
| version | `version.ts` `VERSION` | `Version.swift` `public let megooFlagsVersion` | `Version.kt` `public const val VERSION` | `version.dart` `megooFlagsVersion` |
| config (B4) | `config.ts` `MegooFlagsOptions`, `internal/options.ts` | `Config.swift` `MegooFlagsConfig` | `MegooFlagsConfig.kt` (+ `Builder`) | `config.dart` `MegooFlagsConfig` |
| logger | `internal/transport.ts` `Logger` | `Logger.swift` `MegooFlagsLogger` | `MegooFlagsLogger.kt` | `config.dart` `MegooFlagsLogger` typedef |
| transport (B6) | `internal/transport.ts`, `internal/backoff.ts` | `Transport.swift`, `Backoff.swift` | `internal/Transport.kt`, `internal/Backoff.kt` | `transport.dart`, `backoff.dart` |
| JSON value | native | `JSONValue.swift` (internal enum) | `internal/Json.kt` (org.json ↔ Kotlin values) | native |
| SSE (B7) | `internal/sse.ts` | `SSEParser.swift` | `internal/SseParser.kt` | `sse.dart` |
| context (B3) | `internal/context.ts`, `internal/sha256.ts` | `Context.swift` (CryptoKit SHA-256) | `internal/ContextJson.kt` (`MessageDigest`) | `context.dart`, `sha256.dart` |
| store and mapping (B5) | `internal/store.ts`, `internal/resolve.ts` | `Store.swift`, `Resolve.swift` | `internal/Store.kt`, `internal/Resolve.kt` | `store.dart`, `resolve.dart` |
| cache (B9) | `internal/cache.ts` | `Cache.swift` | `internal/Cache.kt` | `cache.dart` |
| evaluator, streamer, poller, sync (B7, B8) | `internal/evaluator.ts`, `streamer.ts`, `poller.ts`, `sync.ts` | `Evaluator.swift`, `Streamer.swift`, `Poller.swift`, `Sync.swift` | `internal/Evaluator.kt`, `Streamer.kt`, `Poller.kt`, `Sync.kt` | `evaluator.dart`, `streamer.dart`, `poller.dart`, `sync.dart` |
| events (B10) | `internal/exposures.ts`, `internal/batcher.ts` | `Exposures.swift`, `Batcher.swift` | `internal/Exposures.kt`, `internal/Batcher.kt` | `exposures.dart`, `batcher.dart` |
| background (B11) | `platform.ts` (`BackgroundSignal`), `web-platform.ts` | `Background.swift` | `internal/Lifecycle.kt` | `lifecycle.dart` |
| provider (B12) | `provider.ts` `MegooFlagsWebProvider` | `Provider.swift` `MegooFlagsProvider` | `MegooFlagsProvider.kt` | `provider.dart` `MegooFlagsProvider` |

Public surface per SDK: the provider class, the config type, the version constant (plus the logger type where the language needs one). TypeScript also exports `@megooflags/js/platform` (B11, L1) for the React Native package; it is documented as internal.

## B2. Shared cases (`spec/client/`)

Two case files, one schema (`client.schema.json`, JSON Schema 2020-12, `oneOf` on `kind`), a README with the runner algorithm. A runner refuses any `schema_version` other than 1 and reports each test as `<file stem>::<name>`.

`resolution.json`:
```json
{
  "schema_version": 1,
  "kind": "resolution",
  "description": "Stored evaluate results mapped to OpenFeature resolution details",
  "tests": [
    {
      "name": "boolean-served-from-network",
      "results": [{"key": "f", "value": true, "variant": "true", "reason": "TARGETING_MATCH", "error_code": null, "error_message": null}],
      "source": "network",
      "flag": "f",
      "type": "boolean",
      "default": false,
      "expected": {"value": true, "variant": "true", "reason": "TARGETING_MATCH", "error_code": null}
    }
  ]
}
```
- `results` is an array of evaluate results (exactly the `SdkEvaluationResult` fields, no `metadata`) or `null` (no results at all). `source` is `network` or `cache`. `type` is `boolean`, `string`, `integer`, `number` or `object`.
- Runner: build a store from `results` and `source`, resolve `flag` with `type` and `default`, compare `value` with JSON equality (as `spec/conformance/README.md` step 3), and `variant`, `reason` and `error_code` exactly (`null` is a value).
- `integer` uses the SDK's integer read; `number` the double/number read. JS has no integer read: its runner skips `integer` tests and counts them as not applicable (the README says so). Kotlin runs `integer` tests through both `getIntegerEvaluation` and `getLongEvaluation`.
- Required tests (at least one each): each type served from network; each type served from cache (reason `CACHED`); `PREREQUISITE_FAILED` passed through; `variant: null` without error (default, server reason `DISABLED`, variant null, no error); stored `error_code` (`TYPE_MISMATCH`, `PARSE_ERROR`, `INVALID_CONTEXT`, `GENERAL`, and the same from cache); unknown stored error code → `GENERAL`; wrong type for each type (boolean read of a string, string read of a number, number read of a boolean, object read of a string, integer read of `1.5`, integer read of `1e20`); integer read of `3` and of `3.0` served; number read of `3` served as 3; object read of an object; flag missing → `FLAG_NOT_FOUND`; `results: null` → `PROVIDER_NOT_READY`. Arrays as object values are not in the shared cases.
- Error results return `default`, `variant: null`, reason `ERROR`, the error code. Every SDK-side error (`TYPE_MISMATCH`, `FLAG_NOT_FOUND`, `PROVIDER_NOT_READY`) has reason `ERROR`.

`context.json`:
```json
{
  "schema_version": 1,
  "kind": "context",
  "description": "OpenFeature contexts mapped to the evaluate request context, its canonical JSON and hash",
  "tests": [
    {
      "name": "targeting-key-becomes-key",
      "context": {"targetingKey": "user-1", "plan": "pro"},
      "body": {"key": "user-1", "plan": "pro"},
      "canonical": "{\"key\":\"user-1\",\"plan\":\"pro\"}",
      "context_hash": "<64 lowercase hex, computed by api/tests/test_spec_client.py's reference>"
    }
  ]
}
```
- `context` is the OpenFeature context as JSON: `targetingKey` is the targeting key, any other member is an attribute, and `{"$date": "<RFC 3339>"}` stands for a date value. Runner: build the language's `EvaluationContext` from it, map it (B3), compare with `body` (JSON equality), and compare `canonical` and `context_hash` exactly.
- Required tests: targeting key becomes `key`; targeting key replaces a `key` attribute; no targeting key keeps a `key` attribute; empty targeting key is absent; nested objects and lists; booleans, null, integers, `1.5`; a date (`2026-11-10T09:15:02Z` → `"2026-11-10T09:15:02.000Z"`); key order (input keys unsorted); keys sorted by code point (`"～"` before `"😀"`, which UTF-16 order would reverse); string escapes (`"`, `\`, newline, tab, U+0001 → `\u0001`, non-ASCII kept raw).

`api/tests/test_spec_client.py` holds the reference: `canonical(value)` is `megooflags_api.etags.canonical_json` after integral floats are turned into ints; `context_hash` is `hashlib.sha256(canonical.encode()).hexdigest()`; it maps `context` exactly as B3. It checks: the schema is valid 2020-12 and both files validate; every item of every `results` validates with `SdkEvaluationResult.model_validate`; `body`, `canonical` and `context_hash` equal the reference.

## B3. Context mapping, canonical JSON, hash
- `toRequestContext(ctx)`: every attribute is copied; a non-empty targeting key is written as `key`, replacing a `key` attribute; dates become UTC RFC 3339 with exactly three fraction digits and `Z` (`2026-11-10T09:15:02.000Z`); structures become objects, lists arrays, null null; integral doubles become integers.
- `canonicalJson(value)`: no whitespace; object keys sorted by Unicode code point; strings escaped as JSON with only `"`, `\`, `\b \f \n \r \t` and other U+0000–U+001F as `\u00xx` (lowercase hex); everything else raw UTF-8; integral numbers (|x| < 2^53) as integers; other numbers in the language's shortest round-trip form (the README says such numbers may hash differently between SDKs, which only costs a cache miss).
- `contextHash(body)` = lowercase hex SHA-256 of the UTF-8 `canonicalJson(body)`.
- `targetingKeyOf(body)` = `body.key` when it is a string, else null. A context change "changes the targeting key" when these differ.

## B4. Configuration
Options (TypeScript name; Swift/Kotlin/Dart use the same words with seconds as `TimeInterval`, `kotlin.time.Duration`, `Duration`):

| Option | Default | Validation |
|---|---|---|
| `sdkKey` | required | Must start with `mf_client_`. `mf_server_…` → error "megooflags: a server key must never ship in an app; use a client key (mf_client_…)". Anything else → "megooflags: sdkKey must be a client key (mf_client_…)". The key is never in a message or log. |
| `baseUrl` | required unless `offline` | `http:` or `https:` URL; trailing `/` removed |
| `bootstrap` | none | TS: `{results: [...]}` object; others: JSON text. Each result needs `key` (string), `reason` (string), `value`, `variant` (string or null), `error_code` (string or null). Invalid → construction error "megooflags: invalid bootstrap: <why>" |
| `disableCache` | false | |
| `offline` | false | |
| `initTimeoutMs` | 5000 | > 0 |
| `disableStream` | false | |
| `pollIntervalMs` | 30000 | raised to 15000 when smaller |
| `flushIntervalMs` | 30000 | clamped to 10000…60000 |
| `sendContext` | false | |
| `logger` | platform default | |
| HTTP hook | platform default | TS `fetch`; Swift `sessionConfiguration: URLSessionConfiguration`; Dart `httpClient: http.Client`; Kotlin none |
| `storage` | platform default | TS only |

Invalid configuration fails at construction (TS/Swift/Kotlin throw, Dart throws `ArgumentError`). The construction error for the key kind is also what initialisation reports as `PROVIDER_FATAL` when an OpenFeature SDK calls the provider anyway (it cannot, since construction fails).

## B5. Store and result mapping
- `StoredResults { results: map key → result; etag: string | null; source: "bootstrap" | "cache" | "network"; contextHash: string; targetingKey: string | null }`. The store holds one `StoredResults` or none and swaps it atomically (one reference assignment; Swift and Kotlin under a lock or atomic reference).
- `resolve(stored, flag, type, default)`:
  1. no stored results → default, reason `ERROR`, `PROVIDER_NOT_READY`;
  2. flag missing → `FLAG_NOT_FOUND`;
  3. stored `error_code` → default, reason `ERROR`, the code mapped to the SDK's enum (unknown → `GENERAL`), message `error_message`;
  4. `variant: null` → default, the stored reason, no variant, no error;
  5. value of the wrong type → `TYPE_MISMATCH` (integer reads: integral and within the integer type's range; number reads accept any number; object reads accept objects, and arrays where the SDK's object type can hold them — Dart cannot);
  6. else the value, the variant, reason `CACHED` when `source` is `cache`, otherwise the stored reason. No flag metadata.
- `changedFlags(old, new)`: keys whose value (JSON equality) or variant differs, plus keys present in only one.

## B6. Transport
- Headers: `Authorization: Bearer <key>`, `Content-Type: application/json` on POST, `Accept: application/json` (evaluate, events) or `text/event-stream` (stream), `If-None-Match` and `Last-Event-ID` when known. Native SDKs add `User-Agent: megooflags-<lang>/<version>` (`ios`, `android`, `flutter`; Flutter web cannot set it and skips it). Browsers: `credentials: "omit"`, no User-Agent.
- `statusError(status, headers)`: 2xx and 304 → none; 401/403 → `FatalError(status)`; 429 → `RetryAfterError(seconds)` (seconds from `Retry-After`, integer or HTTP date; missing or invalid → 60); other → `HttpError(status)`.
- `Backoff(base, max)`: first wait `min(base, max)`, n-th `min(max, base·2^n)·(0.8 + 0.2·rand)`; `reset()`. Port of `sdks/node/src/internal/transport/backoff.ts`. Evaluate retries use base 1 s, max 60 s; the stream base is the `retry` field (default 3 s), max 60 s; events base 1 s, max 60 s.
- Debug log lines (the e2e tests read them): `megooflags: sent <n> exposures` after a 202; `megooflags: stream connected`; `megooflags: stream failed: <why>`.

## B7. SSE reader and streamer
- `readSse(chunks, callbacks)`: port of `sdks/node/src/internal/delivery/sse.ts` (byte-level LF split, CR stripped, BOM ignored, `retry` only digits, comments call `onLine`, event dispatched on an empty line, unterminated last event discarded).
- `Streamer` opens `GET <baseUrl>/sdk/v1/stream` with B6 headers and `Last-Event-ID` = id of the last processed `changed`:
  - status ≠ 200 → `statusError`; 401/403 → `onFatal`; 429 → wait `Retry-After` (and count a failure); other → failure.
  - 200 → `onConnected()`; every line resets a 45 s dead timer; `retry: n` sets the backoff base; `changed` → remember `id`, call `onChanged()`; `revoked` → `onFatal("revoked")`; other names ignored.
  - End or error without `revoked` → failure; wait `backoff.next()` then reconnect. A connection that lasted ≥ 60 s resets the backoff first.
  - `consecutiveFailures` counts failures since the last 200; at 2 it calls `onDegraded()` (start polling); `onConnected` stops polling.
  - `pause()` closes the connection and stops reconnecting; `resume()` reconnects at once with `Last-Event-ID`; `stop()` is final.

## B8. Evaluator, poller and sync
- `Evaluator.evaluate(body, etag)` → `POST <baseUrl>/sdk/v1/evaluate` with `{"context": body}` (no `flags`) and `If-None-Match: etag` when set. 200 → `{results, etag}` (results validated as B4 bootstrap items; invalid JSON is an `HttpError(0)`-like failure); 304 → `notModified`; else `statusError`.
- `Poller(intervalMs)`: while started, calls `sync.refresh()` every interval; never more often than 15 s.
- `Sync` owns the store, cache, evaluator, streamer, poller and a generation counter:
  - **start:** store := bootstrap (source `bootstrap`) if given; then the cache entry if its `contextHash` equals the initial context's (it replaces bootstrap, source `cache`, its ETag kept). If the store has values → return at once, then `refresh()` in the background. Else wait for the first `refresh()` up to `initTimeout`; on timeout or error, initialisation fails (fatal → `PROVIDER_FATAL`, else `GENERAL`) while refresh keeps retrying with backoff; when values arrive emit ready. After the first refresh settles (success or failure), the streamer starts (unless `disableStream` or `offline`, which starts the poller instead).
  - **refresh():** at most one in flight and one queued; extra calls while one is queued are merged. It captures the generation; evaluates with the store's ETag (null after a context change); on 200 for the current generation: swap (source `network`), write the cache, and if a previous store existed emit configuration-changed with `changedFlags` when non-empty; on 304: switch the source to `network` (values are fresh again) and keep the values; an answer for an older generation is dropped. Failures: fatal → `stopAll(fatal)`; 429 → wait `Retry-After`; others → backoff retry while nothing newer is queued.
  - **onChanged** (stream) → `refresh()`.
  - **setContext(newCtx):** body := `toRequestContext`; generation++; ETag := null; if `targetingKeyOf` differs from the stored one: clear the store and remove the cache entry at once. Then `refresh()` and resolve when that generation's values arrive; reject on failure (the provider reports it) while retries continue.
  - **stale/ready:** stream failure while connected before → emit stale once; next `onConnected` after stale → emit ready.
  - **stopAll(fatal):** stop streamer, poller and batcher for this key; keep the store; emit error with `PROVIDER_FATAL`.
  - **offline:** no network at all; start needs bootstrap or a matching cache entry, otherwise initialisation fails with `GENERAL` "megooflags: offline mode needs bootstrap values or a cache entry".

## B9. Cache
- Key `megooflags-<first 16 hex of sha256(sdkKey)>` (TS: storage key; native: file `megooflags-<…>.json`; Flutter: `shared_preferences` key).
- Entry: `{"v": 1, "context_hash": "<hex>", "etag": "<etag or null>", "results": [...], "saved_at": "<RFC 3339 UTC>"}`.
- `load(contextHash)` → `StoredResults` with source `cache`, or none when missing, unparsable, `v ≠ 1`, or another `context_hash`. `save(stored)` after every 200 for the current generation; `remove()` on a targeting-key change. Write failures are logged at warn and never fail an evaluation. `disableCache` → no reads or writes.

## B10. Exposures and batcher
- `recordExposure(flag, result, body)` runs inside a successful resolve (case 6 of B5 only), never does I/O: dedup key `targetingKey|flag|variant|hourUTC` in an LRU of 10 000; new keys append `{"flag_key", "variant", "reason", "timestamp": RFC 3339 UTC now, "context": body when sendContext}` to a buffer bounded at 10 000 (overflow drops new items and logs one warning per flush interval). `offline` → no exposures.
- `Batcher` flushes every `flushInterval`, at 1000 buffered items, when the app is hidden, and on shutdown (bounded at 5 s). It sends `{"exposures": [≤1000]}` to `POST /sdk/v1/events`. 202 → log `megooflags: sent <n> exposures`; 413 → split in halves and send each (a single item that is 413 is dropped); 422 → drop and warn; 429 → keep, wait `Retry-After`; 5xx/network → keep, back off; 401/403 → `stopAll(fatal)`. No counts are ever sent.

## B11. Background signal
- States `visible`, `hidden`, `terminated` (web `pagehide` only).
- `hidden` → flush exposures now; start a 30 s timer; when it fires, `streamer.pause()` and stop the poller. `terminated` → flush (web: `keepalive`), pause at once. `visible` → cancel the timer; if paused, `streamer.resume()` (with `Last-Event-ID`) and the poller only if it was running.
- Sources: web `visibilitychange` + `pagehide` + `pageshow`; React Native `AppState` (`active` visible, `background`/`inactive` hidden); iOS `UIApplication.didEnterBackgroundNotification` / `willEnterForegroundNotification` (macOS `NSApplication.didHideNotification` / `didUnhideNotification`); Android activity start/stop counter through `Application.ActivityLifecycleCallbacks`; Flutter `AppLifecycleListener` (`onHide` hidden, `onShow` visible).

## B12. Provider events and lifecycle
- Configuration changed (with flag keys), stale, ready (initial values or reconnect), error with fatal code. Reconciling and context-changed: JS — the web SDK emits them (async `onContextChange`); Swift and Dart — the provider emits `reconciling` then `contextChanged` or `error` itself; Kotlin — no such provider events exist (the SDK sets the status).
- Shutdown: stop the streamer, poller and refresh; flush exposures within 5 s; unsubscribe the background signal; idempotent (second call returns at once).

## B13. End-to-end scenario (every SDK)
Skipped unless `MEGOOFLAGS_E2E_URL` and `MEGOOFLAGS_API_TOKEN` are set. Management API calls use `Authorization: Bearer <token>` from the test process (never from a browser page).
1. Setup: project `sdk<lang>-<8 random hex>` with environment `e2e` (`POST /api/projects` with `environments: [{key: "e2e", name: "E2E"}]`); boolean flag `e2e-flag` (`POST /api/projects/{p}/flags`, variations are `true` and `false`); `POST {flagPath}/toggle {"enabled": true}`; read `GET {flagPath}` for `version`; `PUT {flagPath}` with `{"version": v, "enabled": true, "targets": [{"variation": "false", "values": ["vip-user"]}], "rules": [], "fallthrough": {"variation": "true"}, "prerequisites": []}`; client key `POST /api/projects/{p}/environments/e2e/sdk-keys {"name": "<lang>-e2e", "kind": "client"}` (`key` in the response). `flagPath` = `/api/projects/{p}/flags/e2e-flag/environments/e2e`.
2. Provider with context `targetingKey: "user-1"`, `flushInterval` 10 s, a capturing logger. Check 1: `e2e-flag` is `true`.
3. Check 2: toggle off → `false` within 5 s and at least one configuration-changed event; toggle on → `true` within 5 s.
4. Check 3: set context `targetingKey: "vip-user"` → `false` with reason `TARGETING_MATCH`.
5. Check 4: read the flag 5 times, shut down; the logger captured `megooflags: sent ` (≥ 1 exposure accepted); `GET {flagPath}/evaluations?range=24h` has `total ≥ 1` within 10 s (poll every 0.5 s).
6. Check 5: new provider, same cache storage, `baseUrl` `http://127.0.0.1:<closed port>`, context `vip-user`, `initTimeout` 1 s → `false` with reason `CACHED`.
7. Teardown: `DELETE /api/projects/{p}`; close providers.

## L1. TypeScript (`sdks/js`, `sdks/react`, `sdks/react-native`)
- Copy the tooling of `sdks/node`: `package.json` scripts `build` (`tsc -p tsconfig.build.json`), `lint` (`eslint .`), `format:check` (`prettier --check .`), `typecheck` (`tsc --noEmit`), `test` (`vitest run test/`), `e2e`; `.prettierrc.json` `{}`; `.prettierignore` (`dist`, `node_modules`, `pnpm-lock.yaml`, `e2e/page/dist`, `test-results`, `playwright-report`); `eslint.config.js` as node's; `.gitignore` as node's plus `test-results/`, `playwright-report/`, `e2e/page/dist/`; `"packageManager": "pnpm@10.34.9"`; `"type": "module"`, ESM only, `exports` with `types` and `default`; `files: ["dist", "LICENSE", "README.md"]`; `publishConfig.access: public`; `license: Apache-2.0`; `repository.directory`. `LICENSE` copied from `sdks/node/LICENSE`.
- `tsconfig.json`: node's options with `"target": "ES2020"`, `"lib": ["ES2020", "DOM", "DOM.Iterable"]`, `"types": []` (add `"node"` only in a `tsconfig.test.json`-free way: tests import `node:` modules, so keep `"types": ["node"]` and `@types/node` as a dev dependency; `src/` must not import `node:` modules — an ESLint `no-restricted-imports` rule for `node:*` under `src/**` enforces it). React adds `"jsx": "react-jsx"`.
- Dev dependencies: `@eslint/js`, `eslint`, `typescript-eslint`, `prettier`, `typescript`, `vitest`, `@types/node`, `@openfeature/web-sdk` (^1.10.0), `@openfeature/core` (^1.12.0); e2e: `@playwright/test` `1.65.0` (the version `web/` pins) and `esbuild`. React: `react`, `react-dom`, `@types/react`, `@types/react-dom`, `@testing-library/react`, `jsdom`, `@openfeature/react-sdk` (^1.4.1).
- Peers: js `@openfeature/web-sdk ^1.10.0`; react `@openfeature/web-sdk ^1.10.0`, `@openfeature/react-sdk ^1.4.1`, `react >=18`; react-native adds `react-native >=0.76` and optional `@react-native-async-storage/async-storage >=2` (`peerDependenciesMeta.optional`).
- Local linking: react has `"dependencies": {"@megooflags/js": "^0.1.0"}` and `"pnpm": {"overrides": {"@megooflags/js": "link:../js"}}`; react-native depends on `@megooflags/js` and `@megooflags/react` (^0.1.0) with both overrides. Because the linked package resolves to `dist/`, build it first: `pnpm --dir ../js install --frozen-lockfile && pnpm --dir ../js build` (react-native also builds `../react`). CI does the same.
- Timers: tests use Vitest fake timers and a fake `Platform`; network tests use `test/helpers/fake-server.ts`, a port of `sdks/node/test/helpers/fake-server.ts` (node:http) serving evaluate, stream and events.
- Check group: `pnpm install --frozen-lockfile && pnpm format:check && pnpm lint && pnpm typecheck && pnpm test && pnpm build && pnpm pack`.

`Platform` (`sdks/js/src/platform.ts`, exported as `@megooflags/js/platform`):
```ts
export interface StreamResponse {
  status: number;
  header(name: string): string | null;
  body: AsyncIterable<Uint8Array>;
}
export type StreamOpener = (url: string, headers: Record<string, string>, signal: AbortSignal) => Promise<StreamResponse>;
export interface StorageLike {
  getItem(key: string): string | null | Promise<string | null>;
  setItem(key: string, value: string): void | Promise<void>;
  removeItem(key: string): void | Promise<void>;
}
export type BackgroundState = "visible" | "hidden" | "terminated";
export interface BackgroundSignal {
  current(): BackgroundState;
  subscribe(listener: (state: BackgroundState) => void): () => void;
}
export interface Platform {
  openStream: StreamOpener;
  storage: StorageLike | null;
  background: BackgroundSignal | null;
  /** Sends a final events body when the page is going away; false when it could not. */
  sendBeaconLike?: undefined;
}
export function webPlatform(fetchFn?: typeof fetch): Platform;
```
(`sendBeaconLike` is not used; remove that member — the web keepalive path lives in the batcher, see step 12.) `MegooFlagsWebProvider(options: MegooFlagsOptions, platform?: Platform)`; an explicit `options.storage` overrides `platform.storage`; `options.fetch` is used for evaluate and events (and for the web stream opener).

## L2. Swift (`sdks/ios`)
- `Package.swift`: `// swift-tools-version: 6.1`; platforms `.iOS(.v15), .macOS(.v12)`; product library `MegooFlags`; dependency `.package(url: "https://github.com/open-feature/swift-sdk.git", .upToNextMinor(from: "0.7.0"))` product `OpenFeature`; targets `MegooFlags`, test targets `MegooFlagsTests` (unit + shared cases, reads `../../spec/client` via `#filePath`) and `MegooFlagsE2ETests` (B13, `XCTSkip` without the env vars). XCTest.
- OpenFeature 0.7 API: `FeatureProvider` lifecycle returns `Future<Void, Never>` (not async); `initialize(initialContext:)` and `onContextSet(oldContext:newContext:)` must send their event (`.ready`/`.error`, `.reconciling` then `.contextChanged`/`.error`) before the future completes. Status via `ProviderStatusTracker` (`send`, `observe()`). Evaluations are synchronous `throws` and return `ProviderEvaluation` (implement both the plain and the `logger:` overloads if the protocol requires them; check the pinned source in `.build/checkouts/swift-sdk`). `Value.integer` is `Int64`.
- Swift 6 language mode; types used across tasks are `Sendable` (actors for `Sync`, `Batcher`; `final class` + `Mutex`/`NSLock` for the store). Use `@preconcurrency import OpenFeature` if the SDK's types are not `Sendable`.
- Tooling: `swift format lint --strict --recursive Sources Tests Package.swift` with a `.swift-format` file (`{"version": 1, "indentation": {"spaces": 4}, "lineLength": 120}`); `swift build`; `swift test --filter MegooFlagsTests`; `xcodebuild -scheme MegooFlags -destination 'generic/platform=iOS' build` (UIKit paths compile).
- Unit-test seams: an internal `HTTPTransport` protocol (`func data(for: URLRequest) async throws -> (Data, HTTPURLResponse)`, `func stream(for: URLRequest, onResponse:, onData:, onComplete:) -> Cancellable`) with a URLSession implementation and a fake; an injectable `sleep(_ seconds: Double) async throws`; the cache directory is an internal init parameter (default: Application Support `/megooflags`).
- `.gitignore`: `.build/`, `.swiftpm/`, `Package.resolved` is committed (it is the lockfile).

## L3. Kotlin (`sdks/android`)
- Single-project Gradle build: `settings.gradle.kts` (`rootProject.name = "megooflags-android"`), `build.gradle.kts` with `com.android.library` (AGP 8.13.x), `org.jetbrains.kotlin.android` 2.2.x with `languageVersion`/`apiVersion` `KOTLIN_2_1` (consumers on Kotlin 2.1 compile; OpenFeature Kotlin 0.9 is built with 2.2), `explicitApi()`, `com.diffplug.spotless` (ktlint), `com.vanniktech.maven.publish` (Central Portal, in-memory signing). `android { namespace = "io.github.mmhy2003.megooflags"; compileSdk = 35; defaultConfig.minSdk = 21; compileOptions Java 11; testOptions.unitTests.isReturnDefaultValues = true }`. `gradle.properties`: `GROUP=io.github.mmhy2003`, `POM_ARTIFACT_ID=megooflags-android`, `VERSION_NAME=0.1.0`, POM name/description/url/license/developer/scm, `android.useAndroidX=false`. Gradle wrapper 8.14.x committed (`gradlew`, `gradle/wrapper/*`), generated with `gradle wrapper --gradle-version 8.14.3` (or `docker run --rm -v "$PWD:/p" -w /p gradle:8.14-jdk17 gradle wrapper --gradle-version 8.14.3`). JDK 17 to build.
- Dependencies: `api("dev.openfeature:kotlin-sdk:0.9.0")` with a `[0.9.0, 0.10.0)` strict range is not needed; pin `0.9.0`. `implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:<version OpenFeature 0.9 uses, 1.7.3 or what its POM says>")`. Tests: `junit:junit:4.13.2`, `org.jetbrains.kotlinx:kotlinx-coroutines-test`, `org.json:json:20250517` (android.jar's org.json is a stub on the JVM).
- OpenFeature 0.9 API: `suspend initialize(initialContext)`, `shutdown()`, `suspend onContextSet(old, new)`, `getBoolean/String/Integer/Long/Double/ObjectEvaluation` (all abstract, `getLongEvaluation` included), `observe(): Flow<OpenFeatureProviderEvents>` (`ProviderReady`, `ProviderError`, `ProviderConfigurationChanged`, `ProviderStale`, each `EventDetails(flagsChanged, message, errorCode, …)`), errors `OpenFeatureError.ProviderFatalError` etc., `ProviderEvaluation(value, variant, reason, errorCode, errorMessage)`, `Value` (`Integer(Int)`, `Long`, `Double`, `Instant`, `Structure`, `List`, `Null`).
- Concurrency: one `CoroutineScope(SupervisorJob() + dispatcher)` per provider; the dispatcher is injectable (`Dispatchers.IO` default, `StandardTestDispatcher` in tests); events through a `MutableSharedFlow(extraBufferCapacity = 64)`.
- HTTP: `HttpURLConnection`; the stream uses `readTimeout = 45_000` as the dead timer; JSON with `org.json`.
- `MegooFlagsConfig.Builder(context: android.content.Context, sdkKey: String)` uses `context.applicationContext.filesDir/megooflags` and registers the lifecycle callbacks; an `internal` constructor `Builder(sdkKey, cacheDir: File?)` is used by JVM tests (no Android `Context` on the JVM).
- Tests skip e2e unless `-Pe2e`: in `build.gradle.kts` `tasks.withType<Test>().configureEach { if (project.hasProperty("e2e")) filter { includeTestsMatching("io.github.mmhy2003.megooflags.e2e.*") } else exclude("**/e2e/**"); systemProperty("spec.dir", rootDir.resolve("../../spec/client").absolutePath) }` and pass the e2e env vars through.
- Check group: `./gradlew spotlessCheck lint testDebugUnitTest assembleRelease`.

## L4. Dart (`sdks/flutter`)
- `pubspec.yaml`: `name: megooflags`, `version: 0.1.0`, `description`, `repository: https://github.com/mmhy2003/MegooFlags/tree/main/sdks/flutter`, `environment: {sdk: ^3.10.0, flutter: ">=3.38.0"}`; dependencies `flutter` (sdk), `openfeature_dart_client_sdk: ">=0.0.1 <0.1.0"`, `http: ^1.4.0`, `shared_preferences: ^2.5.0`; dev `flutter_test` (sdk), `flutter_lints`, `fake_async`. `analysis_options.yaml` includes `package:flutter_lints/flutter.yaml`. `pubspec.lock` is not committed (library package; `.gitignore` it with `.dart_tool/`, `build/`).
- OpenFeature Dart 0.0.1 API: implement `FeatureProvider` (`metadata`, `resolveBooleanValue/StringValue/IntegerValue/DoubleValue/StructureValue` returning `ResolutionDetails`), `InitializableProvider.initialize(context, {domain})`, `ContextReconciliationProvider.onContextChanged(previous, new)`, `ShutdownProvider.shutdown()`, `ProviderEventSource.events` (`Stream<ProviderEvent>`, `ProviderEventType.ready/error/configurationChanged/stale/reconciling/contextChanged`). The provider emits `reconciling` and `contextChanged` itself. Before step 40, read `lib/src/api.dart` of the pinned package in the pub cache and follow it on one point: emit `ready` after `initialize` only if the SDK does not set ready from the completed future (never twice). Fatal: `error` event with `ErrorCode.providerFatal`, and `initialize` throws `OpenFeatureException(…, errorCode: ErrorCode.providerFatal)`.
- Tests: `flutter test` (unit and shared cases under `test/`, reading `../../spec/client`), `fake_async` for timers, `dart:io HttpServer` as the fake server, `SharedPreferences.setMockInitialValues({})` / the async in-memory store for the cache.
- E2E under `e2e/` run with `flutter test e2e`: in `setUpAll` call `TestWidgetsFlutterBinding.ensureInitialized()` then `HttpOverrides.global = null` (the test binding blocks real HTTP otherwise).
- Check group: `flutter pub get && dart format --output=none --set-exit-if-changed . && flutter analyze && flutter test && flutter pub publish --dry-run`.

## R. Release jobs
Pattern of `release.yml`'s SDK section (`sdk-tag` checks main; `sdk-<lang>-release` verifies and builds; publishing only when `vars.SDK_PUBLISH_<LANG> == 'true' && !contains(github.ref_name, '-rc.')`):

| SDK | Tag pattern | Release job | Publish |
|---|---|---|---|
| js | `sdk-js/v*` | ubuntu, Node 24: check version, check group, `pnpm pack --pack-destination out`, artifact `sdk-js` | `sdk-js-publish`: `id-token: write`, download, `npm publish out/*.tgz --access public` (npm ≥ 11.5.1) |
| react | `sdk-react/v*` | same, after building `../js` | `sdk-react-publish` |
| react-native | `sdk-react-native/v*` | same, after building `../js` and `../react` | `sdk-react-native-publish` |
| ios | `sdk-ios/v*` | macos-15: check version, `swift build`, `swift test --filter MegooFlagsTests` | `sdk-ios-publish` (ubuntu, `fetch-depth: 0`, `persist-credentials: false`): `git subtree split --prefix=sdks/ios -b sdk-ios-split`, push `sdk-ios-split:refs/heads/main` and `refs/tags/X.Y.Z` to `vars.SDK_IOS_SPLIT_REPO || 'mmhy2003/megooflags-swift'` with `secrets.SDK_IOS_SPLIT_TOKEN` |
| android | `sdk-android/v*` | ubuntu, JDK 17: check version, check group, artifact `build/outputs/aar/*.aar`; publish step in the same job: `./gradlew publishAndReleaseToMavenCentral --no-configuration-cache` with `ORG_GRADLE_PROJECT_mavenCentralUsername/Password` = `CENTRAL_USERNAME/PASSWORD`, `ORG_GRADLE_PROJECT_signingInMemoryKey` = `GPG_SIGNING_KEY`, `…KeyPassword` = `GPG_PASSPHRASE` | in the release job, as Java |
| flutter | `sdk-flutter/v*` | ubuntu, Flutter 3.38.x: check version, check group | `sdk-flutter-publish`: `id-token: write`, checkout, `dart-lang/setup-dart@v1`, `subosito/flutter-action@v2` (3.38.x), `flutter pub get`, `flutter pub publish --force` in `sdks/flutter` |

## Final verification
```bash
# shared
(cd api && uv run pytest tests/test_spec_client.py -v) && uv run --frozen --project api pytest scripts/tests
# TypeScript
(cd sdks/js && pnpm install --frozen-lockfile && pnpm format:check && pnpm lint && pnpm typecheck && pnpm test && pnpm build && pnpm pack)
(cd sdks/react && pnpm install --frozen-lockfile && pnpm format:check && pnpm lint && pnpm typecheck && pnpm test && pnpm build && pnpm pack)
(cd sdks/react-native && pnpm install --frozen-lockfile && pnpm format:check && pnpm lint && pnpm typecheck && pnpm test && pnpm build && pnpm pack)
# native (iOS on macOS)
(cd sdks/ios && swift format lint --strict --recursive Sources Tests Package.swift && swift build && swift test --filter MegooFlagsTests)
(cd sdks/android && ./gradlew spotlessCheck lint testDebugUnitTest assembleRelease)
(cd sdks/flutter && flutter pub get && dart format --output=none --set-exit-if-changed . && flutter analyze && flutter test && flutter pub publish --dry-run)
# e2e against the compose stack (AGENTS.md §9): pnpm e2e in js, react, react-native; ./gradlew testDebugUnitTest -Pe2e; flutter test e2e; on macOS swift test --filter MegooFlagsE2ETests
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```
All twelve new CI jobs green on the PR; every shared-cases runner reports 100 % (JS: all non-`integer` tests).
