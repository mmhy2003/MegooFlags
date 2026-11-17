---
title: Upgrades and version pinning
description: Image tags, pinning a release, the upgrade procedure and how update notices work.
---

## Image tags

Every release publishes `ghcr.io/mmhy2003/megooflags-api` and `ghcr.io/mmhy2003/megooflags-web`
for `amd64` and `arm64`, with these tags:

| Tag          | Example      | Moves to                                                           |
| ------------ | ------------ | ------------------------------------------------------------------ |
| `X.Y.Z`      | `1.2.3`      | Never moves. The exact release.                                    |
| `X.Y`        | `1.2`        | The newest patch release of `1.2`.                                 |
| `X`          | `1`          | The newest `1.Y.Z` release. Published from 1.0 on only.            |
| `latest`     | `latest`     | The newest release.                                                |
| `X.Y.Z-rc.N` | `1.3.0-rc.1` | Never moves. Release candidates get only this tag, never `latest`. |

Before 1.0, every release is marked as a pre-release on GitHub, and a `0.Y` release may break
compatibility.

## Pin the version

The compose files take the tag from `MEGOOFLAGS_VERSION` and default to `latest`. With `latest`, any
`docker compose pull` can upgrade you, so in production pin the exact release in `.env`:

```bash
MEGOOFLAGS_VERSION=0.1.0
```

The web, migrate and api services always run the same version. Pinning `X.Y` gets you patch releases
on each pull; pin `X.Y.Z` when you want every upgrade to be a deliberate change.

## Upgrade a single node

1. **Read the release notes** of every release between yours and the new one, in the
   [release notes](/MegooFlags/project/release-notes/) or on the GitHub release. They list breaking
   changes and new settings.
2. **Back up the database** (see [Backups](/MegooFlags/deploy/single-node/#backups)).
3. **Compare the bundle files.** Download the new release's `docker-compose.yml`, `Caddyfile` and
   `.env.example` from `https://github.com/mmhy2003/MegooFlags/releases/download/vX.Y.Z/<file>`.
   Replace the first two, and copy any new variables from `.env.example` into your `.env`.
4. **Change the version** in `.env`: `MEGOOFLAGS_VERSION=X.Y.Z`.
5. **Pull and restart:**

   ```bash
   docker compose pull
   docker compose up -d --wait
   ```

Migrations run on their own: the `migrate` service runs `alembic upgrade head` with the new image,
and the new API starts only after it succeeds. If a migration fails, `docker compose up` stops with
an error before it starts the new API, and `docker compose logs migrate` shows why.

There is no automatic downgrade. To go back, restore the backup you took in step 2 and set the old
version.

### Why replicas can be upgraded one at a time

Schema changes that would break the previous release are split across two releases
(expand/contract): a release first adds the new column or table and writes to both, and only the
next release stops using the old one. So the database after a migration still works for API
processes of the previous release, and old and new replicas can serve side by side while
`docker compose up -d` replaces them. Do not skip the release notes when you jump several releases
at once: each one is still applied in order by the migrations.

## Upgrade a multi-node deployment

Upgrade the **control plane first**, then **each worker in turn**. A worker accepts a control plane
of the same or a newer compatible version, never an older one. Before 1.0, workers must run the same
`0.Y` as the control plane; from 1.0, the same major version and the same or one minor older.

The full procedure, with draining each worker first, is in
[Rolling upgrades](/MegooFlags/deploy/multi-node/#rolling-upgrades).

## SDKs, the CLI and Terraform

The SDKs, the `megooflags` CLI and the Terraform provider have their own versions and release tags
(`sdk-node/vX.Y.Z`, `cli/vX.Y.Z`, `terraform/vX.Y.Z` and so on). They talk to the server over
versioned APIs (`/sdk/v1`, `/ofrep/v1` and the REST API), so you do not need to upgrade them together
with the server. When a new SDK, CLI or provider feature needs server support, upgrade the server
first.

## Update notices

About once a day, the server asks the public GitHub Releases API for the newest release. When it is
newer than the running version, organisation admins see an **Update** pill next to the version in
the sidebar, linking to the release.

Only the running version is sent, in the `User-Agent` header. To turn the check off, set
`MEGOOFLAGS_UPDATE_CHECK=false` and run `docker compose up -d`. [Privacy](/MegooFlags/project/privacy/)
describes the request in full.
