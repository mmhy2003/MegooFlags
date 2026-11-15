# MegooFlags Ruby SDK

The MegooFlags server SDK for Ruby is an [OpenFeature](https://openfeature.dev) provider. It
downloads an environment's ruleset and evaluates flags in-process: there is no network call per
evaluation, and p99 is under 1 ms on a 10 000-flag ruleset. It follows changes over SSE with a
polling fallback, keeps serving when the server is unreachable, and sends evaluation counts and
exposures in the background.

Status: 0.1.0, Alpha.

## Install

```sh
gem install megooflags
```

or, with Bundler, add this line to your `Gemfile`:

```ruby
gem "megooflags", "~> 0.1"
```

The gem needs Ruby 3.4 or later. Its only dependency is the OpenFeature SDK
(`openfeature-sdk ~> 0.8`), which it installs; it uses `Net::HTTP`, `JSON` and `Digest` from the
standard library.

## Quick start

```ruby
require "megooflags"

OpenFeature::SDK.configure do |config|
  config.set_provider_and_wait(
    MegooFlags::Provider.new(
      sdk_key: ENV.fetch("MEGOOFLAGS_SDK_KEY"), # a server key, mf_server_…
      base_url: "https://flags.example.com"
    )
  )
end

client = OpenFeature::SDK.build_client
context = OpenFeature::SDK::EvaluationContext.new(targeting_key: "user-123", "plan" => "pro")
enabled = client.fetch_boolean_value(flag_key: "new-checkout", default_value: false,
                                     evaluation_context: context)
puts "new-checkout: #{enabled}"

OpenFeature::SDK.shutdown # closes the stream and flushes the buffered events
```

The targeting key (`user-123` above) is the context's `key` attribute in MegooFlags rules, and it is
what percentage rollouts bucket on by default.

`set_provider_and_wait` raises `OpenFeature::SDK::ProviderInitializationError` when initialisation
fails; `set_provider` returns at once and initialises in the background. Ruby stops the SDK's threads
when the main thread exits, so call `OpenFeature::SDK.shutdown` when your service stops (for example
in an `at_exit` block), so the buffered events are sent.

### Forking servers (Puma, Unicorn, Passenger)

The provider works in servers that fork workers after the application has loaded, such as Puma in
cluster mode (also with `preload_app!`), Unicorn and Passenger. Threads do not survive `fork`, so
the SDK hooks `Process._fork`, which every fork goes through: in each child, every started provider
keeps the ruleset it had and starts its own stream and event sender. A child's event buffers start
empty, so nothing the parent recorded is sent twice. Creating the provider in each worker after the
fork (for example in Puma's `on_worker_boot`) works as well.

## Configuration

Every keyword argument of `MegooFlags::Provider.new(…)`; `provider.config` holds the result as the
frozen `MegooFlags::Config`. Durations are seconds (`Integer` or `Float`); zero, negative or NaN
means the default.

| Option           | Default                       | Notes                                                                                             |
| ---------------- | ----------------------------- | ------------------------------------------------------------------------------------------------- |
| `sdk_key`        | (required)                    | A server key, `mf_server_…`. A client key is refused.                                             |
| `base_url`       | (required unless `offline`)   | The MegooFlags URL, `http` or `https`, e.g. `https://flags.example.com`. Trailing slashes are removed. |
| `bootstrap_file` | `nil`                         | A ruleset JSON file to start from.                                                                |
| `cache_dir`      | `nil`                         | A directory for the last-known ruleset: read at start, written on each change.                   |
| `offline`        | `false`                       | No network at all: serve from `bootstrap_file` or `cache_dir` only. One of them is required.      |
| `init_timeout`   | `5`                           | How long `init` waits for the first ruleset when neither the bootstrap file nor the cache gave one. |
| `disable_stream` | `false`                       | Poll only, never open the SSE stream.                                                             |
| `poll_interval`  | `30`                          | Minimum 15; a smaller value is raised to 15.                                                      |
| `flush_interval` | `30`                          | How often events are sent. Clamped to 10–60.                                                      |
| `send_context`   | `false`                       | Include the evaluation context in exposures.                                                      |
| `logger`         | warnings and errors to stderr | See below. The SDK key is never logged.                                                           |

An invalid config (no key, a client key, a bad `base_url`, `offline` with neither a bootstrap file
nor a cache directory) is logged. `init` then raises with `PROVIDER_FATAL`, and every evaluation
returns the default value with `PROVIDER_FATAL`.

### Logging

`logger` is any object that responds to `debug`, `info`, `warn` and `error` with a message, such as
a `Logger` or `Rails.logger`:

```ruby
MegooFlags::Provider.new(sdk_key: ENV.fetch("MEGOOFLAGS_SDK_KEY"), base_url: "https://flags.example.com",
                         logger: Rails.logger)
```

Without one, the SDK uses `Logger.new($stderr, level: Logger::WARN)`. From Ruby 4.0 `logger` is a
bundled gem that Bundler loads only when your `Gemfile` lists it; when it cannot be loaded, the SDK
writes warnings and errors to stderr with `Kernel#warn` instead, so it needs no extra dependency.

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
  provider emits `PROVIDER_ERROR` with the `PROVIDER_FATAL` error code and keeps serving the last
  ruleset.
- 5xx and network errors back off and retry; 429 waits for `Retry-After`. In both cases the last
  ruleset keeps being served.

## Offline and bootstrap

At start-up the provider looks for a ruleset in this order:

1. `bootstrap_file`;
2. the cache in `cache_dir`;
3. the network.

When the bootstrap file or the cache gives a valid ruleset, `init` returns at once and the network
refreshes it in the background. Otherwise `init` waits up to `init_timeout` for the first ruleset.
After a timeout the provider is in the error state, keeps retrying, and emits `PROVIDER_READY` when a
ruleset arrives. Until then every evaluation returns the default value with `PROVIDER_NOT_READY`.

With `offline: true` the provider makes no network calls and sends no events. It serves the
bootstrap file or the cache, and `init` raises if neither gives a valid ruleset.

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
- `OpenFeature::SDK.shutdown` (or `provider.shutdown`) stops the sync and sends the buffered events
  within 5 s.

## OpenFeature events

| Event                            | When                                                                                                                       |
| -------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `PROVIDER_READY`                 | `init` succeeded, or the first ruleset arrived after `init` timed out, or the stream reconnected after `PROVIDER_STALE`.  |
| `PROVIDER_CONFIGURATION_CHANGED` | A new ruleset was applied. `flags_changed` lists the flags added, removed or with a new version.                          |
| `PROVIDER_STALE`                 | The stream disconnected; the last ruleset keeps being served.                                                             |
| `PROVIDER_ERROR`                 | `init` failed, or the key was refused (401, 403 or `revoked`), with error code `PROVIDER_FATAL`.                          |

The provider emits these itself (`emits_lifecycle_events?` is true). Subscribe with
`client.add_handler(OpenFeature::SDK::ProviderEvent::PROVIDER_CONFIGURATION_CHANGED) { |details| … }`,
or the same method on `OpenFeature::SDK`.

## Results

- `reason` is one of `STATIC`, `DEFAULT`, `TARGETING_MATCH`, `SPLIT`, `DISABLED` or `ERROR`, plus the
  custom reason `PREREQUISITE_FAILED` when a prerequisite flag did not serve the required variation.
- `variant` is the key of the served variation.
- `flag_metadata` has `rule_index` (the 0-based index of the matching rule) and `prerequisite` (the
  key of the prerequisite that failed), when they apply.
- On an error, `value` is your default and `error_code` and `error_message` say why.
- `fetch_integer_value` returns an `Integer` and refuses a number flag whose value is not integral
  with `TYPE_MISMATCH`; `fetch_float_value` returns a `Float` for any number flag, and
  `fetch_number_value` the number as it is in the ruleset. `fetch_object_value` returns a `Hash` or
  an `Array`; any other value is `TYPE_MISMATCH`.

## Context attributes

- Rules match top-level context attributes only. A nested `Hash` is not looked into.
- Dates for `before` and `after` are RFC 3339 strings or epoch milliseconds. A `Time` in the context
  matches nothing; pass `time.iso8601(3)` or the epoch milliseconds.
- Regular expressions behave like Python's `re` with `re.ASCII`, the semantics every MegooFlags SDK
  reproduces, so a pattern evaluates the same everywhere: `(?i)` folds ASCII letters only, `\d`,
  `\w` and `\s` are ASCII, and `$` also matches before a final newline. The SDK translates each
  pattern to a Ruby `Regexp` with those semantics. Patterns outside the portable subset (for example
  lookarounds, backreferences or `\p{…}`) never match; a ruleset with such a pattern is loaded and a
  warning is logged.

## Versioning and license

The SDK is versioned on its own, following the `0.Y.Z` rules in [AGENTS.md](../../AGENTS.md); it
starts at 0.1.0. Releases are tagged `sdk-ruby/vX.Y.Z`, and only maintainers push those tags.
`MegooFlags::VERSION` holds the version, and requests send
`User-Agent: megooflags-ruby/<MegooFlags::VERSION>`.

Licensed under the [Apache License 2.0](LICENSE).

## Running the tests

From `sdks/ruby/`:

```sh
bundle install
bundle exec rubocop
bundle exec rake test   # unit tests and the conformance suite
bundle exec rake p99    # the p99 guard, on its own
gem build megooflags.gemspec
```

The conformance runner reads `../../spec/conformance` and reports each case as `<file>::<name>`.

The end-to-end test needs a running stack and an API token (see the `sdk-ruby-e2e` job in
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)):

```sh
export MEGOOFLAGS_E2E_URL=http://localhost:8080
export MEGOOFLAGS_API_TOKEN="$(bash ../../scripts/sdk_e2e_token.sh "$MEGOOFLAGS_E2E_URL")"
bundle exec rake e2e
```

Without both variables it is skipped.
