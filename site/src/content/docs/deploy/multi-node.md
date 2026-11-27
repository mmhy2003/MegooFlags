---
title: Multi-node deployment
description: Run one control plane and any number of workers that serve flags from signed snapshots.
---

A single node (`docker-compose.yml`) runs everything on one host. When you need more SDK capacity,
or want flags to keep being served while the management side is down, run one **control plane**
plus any number of **workers**. Every node uses the same images; `MEGOOFLAGS_ROLE` picks what a
node does.

To turn an existing single node into a control plane without downtime, follow
[Migrating from a single node](/MegooFlags/deploy/multi-node-migration/).

## Roles

| Role                   | Compose file                       | Runs                                       | Serves                                                                                                |
| ---------------------- | ---------------------------------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `standalone` (default) | `docker-compose.yml`               | Caddy, web, api, migrate, Postgres, Valkey | Everything; no Nodes page                                                                             |
| `control-plane`        | `docker-compose.control-plane.yml` | The same services as `standalone`          | Everything, including SDK traffic, plus the Nodes page and the worker endpoints (`/api/cluster/v1/*`) |
| `worker`               | `docker-compose.worker.yml`        | Caddy and api only                         | `/sdk/*`, `/ofrep/*`, `/healthz` and `/readyz`                                                        |

- The **control plane** owns the database, the web UI and the management API. For every
  environment it publishes a versioned snapshot of the ruleset and the SDK keys, signed with
  Ed25519, and streams it to every connected worker. It also stores the events workers forward.
  `docker-compose.control-plane.yml` is `docker-compose.yml` with `MEGOOFLAGS_ROLE` defaulting to
  `control-plane`; nothing else differs.
- A **worker** has no database, no Valkey, no web UI and no `/api/*`. It needs no
  `MEGOOFLAGS_SECRET_KEY`. It keeps the snapshots in memory and in its `worker_data` volume, and
  answers SDK and OFREP requests from them exactly like the control plane would (same bodies, same
  ETags). Any worker can serve any request, so workers sit behind a load balancer or DNS
  round-robin without sticky sessions.

There is one control plane. Control-plane failover is not part of this release: while the control
plane is down, workers keep serving, but nobody can change flags.

On Kubernetes, the Helm chart runs the same roles, one per release: see
[Kubernetes (Helm)](/MegooFlags/deploy/kubernetes/).

## Settings

The multi-node settings, in `.env` next to the compose file:

| Setting                                 | Role   | Default               | Meaning                                                                                  |
| --------------------------------------- | ------ | --------------------- | ---------------------------------------------------------------------------------------- |
| `MEGOOFLAGS_ROLE`                       | all    | `standalone`          | `standalone`, `control-plane` or `worker`. Each compose file sets its own default.       |
| `MEGOOFLAGS_CONTROL_PLANE_URL`          | worker | required              | The control plane's external URL, e.g. `https://flags.example.com`.                      |
| `MEGOOFLAGS_NODE_TOKEN`                 | worker | required              | The node token from the Nodes page; it starts with `mf_node_`.                           |
| `MEGOOFLAGS_CONTROL_PLANE_INSECURE`     | worker | `false`               | Allows an `http://` control-plane URL (a private network, tests).                        |
| `MEGOOFLAGS_CONTROL_PLANE_CA_FILE`      | worker | empty                 | A PEM file with a private CA to trust for the control plane's certificate.               |
| `MEGOOFLAGS_DATA_DIR`                   | worker | `/var/lib/megooflags` | Where snapshots and the event buffer are kept; the `worker_data` volume is mounted here. |
| `MEGOOFLAGS_WORKER_BUFFER_MAX_MB`       | worker | `64`                  | Disk space for events buffered while the control plane is unreachable.                   |
| `MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS` | worker | `30`                  | How long a draining worker keeps its SDK streams open before it closes them.             |

A worker also reads `MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE`, `MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`,
`MEGOOFLAGS_LOG_LEVEL`, the `OTEL_*` settings, and Caddy's `MEGOOFLAGS_SITE_ADDRESS`,
`MEGOOFLAGS_HTTP_PORT` and `MEGOOFLAGS_TRUSTED_PROXIES`, as described in `.env.worker.example`.

A worker refuses to start, with a message naming the setting, when the URL or token is missing, the
token is not a node token, the URL is `http://` without `MEGOOFLAGS_CONTROL_PLANE_INSECURE=true`, or
the CA file does not exist.

## One control plane and three workers

This example uses `flags.example.com` for the control plane (web UI and management API) and
`sdk.example.com` for the load balancer in front of three workers.

### 1. Start the control plane

On the control-plane host, download `docker-compose.control-plane.yml`, `Caddyfile` and
`.env.example` from the [release](https://github.com/mmhy2003/MegooFlags/releases/latest), then:

```bash
cp .env.example .env
# Edit .env: MEGOOFLAGS_SITE_ADDRESS=flags.example.com, MEGOOFLAGS_SECRET_KEY, POSTGRES_PASSWORD,
# and pin MEGOOFLAGS_VERSION to an exact release, e.g. 1.0.0.
docker compose -f docker-compose.control-plane.yml up -d
curl -fsS https://flags.example.com/healthz    # {"status":"ok","version":"1.0.0","role":"control-plane"}
```

Open `https://flags.example.com`, finish the first-run setup, and create your projects as usual.

### 2. Add the workers on the Nodes page

**Nodes** appears in the sidebar, above Settings, on a control plane for users with the
`org.manage` permission. For each worker:

1. Click **Add worker** and give it a unique name, e.g. `worker-1`.
2. Copy the token, or **Copy .env lines**, which gives:

   ```env
   MEGOOFLAGS_ROLE=worker
   MEGOOFLAGS_CONTROL_PLANE_URL=https://flags.example.com
   MEGOOFLAGS_NODE_TOKEN=mf_node_...
   ```

   The token is shown only once. If you lose it, rotate the node.

The new node shows as **Pending** until the worker connects.

### 3. Start each worker

Each worker is a host (1 vCPU / 512 MB is enough) with Docker. Download
`docker-compose.worker.yml`, `Caddyfile.worker` and `.env.worker.example` from the same release
into one directory, then:

```bash
cp .env.worker.example .env
# Edit .env: set MEGOOFLAGS_CONTROL_PLANE_URL and MEGOOFLAGS_NODE_TOKEN from the Nodes page, and
# MEGOOFLAGS_VERSION to the control plane's exact release.
mkdir -p certs                     # only needed for MEGOOFLAGS_CONTROL_PLANE_CA_FILE
docker compose -f docker-compose.worker.yml up -d
curl -fsS http://localhost/readyz  # {"status":"ready"} once the first sync is done
```

With `MEGOOFLAGS_SITE_ADDRESS` empty, the worker's Caddy listens on port 80 only and expects the
load balancer to terminate TLS. Set it to a domain (e.g. `worker-1.example.com`) to give the
worker its own certificate instead.

On the Nodes page the worker turns **Healthy** once it has heartbeated and holds the current
snapshot version. Repeat for `worker-2` and `worker-3`.

### 4. Put a load balancer in front

[`docs/examples/multi-node/`](https://github.com/mmhy2003/MegooFlags/tree/main/docs/examples/multi-node) has samples for nginx, HAProxy, Caddy and
Traefik, with notes for AWS, Google Cloud and Azure load balancers. Each one sends `/sdk/` and
`/ofrep/` to the three workers, health-checks `/readyz`, turns response buffering off for SDK
streams and keeps idle connections open longer than 60 s. Replace `worker-1`, `worker-2` and
`worker-3` with your workers' addresses.

Check the load balancer before you send traffic to it:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $SERVER_SDK_KEY" https://sdk.example.com/sdk/v1/ruleset   # 200
```

### 5. Point the SDKs at the load balancer

Point the `sdk.example.com` DNS record at the load balancer and set the SDKs' base URL to
`https://sdk.example.com`. The control plane keeps serving SDK traffic too, so applications can
move over one at a time. Client-side SDKs in browsers work the same way: the workers answer CORS
like the control plane.

## TLS

- A worker connects to the control plane over `https://` only. It sends its node token in the
  `Authorization` header, so plain HTTP would expose it.
- `MEGOOFLAGS_CONTROL_PLANE_INSECURE=true` allows `http://`. Use it only on a private network you
  trust, or in tests.
- If the control plane's certificate comes from a private CA (for example Caddy's internal CA or
  your company's), put the CA's PEM file in `certs/` next to the worker's compose file and set
  `MEGOOFLAGS_CONTROL_PLANE_CA_FILE=/etc/megooflags/certs/ca.crt`. The directory is mounted
  read-only at `/etc/megooflags/certs`.
- Each node keeps its own TLS mode (`MEGOOFLAGS_SITE_ADDRESS`): a worker can get its own
  certificate or sit behind a load balancer on port 80.

## Managing workers

| Status       | Meaning                                                                                                                          |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| Pending      | The worker has never connected.                                                                                                  |
| Healthy      | Heartbeat in the last 30 s and its snapshot version is current. With a fresh heartbeat and an older snapshot it shows "syncing". |
| Stale        | No heartbeat for more than 30 s. The page shows the age of its last synced snapshot.                                             |
| Draining     | A drain was requested.                                                                                                           |
| Incompatible | Its last connection was refused by the version rule (see [Rolling upgrades](#rolling-upgrades)).                                 |
| Revoked      | Every token of the node is revoked.                                                                                              |

Each worker sends a heartbeat every 10 s. The page refreshes every 10 s while it is open.

- **Drain:** the worker's `/readyz` answers 503 `draining` at once, so the load balancer stops
  sending it new clients. After `MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS` it closes its SDK streams,
  and the SDKs reconnect through the load balancer to another worker. Requests that still reach it
  are answered. **Resume** makes it ready again.
- **Rotate:** creates a new token. The old one keeps working for the overlap you choose (none,
  1 hour, 1 day or 7 days). Put the new token in the worker's `.env` and run
  `docker compose -f docker-compose.worker.yml up -d` before the overlap ends. An expired token is
  treated like a revoked one.
- **Revoke:** the worker **stops serving** at once. It gets a `revoked` message, its `/readyz`
  answers 503 `revoked`, and every SDK request gets 503. It stays that way after a restart, until it
  starts with a valid token. This cannot be undone: add a new worker and give the host its token.
  Losing the control plane is different: then the worker keeps serving.
- **Remove:** deletes a revoked node from the list.

Every action is recorded in the audit log (`node.create`, `node.rotate`, `node.revoke`,
`node.drain`, `node.resume`, `node.delete`), never with the token. The same actions are available
through `/api/nodes` with the `org.manage` permission.

## Failure behaviour

| Failure                            | Effect on apps                                                                                   | Effect on admins                                                                                                            |
| ---------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| One worker down                    | None: the load balancer sends traffic to the other workers; SDKs reconnect their streams.        | The worker shows as Stale on the Nodes page.                                                                                |
| All workers lose the control plane | None: flags are served from the last snapshot. New flag changes wait until the link is restored. | The UI and management API are unavailable if the control plane itself is down; workers show as Stale with the snapshot age. |
| Control plane restored             | Workers resync to the latest snapshot automatically and push it to SDKs within 5 s.              | Buffered events are ingested, each batch once.                                                                              |
| Database down                      | None for flag serving.                                                                           | The control plane is unavailable (its `/readyz` answers 503) until the database is back; then changes propagate again.      |

The `scripts/cluster_test.sh` suite checks each row against a real control plane, three workers and
an nginx load balancer.

More details:

- **The control plane is unreachable:** the worker keeps serving, buffers events and retries with a
  backoff from 0.5 s to 30 s. There is **no age limit** on serving: a worker serves its last
  snapshot for as long as the control plane is away. Alert on the snapshot age instead (see
  [Metrics](#metrics)). On reconnect it sends the versions it holds and receives only what changed.
- **A worker restarts while the control plane is down:** it loads and verifies its snapshots from
  disk and is ready at once. A corrupt or tampered file is discarded and logged; the worker is not
  ready until the next sync replaces it.
- **A new worker starts while the control plane is down:** it is not ready (`/readyz` 503
  `not_ready`) and answers SDK requests with 503 until its first full sync.
- **SDK keys:** revoked and expired keys are carried in the snapshot, so a worker refuses them
  without the control plane. A key **created during an outage** works on workers only after the
  link is back (it works on the control plane at once).
- **Deleted environment or project:** the worker deletes its snapshot, closes that environment's
  SDK streams with `revoked`, and its keys answer 401.
- **Client rate limits are counted per worker,** in memory. With N workers behind the load
  balancer, a client key and IP can make up to N times `MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE`
  requests per minute. Server keys are not rate-limited.
- **Events:** exposures and evaluation counts are buffered in memory and spilled to disk, up to
  `MEGOOFLAGS_WORKER_BUFFER_MAX_MB`. When the buffer is full, the oldest exposures are dropped
  (counted in `megooflags_worker_events_dropped_total`); evaluation counts are kept. Buffered data
  older than 7 days is dropped. Contexts are removed before buffering unless the control plane
  stores them (`MEGOOFLAGS_PERSIST_CONTEXT`). A batch sent twice, e.g. after a timeout, is stored
  once.
- **`MEGOOFLAGS_SECRET_KEY` changed on the control plane:** the snapshot signing key is derived
  from it, so workers see a new public key and fetch every snapshot again.
- **A worker pointed at a `standalone` node** is refused with 409 `not_control_plane`; the message
  names the setting to change.

## Rolling upgrades

Workers and the control plane check each other's version when a worker connects. The rule compares
`X.Y`:

- The major versions must match, and the worker may run the same minor or one minor older than
  the control plane, never newer (`1.2.x` and `1.3.x` work together; `1.1.x` and `1.3.x` do not,
  nor do `1.x` and `2.x`).

A refused worker shows as **Incompatible** on the Nodes page and logs a message with both versions.
It keeps serving the snapshot it has, without new changes, and retries every 60 s.

Upgrade the **control plane first**, then **each worker in turn**:

1. On the control plane, set `MEGOOFLAGS_VERSION` to the new release in `.env`, then:

   ```bash
   docker compose -f docker-compose.control-plane.yml pull
   docker compose -f docker-compose.control-plane.yml up -d
   ```

   While the API restarts, workers keep serving and reconnect on their own.

2. For each worker: **Drain** it on the Nodes page and wait for the grace time
   (`MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS`) so its SDKs move to the other workers. Then, on the
   worker, set `MEGOOFLAGS_VERSION` to the same release and run:

   ```bash
   docker compose -f docker-compose.worker.yml pull
   docker compose -f docker-compose.worker.yml up -d
   ```

   Wait until it is synced, then **Resume** it and move on to the next worker. A restarted worker
   is still draining until you resume it.

A major upgrade (`1.x` to `2.x`), or a jump of more than one minor, makes the old workers
Incompatible from the moment the control plane is upgraded until they are upgraded. They keep
serving, but flag changes do not reach them in between, so upgrade them right away. Patch upgrades
and an upgrade by one minor (`1.2` to `1.3`) have no such gap.

## Metrics

Each api container serves Prometheus metrics on `:8000/metrics` inside its compose network (Caddy
answers 404). On a worker, attach Prometheus to the `megooflags-worker_default` network and scrape
`api:8000`.

| Metric                                      | Node          | Meaning                                                                                               |
| ------------------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------- |
| `megooflags_worker_snapshot_age_seconds`    | worker        | Seconds since the worker last heard from the control plane while in sync. Under 15 s while connected. |
| `megooflags_worker_snapshot_version`        | worker        | The highest snapshot version the worker has applied.                                                  |
| `megooflags_worker_control_plane_connected` | worker        | 1 while the worker's stream to the control plane is open.                                             |
| `megooflags_worker_buffered_events`         | worker        | Exposures and counts waiting to be forwarded.                                                         |
| `megooflags_worker_events_dropped_total`    | worker        | Exposures dropped because the buffer was full or older than 7 days.                                   |
| `megooflags_node_streams`                   | control plane | Open worker streams on this control plane.                                                            |

An alert for a worker that has not heard from the control plane for five minutes:

```yaml
groups:
  - name: megooflags
    rules:
      - alert: MegooFlagsWorkerSnapshotStale
        expr: megooflags_worker_snapshot_age_seconds > 300
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Worker {{ $labels.instance }} has not synced with the control plane for 5 minutes"
          description: "It still serves its last snapshot, but flag changes do not reach it."
```

## Workers as an edge relay

A worker does not have to sit next to the control plane. Run one in another region, or inside a
private network your applications use, and point those applications' SDKs at it: SDK traffic stays
local, and only the worker's outbound HTTPS connection reaches the control plane. The worker keeps
serving if that link breaks, and forwards the buffered events when it comes back. Several workers in
one place can share a local load balancer as above.

## Footprint

A worker runs one process and targets 1 vCPU and 512 MB of memory; an idle worker uses less than
256 MiB. `MEGOOFLAGS_API_WORKERS` is ignored on workers (the worker logs a line saying so): to use
more CPU, add workers. Memory grows with the size and number of your environments' rulesets, since
the key index of every environment and the rulesets of the most recently used environments are kept
in memory, and the others are read from disk on first use.
