# MegooFlags Python SDK

The MegooFlags server SDK for Python is an [OpenFeature](https://openfeature.dev) provider. It
downloads an environment's ruleset and evaluates flags in-process: there is no network call per
evaluation, and p99 is under 1 ms on a 10 000-flag ruleset. It follows changes over SSE with a
polling fallback, keeps serving when the server is unreachable, and sends evaluation counts and
exposures in the background.

Status: 0.1.0, Alpha.

## Install

```sh
pip install megooflags
# or
uv add megooflags
```

The package needs Python 3.11 or later. Its only dependency is the OpenFeature SDK
(`openfeature-sdk>=0.10,<1`), which it installs.

## Quick start

```python
import os

from openfeature import api
from openfeature.evaluation_context import EvaluationContext

from megooflags import MegooFlagsProvider

api.set_provider_and_wait(
    MegooFlagsProvider(
        sdk_key=os.environ["MEGOOFLAGS_SDK_KEY"],  # a server key, mf_server_…
        base_url="https://flags.example.com",
    )
)

client = api.get_client()
enabled = client.get_boolean_value(
    "new-checkout", False, EvaluationContext("user-123", {"plan": "pro"})
)
print("new-checkout:", enabled)

api.shutdown()  # closes the stream and flushes the buffered events
```

The targeting key (`user-123` above) is the context's `key` attribute in MegooFlags rules, and it is
what percentage rollouts bucket on by default.

The SDK's threads are daemon threads, so they do not keep the process alive. Call `api.shutdown()`
when your service stops, so the buffered events are sent.

### Forking servers (gunicorn, uWSGI)

The provider works in pre-fork servers such as gunicorn (also with `--preload`) and uWSGI. Threads
do not survive `fork()`, so the SDK registers an `os.register_at_fork` hook: in each worker, every
started provider keeps the ruleset it had and starts its own stream and event sender. A worker's
event buffers start empty, so nothing the parent recorded is sent twice. Creating the provider in
each worker after the fork (for example in gunicorn's `post_fork` hook) works as well.

## Configuration

Every keyword argument of `MegooFlagsProvider(…)`; `provider.config` holds the result as the frozen
dataclass `MegooFlagsConfig`. Durations are seconds (`float`) or `datetime.timedelta`; zero,
negative or NaN means the default.

| Option           | Default                          | Notes                                                                                                  |
| ---------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `sdk_key`        | (required)                       | A server key, `mf_server_…`. A client key is refused.                                                  |
| `base_url`       | (required unless `offline`)      | The MegooFlags URL, `http` or `https`, e.g. `https://flags.example.com`.                               |
| `bootstrap_file` | `None`                           | A ruleset JSON file to start from (`str` or `os.PathLike`).                                            |
| `cache_dir`      | `None`                           | A directory for the last-known ruleset: read at start, written on each change.                        |
| `offline`        | `False`                          | No network at all: serve from `bootstrap_file` or `cache_dir` only. One of them is required.           |
| `init_timeout`   | `5.0`                            | How long `initialize` waits for the first ruleset when neither the bootstrap file nor the cache gave one. |
| `disable_stream` | `False`                          | Poll only, never open the SSE stream.                                                                  |
| `poll_interval`  | `30.0`                           | Minimum 15; a smaller value is raised to 15.                                                           |
| `flush_interval` | `30.0`                           | How often events are sent. Clamped to 10–60.                                                           |
| `send_context`   | `False`                          | Include the evaluation context in exposures.                                                           |
| `logger`         | `logging.getLogger("megooflags")` | A `logging.Logger`. The SDK key is never logged.                                                       |

An invalid config (no key, a client key, a bad `base_url`, `offline` with neither a bootstrap file
nor a cache directory) is logged. `initialize` then raises `ProviderFatalError`, and every
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
- After the stream fails twice in a row, the provider also polls the ruleset every `poll_interval`
  (`GET /sdk/v1/ruleset` with `If-None-Match`) until the stream is back. With `disable_stream` it
  only polls.
- A ruleset that does not parse is logged and ignored; the previous one keeps being served.
- **401, 403 or a `revoked` event** stops streaming, polling and event sending for that key. The
  provider emits `PROVIDER_ERROR` with the fatal error code and keeps serving the last ruleset.
- 5xx and network errors back off and retry; 429 waits for `Retry-After`. In both cases the last
  ruleset keeps being served.

## Offline and bootstrap

At start-up the provider looks for a ruleset in this order:

1. `bootstrap_file`;
2. the cache in `cache_dir`;
3. the network.

When the bootstrap file or the cache gives a valid ruleset, `initialize` returns at once and the
network refreshes it in the background. Otherwise `initialize` waits up to `init_timeout` for the
first ruleset. After a timeout the provider is in the error state, keeps retrying, and emits
`PROVIDER_READY` when a ruleset arrives. Until then every evaluation returns the default value with
`PROVIDER_NOT_READY`.

With `offline=True` the provider makes no network calls and sends no events. It serves the
bootstrap file or the cache, and `initialize` raises if neither gives a valid ruleset.

The cache file is `<cache_dir>/megooflags-<first 16 hex characters of sha256(sdk_key)>.json`, so
several environments can share a directory and the key itself is never written to disk. It is
written atomically (a temp file, then a rename) with mode 0600, each time a new ruleset arrives from
the stream or a poll.

A bootstrap file is a ruleset as returned by `GET /sdk/v1/ruleset`:

```sh
curl -H "Authorization: Bearer $MEGOOFLAGS_SDK_KEY" https://flags.example.com/sdk/v1/ruleset > ruleset.json
```

## Events

Unless `offline`, the provider sends events to `POST /sdk/v1/events` from a background thread,
every `flush_interval`, when 1000 items are waiting, and on shutdown.

- **Evaluation counts** are aggregated per flag, variant and UTC hour. They never carry a context.
- **Exposures**: one per context key, flag, variant and UTC hour (deduplicated in memory, 10 000
  entries).
- An exposure carries no context unless `send_context` is true, and the server stores it only when it
  runs with `MEGOOFLAGS_PERSIST_CONTEXT=true`.
- Evaluation never waits on events. If the buffer is full, events are dropped and counted.
- 429 and 5xx keep the batch and retry with backoff; 413 splits it; 422 drops it.
- `api.shutdown()` (or `provider.shutdown()`) stops the sync and sends the buffered events within
  5 s.

## OpenFeature events

| Event                            | When                                                                                                                                                       |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PROVIDER_READY`                 | `initialize` succeeded (sent by OpenFeature), or the first ruleset arrived after `initialize` timed out, or the stream reconnected after `PROVIDER_STALE`. |
| `PROVIDER_CONFIGURATION_CHANGED` | A new ruleset was applied. `flags_changed` lists the flags added, removed or with a new version.                                                           |
| `PROVIDER_STALE`                 | The stream disconnected; the last ruleset keeps being served.                                                                                              |
| `PROVIDER_ERROR`                 | The key was refused (401, 403 or `revoked`), with error code `PROVIDER_FATAL`.                                                                             |

## Results

- `reason` is one of `STATIC`, `DEFAULT`, `TARGETING_MATCH`, `SPLIT`, `DISABLED` or `ERROR`, plus the
  custom reason `PREREQUISITE_FAILED` when a prerequisite flag did not serve the required variation.
- `variant` is the key of the served variation.
- `flag_metadata` has `rule_index` (the 0-based index of the matching rule) and `prerequisite` (the
  key of the prerequisite that failed), when they apply.
- On an error, `value` is your default and `error_code` and `error_message` say why.
- `get_integer_value` returns an `int` and refuses a number flag whose value is not integral with
  `TYPE_MISMATCH`; `get_float_value` returns a `float` for any number flag.

## Context attributes

- Rules match top-level context attributes only. A nested object is not looked into.
- Dates for `before` and `after` are RFC 3339 strings or epoch milliseconds. A `datetime` in the
  context matches nothing; pass `dt.isoformat()` or the epoch milliseconds.
- Regular expressions use Python's `re` with `re.ASCII`, the semantics every MegooFlags SDK
  reproduces, so a pattern evaluates the same everywhere: `(?i)` folds ASCII letters only, `\d`,
  `\w` and `\s` are ASCII, and `$` also matches before a final newline. Patterns outside the portable
  subset (for example lookarounds, backreferences or `\p{…}`) never match; a ruleset with such a
  pattern is loaded and a warning is logged.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdk-python/vX.Y.Z`, and only maintainers push those tags.
`megooflags.__version__` holds the version, and requests send
`User-Agent: megooflags-python/<__version__>`.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/python/`:

```sh
uv sync --frozen
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest          # unit tests and the conformance suite
uv run pytest perf -s  # the p99 guard, on its own
uv build
```

The conformance runner reads `../../spec/conformance` and reports each case as `<file>::<name>`.

The end-to-end test needs a running stack and an API token (see the `sdk-python-e2e` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
uv run pytest e2e -v
```

Without both variables it is skipped.
