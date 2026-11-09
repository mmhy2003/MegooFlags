# MegooFlags
A free, open-source alternative to hosted feature flag services: runtime toggles, user targeting, and percentage rollouts.

## Quick start

You need Docker with the Compose plugin.

1. Download `docker-compose.yml`, `Caddyfile` and `.env.example` from the
   [latest release](https://github.com/mmhy2003/MegooFlags/releases/latest) into one directory.
2. Create your configuration and start the stack:

   ```bash
   cp .env.example .env
   docker compose up -d
   ```

3. Open http://localhost.

For production, edit `.env`: set a strong database password, set `MEGOOFLAGS_SITE_ADDRESS` to your domain
for automatic HTTPS, and pin `MEGOOFLAGS_VERSION` to an exact release (for example `0.1.0`) so that
upgrades only happen when you change it.

## Development

Prerequisites:

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node 22 and pnpm (run `corepack enable`; the version is pinned in `web/package.json`)
- Docker

API (`api/`):

```bash
cd api
uv sync
uv run uvicorn megooflags_api.main:app --reload    # http://localhost:8000/healthz
uv run ruff check . ../scripts && uv run ruff format --check . ../scripts
uv run mypy
uv run pytest                                      # needs Postgres and MEGOOFLAGS_DATABASE_URL
uv run alembic upgrade head
```

Web (`web/`):

```bash
cd web
pnpm install
pnpm dev                                           # http://localhost:3000
pnpm lint && pnpm format:check && pnpm typecheck && pnpm build
pnpm gen:api                                       # regenerate the typed API client
```

The full list of checks that CI runs, including the Postgres setup, migrations, images and the smoke
test, is in [AGENTS.md](AGENTS.md#9-local-checks-the-same-as-ciyml).

## Versioning and releases

The server, API and web share one version, stored in `VERSION`. The rules for bumping it, for
database migrations and for releases are in [AGENTS.md](AGENTS.md). Changes are listed in
[CHANGELOG.md](CHANGELOG.md). Releases are cut by a maintainer by pushing a `vX.Y.Z` tag, which
publishes multi-arch images to `ghcr.io/mmhy2003/megooflags-api` and `ghcr.io/mmhy2003/megooflags-web`.

Day-to-day work is merged into `develop`, and `main` holds the released code. A release is a pull
request from `develop` into `main`; once it is merged, the maintainer tags the merge commit on `main`.
The branching rules and release steps are in [AGENTS.md](AGENTS.md#2-branches-and-pull-requests).

## Repository layout

```
api/       FastAPI server, Alembic migrations, openapi.json
web/       Next.js UI and the typed API client
deploy/    docker-compose.yml, Caddyfile and .env.example
scripts/   version, release, migration and smoke-test scripts
sdks/      SDKs, one directory per language (reserved)
spec/      shared evaluation spec and SDK conformance suite (reserved)
```

## License

[Apache-2.0](LICENSE)
