---
title: Configuration
description: Every environment variable of MegooFlags, its default and what it does.
---

MegooFlags is configured with environment variables. With the Docker Compose bundle, put them in
`.env` next to the compose file and run `docker compose up -d` to apply a change. `.env.example`
(single node and control plane) and `.env.worker.example` (worker nodes) list the common ones with
comments.

The defaults below are the ones the bundle uses. Durations are numbers of the stated unit and may
have decimals; booleans are `true` or `false`.

## Core

| Variable                 | Default           | Meaning                                                                                                                                                                                          |
| ------------------------ | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `MEGOOFLAGS_VERSION`     | `latest`          | Image tag of the web, migrate and api services. Pin an exact release in production. See [Upgrades](/MegooFlags/deploy/upgrades/).                                                                |
| `MEGOOFLAGS_ROLE`        | `standalone`      | `standalone` (one node), `control-plane` (the default of `docker-compose.control-plane.yml`) or `worker` (set by `docker-compose.worker.yml`). See [Multi-node](/MegooFlags/deploy/multi-node/). |
| `MEGOOFLAGS_PUBLIC_URL`  | empty             | External URL, e.g. `https://flags.example.com`. Empty: taken from each request. Passkeys and SSO need it. See [TLS modes](/MegooFlags/deploy/tls/#megooflags_public_url).                        |
| `MEGOOFLAGS_API_WORKERS` | `0`               | API processes per api container. `0` means one per CPU, at most 8. Docker CPU limits are not detected: set it when you use them. Ignored on a worker node, which runs one process.               |
| `MEGOOFLAGS_API_URL`     | `http://api:8000` | Where the web container reaches the API. Set in the compose file, not in `.env`.                                                                                                                 |

## Database and Valkey

| Variable                        | Default                                                         | Meaning                                                                                                                                                         |
| ------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POSTGRES_PASSWORD`             | `megooflags`                                                    | Password of the bundled PostgreSQL. Change it, and change it in `MEGOOFLAGS_DATABASE_URL` too. It takes effect only when the `db_data` volume is first created. |
| `MEGOOFLAGS_DATABASE_URL`       | `postgresql+asyncpg://megooflags:megooflags@db:5432/megooflags` | SQLAlchemy URL of the database, with the `asyncpg` driver. Point it at your own PostgreSQL to replace the bundled one (the bundle runs 16).                     |
| `MEGOOFLAGS_DB_CONNECT_TIMEOUT` | `2`                                                             | Seconds to wait for a database connection before giving up. Not passed by the compose files (see [below](#settings-the-compose-files-do-not-pass)).             |
| `MEGOOFLAGS_VALKEY_URL`         | `redis://valkey:6379/0`                                         | Valkey or Redis URL. Required: it carries flag changes to every API process and holds the client-key rate limits. The API refuses to start without it.          |

## TLS and proxy

| Variable                     | Default                                             | Meaning                                                                                                                                                                    |
| ---------------------------- | --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MEGOOFLAGS_SITE_ADDRESS`    | empty                                               | Empty: plain HTTP on port 80, behind your own proxy. A domain: automatic HTTPS on 80 and 443. See [TLS modes](/MegooFlags/deploy/tls/).                                    |
| `MEGOOFLAGS_HTTP_PORT`       | `80`                                                | Host port of Caddy's plain HTTP listener. Keep 80 with automatic HTTPS, for the ACME HTTP-01 challenge.                                                                    |
| `MEGOOFLAGS_TRUSTED_PROXIES` | `private_ranges`                                    | Proxies whose `X-Forwarded-*` headers Caddy passes on, in Caddy syntax. Add your proxy's address if it is public.                                                          |
| `FORWARDED_ALLOW_IPS`        | `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1` | Addresses whose `X-Forwarded-*` headers Uvicorn, inside the api container, trusts. The default covers Caddy on the compose network; change it only without Caddy in front. |

## Security and sessions

| Variable                           | Default | Meaning                                                                                                                                                                                                     |
| ---------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MEGOOFLAGS_SECRET_KEY`            | none    | Required, at least 32 characters (`openssl rand -hex 32`). Encrypts SSO client secrets, SAML keys and TOTP seeds in the database. Keep it stable: a new key makes those unreadable. Workers do not need it. |
| `MEGOOFLAGS_SESSION_IDLE_HOURS`    | `12`    | Hours of inactivity after which a sign-in session ends. Not passed by the compose files.                                                                                                                    |
| `MEGOOFLAGS_SESSION_ABSOLUTE_DAYS` | `7`     | Days after which a session ends even when it is in use. Not passed by the compose files.                                                                                                                    |
| `MEGOOFLAGS_PENDING_MFA_MINUTES`   | `5`     | Minutes a user has to enter the second factor after the password. Not passed by the compose files.                                                                                                          |

## SDK delivery

| Variable                                  | Default | Meaning                                                                                                                                                                    |
| ----------------------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE` | `600`   | Requests per minute allowed for each client SDK key and client IP. Shared across replicas through Valkey; counted per worker on worker nodes. Server keys are not limited. |
| `MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`        | `15`    | Seconds between heartbeats on open SDK streams. Keep proxy idle timeouts above it.                                                                                         |
| `MEGOOFLAGS_STREAM_RECHECK_SECONDS`       | `30`    | Seconds between fingerprint re-checks of environments with open streams, in case a Valkey change message is lost. An update is delayed by at most this long.               |
| `MEGOOFLAGS_PERSIST_CONTEXT`              | `false` | Also store the evaluation context with each exposure event. It may contain personal data. See [Privacy](/MegooFlags/project/privacy/).                                     |
| `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS`      | `30`    | Days to keep exposure events.                                                                                                                                              |

## Scheduler and update check

| Variable                                | Default | Meaning                                                                                                                                                                                                   |
| --------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MEGOOFLAGS_SCHEDULER_ENABLED`          | `true`  | Runs the background jobs: scheduled changes, rollouts, the lifecycle sweep, integration deliveries, retention and the update check. One API process at a time runs them. Not passed by the compose files. |
| `MEGOOFLAGS_SCHEDULER_INTERVAL_SECONDS` | `5`     | Seconds between scheduler ticks: how late a scheduled change can be. Not passed by the compose files.                                                                                                     |
| `MEGOOFLAGS_UPDATE_CHECK`               | `true`  | Ask the GitHub Releases API about once a day for the newest release, to show admins an update notice. Sends only the version. See [Privacy](/MegooFlags/project/privacy/).                                |

## Integrations

| Variable                             | Default | Meaning                                                                                                                                   |
| ------------------------------------ | ------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE`   | `false` | Let webhook, Slack and Teams integrations send over plain http and to private, loopback and link-local addresses. Off: public https only. |
| `MEGOOFLAGS_WEBHOOK_TIMEOUT_SECONDS` | `10`    | Seconds to wait for a receiver to answer, at most 60.                                                                                     |
| `MEGOOFLAGS_WEBHOOK_RETENTION_DAYS`  | `7`     | Days to keep the delivery log.                                                                                                            |

## Multi-node

These apply to worker nodes (`.env.worker.example`). A worker needs no database, Valkey or secret
key. See [Multi-node deployment](/MegooFlags/deploy/multi-node/).

| Variable                                | Default               | Meaning                                                                                                                                                                                           |
| --------------------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MEGOOFLAGS_CONTROL_PLANE_URL`          | none                  | Required on a worker. The control plane's external URL; `https://` unless `MEGOOFLAGS_CONTROL_PLANE_INSECURE=true`.                                                                               |
| `MEGOOFLAGS_NODE_TOKEN`                 | none                  | Required on a worker. The `mf_node_` token shown once when you add the worker on the Nodes page.                                                                                                  |
| `MEGOOFLAGS_CONTROL_PLANE_INSECURE`     | `false`               | Allow an `http://` control plane URL. Only for a private network or tests.                                                                                                                        |
| `MEGOOFLAGS_CONTROL_PLANE_CA_FILE`      | empty                 | PEM file of a private CA for the control plane's certificate, e.g. `/etc/megooflags/certs/ca.crt` (from `./certs`). Empty: the system's public CAs.                                               |
| `MEGOOFLAGS_DATA_DIR`                   | `/var/lib/megooflags` | Where a worker keeps its snapshots and event buffer, so it can serve after a restart without the control plane. The bundle mounts the `worker_data` volume here. Not passed by the compose files. |
| `MEGOOFLAGS_WORKER_BUFFER_MAX_MB`       | `64`                  | Disk space for exposures and evaluation counts buffered while the control plane is unreachable. Past it, the oldest exposures are dropped; counts are kept.                                       |
| `MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS` | `30`                  | Seconds a draining worker keeps its open SDK streams before it closes them.                                                                                                                       |

## Observability

| Variable                      | Default                                            | Meaning                                                                                   |
| ----------------------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `MEGOOFLAGS_LOG_LEVEL`        | `INFO`                                             | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Health and metrics requests are logged at `DEBUG`. |
| `MEGOOFLAGS_LOG_FORMAT`       | `json`                                             | `json` (one object per line) or `text`. Not passed by the compose files.                  |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty                                              | OTLP/HTTP endpoint for traces, e.g. `http://otel-collector:4318`. Empty: tracing is off.  |
| `OTEL_SERVICE_NAME`           | `megooflags-api` (`megooflags-worker` on a worker) | Service name on the traces.                                                               |
| `OTEL_TRACES_SAMPLER_ARG`     | `1.0`                                              | Fraction of traces to sample, `0.0` to `1.0`. A parent's sampling decision wins.          |

See [Observability](/MegooFlags/deploy/observability/) for metrics, health checks and logs.

## Settings the compose files do not pass

The compose files pass only the variables they list to the api container. To set one marked "Not
passed by the compose files", add it in an override file next to `docker-compose.yml`:

```yaml
# docker-compose.override.yml
services:
  api:
    environment:
      MEGOOFLAGS_LOG_FORMAT: text
      MEGOOFLAGS_SESSION_IDLE_HOURS: "8"
```

`docker compose up -d` reads `docker-compose.override.yml` on its own. With `-f`, as in
`docker compose -f docker-compose.control-plane.yml`, add `-f docker-compose.override.yml` to every
command.
