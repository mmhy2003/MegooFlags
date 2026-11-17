# MF-86: Multi-node deployment (control plane and worker nodes)

## Goal
An operator can grow from one node to one control plane plus any number of workers, using the same images. Workers serve `/sdk/*` and `/ofrep/*` from a signed snapshot held in memory and on disk, so applications keep getting flags while the control plane or its database is down, including after a worker restart. Admins manage workers on a Nodes page (mockup v1).

Everything ships in one pull request into `develop`. The task is done when the failure-scenario suite that mirrors the PRD 8.5 failure table passes in CI.

## Decisions
- **One design, one pull request (your answer).** *Rejected:* core first with follow-up tasks; backend and UI as two designs.
- **Workers open an SSE stream to the control plane and send heartbeats and events with HTTPS POST (your answer).** *Rejected:* WebSocket; polling only.
- **A revoked worker stops serving (your answer).** It reports "not ready" and answers SDK requests with 503. Losing the control plane is different: the worker keeps serving. *Rejected:* keep serving the last snapshot after a revoke.
- **Nodes page: mockup v1 (your answer),** with the summary cards.
- **PRD Q7 and Q8, taken from the task's defaults:** the control plane also serves SDK traffic, and there is no control-plane failover (HA-10) in v1.
- **My choices, for you to check:**
  - **One application, two back ends.** The SDK and OFREP routers stay one piece of code. They read rulesets, keys, events and rate limits through small interfaces. On `standalone` and `control-plane` these are backed by Postgres and Valkey as today; on `worker` by the snapshot store. So a worker answers exactly like the control plane (same bodies, same ETags). *Rejected:* a separate worker application with copied routes; a worker with its own database replica.
  - **A worker needs no Postgres, no Valkey and no `MEGOOFLAGS_SECRET_KEY`.** Only the role, the control-plane URL and the node token.
  - **A worker runs one process** (the 1 vCPU / 512 MB target). To use more CPU, add workers. `MEGOOFLAGS_API_WORKERS` is ignored on workers, with a log line.
  - **Snapshots are signed with Ed25519.** The key is derived from `MEGOOFLAGS_SECRET_KEY` (HKDF), so every control-plane replica signs identically and nothing new is stored. The worker gets the public key over the authenticated connection and keeps it next to its snapshots.
  - **One version counter for the whole cluster.** Each environment's snapshot gets the next number when its content changes. A worker's "snapshot version" is the highest number it has applied, which gives the Nodes page one number to compare.
  - **`standalone` and `control-plane` differ only in the cluster features.** On `standalone`, the node endpoints answer 409 `not_control_plane` and the Nodes item is hidden.
  - **Nodes are managed with the existing `org.manage` permission.** No new permission and no role migration.
  - **Client-key rate limits on a worker are counted per worker,** in memory. With N workers a client can reach up to N times the limit. The docs say so.
  - **Workers require an `https://` control-plane URL.** `MEGOOFLAGS_CONTROL_PLANE_INSECURE=true` allows plain HTTP (a private network, tests). `MEGOOFLAGS_CONTROL_PLANE_CA_FILE` trusts a private CA.
  - **A worker serves its last snapshot for as long as the control plane is away.** There is no age limit; the snapshot age metric is what operators alert on.
  - **Load balancer samples:** Nginx, HAProxy, Caddy and Traefik, plus notes for cloud load balancers.

## Architecture

### 1. Roles
`MEGOOFLAGS_ROLE` is `standalone` (default), `control-plane` or `worker`. `main.create_app` builds the worker application without a database engine, Valkey or any `/api/*` router: only health, metrics, `/sdk/*` and `/ofrep/*`. `serve.py` checks the worker's three settings instead of the secret key and Valkey, and starts one process. `/healthz` also returns the role.

### 2. Shared delivery interfaces (refactor, no behaviour change)
New `services/delivery.py` defines what the SDK routes need: a ruleset source (`get_entry(environment)`), a key source (`resolve`, `resolve_hash`), an event sink (counts and exposures) and a client limiter. `routers/sdk.py`, `routers/ofrep.py`, `sdk_auth.py` and `services/streams.py` use only these. The existing `RulesetCache`, `KeyResolver`, `EvaluationCounter`, `record_exposures` and `RateLimiter` sit behind adapters. The existing SDK, OFREP and stream tests must pass unchanged.

### 3. Control plane, new package `services/cluster/`
| Module | Purpose |
|---|---|
| `nodes.py` | Node registry: create, list with computed status, rotate, revoke, drain, resume, remove. Tokens are `mf_node_` plus 32 random characters; only the SHA-256 is stored and the token is shown once. |
| `signing.py` | Derives the Ed25519 key, signs, exposes the public key. |
| `snapshots.py` | Builds an environment's snapshot (header plus the exact ruleset bytes SDKs download), computes its digest and assigns the version. The version row is locked while the snapshot is built, so a higher version never carries older content. |
| `compat.py` | The version rule (below), one pure function. |
| `node_hub.py` | The open worker streams of this replica. On a change message, and on the existing 30 s re-check, it rebuilds the affected snapshots and offers them to every worker. Each worker queue keeps only the newest snapshot per environment. |
| `ingest.py` | Stores event batches from workers, once per batch id. |

- `routers/nodes.py` holds the management endpoints; `routers/cluster.py` the worker-facing ones. The worker-facing routes authenticate with the node token and are listed in `PUBLIC_ROUTES`.
- `ChangeBus` gets a `node` message, so a drain, revoke or rotate reaches the replica that holds the worker's stream. Heartbeat answers carry the same state, as the safety net for a lost message.
- Creating, rotating and revoking an SDK key now also publishes the project change, so the key reaches the workers.
- A scheduler job deletes stored batch ids after 7 days.
- Every node action writes an audit entry (`node.create`, `node.rotate`, `node.revoke`, `node.drain`, `node.resume`, `node.delete`), never with the token.

### 4. Worker, new package `services/worker/`
| Module | Purpose |
|---|---|
| `store.py` | Snapshots on disk under `MEGOOFLAGS_DATA_DIR`, written atomically and verified against the signature on every load. In memory: the key index of all environments and the parsed rulesets of the most recently used environments; the others are read from disk on first use. |
| `client.py` | The sync loop: open the stream with the versions it holds, apply `put` and `remove`, mark "synced", reconnect with backoff (0.5 s to 30 s), send a heartbeat every 10 s. |
| `sources.py` | The delivery interfaces backed by the store, plus the in-memory client limiter. |
| `buffer.py` | Counts are summed in memory and saved to disk every 10 s. Exposures go to a bounded memory queue that spills into files on disk. A forwarder sends the oldest batches first and deletes them when the control plane accepts them. |

A snapshot applied on a worker wakes its own `StreamHub`, so SDK streams on that worker get the change at once.

### 5. Web
- New org-level page `app/(app)/nodes/page.tsx` with `components/nodes/`: `node-list` (`DataList`, cards below 768 px, summary cards), `add-node-dialog` (name, then the token once with the `.env` lines and `CopyButton`), and confirm dialogs for drain, resume, rotate (with overlap), revoke and remove.
- `lib/nodes/`: pure helpers for status, snapshot text and relative times, tested with Vitest. Statuses use `StatusPill` (icon, label and colour).
- `lib/nav.ts`: the Nodes item sits above Settings and shows only when the instance role is `control-plane` and the user has `org.manage`. `GET /api/auth/me` gains `role`.
- The list refreshes every 10 s while the page is visible.

### 6. Deployment, CI and docs
- `deploy/docker-compose.yml` passes `MEGOOFLAGS_ROLE` (default `standalone`). `deploy/docker-compose.control-plane.yml` is the same file with the default `control-plane`; a test fails if the two drift apart.
- `deploy/docker-compose.worker.yml`: Caddy and `api` with a `worker_data` volume, plus `deploy/Caddyfile.worker` (only `/sdk/*`, `/ofrep/*`, `/healthz`, `/readyz`; both TLS modes) and `deploy/.env.worker.example`.
- The API image creates `/var/lib/megooflags` for the non-root user.
- `release.yml` attaches the four new files. `ci.yml` gets a `cluster` job that runs the failure suite.
- Docs: `docs/multi-node.md` (roles, settings, 1 control plane plus 3 workers step by step, failure behaviour, rolling upgrades, metrics and an alert example, workers as edge relay) and `docs/multi-node-migration.md` (single node to multi-node). `docs/examples/multi-node/` holds the load balancer samples with health check and SSE settings. README, AGENTS.md (layout, checks, release files), `.env.example` and CHANGELOG are updated.
- Migration guide in short: upgrade the single node; set `MEGOOFLAGS_ROLE=control-plane` (the API restarts; SDKs evaluate from their cache meanwhile); add workers and wait until they are Healthy; point the SDK hostname at the load balancer. Rolling back is the reverse.

## Data and interfaces

**Tables (migration `0015_cluster`, with a working downgrade)**
| Table | Columns |
|---|---|
| `node` | `id`, `name` (unique), `created_at`, `revoked_at`, `drain_requested_at`, `last_heartbeat_at`, `synced_at`, `version`, `snapshot_version`, `sdk_streams`, `buffered_events`, `address`, `refused_version`, `refused_at` |
| `node_token` | `id`, `node_id` (cascade), `prefix`, `secret_hash` (unique), `created_at`, `expires_at`, `revoked_at` |
| `environment_snapshot` | `environment_id` (primary key, cascade), `version`, `digest`, `published_at`; versions come from the sequence `snapshot_version_seq` |
| `node_event_batch` | `batch_id` (primary key), `node_id`, `received_at` |

**Management endpoints (`org.manage`; 409 `not_control_plane` on other roles)**
| Endpoint | Result |
|---|---|
| `GET /api/nodes` | The control plane's version and current snapshot version, and each node with its status |
| `POST /api/nodes` `{name}` | 201 with the node and its `token` (only here) |
| `POST /api/nodes/{id}/rotate` `{overlap_seconds}` | 201 with a new `token`; the old one works until the overlap ends |
| `POST /api/nodes/{id}/revoke`, `/drain`, `/resume` | The node |
| `DELETE /api/nodes/{id}` | 204; only for a revoked node |

**Node status (computed when read)**
| Status | Rule |
|---|---|
| Pending | Never connected |
| Healthy | Heartbeat in the last 30 s and its snapshot version is current |
| Stale | No heartbeat for more than 30 s; the page shows the age of its last synced snapshot. A fresh heartbeat with an older snapshot shows Healthy with "syncing" |
| Draining | Drain requested |
| Incompatible | Its last connection was refused by the version rule |
| Revoked | Every token revoked |

**Worker-facing endpoints (`Authorization: Bearer <node token>`, `X-MegooFlags-Version`)**
| Endpoint | Behaviour |
|---|---|
| `POST /api/cluster/v1/stream` `{versions: {environment_id: version}}` | SSE: `hello` (control-plane version, public key, whether contexts are stored, desired state), then `put` for every environment that differs and `remove` for those gone, then `synced`. After that `put`, `remove`, `state` (draining or active), `revoked`, and a `: ping` every 15 s |
| `POST /api/cluster/v1/heartbeat` | Version, snapshot version and age, SDK streams, buffered events, ready. Answers with the desired state |
| `POST /api/cluster/v1/events` `{batch_id, exposures, counts}` | 202; a batch id seen before is accepted and ignored |

Errors: 401 unknown token, 403 `node_revoked`, 409 `incompatible_version` with both versions in the message, 409 `not_control_plane`.

**Snapshot.** A header (`format`, environment id, project id and key, `version`, `published_at`, the environment's SDK keys as id, kind, hash, `revoked_at`, `expires_at`) followed by the ruleset bytes. The signature covers both. The worker rejects a snapshot whose signature fails or whose `format` it does not know, and keeps the one it has.

**Version rule (HA-8).** Compared on `X.Y`. Different major: refused. Major 0: the worker's `0.Y` must equal the control plane's. From 1.0: the worker may be the same minor or one minor older, never newer. The upgrade order is control plane first, then workers one at a time (drain, upgrade, resume).

**Settings**
| Setting | Role | Default |
|---|---|---|
| `MEGOOFLAGS_ROLE` | all | `standalone` |
| `MEGOOFLAGS_CONTROL_PLANE_URL`, `MEGOOFLAGS_NODE_TOKEN` | worker | required |
| `MEGOOFLAGS_CONTROL_PLANE_INSECURE`, `MEGOOFLAGS_CONTROL_PLANE_CA_FILE` | worker | `false`, empty |
| `MEGOOFLAGS_DATA_DIR` | worker | `/var/lib/megooflags` |
| `MEGOOFLAGS_WORKER_BUFFER_MAX_MB` | worker | 64 |
| `MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS` | worker | 30 |

**Readiness (HA-7).** A worker's `/readyz` answers 200 only when it has completed a full sync at least once (remembered on disk), its snapshots verify, and it is neither draining nor revoked. Otherwise 503 with `not_ready`, `draining` or `revoked`.

**Metrics.** Worker: `megooflags_worker_snapshot_age_seconds` (time since it last heard from the control plane while in sync; stays under 15 s when connected), `megooflags_worker_snapshot_version`, `megooflags_worker_control_plane_connected`, `megooflags_worker_buffered_events`, `megooflags_worker_events_dropped_total`. Control plane: `megooflags_node_streams`.

## Error handling and edge cases
- **Control plane unreachable:** the worker keeps serving, buffers events and retries with backoff. The age metric grows. On reconnect it sends its versions and receives only what changed.
- **Worker restart without the control plane:** it loads and verifies the snapshots from disk and is ready at once. A corrupt or tampered file is discarded and logged; the worker is not ready until the next sync replaces it.
- **First start without the control plane:** not ready; SDK requests get 503.
- **Revoke:** the stream sends `revoked` and closes; the worker goes not ready and answers 503. A restart keeps it that way until it has a valid token.
- **Rotate:** both tokens work during the overlap. An expired token is treated like a revoked one.
- **Drain:** `/readyz` turns 503. After the grace time the worker closes its SDK streams so SDKs reconnect through the load balancer. Requests that still arrive are answered. Resume makes it ready again.
- **Incompatible version:** refused with a clear message on both sides. The worker keeps serving the snapshot it already has and retries every 60 s; the page shows Incompatible.
- **Changed `MEGOOFLAGS_SECRET_KEY`:** the public key in `hello` differs, so the worker fetches every snapshot again.
- **Deleted environment or project:** `remove`; the worker deletes the file, closes that environment's SDK streams with `revoked`, and its keys answer 401.
- **Revoked or expired SDK key:** carried in the snapshot, so the worker refuses it without the control plane. A key created during an outage works on workers only after the link is back.
- **Buffer full:** the oldest exposures are dropped and counted in the metric; counts are kept, because they only grow with the number of flags. Buffered data older than 7 days is dropped. Contexts are removed before buffering unless the control plane stores them.
- **Ambiguous forward (timeout after sending):** the batch is sent again with the same id and stored once.
- **Database down:** workers are not affected. The control plane's `/readyz` is 503 and worker streams stay open; snapshots resume when it is back.
- **Several control-plane replicas:** any replica can hold a worker's stream. Versions come from the database, so all replicas agree.
- **A worker joining a `standalone` node:** 409 `not_control_plane`, with the setting to change in the message.

## Testing
Every step is test-first; ruff, format, strict mypy, pytest, migrations, OpenAPI and client drift, and the web checks stay clean.
- **Unit:** the version rule as a table; signing and tamper rejection; snapshot digest and versions (unchanged content keeps its version; two concurrent builds never go backwards); node tokens, rotation overlap and revoke; node endpoints with RBAC, audit and the `standalone` 409; the store (atomic write, reload, corrupt file, remove); the buffer (bounds, spill, drop oldest, restart, duplicate batch); the in-memory limiter; worker readiness states; `serve` on a worker.
- **In-process cluster tests** (`api/tests/cluster/`): a control-plane app and worker apps joined through `httpx.ASGITransport` with a link that can be cut, each worker with its own temporary data directory. They cover: first sync and readiness; a change reaching a worker's SDK stream in under 5 s; link down then serving; restart from disk with the link down; resync on reconnect; buffered events stored once; revoke, rotate, drain; incompatible versions; key and environment changes; and that a worker's ruleset, evaluate and OFREP answers equal the control plane's, ETags included.
- **Failure-scenario suite (the "done when")**: `scripts/cluster_test.sh` starts 1 control plane, 3 workers and a load balancer from the sample config, over TLS, and runs `scripts/cluster_failures.py`:

  | PRD row | Checks |
  |---|---|
  | One worker down | Requests through the load balancer keep succeeding and a stream reconnects; `GET /api/nodes` shows the worker Stale |
  | Control plane lost | Ruleset, evaluate, OFREP and streams keep working on all workers; a worker restarted meanwhile becomes ready from disk; events are accepted |
  | Control plane restored | Workers are Healthy and current again; a kill switch reaches every worker's stream with p95 under 5 s over 20 flips; the events buffered during the outage appear in the evaluation counts exactly once |
  | Database down | Workers keep serving; the control plane's `/readyz` is 503; after the database returns, a change propagates |

  It also checks that a worker without a snapshot is not ready, and that an idle worker stays under 256 MiB.
- **Web:** Vitest for `lib/nodes` and the nav rule. Playwright for add (token shown once), drain and resume, rotate, revoke and remove, against an API started with `MEGOOFLAGS_ROLE=control-plane`. The page joins the visual and axe matrix (six widths, both themes).
- **Existing suites:** the smoke test and the multi-replica propagation test stay green, which shows `standalone` is unchanged.

## Out of scope
- Control-plane redundancy and failover (HA-10).
- A switch to stop the control plane from serving SDK traffic.
- Several processes per worker, and automatic scaling of workers.
- Mutual TLS, a UI to rotate the signing key, and incremental (patch) snapshots.
- Workers that hold only some projects or environments.
- Node management in the CLI and the Terraform provider.
- A Helm chart.
