---
title: Single node
description: Run the whole MegooFlags stack on one host with Docker Compose, and back it up.
---

The single-node bundle runs everything MegooFlags needs on one host with Docker Compose. It is the
setup the [quick start](/MegooFlags/start/quick-start/) uses, and the one to start with for
production: you can turn it into the control plane of a
[multi-node deployment](/MegooFlags/deploy/multi-node/) later without downtime.

## Requirements

- A Linux host with Docker Engine and the Compose plugin. Images are published for `amd64` and
  `arm64`.
- **2 vCPU and 2 GB of RAM** for a small team. See [Configuration](/MegooFlags/deploy/configuration/)
  for `MEGOOFLAGS_API_WORKERS`, which sets how many processes serve requests.
- **Ports:** 80 always (`MEGOOFLAGS_HTTP_PORT` changes the host port). 443, TCP and UDP, only when
  Caddy runs with automatic HTTPS. See [TLS modes](/MegooFlags/deploy/tls/).

## What runs

`docker-compose.yml` starts six services from the same release:

| Service   | Image                             | What it does                                                                                                                                         |
| --------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `caddy`   | `caddy:2`                         | The only service with published ports. Sends `/api/*`, `/sdk/*`, `/ofrep/*`, `/healthz` and `/readyz` to the API and everything else to the web app. |
| `web`     | `ghcr.io/mmhy2003/megooflags-web` | The Next.js management UI.                                                                                                                           |
| `migrate` | `ghcr.io/mmhy2003/megooflags-api` | Runs `alembic upgrade head` once and exits. The API starts only after it succeeds.                                                                   |
| `api`     | `ghcr.io/mmhy2003/megooflags-api` | The management API, the SDK endpoints (`/sdk/*`, OFREP) and the background scheduler.                                                                |
| `db`      | `postgres:16`                     | All data. Stored in the `db_data` volume.                                                                                                            |
| `valkey`  | `valkey/valkey:8`                 | Carries flag changes between API processes and holds the shared rate limits. It keeps nothing on disk, so it needs no backup.                        |

The volumes are `db_data` (the database), `caddy_data` (certificates, in automatic-HTTPS mode) and
`caddy_config`.

## Install

Download the bundle of the release you want into a new directory. Use `latest` or an exact version
such as `v1.0.0` in the URL:

```bash
mkdir megooflags && cd megooflags
for f in docker-compose.yml Caddyfile .env.example; do
  curl -fsSLO "https://github.com/mmhy2003/MegooFlags/releases/latest/download/$f"
done
cp .env.example .env
```

Then edit `.env`. The essentials:

- `MEGOOFLAGS_SECRET_KEY`: required. Generate it with `openssl rand -hex 32` and keep it safe. It
  encrypts SSO secrets and TOTP seeds in the database; losing it makes them unreadable.
- `MEGOOFLAGS_VERSION`: pin the exact release, for example `1.0.0`. See
  [Upgrades and version pinning](/MegooFlags/deploy/upgrades/).
- `MEGOOFLAGS_SITE_ADDRESS` and `MEGOOFLAGS_PUBLIC_URL`: your domain and external URL. See
  [TLS modes](/MegooFlags/deploy/tls/).
- `POSTGRES_PASSWORD` and `MEGOOFLAGS_DATABASE_URL`: change the database password in both.

Start the stack and wait until every service is healthy:

```bash
docker compose up -d --wait
```

Open `/setup` on your address and create the first administrator straight away: until then,
anyone who reaches the instance can, and the API logs a warning on every readiness check.

Every other variable is listed in [Configuration](/MegooFlags/deploy/configuration/). After you change
`.env`, run `docker compose up -d` again to apply it.

## More API replicas

Caddy finds every replica of the `api` service by DNS and balances across them, so you can run more
than one on a larger host:

```bash
docker compose up -d --scale api=3
```

Each replica already runs several worker processes (`MEGOOFLAGS_API_WORKERS`). Replicas keep each
other up to date through Valkey; only one of them runs the background scheduler at a time.

## Backups

Everything MegooFlags stores is in PostgreSQL. Back it up with `pg_dump` from the `db` service:

```bash
docker compose exec -T db pg_dump -U megooflags -Fc megooflags > megooflags-$(date +%F).dump
```

Also keep a copy of `.env`: without `MEGOOFLAGS_SECRET_KEY` a restored database cannot decrypt its
SSO and TOTP secrets. Valkey holds nothing that needs a backup, and Caddy gets new certificates on
its own if `caddy_data` is lost.

To restore into a fresh stack, start only the database, restore, then start the rest:

```bash
docker compose up -d --wait db
docker compose exec -T db pg_restore -U megooflags -d megooflags --clean --if-exists < megooflags-2026-11-17.dump
docker compose up -d --wait
```

The `migrate` service then brings the restored schema up to the running version.

## Logs

Every service logs to stdout. The API writes one JSON object per line (see
[Observability](/MegooFlags/deploy/observability/)):

```bash
docker compose logs -f api
docker compose logs --since 1h caddy
```

Docker keeps container logs with its default logging driver. On a long-running host, set a size
limit for it (for example `max-size` with the `json-file` driver) or ship the logs to your log
platform.
