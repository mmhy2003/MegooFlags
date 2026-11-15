# MF-256: Node.js, Python, Java, .NET, Ruby and PHP server SDKs as OpenFeature providers

## Goal
A back-end service written in Node.js, Python, Java, .NET, Ruby or PHP can register a MegooFlags provider with its language's official OpenFeature SDK, give it a server SDK key, and evaluate flags in-process: p99 under 1 ms on 10 000 flags, no network call per evaluation. Each SDK meets the bar the Go SDK set in MF-83: 100 % of `spec/conformance`, an end-to-end test against the compose stack in CI, SSE with a polling fallback, offline start from a bootstrap file or the cache, and background event batches.

## Decisions
- **Scope: all six SDKs in this task, one PR into `develop` (your answer).** Built in order: Node.js, Python, Java, .NET, Ruby, PHP. Each SDK is its own series of commits and must be green (unit, conformance, e2e, CI jobs) before the next one starts. *Rejected:* Node.js only with follow-up tasks (my recommendation); the three P0 SDKs only. *Consequence:* the PR is very large (roughly six times MF-83), so it is reviewed SDK by SDK.
- **Dependencies: standard library plus the OpenFeature SDK only (your answer).** No other runtime dependency in any SDK. The SSE reader, poller, SemVer and RFC 3339 parsers and the regex translator are written by hand in each language. Java gets a small internal JSON reader and writer. PHP uses only bundled extensions (`json`, `pcre`, `hash`, `curl`). Test and build tools are development dependencies and are not restricted. *Rejected:* a JSON library in Java; RE2 and HTTP libraries.
- **PHP: shared cache plus an optional sync daemon (your answer).** *Rejected:* in-request polling only (no SSE); a required daemon.
- **Names and minimum runtimes (your answer):**

| SDK | Directory | Package | Minimum | CI matrix | Tag |
|---|---|---|---|---|---|
| Node.js | `sdks/node` | npm `@megooflags/node` | Node 22 | 22, 24, 26 | `sdk-node/vX.Y.Z` |
| Python | `sdks/python` | PyPI `megooflags` | Python 3.11 | 3.11 to 3.15 | `sdk-python/vX.Y.Z` |
| Java | `sdks/java` | Maven `io.github.mmhy2003:megooflags` | Java 11 | 11, 17, 21, 25 | `sdk-java/vX.Y.Z` |
| .NET | `sdks/dotnet` | NuGet `MegooFlags` | `net8.0` | 8, 10 | `sdk-dotnet/vX.Y.Z` |
| Ruby | `sdks/ruby` | gem `megooflags` | Ruby 3.4 | 3.4, 4.0 | `sdk-ruby/vX.Y.Z` |
| PHP | `sdks/php` | Packagist `megooflags/megooflags` | PHP 8.3 | 8.3, 8.4, 8.5 | `sdk-php/vX.Y.Z` |

- **Releases: verify, build, and publish when switched on (your answer).** A tag always checks the version, runs the tests and builds the package. It publishes only when the repository variable `SDK_PUBLISH_<LANG>` is `true`. *Rejected:* build only; always publish.
- **Behaviour is copied from the Go SDK, not redesigned.** Every default, limit and stop/retry rule below is the Go SDK's. Where a language cannot do what Go does, the difference is listed under that language.
- **Regex: one translator design, ported five times.** It accepts exactly the portable subset of `evaluation.md` §6.3 and emits only constructs that mean the same in every host engine. Python needs none: it uses `re.compile(pattern, re.ASCII)`, like the reference engine.
- Every SDK starts at version `0.1.0` (Alpha), is Apache 2.0 with its own `LICENSE` copy, and sends `User-Agent: megooflags-<lang>/<version>`. Agents never push tags.

## Architecture

### The blueprint (all six SDKs)
Each SDK has the same layers as `sdks/go`, as internal (non-public) modules:

| Layer | Responsibility |
|---|---|
| `engine` | Parses and validates a ruleset (`evaluation.md` §11) and prepares clause values (translated regexes, parsed SemVers, epoch milliseconds, segment sets). Evaluates §3 to §9: targets, rules, operators, segments, SHA-256 bucketing, prerequisites with depth guard 32, expected-type checks. Pure, no I/O. |
| `transport` | Builds requests (`Authorization: Bearer`, never the key in a URL), maps statuses (401/403 fatal, 429 with `Retry-After`, other errors), jittered exponential backoff. |
| `delivery` | Store: the current ruleset and ETag, swapped atomically. SSE client for `GET /sdk/v1/stream`: `retry`, `: ping`, `put`, `revoked`, unknown events ignored, `Last-Event-ID`, a 45 s dead-connection timer, backoff from 3 s to 60 s, reset after 60 s connected. Poller: `GET /sdk/v1/ruleset` with `If-None-Match` every 30 s (never under 15 s), started after the stream fails twice in a row and stopped when it reconnects. Cache: the last valid ruleset on disk. |
| `events` | Counts per flag, variant and UTC hour. One exposure per context key, flag, variant and UTC hour (LRU of 10 000). Bounded buffers; evaluation never blocks and drops on overflow. Flush every 30 s (10 to 60 s), at 1000 items, and on shutdown (5 s deadline). 1 MB body limit; 413 splits, 422 drops, 429 and 5xx keep the batch and retry with backoff and `Retry-After`; 401/403 stops. |
| provider | The OpenFeature provider: config validation, start-up order, lifecycle, provider events, and mapping of engine results to the SDK's resolution details. |

Rules that apply everywhere:
- **Server keys only.** A key starting with `mf_client_` (or anything not starting with `mf_server_`) is refused when the provider is created. The provider then fails initialisation with `PROVIDER_FATAL`.
- **Start-up order:** bootstrap file, then cache, then network. With a ruleset from the file or the cache, initialisation returns at once and the network refreshes in the background. Otherwise it waits up to `initTimeout` (5 s), then fails while it keeps retrying, and emits `PROVIDER_READY` when a ruleset arrives.
- **Cache file:** `<cacheDir>/megooflags-<first 16 hex of sha256(key)>.json` holding `{"etag", "ruleset"}`, written to a temp file and renamed, mode 0600. This is the Go SDK's format, so a cache directory can be shared between SDKs.
- **Provider events:** `PROVIDER_CONFIGURATION_CHANGED` with the flags added, removed or with a new version; `PROVIDER_STALE` when disconnected; `PROVIDER_READY` when reconnected; `PROVIDER_ERROR` with the fatal code on 401, 403 or `revoked`.
- **Result mapping:** `targetingKey` is copied to `key`. Flag metadata carries `rule_index` and `prerequisite`. `PREREQUISITE_FAILED` is passed through as a custom reason. An integer evaluation of a non-integral number is `TYPE_MISMATCH`.

### Per language

| SDK | Layout and tooling | Concurrency and HTTP | OpenFeature integration |
|---|---|---|---|
| **Node.js** | TypeScript, ESM-only package with type declarations (CommonJS loads it with `require()` on Node 22.12+). pnpm with its own lockfile, ESLint, Prettier, `tsc`, Vitest. | One event loop. Global `fetch` with `AbortController`; the SSE reader consumes the response body stream. Timers are unreferenced. | `@openfeature/server-sdk` as a peer dependency. Implements `Provider` with an `OpenFeatureEventEmitter`. Errors are returned as `errorCode`; initialisation throws `ProviderFatalError` for a bad key. |
| **Python** | `src/megooflags/`, uv with its own lockfile, ruff, strict mypy, pytest, `py.typed`. The engine starts as a copy of the stdlib-only reference engine in `api/src/megooflags_api/evaluation/`. | Daemon threads; `http.client` with a 45 s socket timeout on the stream. Threads restart in a forked child (`os.register_at_fork`), for gunicorn and uWSGI. | `openfeature-sdk`. Extends `AbstractProvider`; raises `ProviderFatalError` in `initialize` for a bad key; uses `emit_provider_*`. |
| **Java** | Maven, `release` 11, package `io.github.mmhy2003.megooflags` with `internal` subpackages. JUnit 5, formatter check, `-Xlint:all -Werror`. Internal JSON reader and writer (about 300 lines). Logging through `System.Logger`. | Daemon threads; `java.net.http.HttpClient` with a streaming body; a watchdog closes a stream that is silent for 45 s. | `dev.openfeature:sdk`. Extends `EventProvider`; throws `FatalError` in `initialize`; converts values to `Value` and `Structure`. |
| **.NET** | `MegooFlags.sln`, `src/MegooFlags` (`net8.0`, nullable, warnings as errors), `tests/MegooFlags.Tests` (xUnit), `dotnet format`. `System.Text.Json`. Logging through `ILogger`, which the OpenFeature package already brings. | Tasks with cancellation; `HttpClient` reading headers first. Provider events go through one pump task into `EventChannel`, so delivery never blocks on a handler. | `OpenFeature`. Extends `FeatureProvider`; throws `ProviderFatalException`; returns the caller's default itself on errors, because the .NET SDK does not replace it. |
| **Ruby** | Gem with `lib/megooflags/`, Bundler, RuboCop, Minitest. Stdlib `net/http`, `json`, `digest`, `logger`. | Threads; `Net::HTTP` with `read_body` and a 45 s read timeout. Threads restart in a forked child (`Process._fork` hook), for Puma, Unicorn and Passenger. | `openfeature-sdk` 0.8. Duck-typed provider including `Provider::EventEmitter`, on the non-deprecated lifecycle-events path. |
| **PHP** | Composer package, PSR-4 namespace `MegooFlags`, PHPUnit, PHPStan, PHP-CS-Fixer. Requires `ext-curl` and `ext-json`. | No background work in a request. See the next section. | `open-feature/sdk`. Extends `AbstractProvider`. The PHP SDK has no init, shutdown, events or `PROVIDER_FATAL` code, so the provider loads lazily and reports a refused key as `PROVIDER_NOT_READY` with a message. |

### PHP: shared cache plus optional sync daemon
Files in `cacheDir`, all named with the same key hash:

| File | Content |
|---|---|
| `megooflags-<hash>.json` | Raw ruleset and ETag (the common cache format) |
| `megooflags-<hash>.php` | The validated ruleset as a PHP array with regexes already translated. Requests `require` it, so OPcache serves it from shared memory |
| `megooflags-<hash>.state.json` | Time of the last check, failure count and next attempt, fatal marker. A lock file sits next to it |
| `megooflags-<hash>.events.jsonl` | The events spool: one line per request |

- **Every request** loads the compiled ruleset, evaluates locally and keeps counts and exposures in memory. At request end it appends one line to the spool (bounded in size; lines beyond the limit are dropped).
- **Upkeep without the daemon.** When the state file is older than `pollInterval` and the request gets the non-blocking lock, it does the upkeep after the response is sent (`fastcgi_finish_request()` when available): a conditional `GET /sdk/v1/ruleset`, a cache write on 200, and sending the spooled events in batches. A cold start with no cache and no bootstrap file fetches synchronously, bounded by `initTimeout`.
- **Daemon:** `vendor/bin/megooflags-sync`, configured by environment variables and flags. It holds the SSE stream with the polling fallback, writes both cache files on every valid `put`, refreshes the state file so that requests skip upkeep, and ships the spool every `flushInterval` with the hour-long LRU dedup. If it stops, the state file ages and requests take over by themselves.
- **Stop rule.** 401, 403 or `revoked` writes the fatal marker. No request and no daemon run retries with that key; the cached ruleset keeps being served. The daemon exits with a configuration error.
- **Known difference:** without the daemon, exposures are deduplicated per shipped spool, not per hour. Documented in the README.

### Portable regex translator (Node.js, Java, .NET, Ruby, PHP)
- **Accepted input**, the subset common to RE2 and Python `re`:
  - literals, `.`, alternation, groups `(…)`, `(?:…)` and `(?P<name>…)`;
  - quantifiers `* + ? {n} {n,} {n,m}` and their lazy forms;
  - classes with ranges and negation; `\d \D \w \W \s \S`;
  - anchors `^ $ \A \b \B`;
  - escapes `\t \n \r \f \v \a \xHH` and escaped punctuation;
  - flags `i`, `m`, `s`, global at the start of the pattern or scoped as `(?flags:…)`.
- Anything else makes the pattern fail to compile, so it never matches (clause rule 6). The failure is logged once when the ruleset loads.
- **Output:** only literals, explicit classes, groups, alternation, quantifiers and lookaround. Nothing depends on the host engine's defaults:
  - `\d`, `\w`, `\s` become explicit ASCII classes, and `\b` an ASCII-word lookaround pair;
  - `.` becomes `[^\n]`, or any character under `s`;
  - `$` becomes "end, or before a final `\n`", and under `m` "before any `\n` or at the end"; `^` under `m` becomes "start, or after a `\n`";
  - case-insensitivity is expanded by the translator: an ASCII letter becomes `[Kk]` and class ranges gain their ASCII counterpart. The host's ignore-case flag is never used, so `k` does not match U+212A and non-ASCII letters never fold;
  - named groups become plain groups.
- **Code points:** JS uses the `u` flag, PHP `(*UTF)` without `/u`, Java and Ruby match by code point natively, and .NET expands `.` and classes over surrogate pairs.
- No match timeout is added; the reference engine has none.

### Shared files this PR touches
- `spec/conformance/cases/operators_regex.json`: new cases where host engines differ. Each case must pass the Python reference engine and the unchanged Go SDK; a case Go cannot pass stays a per-SDK unit test. They cover:
  - `.` against `\r`, U+2028 and a character above U+FFFF;
  - `$` against `"ab\r"`;
  - `\s` against U+00A0 and U+0085; `\w` and `\b` next to a non-ASCII letter;
  - `(?m)`, `(?s)` and `(?i:…)`; `(?P<name>…)`; `\A`;
  - `[` inside a class; a case-insensitive class range.
- `.github/workflows/ci.yml`: jobs `sdk-<lang>` and `sdk-<lang>-e2e` for the six languages. A local composite action builds the images and starts the compose stack (the steps `sdk-go-e2e` has today), and `sdk-go-e2e` uses it too.
- `scripts/sdk_e2e_token.sh`: mints the service token the e2e tests use (first-run setup or sign-in, then `POST /api/tokens`), as `scripts/smoke.sh` does.
- `.github/workflows/release.yml`: `sdk-<lang>/v*` triggers and one `sdk-<lang>-release` job per language.
- `sdks/README.md`: six status rows, and `dotnet`, `ruby`, `php` added to the planned order.
- `sdks/RELEASING.md` (new): the one-time registry setup and the release steps.
- `AGENTS.md` §1 and §9: SDK package managers and local check commands.
- `CHANGELOG.md`: one `[Unreleased]` "Added" line per SDK.

## Data and interfaces
Configuration, with each language's naming and duration types:

| Option | Default | Notes |
|---|---|---|
| `sdkKey` | required | `mf_server_…` only |
| `baseUrl` | required unless offline | `http` or `https` |
| `bootstrapFile` | none | A ruleset as returned by `GET /sdk/v1/ruleset` |
| `cacheDir` | none (PHP: required) | Read at start, written on each change |
| `offline` | false | No network and no events; needs a bootstrap file or a cache |
| `initTimeout` | 5 s | |
| `disableStream` | false | Poll only |
| `pollInterval` | 30 s | Raised to 15 s when smaller |
| `flushInterval` | 30 s | Clamped to 10 to 60 s |
| `sendContext` | false | Include the context in exposures |
| `logger` | language default | The key is never logged |
| HTTP client hook | language default | Node.js `fetch`, Java `HttpClient`, .NET `HttpClient` only |

Public surface per SDK: one provider class, one config type and a version constant. Entry points: `new MegooFlagsProvider({...})` (Node.js), `MegooFlagsProvider(...)` (Python), `new MegooFlagsProvider(MegooFlagsConfig)` (Java), `new MegooFlagsProvider(MegooFlagsOptions)` (.NET), `MegooFlags::Provider.new(...)` (Ruby), `new \MegooFlags\Provider(new \MegooFlags\Config(...))` (PHP).

No server change: the SDKs use only `/sdk/v1/ruleset`, `/sdk/v1/stream` and `/sdk/v1/events` as specified in `spec/delivery.md`.

Release jobs (`release.yml`), one per language:
1. The tag matches the version in the package manifest (`-rc.N` stripped).
2. Lint, unit tests and conformance run.
3. The package is built and uploaded as a workflow artifact.
4. If `vars.SDK_PUBLISH_<LANG> == 'true'` and the tag is not an `-rc` tag, it is published:

| SDK | Publishing | You set up once |
|---|---|---|
| Node.js | `npm publish` with OIDC trusted publishing | The `@megooflags` scope, a first manual publish, the trusted publisher |
| Python | `pypa/gh-action-pypi-publish` with OIDC | A pending trusted publisher on PyPI |
| Java | Central Publisher Portal with a GPG signature | Secrets `CENTRAL_USERNAME`, `CENTRAL_PASSWORD`, `GPG_SIGNING_KEY`, `GPG_PASSPHRASE` |
| .NET | `NuGet/login` (OIDC), then `dotnet nuget push` | A trusted publishing policy; variable `NUGET_USER` |
| Ruby | `gem build` and `gem push` with OIDC | A pending trusted publisher on RubyGems |
| PHP | `git subtree split` of `sdks/php`, pushed with tag `vX.Y.Z` to a read-only repository that Packagist reads | The repository (for example `mmhy2003/megooflags-php`), its Packagist entry, secret `SDK_PHP_SPLIT_TOKEN` |

`-rc` tags verify and build but never publish.

## Error handling and edge cases
- An invalid ruleset from any source keeps the previous one, is logged and never throws. With no ruleset, every evaluation returns the default with `PROVIDER_NOT_READY`.
- 401, 403 or `revoked` stops the stream, the poller and the event sender for that key. The last ruleset keeps being served and a fatal error is reported.
- 5xx and network errors back off and keep serving. 429 waits `Retry-After`.
- Shutdown stops the sync, flushes events within 5 s and is idempotent.
- The evaluation path does no I/O and takes no lock beyond reading the current ruleset reference.
- **Node.js:** the open stream keeps the process alive until the provider is closed (`OpenFeature.close()`).
- **Python and Ruby:** after a fork the child restarts its own threads and starts with empty event buffers, so nothing is sent twice.
- **PHP:** a corrupt or half-written cache file is ignored. Cache and spool writes are atomic (temp file and rename, or an append under a lock). A subject that is not valid UTF-8 never matches a regex.
- **.NET:** the error value is always the caller's default.
- JSON numbers follow the conformance rules in every language (`1` equals `1.0`; a boolean is never a number; `{}` is not `[]`).

## Testing (TDD)
- **Unit tests** per SDK:
  - engine: ruleset parser, every operator, the regex translator (each construct, ASCII folding, code points), SemVer, dates, bucketing;
  - delivery: SSE parser, backoff and stop rules, poller ETags, cache write and read;
  - events: aggregator dedup, batcher split, drop and retry;
  - provider: config validation, lifecycle and provider events.

  HTTP tests run against a local fake server from the standard library.
- **Conformance:** a runner per SDK reads `../../spec/conformance`, refuses any other `schema_version`, compares with the README's JSON equality, checks the bucketing vectors and reports `<stem>::<name>`. 100 % must pass, including the new regex cases. The Python reference engine and the Go SDK must still pass them.
- **p99 guard:** a test builds a 10 000-flag ruleset and asserts p99 under 1 ms per evaluation.
- **End to end** (`sdk-<lang>-e2e`, skipped without `MEGOOFLAGS_E2E_URL` and `MEGOOFLAGS_API_TOKEN`): create a project, a non-approval environment, a boolean flag and a server key through the management API. Then check four things:
  1. the evaluated value;
  2. a toggle arriving over SSE within 5 s, with a configuration-changed event;
  3. at least 21 evaluations counted by the server after shutdown;
  4. a new provider pointed at a closed port serving the cached ruleset.

  For PHP, checks 2 and 3 run with the daemon as a child process, and one more check covers upkeep without it.
- **CI:** each `sdk-<lang>` job runs the format check, lint, type check, unit tests, conformance, the p99 guard and a package build on the version matrix above. Each `sdk-<lang>-e2e` job runs on one version. `actionlint` covers the workflow changes.
- **Docs:** `sdks/<lang>/README.md` with the same sections as the Go README (install, quick start, configuration, server keys, updates, offline and bootstrap, events, OpenFeature events, results, context attributes, tests). The PHP README adds the daemon and the upkeep model.

## Out of scope
- Browser, React and mobile SDKs; client-key support; the edge relay; any API or spec change beyond the new conformance cases.
- Fixing the Go SDK's documented residual (`(?i)` with non-ASCII letters) and any other change to `sdks/go` beyond reusing the composite CI action.
- Regex match timeouts or other ReDoS hardening.
- Publishing `-rc` versions to registries; creating registry accounts, scopes, secrets or the PHP split repository (maintainer steps in `sdks/RELEASING.md`).
- Framework integrations (Spring, ASP.NET dependency injection, Rails, Laravel, Symfony), APCu or Redis caches for PHP, and async-native (asyncio) networking in Python.
