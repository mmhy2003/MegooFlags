# MF-78: SDK delivery API (SDK keys, ruleset download, client evaluation, SSE, OFREP, exposure events)

## Goal
An SDK can authenticate with an environment-scoped SDK key and then:
- **Server keys** download the full ruleset and stream changes, so the SDK can evaluate locally.
- **Client keys** send a context and receive evaluated values only, so rules are never exposed.

Any OpenFeature OFREP provider can evaluate against `/ofrep/v1`. SDKs send exposure events and evaluation counts in batches. A flag change, including the kill switch, reaches every open stream on every API replica in under 5 s at p95.

## Decisions
- **SDK keys are built here (your answer).** The governance task (G-5) has not shipped, so MF-78 adds a minimal key model. It has per-environment server and client keys, hashed and revocable, with management endpoints to create, list and revoke them. Governance later adds RBAC, rotation (G-6) and a UI. Rejected: pausing MF-78, or a stub key resolver.
- **One design, built in ordered steps (your answer):** keys, then download and evaluation, then streaming and fan-out, then OFREP, then events. One branch and one PR.
- **Fan-out uses Valkey pub/sub (your answer, PRD Q4).** Rejected: Postgres LISTEN/NOTIFY and fingerprint polling.
- **Valkey is required (your answer).** The API refuses to start if it cannot reach `MEGOOFLAGS_VALKEY_URL`, and `/readyz` checks Valkey as well as Postgres. The compose bundle, CI and local development all run Valkey. Client-key rate limits are shared across replicas through Valkey.
- **Exposure events go to a Postgres table with a retention window (your answer):**
  - Each row stores the flag, environment, variant, reason and time.
  - The context is stored only when `MEGOOFLAGS_PERSIST_CONTEXT=true`. The default is off.
  - Rows older than `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS` (default 30) are deleted.
  - Evaluation counts are aggregated per environment, flag, variant and hour, and never store a context.
- **Lost-message safety net.** A Valkey publish happens after the commit and can be lost if a replica dies in between. Every replica therefore re-checks the fingerprint of each environment that has open streams every 30 s (`MEGOOFLAGS_STREAM_RECHECK_SECONDS`), and immediately after a Valkey reconnect. A lost message delays an update; it never loses it.
- **Fast hot path.**
  - Today `RulesetCache` runs a fingerprint query on every lookup. At 2k RPS with 10k flags that query alone would break the 50 ms p99 budget.
  - SDK endpoints therefore trust a cache entry for up to 1 s after its last fingerprint check (`max_age`), and a Valkey change message invalidates it at once.
  - The management `evaluate` endpoint keeps `max_age=0`, so a write is always visible to the next read.
  - The cache also keeps the ruleset serialized to bytes and its ETag, so a download does not re-encode 10k flags.
- **Key transport.**
  - Keys are sent as `Authorization: Bearer <key>`. OFREP also accepts `X-API-Key`.
  - Browser `EventSource` cannot set headers, so **client keys only** may also be passed as `?key=` on the stream endpoint.
  - A server key in a query string is refused with 401, and the access log redacts `key=`.
- **Client keys are read-only and rate-limited.**
  - They can evaluate, stream "changed" notices, call OFREP and send events. They can never download the ruleset.
  - The limit is a Valkey fixed window per key and client IP: `MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE`, default 600. Over the limit, the API returns 429 with `Retry-After`.
  - Server keys are not rate-limited.
- **Client responses carry no `metadata`,** so nothing about rule structure leaks.
- **CORS:** `Access-Control-Allow-Origin: *` without credentials on client-capable endpoints (`/sdk/v1/evaluate`, `/sdk/v1/stream`, `/sdk/v1/events`, `/ofrep/*`). `/sdk/v1/ruleset` has no CORS.

## Architecture
New and changed modules in `api/src/megooflags_api/`:

| Module | Purpose |
|---|---|
| `valkey.py` | Creates the client from settings, does a PING at startup (failure aborts startup), and gives a readiness check. Uses the `valkey` package's asyncio client. |
| `models/sdk.py` | `SdkKey`, `ExposureEvent`, `EvaluationCount`. Alembic revision `0003`. |
| `services/sdk_keys.py` | Generates keys (`mf_server_` / `mf_client_` plus 32 random base62 characters) and stores a SHA-256 hash and a display prefix. Resolves keys through a per-replica cache with a 10 s TTL, and revocation also invalidates over Valkey. |
| `sdk_auth.py` | FastAPI dependencies `require_server_key` and `require_any_key`. Each returns the resolved key, environment and kind, and applies the client rate limit. |
| `services/ruleset_cache.py` | Adds `max_age`, `invalidate(project_id)` and serialized bytes with an ETag. The fingerprint stays the source of truth. |
| `services/changes.py` | `ChangeBus`. `publish(project_id)` sends to channel `megooflags:changes` after a commit. One subscriber task per replica invalidates the cache and wakes the `StreamHub`. A Valkey publish failure is logged and does not fail the write, because the 30 s re-check covers it. |
| `services/streams.py` | `StreamHub`. Holds the per-environment subscriber queues. On a change, or on the periodic re-check, it reloads the ruleset through the cache and, if the ETag changed, pushes to every subscriber of that environment. Bursts are merged within 100 ms. |
| `services/events.py` | Bulk-inserts exposures, upserts counts, aggregates remote-evaluation counts in memory (flushed every 10 s) and runs retention cleanup (hourly, one replica at a time via `pg_try_advisory_lock`). |
| `routers/sdk_keys.py` | Management endpoints for keys. |
| `routers/sdk.py` | `/sdk/v1/*`. |
| `routers/ofrep.py` | `/ofrep/v1/*`. |
| Management write routers | `projects`, `environments`, `flags`, `flag_environments`, `segments` call `changes.publish(project_id)` right after `session.commit()`. A test checks that every write route publishes. |

`main.py` connects Valkey in the lifespan, starts the subscriber, the re-check loop and the event flusher, and stops them on shutdown.

Deployment and CI:
- `deploy/docker-compose.yml` adds `valkey` (`valkey/valkey:8`, healthcheck `valkey-cli ping`, no published port). `api` depends on it being healthy and gets `MEGOOFLAGS_VALKEY_URL=redis://valkey:6379/0`.
- `deploy/.env.example` documents the new settings.
- `ci.yml` adds a Valkey service to `api-test`.
- AGENTS.md §9 adds the `docker run valkey` line.

## Data and interfaces
**Tables (migration `0003`, with a working downgrade):**

| Table | Columns | Notes |
|---|---|---|
| `sdk_key` | `id` uuid, `environment_id` FK (cascade), `kind` (`server` \| `client`), `name`, `prefix` (first 14 chars), `secret_hash`, `created_at`, `revoked_at` | `secret_hash` is unique. |
| `exposure_event` | `id` bigserial, `environment_id` FK (cascade), `flag_key`, `variant`, `reason`, `occurred_at`, `received_at`, `context` jsonb | `context` is null unless persisting is enabled. Index on `received_at`. |
| `evaluation_count` | `environment_id`, `flag_key`, `variant`, `hour`, `count` | Primary key is the first four columns. Upserts add to `count`. |

`flag_key` is stored as text, not a foreign key, so deleting a flag keeps its history.

**Management** (snake_case, `{env}` path parameter, same conventions as today):
- `POST /api/projects/{project}/environments/{env}/sdk-keys` with `{name, kind}` returns 201 with `{id, name, kind, prefix, created_at, key}`. The secret is shown only once.
- `GET .../sdk-keys` lists keys without secrets, including revoked ones with `revoked_at`.
- `DELETE .../sdk-keys/{id}` revokes the key (204). Its open streams close within the cache TTL, or at once via Valkey.

**SDK API:**

| Endpoint | Keys | Behaviour |
|---|---|---|
| `GET /sdk/v1/ruleset` | server | The ruleset JSON (`spec/ruleset.schema.json`) with `ETag`. `If-None-Match` returns 304. This is the polling fallback for server SDKs. |
| `POST /sdk/v1/evaluate` `{context, flags?}` | server, client | `{results:[{key, value, variant, reason, error_code, error_message}]}`. `metadata` is added only for server keys. `ETag` is a hash of the ruleset ETag, the context and the flags, and `If-None-Match` returns 304. This is the polling fallback for client SDKs. |
| `GET /sdk/v1/stream` | server; client also via `?key=` | SSE (details below). |
| `POST /sdk/v1/events` | server, client | 202. Body `{exposures:[{flag_key, variant, reason, timestamp, context?}], counts:[{flag_key, variant, hour, count}]}`. At most 1000 items per list and a 1 MB body. `context` is dropped unless persisting is enabled. |

**SSE stream:**
- The response sends `retry: 3000` and a `: ping` comment every 15 s (`MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`), with `Cache-Control: no-cache` and `X-Accel-Buffering: no`.
- **Server keys** get `event: put`, whose data is the full ruleset and whose `id` is the ETag. It is sent on connect and after every change.
- **Client keys** get `event: changed` with `{"etag": ...}`. The SDK then calls `POST /sdk/v1/evaluate` again.
- If `Last-Event-ID` equals the current ETag, the first `put` or `changed` message is skipped.
- When the key is revoked or the environment is deleted, the stream sends `event: revoked` and closes.

**OFREP** (camelCase, as the protocol requires; server or client key):
- `POST /ofrep/v1/evaluate/flags/{key}` `{context}` returns 200 `{key, value, reason, variant, metadata}`. `metadata` is `{}` for client keys.
  - `FLAG_NOT_FOUND` returns 404 `{key, errorCode, errorDetails}`.
  - Evaluation errors (`PARSE_ERROR`, `TYPE_MISMATCH`, `INVALID_CONTEXT`, `GENERAL`) return 400 with the same body.
- `POST /ofrep/v1/evaluate/flags` `{context}` returns 200 `{flags:[…]}`. Each entry is a success or an error item. It has an `ETag`, and `If-None-Match` returns 304.
- Errors: 401 for a missing or unknown key, 403 for a revoked key, 429 with `Retry-After` from the rate limit.
- `context.targetingKey` maps to `key`, as `spec/evaluation.md` already says. `PREREQUISITE_FAILED` passes through as the reason.

**Settings:**
- `MEGOOFLAGS_VALKEY_URL` (required)
- `MEGOOFLAGS_PERSIST_CONTEXT` (false)
- `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS` (30)
- `MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE` (600)
- `MEGOOFLAGS_SSE_HEARTBEAT_SECONDS` (15)
- `MEGOOFLAGS_STREAM_RECHECK_SECONDS` (30)

**Spec:** a new `spec/delivery.md` documents the key header, endpoints, ETag and polling rules, SSE events and the reconnect behaviour that SDK authors need. It recommends 30 s polling when SSE is unavailable.

## Error handling and edge cases
- Valkey down at startup: the API exits with a clear log line. Valkey down at runtime: `/readyz` returns 503. Serving continues from the cache, rate limiting fails open (logged and counted in a metric), and streams rely on the 30 s re-check until the subscriber reconnects, then re-check at once.
- Unknown key: 401. Revoked key: 403. A client key on `/sdk/v1/ruleset`: 403. A server key in `?key=`: 401.
- An environment or project deleted while streams are open: `revoked`, then close. Requests with keys for it fail with 401, because the keys cascade away.
- Oversized or invalid event batches: 413 or 422, and nothing is inserted. Unknown flag keys in events are accepted and stored as given. A future `timestamp` is clamped to the receive time.
- Slow SSE consumers: each subscriber queue holds one item, and a newer ruleset replaces an older pending one, so memory stays bounded.
- Archived flags are absent from the ruleset, as today. Evaluating one returns `FLAG_NOT_FOUND`.

## Testing
Every step is test-first. ruff, format, strict mypy, pytest, migrations, openapi and `schema.d.ts` regeneration stay clean.
- **Keys:** create, list and revoke. The hash is stored and the secret shown once. Scope is enforced: a key for env A cannot read env B. Revocation takes effect. Migration upgrade, downgrade and upgrade.
- **SDK endpoints:** the downloaded ruleset matches `build_ruleset` and the schema. ETag and 304 work for ruleset and evaluate. Client keys get no `metadata` and a 403 on the ruleset. CORS headers are present where specified. The rate limit returns 429 with `Retry-After`, shared across two app instances.
- **Cache:** with `max_age`, a cache hit issues 0 statements (statement-count test). `invalidate` forces a fresh check. The management `evaluate` endpoint still sees a write immediately.
- **Streams and fan-out:**
  - An in-process test with two app instances sharing Postgres and Valkey: the kill switch flipped through instance A reaches a stream on instance B in under 5 s.
  - A dropped Valkey message is caught by the re-check, with the interval shortened in the test.
  - Also tested: heartbeat, `Last-Event-ID`, and `revoked`.
  - A test checks that every management write route publishes.
- **Propagation test with several replicas:** `scripts/propagation_test.py` runs in the compose smoke job (`--scale api=3`). It opens streams to every replica directly, flips the kill switch 20 times, and asserts p95 under 5 s.
- **OFREP:** single and bulk evaluation, the error-code to HTTP status mapping, `X-API-Key` and Bearer auth, ETag and 304, and responses checked against the OFREP OpenAPI shapes (camelCase).
- **Events:** bulk insert, count upsert, context dropped by default and kept when enabled, size limits, retention deletes old rows, and remote evaluations are counted after a flush.
- **Performance:** `scripts/bench_sdk_evaluate.py` is a manual benchmark against a running stack, with results recorded in the PR. CI does not gate on latency, because shared runners are too noisy.

## Build order
1. Valkey: dependency, settings, startup check, readiness, compose, CI, AGENTS.md.
2. SDK keys: model, migration, management endpoints, auth dependencies.
3. Cache upgrades, `/sdk/v1/ruleset`, `/sdk/v1/evaluate`, CORS, rate limit.
4. `ChangeBus`, `StreamHub`, `/sdk/v1/stream`, publishing from write routes, propagation tests.
5. OFREP.
6. Events, counts and retention.
7. `spec/delivery.md`, CHANGELOG (Added, plus the Valkey requirement as an upgrade step), regenerated OpenAPI and TypeScript client.

## Out of scope
- Multi-node control plane and workers, snapshots, node tokens, worker buffering (HA-*).
- RBAC on key management, key rotation without downtime (G-6), and the key management UI.
- Stale-flag detection and lifecycle states (F-7) beyond storing counts. Exposure export and integrations (D-7).
- Per-flag "available to client keys" switches. Incremental (patch) stream messages, because full `put` messages are enough for now.
- SDKs themselves.
