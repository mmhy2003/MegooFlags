# MegooFlags PHP SDK

The MegooFlags server SDK for PHP is an [OpenFeature](https://openfeature.dev) provider. It
evaluates flags in-process against an environment's ruleset kept in a shared cache directory: there
is no network call per evaluation, and p99 is under 1 ms on a 10 000-flag ruleset. An optional sync
daemon follows changes over SSE with a polling fallback; without it, requests refresh the cache
themselves after the response was sent. It keeps serving when the server is unreachable and sends
evaluation counts and exposures in batches.

Status: 0.1.0, Alpha.

## Install

```sh
composer require megooflags/megooflags
```

The package needs PHP 8.3 or later with the `curl` and `json` extensions. Its only dependency is
the OpenFeature SDK (`open-feature/sdk ^2.4`), which brings `psr/log`.

## Quick start

```php
<?php

use MegooFlags\Config;
use MegooFlags\Provider;
use OpenFeature\implementation\flags\Attributes;
use OpenFeature\implementation\flags\EvaluationContext;
use OpenFeature\OpenFeatureAPI;

require __DIR__ . '/vendor/autoload.php';

$api = OpenFeatureAPI::getInstance();
$api->setProvider(new Provider(new Config(
    sdkKey: (string) getenv('MEGOOFLAGS_SDK_KEY'), // a server key, mf_server_…
    baseUrl: 'https://flags.example.com',
    cacheDir: '/var/cache/megooflags',    // shared by every request and the sync daemon
)));

$client = $api->getClient();
$context = new EvaluationContext('user-123', new Attributes(['plan' => 'pro']));
$enabled = $client->getBooleanValue('new-checkout', false, $context);
echo 'new-checkout: ' . var_export($enabled, true) . PHP_EOL;
```

The targeting key (`user-123` above) is the context's `key` attribute in MegooFlags rules, and it is
what percentage rollouts bucket on by default.

PHP starts every request from scratch, so the provider does nothing in its constructor: it loads the
ruleset on the first evaluation (see [Offline and bootstrap](#offline-and-bootstrap)) and registers
`flush()` to run when the request ends (see [Upkeep without the daemon](#upkeep-without-the-daemon)).
Every process and request that uses the same SDK key and `cacheDir` shares one ruleset on disk.

## Configuration

Every named argument of `new Config(…)`. Durations are seconds (`float`); zero, negative, infinite
or NaN means the default.

| Option          | Default                     | Notes                                                                                                   |
| --------------- | --------------------------- | ------------------------------------------------------------------------------------------------------- |
| `sdkKey`        | (required)                  | A server key, `mf_server_…`. A client key is refused.                                                   |
| `baseUrl`       | (required unless `offline`) | The MegooFlags URL, `http` or `https`, e.g. `https://flags.example.com`. Trailing slashes are removed. |
| `cacheDir`      | (required)                  | The directory of the shared cache files (see below). Created with mode 0700 when missing.               |
| `bootstrapFile` | `null`                      | A ruleset JSON file to start from when the cache is empty.                                              |
| `offline`       | `false`                     | No network at all and no events: serve from the cache or `bootstrapFile` only.                         |
| `initTimeout`   | `5.0`                       | How long the first evaluation of a cold start waits for the ruleset.                                    |
| `disableStream` | `false`                     | Used by the sync daemon only (`--disable-stream`).                                                     |
| `pollInterval`  | `30.0`                      | How often the ruleset is checked. Minimum 15; a smaller value is raised to 15.                          |
| `flushInterval` | `30.0`                      | Used by the sync daemon only (`--flush-interval`). Clamped to 10–60.                                   |
| `sendContext`   | `false`                     | Include the evaluation context in exposures.                                                            |
| `logger`        | `NullLogger`                | Any PSR-3 `LoggerInterface`, such as Monolog. The SDK key is never logged.                              |

An invalid config (a client key, a key that is not a server key, no `cacheDir`, a bad `baseUrl`) does
not throw: it is logged, and every evaluation returns the default value with `PROVIDER_NOT_READY` and
a message that says why. `var_dump` of a `Config` shows the key as `[hidden]`.

## Server keys only

This SDK downloads the whole ruleset, rules and segments included, so it needs a **server** key. A
client key (`mf_client_…`) is refused. Never ship a server key to browsers or mobile apps: they use
client keys, which only return evaluated values.

## How updates arrive

- **With the sync daemon** (recommended): `vendor/bin/megooflags-sync` keeps an SSE stream open
  (`GET /sdk/v1/stream`) and rewrites the cache files each time the server sends a ruleset: when the
  stream connects, and after every change, including the kill switch. Requests see the change on
  their next evaluation. See [The sync daemon](#the-sync-daemon).
- **Without it**: at the end of a request, when nobody checked the ruleset for `pollInterval`, that
  one request makes a conditional `GET /sdk/v1/ruleset` (with `If-None-Match`) after the response
  was sent. Changes then arrive within about `pollInterval`, as long as requests keep coming. See
  [Upkeep without the daemon](#upkeep-without-the-daemon).
- A ruleset that does not parse is logged and ignored; the previous one keeps being served.
- **401, 403 or a `revoked` event** writes a fatal marker to the state file. From then on no process
  fetches, streams or sends events for that key, and the last ruleset keeps being served. A new key
  has its own cache files; to retry the same key, delete its `megooflags-<hash>.state.json`.
- 5xx and network errors back off and retry (2 s to 60 s for requests, 3 s to 60 s for the daemon's
  stream); 429 waits for `Retry-After`. In both cases the last ruleset keeps being served.

## Offline and bootstrap

On its first evaluation, a provider looks for a ruleset in this order:

1. the compiled cache file in `cacheDir`;
2. the JSON cache file in `cacheDir` (compiled on the spot, and the compiled file written);
3. `bootstrapFile` (compiled, and written as the compiled file, so the next request loads it);
4. the network: a cold start. One request fetches `GET /sdk/v1/ruleset` while holding the lock file;
   others wait for the lock, then use what it wrote. All of it is bounded by `initTimeout`.

**The compiled cache wins over the bootstrap file.** Once any request has fetched or compiled a
ruleset, the bootstrap file is not read again; delete the `megooflags-<hash>.php` file to make it
count. When the cold start fails, the next attempt waits for a backoff (2 s to 60 s), and until a
ruleset is found every evaluation returns the default value with `PROVIDER_NOT_READY`.

With `offline: true` the provider makes no network calls and sends no events. It serves the cache or
the bootstrap file, and returns `PROVIDER_NOT_READY` when neither gives a valid ruleset.

A bootstrap file is a ruleset as returned by `GET /sdk/v1/ruleset`:

```sh
curl -H "Authorization: Bearer $MEGOOFLAGS_SDK_KEY" https://flags.example.com/sdk/v1/ruleset > ruleset.json
```

## Events

Unless `offline` or the key was refused, each request records evaluation counts and exposures in
memory and, when it ends, appends them as one line to the events spool,
`megooflags-<hash>.events.jsonl`. The daemon ships the spool every `flushInterval`; without the
daemon, the request that does the upkeep ships it. Shipping sends `POST /sdk/v1/events`.

- **Evaluation counts** are aggregated per flag, variant and UTC hour. They never carry a context.
- **Exposures**: one per context key, flag, variant and UTC hour with the daemon (10 000 keys kept in
  memory across shipments). Without the daemon, see the known difference below.
- An exposure carries no context unless `sendContext` is true, and the server stores it only when it
  runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- Evaluation never waits on events. A request keeps at most 10 000 exposures, and when the spool has
  reached 8 MiB new lines are dropped (and a warning is logged) until it is shipped.
- Batches hold at most 1000 counts and 1000 exposures. 429, 5xx and network errors put the unsent
  part back into the spool and retry later with backoff; 413 or a body over 1 MB splits the batch;
  422 drops it.

## OpenFeature events

The PHP OpenFeature SDK has no provider lifecycle and no provider events, so this provider emits
none. There is no initialisation step and no `PROVIDER_FATAL` error code: a refused key or a missing
ruleset shows as `PROVIDER_NOT_READY` on each evaluation (see
[Upkeep without the daemon](#upkeep-without-the-daemon)).

## Results

- `reason` is one of `STATIC`, `DEFAULT`, `TARGETING_MATCH`, `SPLIT`, `DISABLED` or `ERROR`, plus the
  custom reason `PREREQUISITE_FAILED` when a prerequisite flag did not serve the required variation.
- `variant` is the key of the served variation.
- The flag metadata has `rule_index` (the 0-based index of the matching rule) and `prerequisite` (the
  key of the prerequisite that failed), when they apply.
- On an error, the value is your default, the reason is `ERROR`, and the error code and message say
  why.
- `getIntegerValue` returns an `int` and refuses a number flag whose value is not integral with
  `TYPE_MISMATCH`; `getFloatValue` returns a `float` for any number flag. `getObjectValue` returns a
  PHP array (a JSON object becomes an associative array, a JSON array a list); any other value is
  `TYPE_MISMATCH`.

## Context attributes

- Rules match top-level context attributes only. A nested array that is not a list, or an object,
  matches nothing.
- Dates for `before` and `after` are RFC 3339 strings or epoch milliseconds. A `DateTimeInterface`
  in the context matches nothing; pass `$date->format(DATE_RFC3339_EXTENDED)` or the epoch
  milliseconds.
- Regular expressions behave like Python's `re` with `re.ASCII`, the semantics every MegooFlags SDK
  reproduces, so a pattern evaluates the same everywhere: `(?i)` folds ASCII letters only, `\d`,
  `\w` and `\s` are ASCII, and `$` also matches before a final newline. The SDK translates each
  pattern to a PCRE pattern with those semantics when it compiles the ruleset. Patterns outside the
  portable subset (for example lookarounds, backreferences or `\p{…}`) never match; the ruleset is
  still loaded, and the sync daemon logs a warning for each such pattern.

## The sync daemon

`vendor/bin/megooflags-sync` is an optional long-running process, one per SDK key and cache
directory. It keeps the cache files fresh so that requests never fetch the ruleset or ship events
themselves, and it delivers changes within a second or two instead of about `pollInterval`.

What it does:

- follows the SSE stream and writes the cache files on every ruleset; after two stream failures in a
  row it polls every `pollInterval` until the stream is back;
- every 5 s writes `checked_at` to the state file, so requests skip their upkeep;
- every `flushInterval` ships the events spool;
- on SIGTERM or SIGINT ships the spool once more and exits 0 (this needs the `pcntl` extension;
  without it the signal stops the process at once and the spool is shipped by the next run).

Run it as the same user as PHP-FPM (the cache files are mode 0600 and a directory the SDK creates is
0700), with the same `cacheDir` as your `Config`.

The SDK key is read from the environment only, so it never shows in a process list. Every option is
also read from an environment variable; a flag wins over the variable.

| Flag                   | Variable                     | Default    | Notes                                                    |
| ---------------------- | ---------------------------- | ---------- | -------------------------------------------------------- |
|                        | `MEGOOFLAGS_SDK_KEY`         | (required) | A server key, `mf_server_…`.                             |
| `--base-url=URL`       | `MEGOOFLAGS_BASE_URL`        | (required) | The MegooFlags URL.                                      |
| `--cache-dir=DIR`      | `MEGOOFLAGS_CACHE_DIR`       | (required) | The providers' `cacheDir`.                               |
| `--poll-interval=SEC`  | `MEGOOFLAGS_POLL_INTERVAL`   | `30`       | Polling interval while the stream is down; at least 15.  |
| `--flush-interval=SEC` | `MEGOOFLAGS_FLUSH_INTERVAL`  | `30`       | Events shipping interval, 10 to 60.                      |
| `--disable-stream`     | `MEGOOFLAGS_DISABLE_STREAM=1` | off       | Poll only, never open the SSE stream.                    |
| `--once`               |                              |            | Fetch the ruleset and ship the spool once, then exit.    |
| `--help`               |                              |            | Print the options and exit.                              |

Exit status: 0 when stopped by SIGTERM or SIGINT or after `--once`; 1 when `--once` failed; **78**
(`EX_CONFIG`) for an invalid configuration or a key the server refused (401, 403, `revoked`), and at
once when the state file already holds a fatal marker. Configure your supervisor not to restart on
78: restarting cannot fix the key.

Under systemd:

```ini
# /etc/systemd/system/megooflags-sync.service
[Unit]
Description=MegooFlags sync daemon
After=network-online.target
Wants=network-online.target

[Service]
User=www-data
Environment=MEGOOFLAGS_BASE_URL=https://flags.example.com
Environment=MEGOOFLAGS_CACHE_DIR=/var/cache/megooflags
EnvironmentFile=/etc/megooflags/sync.env
ExecStart=/usr/bin/php /var/www/app/vendor/bin/megooflags-sync
Restart=on-failure
RestartSec=5
RestartPreventExitStatus=78

[Install]
WantedBy=multi-user.target
```

with `MEGOOFLAGS_SDK_KEY=mf_server_…` in `/etc/megooflags/sync.env` (mode 0600).

In a container, run it as a sidecar next to PHP-FPM, from the same image and with a shared volume for
the cache directory:

```yaml
services:
  app:
    image: my-app
    volumes: ["megooflags-cache:/var/cache/megooflags"]
  megooflags-sync:
    image: my-app
    command: ["php", "vendor/bin/megooflags-sync"]
    environment:
      MEGOOFLAGS_SDK_KEY: ${MEGOOFLAGS_SDK_KEY}
      MEGOOFLAGS_BASE_URL: https://flags.example.com
      MEGOOFLAGS_CACHE_DIR: /var/cache/megooflags
    volumes: ["megooflags-cache:/var/cache/megooflags"]
    restart: on-failure
volumes:
  megooflags-cache:
```

For a cron job or a deploy step instead of a process, `megooflags-sync --once` refreshes the cache
and ships the spool once.

## Upkeep without the daemon

Without the daemon, the requests themselves keep the cache fresh. The cache directory holds, per SDK
key (`<hash>` is the first 16 hex characters of sha256 of the key, so the key is never written to
disk and several keys can share a directory):

| File                               | Content                                                                                                |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `megooflags-<hash>.json`           | The last ruleset from the network and its ETag.                                                        |
| `megooflags-<hash>.php`            | The same ruleset compiled to a PHP array, loaded with `require`.                                       |
| `megooflags-<hash>.state.json`     | When the ruleset was last checked, its ETag, the failure count, the next allowed attempt, the fatal marker. |
| `megooflags-<hash>.lock`           | The lock file (`flock`) that lets one process at a time fetch or ship.                                 |
| `megooflags-<hash>.events.jsonl`   | The events spool, one line per request.                                                                |

Files are written atomically (a temp file, then a rename) with mode 0600.

When a request ends, `flush()` (registered with `register_shutdown_function`) does this:

1. it appends the request's counts and exposures to the spool as one line;
2. when nobody checked the ruleset for `pollInterval`, no backoff is pending, there is no fatal
   marker and it gets the lock without waiting, it does the upkeep: a conditional
   `GET /sdk/v1/ruleset` (10 s timeout) that rewrites the cache files on a change, then it ships the
   spool. Under PHP-FPM it first calls `fastcgi_finish_request()`, so the client already has the
   response and does not wait for this; under other SAPIs the request's process does the work before
   it exits.

At most one request per `pollInterval` across all processes does the upkeep; every other request
only appends its line.

**OPcache.** The compiled file is plain constant data, so OPcache keeps it in shared memory and
loading the ruleset costs no parsing. When the SDK rewrites it, it calls `opcache_invalidate()`, and a
request that sees a newer ETag in the state file invalidates it again before loading it, which
covers a file the daemon (whose CLI OPcache is separate) rewrote. So `opcache.validate_timestamps=0`
is fine. If you set `opcache.restrict_api`, it must allow the SDK's scripts to call
`opcache_invalidate()`; otherwise a change only shows once OPcache revalidates the file on its own.

**Known difference in exposure dedup.** Without the daemon, exposures are deduplicated within a
request and within each shipped spool, not per hour: the same user seeing the same variant in two
shipments is sent twice. Counts are exact either way. Run the daemon for hourly dedup.

**`PROVIDER_NOT_READY` instead of `PROVIDER_FATAL`.** The PHP OpenFeature SDK has no fatal state,
so an invalid config, a refused key without a cached ruleset, or no ruleset at all gives the default
value with `PROVIDER_NOT_READY` and a message that names the cause.

**Long-running workers.** In a queue worker, a ReactPHP or Swoole server, or a RoadRunner worker the
request never ends, so call `$provider->flush()` yourself, for example after each job or request.
Each call appends the evaluations recorded since the last call and does the upkeep when it is due.
A provider keeps the ruleset it loaded first; create a new `Provider` (for example per job) to pick
up a newer ruleset from the cache.

## Versioning and license

The package is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdk-php/vX.Y.Z`, and only maintainers push those tags. `MegooFlags\Version::VERSION` holds the version, and requests send
`User-Agent: megooflags-php/<MegooFlags\Version::VERSION>`.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/php/`:

```sh
composer install
composer validate --strict
composer cs     # php-cs-fixer, dry run
composer stan   # PHPStan, level 8
composer test   # unit tests, the daemon tests and the conformance suite
composer p99    # the p99 guard, on its own
```

The conformance runner reads `../../spec/conformance` and reports each case as `<file>::<name>`. The
daemon tests that send SIGTERM are skipped without the `pcntl` extension.

The end-to-end test needs a running stack and an API token (see the `sdk-php-e2e` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
composer e2e
```

Without both variables it is skipped.
