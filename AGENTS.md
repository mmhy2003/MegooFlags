# AGENTS.md

Rules for coding agents (Claude Code, Codex, Cursor and others) working in this repository.
Humans follow them too. When a rule here and a habit of yours disagree, this file wins.

## 1. Project layout

| Path | What lives there |
|---|---|
| `api/` | FastAPI server (`megooflags_api`, Python 3.12, src layout), Alembic migrations, `openapi.json` |
| `web/` | Next.js 16 App Router UI (TypeScript, Tailwind 4, shadcn/ui, `output: "standalone"`) |
| `deploy/` | Reference single-node bundle: `docker-compose.yml`, `Caddyfile`, `.env.example`, and the CI override `compose.ci.yml` |
| `scripts/` | Version, release and check scripts shared by CI, the release workflow and developers |
| `sdks/` | Reserved for in-repo SDKs, one directory per language (see `sdks/README.md`) |
| `spec/` | Reserved for the shared evaluation spec and SDK conformance suite (see `spec/README.md`) |

Package managers:

- Python uses **uv** (`api/uv.lock`). Web uses **pnpm** (`web/pnpm-lock.yaml`, pinned by `packageManager` in `web/package.json`; enable it with `corepack enable`).
- Never switch to pip or npm, and never add a second lockfile.
- Always commit lockfile changes together with the manifest change that caused them.

## 2. Versioning (X.Y.Z)

The server, both images and both packages share one version, stored in `VERSION`. From 1.0.0 on:

| Bump | When | Examples |
|---|---|---|
| **Major** (`X`) | A change that breaks existing users, deployments or SDKs | Removing or renaming an API endpoint or field; a change to the SDK protocol; renaming or removing an environment variable; a migration that cannot run automatically on upgrade |
| **Minor** (`Y`) | A backwards-compatible feature | A new API endpoint; a new flag type; a new optional setting with a safe default |
| **Patch** (`Z`) | A backwards-compatible fix with no new features | A bug fix; a dependency or base-image bump; a rebuild for a security update |

When in doubt between two levels, pick the higher one.

## 3. Before 1.0.0 (0.Y.Z)

While the major version is 0, the API is not yet stable and the rules shift one place to the right:

- Breaking changes **and** new features bump `Y`: `0.3.4` → `0.4.0`.
- Fixes bump `Z`: `0.3.4` → `0.3.5`.
- Patch releases never break anything, even before 1.0.0.
- Breaking changes go at the top of the CHANGELOG entry, with the upgrade steps a user must follow.
- Every `0.Y.Z` release is published as a GitHub pre-release.
- Going to 1.0.0 is a maintainer decision. Agents never propose it on their own.

## 4. Bumping the version

Change all of these **in one commit**:

1. `VERSION`
2. `api/pyproject.toml` (`version`)
3. `web/package.json` (`version`)
4. `CHANGELOG.md`: move the `[Unreleased]` items into a new `## [X.Y.Z] - YYYY-MM-DD` section and leave an empty `[Unreleased]` above it.

Then regenerate the files that embed the version, and include them in the same commit:

```bash
cd api && uv lock                                                   # uv.lock records the project version
cd api && uv run python -m megooflags_api.export_openapi openapi.json   # the spec embeds the version
cd web && pnpm gen:api                                              # regenerates lib/api/schema.d.ts
```

Check that everything agrees:

```bash
python3 scripts/check_version.py        # prints "version OK: X.Y.Z"
```

The scripts need Python 3.12 or later. If your system `python3` is older, run them with
`uv run --project api python scripts/check_version.py`.

## 5. CHANGELOG

Every user-visible change adds a line under `## [Unreleased]` in `CHANGELOG.md`, in the right group:
**Added**, **Changed**, **Fixed** or **Removed**. Write it for users, not for reviewers: what changed and
what they need to do, not how the code works. Internal refactors and test-only changes need no entry.
The release notes are built from this file, so a missing entry is a missing release note.

## 6. Database migrations

- Every schema change is an Alembic revision with a working `downgrade()`:

  ```bash
  cd api && uv run alembic revision --autogenerate -m "<short_slug>"
  ```

  Review the generated file; autogenerate misses things (renames, server defaults, data changes).
- Keep exactly **one head**. If two branches each add a revision, rebase yours onto the other's revision before merging.
- Never edit a migration that has been released. Fix it with a new revision.
- Breaking schema changes use **expand/contract** across two releases, so that old and new API replicas can run side by side during a rolling upgrade:
  - **Release N (expand):** add the new column or table, write to both the old and the new one, and backfill. Old code keeps working because nothing it uses is gone.
  - **Release N+1 (contract):** read only from the new column or table, then drop the old one.

  Example: renaming `flags.key` to `flags.slug`. In 0.4.0, add a nullable `slug`, backfill it from `key`, write both on every insert and update, and keep reading `key`. In 0.5.0, read `slug`, make it `NOT NULL`, and drop `key`. A single migration that renames the column would break every replica still running 0.4.0.

`bash scripts/check_migrations.sh` checks the single head, then upgrades to head, walks down one revision at a time to base and upgrades again. It needs Postgres (see section 8).

## 7. API client

The web client is generated from the API's OpenAPI spec. After any change to API routes or models, run:

```bash
cd api && uv run python -m megooflags_api.export_openapi openapi.json
cd web && pnpm gen:api
```

Commit both `api/openapi.json` and `web/lib/api/schema.d.ts`. CI regenerates both and fails on any drift.

## 8. Local checks (the same as `ci.yml`)

Run all of these before you open a pull request or say a task is done. They are the commands that CI runs.

Start a Postgres for the database tests:

```bash
docker run -d --name mf-pg -e POSTGRES_USER=megooflags -e POSTGRES_PASSWORD=megooflags \
  -e POSTGRES_DB=megooflags -p 5432:5432 postgres:16
export MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags
```

Then, from the repository root:

```bash
# version
python3 scripts/check_version.py
uv run --frozen --project api pytest scripts/tests

# api-lint and api-test
(cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts \
  && uv run mypy && uv run pytest)

# migrations
bash scripts/check_migrations.sh

# web
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build)

# OpenAPI spec and client drift
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json
pnpm --dir web gen:api
git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts

# images and smoke
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh
```

If you change a workflow, also run `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest`.

## 9. Releases

Agents prepare release commits and pull requests (the version bump in section 4). Agents **never**:

- create or push `v*` or `sdk-*` tags,
- create GitHub releases,
- push images to any registry.

A maintainer merges the release pull request and pushes the tag. `release.yml` does the rest:

- `vX.Y.Z` is a release. The tag must match `VERSION`, or the workflow fails before anything is built.
- `vX.Y.Z-rc.N` is a test release. It publishes only the `X.Y.Z-rc.N` image tag and is always a pre-release.

Images go to `ghcr.io/mmhy2003/megooflags-api` and `ghcr.io/mmhy2003/megooflags-web` for linux/amd64 and linux/arm64. A release `vX.Y.Z` gets the image tags:

| Tag | Example for `v1.4.2` | Example for `v0.3.4` |
|---|---|---|
| `X.Y.Z` | `1.4.2` | `0.3.4` |
| `X.Y` | `1.4` | `0.3` |
| `X` (only from 1.0.0) | `1` | none |
| `latest` | `latest` | `latest` |

An rc tag never moves `X.Y`, `X` or `latest`. The GitHub release notes come from the matching `CHANGELOG.md` section, and the release attaches `docker-compose.yml`, `Caddyfile` and `.env.example`.

## 10. SDKs

SDKs live in this repository under `sdks/<lang>/`, each with its own version, independent of the server.
Their tags are `sdk-<lang>/vX.Y.Z` (for example `sdk-node/v1.2.0`), except Go, which must use
`sdks/go/vX.Y.Z` because Go modules in a subdirectory are tagged with their path. The rules in section 9
apply: agents never push SDK tags. See `sdks/README.md` and `spec/README.md`.
