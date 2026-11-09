# MF-74 Implementation Plan: repo scaffold, versioning, CI and release pipeline

**Goal:** Turn the repo (today only `README.md`) into a releasable monorepo: an empty FastAPI `api/` and Next.js `web/` that build, share one version, pass a CI that enforces PRD §8.4, and publish multi-arch GHCR images when a `vX.Y.Z` or `vX.Y.Z-rc.N` tag is pushed.

**Architecture:** `api/` is a uv-managed Python 3.12 package (`megooflags_api`, src layout) with `/healthz` and `/readyz`, async SQLAlchemy, async Alembic (one no-op baseline) and a committed `api/openapi.json`. `web/` is a pnpm-managed Next.js 16 app (`output: "standalone"`, shadcn/ui, Tailwind 4) with a typed `openapi-fetch` client generated from that spec. Stdlib-only scripts in `scripts/` hold the version, tag and release-notes logic, so `ci.yml`, `release.yml` and developers all run the same code. `deploy/` holds the PRD §8.2 compose bundle, and a CI override runs it against locally built images.

**Current state (verified):** `git ls-files` lists only `README.md` (2 lines). Every other path in this plan is new.

**Conventions used in every step:**
- Python commands run from `api/` with `uv run …`. Script tests run from the repo root with `uv run --project api pytest scripts/tests`.
- JS commands run from `web/` with `pnpm …`.
- A local Postgres for DB tests: `docker run -d --name mf-pg -e POSTGRES_USER=megooflags -e POSTGRES_PASSWORD=megooflags -e POSTGRES_DB=megooflags -p 5432:5432 postgres:16`, then `export MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags`.
- Commit messages are given per step. Every commit ends with the `Co-Authored-By` trailer that the session requires.

---

## File map

| Path | Responsibility |
|---|---|
| `VERSION` | `0.1.0`, the single source of truth |
| `LICENSE` | Apache License 2.0 full text |
| `CHANGELOG.md` | Keep-a-Changelog: `## [Unreleased]`, `## [0.1.0]` with Added/Changed/Fixed/Removed |
| `.gitignore`, `.editorconfig` | Python, Node, Next, env, IDE ignores; LF, 2-space indent (4 for `*.py`) |
| `README.md` | Product line, quick start (compose), dev setup, links to AGENTS.md, CHANGELOG, LICENSE |
| `AGENTS.md` | Agent rules (versioning, bump-together, Alembic, expand/contract, local checks, client regen, no tags, SDK tag scheme) |
| `CLAUDE.md` | `@AGENTS.md` and Claude-only notes |
| `scripts/check_version.py` | `parse_tag`, `read_versions`, `check`, CLI `[--root DIR] [--tag TAG]` |
| `scripts/release_meta.py` | `image_tags`, `is_prerelease`, `changelog_section`, `release_notes`, CLI that prints `key=value` for `$GITHUB_OUTPUT` |
| `scripts/tests/conftest.py`, `test_check_version.py`, `test_release_meta.py` | pytest for the two scripts |
| `scripts/check_migrations.sh` | single-head check, then upgrade head, walk down to base, upgrade head |
| `scripts/smoke.sh` | compose up with the CI override, curl through Caddy, logs on failure, teardown |
| `api/pyproject.toml`, `api/uv.lock`, `api/.python-version` | package metadata (version 0.1.0), dependencies, ruff, mypy and pytest config |
| `api/src/megooflags_api/__init__.py` | `__version__` from package metadata |
| `api/src/megooflags_api/settings.py` | `Settings` (pydantic-settings, `MEGOOFLAGS_` prefix), `get_settings()` |
| `api/src/megooflags_api/db.py` | `Base` (DeclarativeBase), `create_engine(settings)` |
| `api/src/megooflags_api/main.py` | `create_app(settings=None)`, module-level `app` |
| `api/src/megooflags_api/routers/__init__.py`, `routers/health.py` | `/healthz`, `/readyz` |
| `api/src/megooflags_api/export_openapi.py` | `python -m megooflags_api.export_openapi [PATH]` |
| `api/alembic.ini`, `api/alembic/env.py`, `api/alembic/script.py.mako`, `api/alembic/versions/0001_baseline.py` | async Alembic, no-op baseline (revision id `0001`) |
| `api/openapi.json` | committed spec |
| `api/tests/…` | `test_health.py`, `test_readyz.py`, `test_migrations.py`, `test_openapi.py` |
| `api/Dockerfile`, `api/.dockerignore` | `megooflags-api` image |
| `web/…` | Next.js 16 app: `package.json` (version 0.1.0, `packageManager` pnpm), `pnpm-lock.yaml`, `next.config.ts`, `eslint.config.mjs`, `.prettierrc.json`, `.prettierignore`, `app/`, `components/ui/button.tsx`, `components.json`, `lib/utils.ts`, `lib/api/schema.d.ts`, `lib/api/client.ts` |
| `web/Dockerfile`, `web/.dockerignore` | `megooflags-web` image |
| `deploy/docker-compose.yml`, `deploy/Caddyfile`, `deploy/.env.example`, `deploy/compose.ci.yml` | reference single-node bundle and the CI override |
| `sdks/README.md`, `spec/README.md` | reserved layout and SDK tag scheme |
| `.github/workflows/ci.yml`, `.github/workflows/release.yml` | CI and release |

---

## Steps

### 1. API skeleton with /healthz, VERSION, LICENSE and ignores
See the `steps[0]` detail (the same text). Reference code is in Appendix A.

### 2. scripts/check_version.py with tests, plus CHANGELOG.md
Appendix B.

### 3. scripts/release_meta.py: image tags, pre-release flag and release notes
Appendix C.

### 4. Database engine and /readyz
Appendix A.

### 5. Async Alembic with a no-op baseline and the migration check script
Appendix D.

### 6. OpenAPI export and the committed api/openapi.json
Appendix A.

### 7. api Dockerfile (megooflags-api image)
Appendix E.

### 8. Next.js 16 web scaffold (pnpm, Tailwind 4, shadcn/ui, ESLint, Prettier, standalone)
Appendix F.

### 9. Typed API client generated from the OpenAPI spec
Appendix F.

### 10. web Dockerfile (megooflags-web image)
Appendix E.

### 11. Deploy bundle (compose, Caddyfile, .env.example), CI override and smoke script
Appendix G.

### 12. CI workflow (.github/workflows/ci.yml)
Appendix H.

### 13. Release workflow (.github/workflows/release.yml)
Appendix I.

### 14. AGENTS.md, CLAUDE.md, README, sdks/ and spec/ reservations
Appendix J.

(The full detail of each step is in the `steps` array. The appendices hold the long code and contracts that the steps refer to.)

---

## Appendix A: API code

`api/pyproject.toml`
```toml
[project]
name = "megooflags-api"
version = "0.1.0"
description = "MegooFlags API server"
license = "Apache-2.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "pydantic>=2.9",
  "pydantic-settings>=2.6",
  "sqlalchemy[asyncio]>=2.0.36",
  "asyncpg>=0.30",
  "alembic>=1.14",
]

[dependency-groups]
dev = ["ruff>=0.8", "mypy>=1.13", "pytest>=8.3", "pytest-asyncio>=0.24", "httpx>=0.28"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/megooflags_api"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC"]

[tool.mypy]
strict = true
files = ["src"]
plugins = ["pydantic.mypy"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```
`api/.python-version`: `3.12`.

`src/megooflags_api/__init__.py`
```python
from importlib.metadata import version

__version__ = version("megooflags-api")
```

`settings.py`
```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEGOOFLAGS_", extra="ignore")

    database_url: str = "postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags"
    db_connect_timeout: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
```
Only the database settings are read now. `MEGOOFLAGS_VERSION` is the image tag that compose uses and is not read by the API. The version that the API reports comes from `__version__`.

`main.py` in step 1 (step 4 adds the engine and lifespan):
```python
from fastapi import FastAPI

from megooflags_api import __version__
from megooflags_api.routers import health
from megooflags_api.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="MegooFlags API", version=__version__)
    app.state.settings = settings
    app.include_router(health.router)
    return app


app = create_app()
```

`routers/health.py` in step 1:
```python
from fastapi import APIRouter
from pydantic import BaseModel

from megooflags_api import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
```

Step 4 additions. `db.py`:
```python
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from megooflags_api.settings import Settings


class Base(DeclarativeBase):
    pass


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"timeout": settings.db_connect_timeout},
    )
```
In `main.py`, add a lifespan that disposes the engine, and create the engine in `create_app` (creating it does not connect):
```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.engine.dispose()
# in create_app: app = FastAPI(..., lifespan=lifespan); app.state.engine = create_engine(settings)
```
`/readyz` in `routers/health.py`:
```python
class ReadyResponse(BaseModel):
    status: str

@router.get("/readyz", response_model=ReadyResponse,
            responses={503: {"model": ReadyResponse}})
async def readyz(request: Request) -> ReadyResponse | JSONResponse:
    engine: AsyncEngine = request.app.state.engine
    settings: Settings = request.app.state.settings
    try:
        async with asyncio.timeout(settings.db_connect_timeout + 1):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return ReadyResponse(status="ready")
```
(If FastAPI rejects the union return annotation, set `response_model=ReadyResponse` and annotate `-> Response`.)

Step 6 `export_openapi.py`:
```python
import json
import sys
from pathlib import Path

from megooflags_api.main import create_app


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = render()
    if args:
        Path(args[0]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## Appendix B: `scripts/check_version.py` contract

Stdlib only (`argparse`, `json`, `re`, `tomllib`, `dataclasses`, `pathlib`). It runs as `python3 scripts/check_version.py [--root DIR] [--tag TAG]`. `--root` defaults to `Path(__file__).resolve().parent.parent`.

```python
TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-rc\.([1-9]\d*))?$")
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

@dataclass(frozen=True)
class Tag:
    major: int
    minor: int
    patch: int
    rc: int | None
    @property
    def version(self) -> str: ...        # "0.1.0"
    @property
    def image_version(self) -> str: ...  # "0.1.0" or "0.1.0-rc.1"

def parse_tag(tag: str) -> Tag            # ValueError("invalid tag ...") if no match
def read_versions(root: Path) -> dict[str, str]
    # {"VERSION": ..., "api/pyproject.toml": project.version, "web/package.json": version}
    # a missing file → value "<missing>"
def check(root: Path, tag: str | None = None) -> list[str]   # list of error messages, empty = OK
    # errors: VERSION not X.Y.Z; any file != VERSION ("api/pyproject.toml has 0.2.0, VERSION has 0.1.0");
    # an invalid tag; tag.version != VERSION ("tag v0.2.0 does not match VERSION 0.1.0")
def main(argv: list[str] | None = None) -> int
    # prints each error to stderr and returns 1; otherwise prints "version OK: 0.1.0[ (tag vX)]" and returns 0
```

`scripts/tests/conftest.py` inserts `Path(__file__).resolve().parents[1]` into `sys.path`. It also provides the fixture `make_repo(tmp_path)`, a factory `make_repo(version="0.1.0", api="0.1.0", web="0.1.0")` that writes `VERSION` (with a trailing newline), `api/pyproject.toml` (`[project]\nname="x"\nversion="…"`) and `web/package.json`, and returns the root.

Tests in `test_check_version.py` (each is its own function):
- `test_matching_versions_pass`
- `test_version_file_trailing_whitespace_ignored`
- `test_pyproject_mismatch_fails`
- `test_package_json_mismatch_fails`
- `test_missing_package_json_fails`
- `test_invalid_version_file_fails` (`0.1`)
- `test_parse_final_tag` (`v0.1.0` → rc None, image_version `0.1.0`)
- `test_parse_rc_tag` (`v0.1.0-rc.1` → rc 1, version `0.1.0`, image_version `0.1.0-rc.1`)
- `test_invalid_tags_rejected`, parametrized over `0.1.0`, `v0.1`, `v0.1.0-beta.1`, `v01.0.0`, `v0.1.0-rc`, `v0.1.0-rc.0`, `v0.1.0+build`
- `test_tag_matching_version_passes` (final and rc)
- `test_tag_mismatch_fails` (`v0.2.0` and `v0.2.0-rc.1` against 0.1.0)
- `test_main_exit_codes` (`main(["--root", str(root)])` returns 0, and 1 on a mismatch)

## Appendix C: `scripts/release_meta.py` contract

Stdlib only. It imports `Tag` and `parse_tag` from `check_version` (same directory, so this works when run as `python3 scripts/release_meta.py`).

```python
def image_tags(tag: Tag) -> list[str]
    # rc:    [tag.image_version]
    # final: ["X.Y.Z", "X.Y"] + (["X"] if major >= 1 else []) + ["latest"]
def is_prerelease(tag: Tag) -> bool          # major == 0 or rc is not None
def changelog_section(text: str, heading: str) -> str | None
    # finds a line starting with f"## [{heading}]"; returns the stripped body up to the next "## [" line;
    # None if the heading is missing or the body is empty
def release_notes(changelog: str, tag: Tag) -> str
    # final: section tag.version or raise ValueError("CHANGELOG.md has no section for X.Y.Z")
    # rc: section tag.version, else section "Unreleased", else raise ValueError
def main(argv: list[str] | None = None) -> int
    # args: --tag TAG (required), --changelog PATH (default <root>/CHANGELOG.md), --notes-out PATH (optional)
    # prints exactly these lines to stdout:
    #   version=0.1.0
    #   image_version=0.1.0-rc.1
    #   tags=0.1.0-rc.1           (comma-separated)
    #   prerelease=true|false
    # writes the notes to --notes-out when given; on ValueError prints to stderr and returns 1
```

Tests in `test_release_meta.py`:
- `test_image_tags`, parametrized: `v0.1.0` → `["0.1.0","0.1","latest"]`; `v0.1.0-rc.1` → `["0.1.0-rc.1"]`; `v1.4.2` → `["1.4.2","1.4","1","latest"]`; `v1.4.2-rc.3` → `["1.4.2-rc.3"]`
- `test_no_major_tag_before_1_0` (`v0.9.9` has no `"0"`)
- `test_is_prerelease`, parametrized: 0.1.0 True, 0.1.0-rc.1 True, 1.4.2 False, 1.4.2-rc.1 True
- `test_changelog_section_extracts_body`, `test_changelog_section_missing_returns_none`
- `test_final_release_requires_section` (ValueError)
- `test_rc_falls_back_to_unreleased`
- `test_rc_without_any_section_fails`
- `test_main_outputs`: uses `capsys`, a temp CHANGELOG and `--notes-out`, and asserts the four lines and the notes file

`CHANGELOG.md` (step 2):
```markdown
# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows the versioning rules in [AGENTS.md](AGENTS.md).

## [Unreleased]

## [0.1.0]

### Added
- Monorepo scaffold: FastAPI `api/` and Next.js `web/` with a shared `VERSION`.
- `/healthz` and `/readyz` endpoints.
- Typed web API client generated from the OpenAPI spec.
- Docker images `megooflags-api` and `megooflags-web`, reference Docker Compose bundle with Caddy.
- CI (lint, types, tests, migrations, build, images, smoke) and tag-driven release pipeline.

### Changed

### Fixed

### Removed
```

## Appendix D: Alembic

- Create it with `uv run alembic init -t async alembic` from `api/`. Then edit `alembic.ini`: `script_location = %(here)s/alembic`, `prepend_sys_path = .`, `file_template = %%(rev)s_%%(slug)s`, and delete the `sqlalchemy.url` line.
- `alembic/env.py`: keep the async template and add:
```python
from megooflags_api.db import Base
from megooflags_api.settings import get_settings

config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata
```
- Baseline: `uv run alembic revision --rev-id 0001 -m baseline` creates `alembic/versions/0001_baseline.py` with `revision = "0001"`, `down_revision = None`, and `upgrade()` and `downgrade()` that only `pass`, with the docstring "Baseline: empty schema."

`scripts/check_migrations.sh` (chmod +x):
```bash
#!/usr/bin/env bash
# Alembic checks used by CI: single head, upgrade, walk down to base, upgrade again.
set -euo pipefail
cd "$(dirname "$0")/../api"

heads=$(uv run --frozen alembic heads 2>/dev/null | grep -c .)
if [ "$heads" -ne 1 ]; then
  echo "expected exactly 1 alembic head, found $heads" >&2
  uv run --frozen alembic heads >&2
  exit 1
fi

uv run --frozen alembic upgrade head
revisions=$(uv run --frozen alembic history 2>/dev/null | grep -c -- '->')
for _ in $(seq "$revisions"); do
  uv run --frozen alembic downgrade -1
done
if [ -n "$(uv run --frozen alembic current 2>/dev/null)" ]; then
  echo "database not at base after walking down $revisions revisions" >&2
  exit 1
fi
uv run --frozen alembic upgrade head
echo "migrations OK ($revisions revisions)"
```

`api/tests/test_migrations.py`:
- `test_single_head`: `ScriptDirectory.from_config(Config(str(API_DIR / "alembic.ini"))).get_heads() == ["0001"]`. No DB is needed.
- `test_upgrade_downgrade_roundtrip`: skipped when `MEGOOFLAGS_DATABASE_URL` is unset. It runs `subprocess.run(["bash", str(REPO / "scripts/check_migrations.sh")], check=True)`.

## Appendix E: Dockerfiles

`api/Dockerfile`
```dockerfile
# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.9 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="megooflags-api" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="https://github.com/mmhy2003/MegooFlags" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN useradd --system --uid 10001 --no-create-home megooflags
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "megooflags_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
```
Use the uv image tag that matches the local `uv --version` minor. `api/.dockerignore`: `.venv`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `tests`.

`web/Dockerfile`
```dockerfile
# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS base
RUN corepack enable
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml* ./
RUN --mount=type=cache,id=pnpm,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile

FROM base AS build
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

FROM node:22-alpine
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="megooflags-web" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="https://github.com/mmhy2003/MegooFlags" \
      org.opencontainers.image.licenses="Apache-2.0"
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -S -g 10001 megooflags && adduser -S -u 10001 -G megooflags megooflags
COPY --from=build --chown=10001:10001 /app/public ./public
COPY --from=build --chown=10001:10001 /app/.next/standalone ./
COPY --from=build --chown=10001:10001 /app/.next/static ./.next/static
USER 10001
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "server.js"]
```
`web/.dockerignore`: `node_modules`, `.next`, `.env*`, `Dockerfile`.

## Appendix F: web

- Scaffold from the repo root: `pnpm create next-app@16 web --ts --eslint --tailwind --app --no-src-dir --import-alias "@/*" --use-pnpm --turbopack`. Inside a git repo it does not run `git init`. Then `cd web && corepack use pnpm@10`, which writes `packageManager` into `package.json`.
- `package.json` must have `"name": "megooflags-web"`, `"version": "0.1.0"`, `"private": true` and these scripts:
```json
{
  "dev": "next dev",
  "build": "next build",
  "start": "next start",
  "lint": "eslint .",
  "format": "prettier --write .",
  "format:check": "prettier --check .",
  "typecheck": "tsc --noEmit",
  "gen:api": "openapi-typescript ../api/openapi.json -o lib/api/schema.d.ts"
}
```
- With pnpm 10, if `pnpm install` warns about ignored build scripts (for example `sharp`, `unrs-resolver`), add them with `pnpm approve-builds`, so that the frozen install in CI and Docker is quiet and deterministic.
- `next.config.ts`:
```ts
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;
```
- Prettier: `pnpm add -D prettier`. `.prettierrc.json`: `{}`. `.prettierignore`: `.next`, `node_modules`, `pnpm-lock.yaml`, `lib/api/schema.d.ts`, `next-env.d.ts`. ESLint: add the same generated paths to the `ignores` of `eslint.config.mjs` (`.next/**`, `lib/api/schema.d.ts`, `next-env.d.ts`).
- `tsconfig.json`: keep `"strict": true`.
- shadcn: `pnpm dlx shadcn@latest init -d`, then `pnpm dlx shadcn@latest add button`. This creates `components.json`, `lib/utils.ts`, `components/ui/button.tsx` and updates `app/globals.css`.
- `app/page.tsx`, a placeholder:
```tsx
import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-6">
      <h1 className="text-3xl font-semibold">MegooFlags</h1>
      <p className="text-muted-foreground">Open source feature flags. Nothing to see yet.</p>
      <Button asChild variant="outline">
        <a href="https://github.com/mmhy2003/MegooFlags">GitHub</a>
      </Button>
    </main>
  );
}
```
  `app/layout.tsx`: set `metadata = { title: "MegooFlags", description: "Open source feature flag platform" }` and remove the create-next-app boilerplate (Vercel links, SVGs in `public/` other than what the page uses; keep `public/` with a `.gitkeep` so the Dockerfile `COPY public` works).
- Step 9 client: `pnpm add openapi-fetch` and `pnpm add -D openapi-typescript`, then `pnpm gen:api`. `lib/api/client.ts`:
```ts
import createClient from "openapi-fetch";

import type { paths } from "./schema";

export type { components, paths } from "./schema";

/** Typed client for the MegooFlags API. In the browser, the default "" base URL is same-origin through Caddy. */
export function createApiClient(baseUrl = "") {
  return createClient<paths>({ baseUrl });
}

export const api = createApiClient();
```
  `lib/api/check.ts` is a type-only compile check that is part of `tsc --noEmit`:
```ts
import type { components } from "./schema";

// Fails to compile if the generated schema stops matching the API's health contract.
export const healthExample: components["schemas"]["HealthResponse"] = { status: "ok", version: "0.1.0" };
export const readyExample: components["schemas"]["ReadyResponse"] = { status: "ready" };
```

## Appendix G: deploy

`deploy/docker-compose.yml`
```yaml
name: megooflags

services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports:
      - "${MEGOOFLAGS_HTTP_PORT:-80}:80"
      - "443:443"
      - "443:443/udp"
    environment:
      MEGOOFLAGS_SITE_ADDRESS: ${MEGOOFLAGS_SITE_ADDRESS:-:80}
      MEGOOFLAGS_TRUSTED_PROXIES: ${MEGOOFLAGS_TRUSTED_PROXIES:-private_ranges}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      api:
        condition: service_healthy
      web:
        condition: service_healthy

  web:
    image: ghcr.io/mmhy2003/megooflags-web:${MEGOOFLAGS_VERSION:-latest}
    restart: unless-stopped

  migrate:
    image: ghcr.io/mmhy2003/megooflags-api:${MEGOOFLAGS_VERSION:-latest}
    command: ["alembic", "upgrade", "head"]
    environment:
      MEGOOFLAGS_DATABASE_URL: ${MEGOOFLAGS_DATABASE_URL:-postgresql+asyncpg://megooflags:megooflags@db:5432/megooflags}
    depends_on:
      db:
        condition: service_healthy
    restart: "no"

  api:
    image: ghcr.io/mmhy2003/megooflags-api:${MEGOOFLAGS_VERSION:-latest}
    restart: unless-stopped
    environment:
      MEGOOFLAGS_DATABASE_URL: ${MEGOOFLAGS_DATABASE_URL:-postgresql+asyncpg://megooflags:megooflags@db:5432/megooflags}
      MEGOOFLAGS_PUBLIC_URL: ${MEGOOFLAGS_PUBLIC_URL:-}
    depends_on:
      migrate:
        condition: service_completed_successfully

  db:
    image: postgres:16
    restart: unless-stopped
    environment:
      POSTGRES_USER: megooflags
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-megooflags}
      POSTGRES_DB: megooflags
    volumes:
      - db_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U megooflags -d megooflags"]
      interval: 5s
      timeout: 5s
      retries: 20

volumes:
  caddy_data:
  caddy_config:
  db_data:
```

`deploy/Caddyfile`
```caddyfile
{
	servers {
		trusted_proxies static {$MEGOOFLAGS_TRUSTED_PROXIES:private_ranges}
	}
}

{$MEGOOFLAGS_SITE_ADDRESS::80} {
	@api path /api/* /sdk/* /ofrep/* /healthz /readyz
	reverse_proxy @api api:8000 {
		flush_interval -1
	}
	reverse_proxy web:3000
}
```
Validate it with `docker run --rm -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2 caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile`. Also run `caddy fmt` this way and apply its output, so the indentation is tabs.

`deploy/.env.example`
```dotenv
# MegooFlags configuration. Copy to .env next to docker-compose.yml and adjust.

# Image version for web, migrate and api. Defaults to "latest".
# For production, pin an exact release so upgrades are deliberate, e.g. MEGOOFLAGS_VERSION=0.1.0
MEGOOFLAGS_VERSION=latest

# TLS mode. ":80" (default) = plain HTTP behind your own proxy/load balancer.
# A domain (e.g. flags.example.com) = automatic HTTPS with Let's Encrypt on ports 80 and 443.
MEGOOFLAGS_SITE_ADDRESS=:80

# Host port for plain HTTP.
MEGOOFLAGS_HTTP_PORT=80

# Proxies trusted for X-Forwarded-* headers (Caddy syntax). Default: private IP ranges.
MEGOOFLAGS_TRUSTED_PROXIES=private_ranges

# External URL used for links, webhooks and SDK snippets.
MEGOOFLAGS_PUBLIC_URL=http://localhost

# Database. Change the password and keep both lines in sync.
POSTGRES_PASSWORD=megooflags
MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://megooflags:megooflags@db:5432/megooflags
```

`deploy/compose.ci.yml`
```yaml
# Override for CI and local smoke tests: run locally built images on a non-privileged port.
services:
  caddy:
    ports: !override
      - "${MEGOOFLAGS_HTTP_PORT:-8080}:80"
  web:
    image: megooflags-web:ci
    pull_policy: never
  migrate:
    image: megooflags-api:ci
    pull_policy: never
  api:
    image: megooflags-api:ci
    pull_policy: never
```

`scripts/smoke.sh` (chmod +x)
```bash
#!/usr/bin/env bash
# Compose smoke test: start the stack from locally built megooflags-{api,web}:ci images
# and hit the health endpoints and the web root through Caddy.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export MEGOOFLAGS_HTTP_PORT="${MEGOOFLAGS_HTTP_PORT:-8080}"
compose=(docker compose -p megooflags-smoke -f "$root/deploy/docker-compose.yml" -f "$root/deploy/compose.ci.yml")

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    "${compose[@]}" ps -a || true
    "${compose[@]}" logs --no-color || true
  fi
  "${compose[@]}" down -v --remove-orphans || true
  exit "$status"
}
trap cleanup EXIT

"${compose[@]}" up -d --wait --wait-timeout 180 caddy

check() {
  local path=$1 expected=$2 body
  body=$(curl -fsS --retry 10 --retry-delay 2 --retry-all-errors "http://localhost:${MEGOOFLAGS_HTTP_PORT}${path}")
  if ! grep -q -- "$expected" <<<"$body"; then
    echo "GET $path: expected '$expected', got: $body" >&2
    return 1
  fi
  echo "GET $path OK"
}

check /healthz '"status":"ok"'
check /readyz '"status":"ready"'
check / 'MegooFlags'
echo "smoke OK"
```
If `up --wait` reports the finished `migrate` container as a failure on the installed Compose version, drop `--wait --wait-timeout 180` and rely on the `curl --retry` loop. Note the change in the commit message.

## Appendix H: `.github/workflows/ci.yml`

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]
  workflow_call:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

env:
  PYTHON_VERSION: "3.12"
  NODE_VERSION: "22"
  DATABASE_URL_CI: postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags

jobs:
  version:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          enable-cache: true
          cache-dependency-glob: api/uv.lock
      - run: python3 scripts/check_version.py
      - run: uv run --frozen --project api pytest scripts/tests

  api-lint:
    runs-on: ubuntu-24.04
    defaults:
      run:
        working-directory: api
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          enable-cache: true
          cache-dependency-glob: api/uv.lock
      - run: uv sync --frozen
      - run: uv run ruff check . ../scripts
      - run: uv run ruff format --check . ../scripts
      - run: uv run mypy

  api-test:
    runs-on: ubuntu-24.04
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: megooflags
          POSTGRES_PASSWORD: megooflags
          POSTGRES_DB: megooflags
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U megooflags" --health-interval 5s
          --health-timeout 5s --health-retries 20
    env:
      MEGOOFLAGS_DATABASE_URL: ${{ env.DATABASE_URL_CI }}
    defaults:
      run:
        working-directory: api
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          enable-cache: true
          cache-dependency-glob: api/uv.lock
      - run: uv sync --frozen
      - run: uv run pytest -v

  migrations:
    runs-on: ubuntu-24.04
    services:
      postgres:  # same block as api-test
        image: postgres:16
        env:
          POSTGRES_USER: megooflags
          POSTGRES_PASSWORD: megooflags
          POSTGRES_DB: megooflags
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U megooflags" --health-interval 5s
          --health-timeout 5s --health-retries 20
    env:
      MEGOOFLAGS_DATABASE_URL: ${{ env.DATABASE_URL_CI }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          enable-cache: true
          cache-dependency-glob: api/uv.lock
      - run: bash scripts/check_migrations.sh

  web:
    runs-on: ubuntu-24.04
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with:
          package_json_file: web/package.json
      - uses: actions/setup-node@v4
        with:
          node-version: ${{ env.NODE_VERSION }}
          cache: pnpm
          cache-dependency-path: web/pnpm-lock.yaml
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          enable-cache: true
          cache-dependency-glob: api/uv.lock
      - run: pnpm install --frozen-lockfile
      - run: pnpm lint
      - run: pnpm format:check
      - run: pnpm typecheck
      - run: pnpm build
      - name: OpenAPI spec and client drift
        working-directory: .
        run: |
          uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json
          pnpm --dir web gen:api
          git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts \
            || { echo "::error::api/openapi.json or web/lib/api/schema.d.ts is stale. Run the commands in AGENTS.md and commit."; exit 1; }

  images:
    runs-on: ubuntu-24.04
    strategy:
      matrix:
        image: [api, web]
    steps:
      - uses: actions/checkout@v4
      - id: v
        run: echo "version=$(cat VERSION)" >> "$GITHUB_OUTPUT"
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: ${{ matrix.image }}
          push: false
          tags: megooflags-${{ matrix.image }}:ci
          build-args: |
            VERSION=${{ steps.v.outputs.version }}
            REVISION=${{ github.sha }}
          cache-from: type=gha,scope=${{ matrix.image }}
          cache-to: type=gha,mode=max,scope=${{ matrix.image }}

  smoke:
    runs-on: ubuntu-24.04
    needs: images
    steps:
      - uses: actions/checkout@v4
      - id: v
        run: echo "version=$(cat VERSION)" >> "$GITHUB_OUTPUT"
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: api
          load: true
          tags: megooflags-api:ci
          build-args: |
            VERSION=${{ steps.v.outputs.version }}
            REVISION=${{ github.sha }}
          cache-from: type=gha,scope=api
      - uses: docker/build-push-action@v6
        with:
          context: web
          load: true
          tags: megooflags-web:ci
          build-args: |
            VERSION=${{ steps.v.outputs.version }}
            REVISION=${{ github.sha }}
          cache-from: type=gha,scope=web
      - run: bash scripts/smoke.sh
```
The `migrations` job repeats the service block literally, because YAML anchors are not allowed in workflows. Remove the `# same block as api-test` comment if actionlint complains. Validate the file with `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest -color`.

## Appendix I: `.github/workflows/release.yml`

```yaml
name: Release

on:
  push:
    tags:
      - "v[0-9]+.[0-9]+.[0-9]+"
      - "v[0-9]+.[0-9]+.[0-9]+-rc.[0-9]+"

permissions:
  contents: read

env:
  IMAGE_PREFIX: ghcr.io/mmhy2003/megooflags

jobs:
  meta:
    runs-on: ubuntu-24.04
    outputs:
      version: ${{ steps.meta.outputs.version }}
      image_version: ${{ steps.meta.outputs.image_version }}
      tags: ${{ steps.meta.outputs.tags }}
      prerelease: ${{ steps.meta.outputs.prerelease }}
    steps:
      - uses: actions/checkout@v4
      - name: Tag matches VERSION
        run: python3 scripts/check_version.py --tag "$GITHUB_REF_NAME"
      - id: meta
        name: Image tags and release notes
        run: python3 scripts/release_meta.py --tag "$GITHUB_REF_NAME" --notes-out release-notes.md >> "$GITHUB_OUTPUT"
      - uses: actions/upload-artifact@v4
        with:
          name: release-notes
          path: release-notes.md
          retention-days: 1

  ci:
    needs: meta
    uses: ./.github/workflows/ci.yml
    permissions:
      contents: read

  build:
    needs: [meta, ci]
    permissions:
      contents: read
      packages: write
    strategy:
      fail-fast: true
      matrix:
        image: [api, web]
        arch: [amd64, arm64]
        include:
          - arch: amd64
            runner: ubuntu-24.04
          - arch: arm64
            runner: ubuntu-24.04-arm
    runs-on: ${{ matrix.runner }}
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: build
        uses: docker/build-push-action@v6
        with:
          context: ${{ matrix.image }}
          platforms: linux/${{ matrix.arch }}
          build-args: |
            VERSION=${{ needs.meta.outputs.image_version }}
            REVISION=${{ github.sha }}
          outputs: type=image,name=${{ env.IMAGE_PREFIX }}-${{ matrix.image }},push-by-digest=true,name-canonical=true,push=true
          cache-from: type=gha,scope=release-${{ matrix.image }}-${{ matrix.arch }}
          cache-to: type=gha,mode=max,scope=release-${{ matrix.image }}-${{ matrix.arch }}
      - name: Export digest
        env:
          DIGEST: ${{ steps.build.outputs.digest }}
        run: |
          mkdir -p "$RUNNER_TEMP/digests"
          touch "$RUNNER_TEMP/digests/${DIGEST#sha256:}"
      - uses: actions/upload-artifact@v4
        with:
          name: digests-${{ matrix.image }}-${{ matrix.arch }}
          path: ${{ runner.temp }}/digests/*
          if-no-files-found: error
          retention-days: 1

  merge:
    needs: [meta, build]
    runs-on: ubuntu-24.04
    permissions:
      contents: read
      packages: write
    strategy:
      matrix:
        image: [api, web]
    steps:
      - uses: actions/download-artifact@v4
        with:
          pattern: digests-${{ matrix.image }}-*
          merge-multiple: true
          path: ${{ runner.temp }}/digests
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: Create multi-arch manifest
        working-directory: ${{ runner.temp }}/digests
        env:
          IMAGE: ${{ env.IMAGE_PREFIX }}-${{ matrix.image }}
          TAGS: ${{ needs.meta.outputs.tags }}
        run: |
          args=()
          IFS=',' read -ra tags <<<"$TAGS"
          for t in "${tags[@]}"; do args+=(-t "$IMAGE:$t"); done
          sources=()
          for d in *; do sources+=("$IMAGE@sha256:$d"); done
          docker buildx imagetools create "${args[@]}" "${sources[@]}"
      - name: Inspect
        run: docker buildx imagetools inspect "${{ env.IMAGE_PREFIX }}-${{ matrix.image }}:${{ needs.meta.outputs.image_version }}"

  release:
    needs: [meta, merge]
    runs-on: ubuntu-24.04
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with:
          name: release-notes
      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PRERELEASE: ${{ needs.meta.outputs.prerelease }}
        run: |
          flags=()
          if [ "$PRERELEASE" = "true" ]; then flags+=(--prerelease); fi
          gh release create "$GITHUB_REF_NAME" \
            --verify-tag \
            --title "MegooFlags $GITHUB_REF_NAME" \
            --notes-file release-notes.md \
            "${flags[@]}" \
            deploy/docker-compose.yml deploy/Caddyfile deploy/.env.example
```
Fallback when the `ubuntu-24.04-arm` runner is unavailable for the repo (as the design says): set the arm64 row to `runner: ubuntu-24.04` and add `docker/setup-qemu-action@v3` before buildx in `build`. The rest stays the same.

## Appendix J: AGENTS.md outline (write it out in full prose and lists)

1. **Project layout:** `api/`, `web/`, `deploy/`, `scripts/`, `sdks/`, `spec/`. Package managers: uv for Python (`uv.lock`) and pnpm for JS (`pnpm-lock.yaml`). Never switch to pip or npm, and always commit lockfile changes.
2. **Versioning (X.Y.Z):** the table from PRD §8.4 with examples. Major: removing or renaming an API endpoint, an SDK protocol change, a renamed env var, a migration that cannot run automatically. Minor: a new endpoint or a new flag type. Patch: a bug fix, a dependency or base-image bump, a rebuild.
3. **Before 1.0.0 (0.Y.Z):** breaking or feature changes bump Y (`0.3.4` → `0.4.0`), and fixes bump Z (`0.3.4` → `0.3.5`). Breaking changes go at the top of the CHANGELOG entry with upgrade steps. Patch releases never break. Going to 1.0.0 is a maintainer decision.
4. **Bumping:** in one commit, change `VERSION`, `api/pyproject.toml` (`version`), `web/package.json` (`version`) and `CHANGELOG.md` (move `[Unreleased]` items into `## [X.Y.Z] - YYYY-MM-DD`). Then run `cd api && uv lock` (the lock records the project version), `cd api && uv run python -m megooflags_api.export_openapi openapi.json` and `cd web && pnpm gen:api` (the spec embeds the version). Check with `python3 scripts/check_version.py`.
5. **CHANGELOG:** every user-visible change adds a line under `[Unreleased]` in Added / Changed / Fixed / Removed.
6. **Database:** every schema change is an Alembic revision (`cd api && uv run alembic revision --autogenerate -m "<slug>"`) with a working `downgrade()`. Keep one head. Breaking changes use expand/contract across two releases (add the new column or table and dual-write in release N; drop the old one in release N+1), so old and new API replicas can run side by side. Never edit a migration that has been released.
7. **API client:** after any change to API routes or models, regenerate with `cd api && uv run python -m megooflags_api.export_openapi openapi.json` and `cd web && pnpm gen:api`, and commit both files. CI fails on drift.
8. **Local checks (the same as ci.yml):** `python3 scripts/check_version.py`; `uv run --project api pytest scripts/tests`; `cd api && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest` (with Postgres and `MEGOOFLAGS_DATABASE_URL`); `bash scripts/check_migrations.sh`; `cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build`; `docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh`.
9. **Releases:** agents prepare release commits and PRs, but never push `v*` or `sdk-*` tags, never create GitHub releases and never push images. A maintainer tags `vX.Y.Z` (or `vX.Y.Z-rc.N` for a test release, which publishes only the `X.Y.Z-rc.N` image tag and a pre-release). Image tags: `X.Y.Z`, `X.Y`, `X` (from 1.0) and `latest`.
10. **SDKs:** they live in `sdks/<lang>/` with independent versions. Tags are `sdk-<lang>/vX.Y.Z` (for example `sdk-node/v1.2.0`), except Go, which needs `sdks/go/vX.Y.Z` for Go modules. See `sdks/README.md`.

`CLAUDE.md`:
```markdown
@AGENTS.md

## Claude Code notes
- Use `uv run` and `pnpm` from the package directory; do not install tools globally.
- Run the local checks from AGENTS.md before declaring a task done.
- Never run `git tag`, `git push --tags` or `gh release` for this repository.
```

---

## Final verification

From a clean checkout, with Docker running:
```bash
python3 scripts/check_version.py                      # version OK: 0.1.0
python3 scripts/check_version.py --tag v0.1.0-rc.1    # version OK
python3 scripts/check_version.py --tag v0.2.0; echo $?  # 1
uv run --project api pytest scripts/tests
cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest && cd ..
bash scripts/check_migrations.sh                      # needs Postgres + MEGOOFLAGS_DATABASE_URL
cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && cd ..
uv run --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api && git diff --exit-code
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```
Then: open the PR and confirm all CI jobs are green. After merge, a maintainer (not an agent) pushes `v0.1.0-rc.1`. Confirm that `release.yml` succeeds, that `docker buildx imagetools inspect ghcr.io/mmhy2003/megooflags-api:0.1.0-rc.1` (and `-web`) lists linux/amd64 and linux/arm64, that no `0.1` or `latest` tag exists, and that a GitHub pre-release has `docker-compose.yml`, `Caddyfile` and `.env.example` attached. New GHCR packages start private, so the maintainer sets both packages to public once.
