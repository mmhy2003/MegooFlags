<picture>
  <source media="(prefers-color-scheme: dark)" srcset="site/src/assets/logo-dark.svg">
  <img src="site/src/assets/logo-light.svg" alt="MegooFlags" height="48">
</picture>

[![CI](https://img.shields.io/github/actions/workflow/status/mmhy2003/MegooFlags/ci.yml?branch=main&label=CI&labelColor=1a1033)](https://github.com/mmhy2003/MegooFlags/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/mmhy2003/MegooFlags?include_prereleases&labelColor=1a1033&color=c8006a)](https://github.com/mmhy2003/MegooFlags/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0-007a87?labelColor=1a1033)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-mmhy2003.github.io%2FMegooFlags-7a1fd6?labelColor=1a1033)](https://mmhy2003.github.io/MegooFlags/)
[![OpenFeature](https://img.shields.io/badge/OpenFeature-providers-c8006a?labelColor=1a1033)](https://openfeature.dev)

A free, open-source alternative to hosted feature flag services: runtime toggles, user targeting, and percentage rollouts.

Documentation: https://mmhy2003.github.io/MegooFlags/

## Quick start

You need Docker with the Compose plugin. Download the release bundle, set a secret key and start the stack:

```bash
for f in docker-compose.yml Caddyfile .env.example; do curl -fsSLO "https://github.com/mmhy2003/MegooFlags/releases/latest/download/$f"; done
cp .env.example .env
sed -i "s/^MEGOOFLAGS_SECRET_KEY=.*/MEGOOFLAGS_SECRET_KEY=$(openssl rand -hex 32)/" .env
docker compose up -d --wait
```

Then open http://localhost/setup and create the admin.

For production, edit `.env`: set a strong database password, set `MEGOOFLAGS_SITE_ADDRESS` to your domain
for automatic HTTPS, and pin `MEGOOFLAGS_VERSION` to an exact release (for example `1.0.0`) so that
upgrades only happen when you change it.

Full quick start, from a new flag to an evaluation with curl and the SDKs:
https://mmhy2003.github.io/MegooFlags/start/quick-start/

## Multi-node

For more SDK capacity, or to keep serving flags while the management side is down, run one control
plane (`docker-compose.control-plane.yml`) and any number of workers (`docker-compose.worker.yml`)
behind a load balancer, all from the same images. Workers serve `/sdk/*` and `/ofrep/*` from signed
snapshots kept on disk. See [Multi-node deployment](https://mmhy2003.github.io/MegooFlags/deploy/multi-node/), and
[Migrating from a single node](https://mmhy2003.github.io/MegooFlags/deploy/multi-node-migration/) to move a single node over without
downtime.

## Kubernetes

The Helm chart runs a single node, a control plane or workers on Kubernetes, routed by an Ingress or a
Gateway API HTTPRoute:

```bash
helm install megooflags oci://ghcr.io/mmhy2003/charts/megooflags --version <v> \
  --namespace megooflags --create-namespace \
  --set secretKey=$(openssl rand -hex 32)
```

See [Kubernetes (Helm)](https://mmhy2003.github.io/MegooFlags/deploy/kubernetes/).

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

To manage MegooFlags from a terminal or CI, use the `megooflags` CLI ([guide](https://mmhy2003.github.io/MegooFlags/use/cli/)), with
flags as code ([guide](https://mmhy2003.github.io/MegooFlags/use/flags-as-code/)) and imports from other tools
([guide](https://mmhy2003.github.io/MegooFlags/migrate/overview/)). Webhooks, Slack and Teams notifications are described in
[Integrations](https://mmhy2003.github.io/MegooFlags/use/integrations/), and the Terraform provider in
[terraform/README.md](terraform/README.md).

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
cli/       megooflags CLI and Go API client
terraform/ Terraform provider
deploy/    single-node, control-plane and worker compose files, Caddyfiles, .env examples and the Helm chart
scripts/   version, release, migration and smoke-test scripts
sdks/      SDKs, one directory per language (reserved)
spec/      shared evaluation spec and SDK conformance suite (reserved)
site/      docs site (Astro Starlight), published to GitHub Pages
perf/      load test suite (mfload) and the performance report
rfcs/      requests for comments on major changes
```

## Community

- [Contributing](CONTRIBUTING.md): how to set up, which checks to run and how pull requests work
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md): report vulnerabilities privately
- [Roadmap](ROADMAP.md)
- [RFC process](rfcs/README.md): how to propose a major change

## License

[Apache-2.0](LICENSE)
