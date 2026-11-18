# MF-87 Plan: launch readiness (docs site, community files, update notices, load tests, GA checklist)

## Goal

Ship a published Starlight docs site in the brand palette with a quick start that takes under five minutes, the usual community files, an update notice for org admins, a load test suite with a committed report against the PRD NFRs, and a GA checklist a maintainer can use to decide on `v1.0.0`.

## Architecture

- **API**: `services/updates.py` asks the GitHub Releases API about once a day from a scheduler job on the leader. It stores the result in the single-row table `update_status` (migration `0016`). The new `routers/system.py` serves `GET /api/system/version`. `MEGOOFLAGS_UPDATE_CHECK=false` turns the check off.
- **Web**: `loadShell()` loads the version. `components/shell/version-footer.tsx` shows it in the sidebar footer, and shows the update pill to `org.manage` holders.
- **Site**: `site/` is a separate pnpm project (Astro Starlight). `scripts/sync.mjs` copies the texts that live elsewhere into the site from a manifest. `docs.yml` deploys it to GitHub Pages, and a `site` CI job checks it.
- **Load tests**: `perf/` is a Go module with the `mfload` program. `scripts/perf.sh` starts size-limited stacks from the existing compose files. `scripts/perf_report.py` renders `perf/REPORT.md`, which the site shows as "Performance and sizing".
- **GA checklist**: `docs/ga-checklist.md`.

## Conventions every step follows

- Branch: the task branch. Commits use the form `<type>(<scope>): <summary> (MF-87)` and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Use one commit per step.
- TDD: write the test first, run it and see it fail for the expected reason, implement, then run it again and see it pass.
- When API routes or schemas change, regenerate `api/openapi.json` and `web/lib/api/schema.d.ts` (AGENTS.md section 8) in the same commit.
- Every user-visible change adds a line under `CHANGELOG.md` `## [Unreleased]` in the right group.
- Never commit `.megoocolab/`, caches, `web/e2e/.auth`, `test-results`, `node_modules`, `site/dist`, `site/.astro` or synced pages.
- API checks: `cd api && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest <files>`. They need Postgres and Valkey as AGENTS.md section 9 shows. Before the API work is done, also run the full `uv run pytest` and `bash scripts/check_migrations.sh`.
- Site checks, from step 6 on: `cd site && pnpm install --frozen-lockfile && pnpm format:check && pnpm check && pnpm test && pnpm build`.
- Pages link only to pages that already exist when the step is done: the link validator fails the build otherwise. Each step that adds pages also adds them to the `sidebar` in `site/astro.config.mjs`.

## File map

| File | Create/Modify | Responsibility |
|---|---|---|
| `api/src/megooflags_api/services/updates.py` | C | Version parsing and comparison, GitHub fetch, due logic, the scheduler job, reading the stored update |
| `api/src/megooflags_api/models/system.py`, `models/__init__.py` | C/M | `UpdateStatus` model |
| `api/alembic/versions/0016_update_status.py` | C | Table plus seed row; downgrade drops it |
| `api/src/megooflags_api/settings.py` | M | `update_check: bool = True` |
| `api/src/megooflags_api/services/scheduler.py` | M | Registers the `update_check` job when the setting is on |
| `api/src/megooflags_api/schemas/system.py`, `routers/system.py`, `main.py` | C/C/M | `GET /api/system/version` |
| `api/tests/test_updates.py`, `test_update_job.py`, `test_system_version.py` | C | API tests |
| `api/tests/helpers.py`, `api/tests/conftest.py`, `api/tests/test_migrations.py` | M | Reset of `update_status`, `MEGOOFLAGS_UPDATE_CHECK=false` default for tests, head `0016` |
| `deploy/docker-compose.yml`, `deploy/docker-compose.control-plane.yml`, `deploy/.env.example`, `scripts/tests/test_compose_files.py` | M | Setting wiring and its test |
| `web/lib/version.ts`, `web/tests/unit/version.test.ts` | C | Label helpers |
| `web/lib/shell-data.ts`, `web/components/shell/app-shell.tsx`, `web/components/shell/version-footer.tsx` | M/M/C | Footer and pill |
| `web/app/(app)/dev/kit/kit-samples.tsx`, `web/e2e/shell.spec.ts`, `web/e2e/kit.spec.ts`, `web/e2e/matrix.spec.ts`, snapshots | M | Pill samples, e2e, masks, baselines |
| `web/lib/api/check.ts`, `api/openapi.json`, `web/lib/api/schema.d.ts` | M | Contract files |
| `site/` (`package.json`, `pnpm-lock.yaml`, `astro.config.mjs`, `tsconfig.json`, `.prettierrc.json`, `.prettierignore`, `.gitignore`, `vitest.config.ts`, `src/content.config.ts`, `src/styles/brand.css`, `src/assets/logo-{light,dark}.svg`, `src/content/docs/**`, `scripts/sync.mjs`, `scripts/sync-lib.mjs`, `sync.manifest.json`, `tests/*.test.ts`) | C | Docs site |
| `docs/*.md` (8 guides) | Move into `site/src/content/docs/` | One home for guides |
| `docs/README.md` | C | Points to the site |
| `docs/examples/proxies/*` | C | nginx, Caddy, Traefik, HAProxy samples |
| `README.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `ROADMAP.md`, `rfcs/README.md`, `rfcs/0000-template.md` | M/C | Community files |
| `scripts/quickstart_test.sh`, `scripts/tests/test_quickstart_doc.py` | C | Quick start timing test |
| `scripts/proxy_samples_test.sh`, `scripts/proxy_stream_check.py`, `scripts/tests/test_proxy_stream_check.py` | C | Proxy sample test |
| `scripts/tests/test_configuration_doc.py` | C | Every setting is documented |
| `.github/workflows/ci.yml`, `.github/workflows/docs.yml` | M/C | `site`, `quickstart`, `proxies`, `perf-smoke` jobs; Pages deploy |
| `perf/` (`go.mod`, `cmd/mfload/main.go`, `internal/{stats,rate,sse,seed,result,api}/*.go` with tests, `results/`, `REPORT.md`) | C | Load tool and committed results |
| `scripts/cluster/lib.sh`, `scripts/cluster_test.sh` | C/M | Shared compose helpers |
| `scripts/perf.sh`, `scripts/perf/*.yml`, `scripts/perf/local_eval.sh` | C | Harness |
| `scripts/perf_report.py`, `scripts/tests/test_perf_report.py` | C | Merge and render |
| `scripts/bench_sdk_evaluate.py`, `scripts/tests/test_bench.py` | Delete | Replaced by `mfload evaluate` |
| `docs/ga-checklist.md` | C | GA checklist |
| `AGENTS.md`, `CHANGELOG.md`, `cli/README.md` | M | Layout, checks, references, entries |

## Shared contracts

### `services/updates.py`

```python
RELEASES_URL = "https://api.github.com/repos/mmhy2003/MegooFlags/releases/latest"
RELEASE_URL = "https://github.com/mmhy2003/MegooFlags/releases/tag/v{version}"
CHECK_EVERY = timedelta(hours=24)
RETRY_AFTER = timedelta(hours=1)
MAX_BODY_BYTES = 1_048_576
TIMEOUT_SECONDS = 10.0
_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

class UpdateCheckError(Exception): ...

def parse_release_tag(tag: object) -> str | None       # "v1.2.3" -> "1.2.3"; anything else, including "v1.2.3-rc.1", -> None
def version_key(version: str) -> tuple[Any, ...] | None # SemVer 2.0 precedence for X.Y.Z[-pre][+build]; None if unparseable
def is_newer(candidate: str, running: str) -> bool      # False when either side does not parse
def release_url(version: str) -> str
def check_due(status: UpdateStatus, now: datetime) -> bool
async def fetch_latest(client: httpx.AsyncClient, *, running: str) -> str | None
    # GET RELEASES_URL with headers User-Agent "MegooFlags/<running>" and Accept "application/vnd.github+json" only.
    # 404 -> None. Non-200, timeout, network error, body > MAX_BODY_BYTES (streamed and counted),
    # bad JSON or a tag that does not parse -> UpdateCheckError.
async def run_update_check(sessionmaker, now, *, transport: httpx.AsyncBaseTransport | None = None,
                           running: str = __version__) -> JobResult
async def available_update(session: AsyncSession, running: str) -> str | None  # stored latest_version if is_newer, else None
```

`check_due`: when `attempted_at is None`, return True. `failed = checked_at is None or attempted_at > checked_at`. Return `now - attempted_at >= (RETRY_AFTER if failed else CHECK_EVERY)`.

`run_update_check`: load row 1. If it is not due, return `JobResult()`. Otherwise set `attempted_at = now`, then fetch with `httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS)`. On success, set `checked_at = now`, set `latest_version` to the version when it is not None (a 404 leaves it as it is), and return `JobResult(ok=1)`. On `UpdateCheckError`, log a warning without a traceback, keep `latest_version` and `checked_at`, and return `JobResult(failed=1)`. Commit in both cases.

### `update_status` table (migration `0016`, `models/system.py`)

`id SMALLINT PK CHECK (id = 1)` (constraint name `single_row`, as in `OrgSettings`), `latest_version VARCHAR(50) NULL`, `checked_at TIMESTAMPTZ NULL`, `attempted_at TIMESTAMPTZ NULL`. The upgrade inserts row `{"id": 1}`. The downgrade drops the table.

### `GET /api/system/version`

Schemas in `schemas/system.py`: `UpdateOut(version: str, url: str)` and `SystemVersionOut(version: str, update: UpdateOut | None)`. The guard is `authenticated()`. `update` is set only when `settings.update_check` is true, `authz.authorizer.can(Permission.ORG_MANAGE)` holds, and `available_update(...)` returns a version.

### Web

```ts
// web/lib/version.ts
export type SystemVersion = components["schemas"]["SystemVersionOut"];
export function versionLabel(version: string): string;            // "1.0.0" -> "v1.0.0"
export function updateLabel(update: { version: string }): string;  // "Update: 1.1.0"
export function updateTooltip(update: { version: string }): string; // "Update available: 1.1.0"
// ShellData gains: version: SystemVersion | null
// components/shell/version-footer.tsx
export function UpdatePill(props: { update: { version: string; url: string }; collapsed: boolean }): JSX.Element;
export function VersionFooter(props: { version: SystemVersion | null }): JSX.Element | null;
```

### Site slugs and sidebar (final state)

- **Start here**: `start/introduction`, `start/quick-start`, `start/concepts`.
- **Deploy**: `deploy/single-node`, `deploy/tls`, `deploy/reverse-proxy`, `deploy/multi-node`, `deploy/multi-node-migration`, `deploy/upgrades`, `deploy/configuration`, `deploy/observability`, `deploy/security`, `deploy/performance` (synced from `perf/REPORT.md`).
- **Use**: `use/release-workflows`, `use/flags-as-code`, `use/integrations`, `use/cli`, `use/terraform` (synced).
- **SDKs**: `sdks/overview`, then `sdks/{go,node,python,java,dotnet,ruby,php,js,react,react-native,ios,android,flutter}` (synced from `sdks/<lang>/README.md`).
- **Reference**: REST API (the `starlight-openapi` base `reference/api`), `reference/evaluation` and `reference/delivery` (synced from `spec/evaluation.md` and `spec/delivery.md`).
- **Migrate**: `migrate/overview`, `migrate/unleash`, `migrate/flagsmith`, `migrate/launchdarkly`.
- **Project**: `project/roadmap`, `project/contributing`, `project/rfcs`, `project/code-of-conduct`, `project/security-policy` (synced), `project/privacy` (committed), `project/release-notes` (synced from `CHANGELOG.md`).

### `site/sync.manifest.json`

```json
[{ "source": "sdks/go/README.md", "dest": "sdks/go.md", "title": "Go", "description": "Server SDK for Go: install, configure and evaluate flags." }]
```

`sync.mjs` reads the manifest. For each entry it fails (exit 1, printing the path) if `source` is missing. Otherwise it removes the first `# ` heading, writes front matter (`title`, `description`, `editUrl: https://github.com/mmhy2003/MegooFlags/edit/main/<source>`), rewrites links with `rewriteLinks`, and writes `src/content/docs/<dest>`.

`rewriteLinks(markdown, sourcePath, manifest)` in `scripts/sync-lib.mjs` applies these rules to Markdown links `[t](x)` outside fenced code:

1. Links with a scheme (`https:`, `mailto:`) and `#anchor`-only links stay as they are.
2. Otherwise resolve `x` against the source file's directory to a repository path, keeping the `#anchor`.
3. If that path, or that path plus `/README.md`, is a manifest `source`, the link becomes `/MegooFlags/<dest without .md>/` plus the anchor.
4. Otherwise it becomes `https://github.com/mmhy2003/MegooFlags/blob/main/<path>` for a file or `.../tree/main/<path>` for a directory, plus the anchor.

Every `dest` is listed in `site/.gitignore`. A test checks this with `git check-ignore`.

### Load test result format (`perf/results/<version>.json`)

```json
{
  "version": "0.1.0", "profile": "full", "started_at": "2026-11-20T10:00:00Z",
  "host": {"cpus": 16, "memory_mib": 64215, "cpu_model": "...", "kernel": "...", "docker": "..."},
  "scenarios": {
    "remote_ofrep": {"status": "full", "reduced_to": null, "limits": {"api": "unlimited"},
                     "metrics": {"rps": 2000, "count": 120000, "errors": 0, "p50_ms": 3.1, "p95_ms": 9.0, "p99_ms": 14.2, "max_ms": 80.0},
                     "notes": ""}
  }
}
```

- Scenario keys: `local_eval_{go,node,python,java,dotnet,ruby,php}`, `remote_ofrep`, `remote_evaluate`, `propagation_replicas`, `propagation_cluster`, `flags_10k`, `streams_100k`, `footprint_single`.
- `status` is one of `full`, `reduced` or `skipped`.
- Each `mfload` command writes one scenario object: `{"metrics": {...}, "errors": n}`. `perf.sh` adds `status`, `limits` and `notes`.

## Steps

### 1. API: version parsing and the GitHub release fetch

**Files:** create `api/src/megooflags_api/services/updates.py` (only the pure functions and `fetch_latest` from "Shared contracts" for now) and `api/tests/test_updates.py`.

**Test first** (`api/tests/test_updates.py`, no database, no network). Use `httpx.MockTransport` handlers:

- `test_parse_release_tag`: `"v1.2.3"` gives `"1.2.3"`. `"1.2.3"`, `"v1.2"`, `"v01.2.3"`, `"v1.2.3-rc.1"`, `"v1.2.3+b"`, `None` and `5` give `None`.
- `test_is_newer`: `("1.1.0", "1.0.0")` is True; `("1.0.0", "1.0.0")` is False; `("1.0.0", "1.1.0")` is False; `("1.1.0", "1.1.0-rc.1")` is True; `("1.0.10", "1.0.9")` is True; `("1.0.0", "garbage")` is False.
- `test_version_key_prerelease_order`: `1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-beta < 1.0.0-rc.1 < 1.0.0`.
- `test_fetch_latest_ok`: `{"tag_name": "v1.4.0"}` gives `"1.4.0"`. The handler asserts the URL equals `RELEASES_URL`, the `User-Agent` equals `MegooFlags/1.0.0`, and the request has no body and no query.
- `test_fetch_latest_404_is_none`.
- `test_fetch_latest_errors` (parametrized): status 500; status 403; a body that is not JSON; `{"tag_name": "v1.4.0-rc.1"}`; `{}`; a body of `MAX_BODY_BYTES + 1` bytes; `httpx.ConnectError` raised in the handler; `httpx.ReadTimeout`. Each raises `UpdateCheckError`.
- `test_release_url`: `release_url("1.1.0") == "https://github.com/mmhy2003/MegooFlags/releases/tag/v1.1.0"`.

**Run** `cd api && uv run pytest tests/test_updates.py -q`. It fails with `ModuleNotFoundError: megooflags_api.services.updates`.

**Implement** the contract.

- Parse the version with a regex `^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$`.
- The key is `(major, minor, patch, 1, ())` for a release and `(major, minor, patch, 0, ids)` for a pre-release. Each id is `(0, int)` when numeric and `(1, str)` otherwise.
- `fetch_latest` uses `client.stream("GET", ...)`, counts bytes from `aiter_bytes()`, and raises once the count passes the limit.
- Wrap `httpx.HTTPError`, `json.JSONDecodeError` and `UnicodeDecodeError` in `UpdateCheckError`.

**Run again**: pass. Also run ruff, format and mypy.

**Commit:** `feat(api): parse and fetch the latest MegooFlags release (MF-87)`.

### 2. API: `update_status` table (migration 0016) and model

**Files:**

- Create `api/src/megooflags_api/models/system.py` with `class UpdateStatus(Base)`: `__tablename__ = "update_status"`, `__table_args__ = (CheckConstraint("id = 1", name="single_row"),)`, columns as in "Shared contracts", nullable `DateTime(timezone=True)` and `String(50)`. Export it from `models/__init__.py` and `__all__`.
- Create `api/alembic/versions/0016_update_status.py` with revision `"0016"` and down revision `"0015"`. Follow the header style of `0015_cluster.py` and the `op.f("ck_update_status_single_row")` and `op.f("pk_update_status")` naming of `0004_identity.py`. Insert the seed row with `op.bulk_insert`. The downgrade runs `op.drop_table("update_status")`.
- Modify `api/tests/test_migrations.py`: `script.get_heads() == ["0016"]`.
- Modify `api/tests/helpers.py`: add `"UPDATE update_status SET latest_version = NULL, checked_at = NULL, attempted_at = NULL"` to `RESET_SQL`.

**Test first:** change `test_single_head` to expect `"0016"`. Run `uv run pytest tests/test_migrations.py -q`: it fails with `['0015'] != ['0016']`.

**Implement**, then run `uv run pytest tests/test_migrations.py tests/test_models_migration.py -q` (the roundtrip and `alembic check` need Postgres) and `bash scripts/check_migrations.sh`. Both pass.

**Commit:** `feat(api): update_status table for release checks (MF-87)`.

### 3. API: update check scheduler job and `MEGOOFLAGS_UPDATE_CHECK`

**Files:**

- `settings.py`: add `update_check: bool = True`, with a comment saying it asks GitHub once a day for the newest release and sends no instance data.
- `services/updates.py`: add `check_due`, `run_update_check` and `available_update` (contract above).
- `services/scheduler.py` `default_jobs`: append `Job("update_check", partial(run_update_check), every=timedelta(minutes=5))` only when `settings is not None and settings.update_check`. Put it last. The job decides itself whether a request is due, from the stored times.
- `api/tests/conftest.py`: at import, `os.environ.setdefault("MEGOOFLAGS_UPDATE_CHECK", "false")`, so apps built by other tests never reach GitHub. Comment why.
- Deploy files: add `MEGOOFLAGS_UPDATE_CHECK: ${MEGOOFLAGS_UPDATE_CHECK:-true}` after `MEGOOFLAGS_WEBHOOK_RETENTION_DAYS` in the `api` environment of `deploy/docker-compose.yml` and `deploy/docker-compose.control-plane.yml` (identical lines, so `test_control_plane_file_matches_single_node` holds). In `deploy/.env.example`, add a commented block: "Update notices: about once a day the server asks the public GitHub Releases API for the newest MegooFlags release. Nothing about this instance is sent. Set to false to turn it off." followed by `MEGOOFLAGS_UPDATE_CHECK=true`. The worker files do not change, because workers have no scheduler.

**Tests first:**

- `api/tests/test_update_job.py` (DB, `migrated`/`app` fixtures, `FakeClock`, a `MockTransport` that counts calls and serves a configurable answer). Call `run_update_check(app.state.sessionmaker, clock(), transport=t, running="1.0.0")` directly:
  - `test_first_run_fetches_and_stores`: one call; the row has `latest_version == "1.1.0"` and `checked_at == attempted_at == now`; `JobResult.ok == 1`.
  - `test_not_again_within_24h`: advance 23 h 59 m: no call. Advance to 24 h: one more call.
  - `test_failure_retries_after_1h_and_keeps_last_version`: success, then advance 24 h with a 500 answer: `failed == 1` and the version stays. Advance 59 m: no call. Advance to 1 h: a call.
  - `test_404_is_success_without_version`.
  - `test_available_update`: stored `1.1.0` and running `1.0.0` give `"1.1.0"`; running `1.1.0` gives `None`; running `1.1.0-rc.1` gives `"1.1.0"`; nothing stored gives `None`.
- In `api/tests/test_scheduler.py`:
  - `test_update_check_job_follows_setting`: `"update_check" in names(default_jobs(Settings(update_check=True)))`; not in `default_jobs(Settings(update_check=False))`; not in `default_jobs()`.
- In `scripts/tests/test_compose_files.py`:
  - `test_update_check_passed_to_api`: the line is in `_api_environment` of `docker-compose.yml`.
  - `test_env_example_lists_update_check`: the line is in `deploy/.env.example`.

**Run** `uv run pytest tests/test_update_job.py tests/test_scheduler.py -q` and `uv run --frozen --project api pytest scripts/tests/test_compose_files.py -q`. They fail with ImportError or missing names.

**Implement.** Run again: pass. Then run the full `uv run pytest` and report the counts.

**CHANGELOG** `### Added`: "Update notices: about once a day the server checks GitHub for a newer MegooFlags release and org admins see it in the sidebar. No data about your instance is sent. Turn it off with `MEGOOFLAGS_UPDATE_CHECK=false`."

**Commit:** `feat(api): daily update check job with MEGOOFLAGS_UPDATE_CHECK (MF-87)`.

### 4. API: `GET /api/system/version`

**Files:**

- Create `api/src/megooflags_api/schemas/system.py` and `api/src/megooflags_api/routers/system.py`: `router = APIRouter(prefix="/system", tags=["system"], responses=ERROR_RESPONSES)` and `Caller = Annotated[Authz, Depends(authenticated())]`. `@router.get("/version") async def system_version(request: Request, session: SessionDep, authz: Caller) -> SystemVersionOut`. Its docstring: "The running version, and a newer release for org admins when the update check is on."
- Register it in `main.py`'s `for module in (...)` list (with `get_principal`).
- Regenerate `api/openapi.json` and `web/lib/api/schema.d.ts`. Add `systemVersionExample: components["schemas"]["SystemVersionOut"] = { version: "1.0.0", update: { version: "1.1.0", url: "https://github.com/mmhy2003/MegooFlags/releases/tag/v1.1.0" } }` to `web/lib/api/check.ts`.

**Tests first** (`api/tests/test_system_version.py`):

- `test_admin_sees_update`: write `latest_version="9.9.9"` to row 1, call with the `api` fixture (org admin token). Expect 200, `version == __version__`, and `update == {"version": "9.9.9", "url": ".../tag/v9.9.9"}`.
- `test_viewer_sees_no_update`: a token from `seed_service_token(app, "viewer")` gets `update` null and the version present.
- `test_no_update_when_not_newer`: stored `"0.0.1"` gives null.
- `test_check_off_hides_stored_update`: build the app with `create_app(Settings(update_check=False))` (as `client_for` does), store `9.9.9`, admin token: `update` is null.
- `test_needs_credentials`: the `anon` fixture gets 401.

`test_route_permissions.py` must keep passing unchanged.

**Run:** `uv run pytest tests/test_system_version.py tests/test_route_permissions.py tests/test_openapi.py -q`. The new tests fail with 404.

**Implement**, run again and pass. Run the drift check: `git diff --exit-code` on the regenerated files after committing them. Run `cd web && pnpm typecheck`.

**CHANGELOG** `### Added`: "`GET /api/system/version`: the running version and, for org admins, a newer release when one exists."

**Commit:** `feat(api): GET /api/system/version (MF-87)`.

### 5. Web: version and update pill in the sidebar footer

**Files:** `web/lib/version.ts` (contract), `web/tests/unit/version.test.ts`, `web/lib/shell-data.ts`, `web/components/shell/version-footer.tsx`, `web/components/shell/app-shell.tsx`, `web/app/(app)/dev/kit/kit-samples.tsx`, `web/e2e/shell.spec.ts`, `web/e2e/kit.spec.ts`, `web/e2e/matrix.spec.ts`, and the matrix snapshots.

**Unit test first** (`web/tests/unit/version.test.ts`):

- `versionLabel("1.0.0") === "v1.0.0"`.
- `updateLabel({version: "1.1.0"}) === "Update: 1.1.0"`.
- `updateTooltip(...) === "Update available: 1.1.0"`.

Run `cd web && pnpm test -- version`: it fails because the module is not found. Implement `version.ts`, run again: pass.

**Shell:**

- `loadShell()` adds `version: SystemVersion | null`. For an active session it loads `orNull("version", api.GET("/api/system/version"))`, otherwise `null`.
- `VersionFooter` renders nothing when `version` is null. Otherwise it renders:
  - `<span data-testid="app-version" className="text-xs text-muted-foreground">` with `versionLabel`. It is hidden when the sidebar is collapsed to icons: `group-data-[collapsible=icon]:hidden`.
  - When `version.update` is set, `<UpdatePill update={...} collapsed={state === "collapsed" && !isMobile} />`, using `useSidebar()`.
- `UpdatePill` is an `<a href={update.url} target="_blank" rel="noopener noreferrer">` with the lucide `CircleArrowUp` icon (`aria-hidden`) and the text `updateLabel` plus visually hidden "(opens in a new tab)". Its classes are `min-h-11 inline-flex items-center gap-1.5 rounded-full px-3` in the primary or accent tokens (no hex; `no-hex.test.ts` checks this).
- When `collapsed`, it is a 44 px (`size-11`) icon link with `aria-label={updateTooltip(update) + " (opens in a new tab)"}`, wrapped in the existing `Tooltip` from `components/ui/tooltip` with the content `updateTooltip(update)`.
- In `AppShell`'s `<SidebarFooter>`, render `<VersionFooter version={shell.version} />` above `<CollapseButton />`.
- Kit page: add a "Update pill" section with `UpdatePill` open and collapsed for `{version: "1.1.0", url: "https://github.com/mmhy2003/MegooFlags/releases/tag/v1.1.0"}`.

**E2E:**

- `shell.spec.ts`: "the admin and a viewer see the running version": `page.getByTestId("app-version")` matches `/^v\d+\.\d+\.\d+/` for the admin and for `VIEWER` (storageState). The e2e API has `MEGOOFLAGS_UPDATE_CHECK` unset, so it is on, but there is no stored version, so no pill shows. Assert `getByRole("link", { name: /Update:/ })` has count 0.
- `kit.spec.ts`: "the update pill has a label, an icon and a new-tab link" checks `target="_blank"`, the accessible name and a box height of at least 44.
- `matrix.spec.ts`: add `page.getByTestId("app-version")` to the `mask` list for every page.
- Refresh the baselines only with `cd web && pnpm e2e:docker --update-snapshots`, and commit the changed PNGs.

**Run:** `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build`, then `pnpm e2e` with the API as AGENTS.md section 9 shows. All pass.

**CHANGELOG:** covered by step 3's line.

**Commit:** `feat(web): running version and update pill in the sidebar footer (MF-87)`.

### 6. Site: Starlight scaffold, brand theme, logo and palette test

**Before coding:** look up with Context7 (`/withastro/starlight`) and on npm the current versions of `astro`, `@astrojs/starlight`, `starlight-links-validator` and `starlight-openapi`, and confirm their peer ranges agree. Pin carets to those majors.

**Files** (new pnpm project `site/`):

- `package.json`:
  - `"name": "megooflags-docs"`, `"private": true`, `"type": "module"`, `"packageManager": "pnpm@10.34.9"`, `"engines": {"node": ">=22"}`.
  - Scripts: `"sync": "node scripts/sync.mjs"` (for now a stub that exits 0, until step 8), `"dev": "pnpm sync && astro dev"`, `"build": "pnpm sync && astro build"`, `"check": "pnpm sync && astro check"`, `"test": "vitest run"`, `"format:check": "prettier --check ."`, `"format": "prettier --write ."`.
  - Dependencies: `astro`, `@astrojs/starlight`, `starlight-links-validator`, `@fontsource-variable/geist`, `@fontsource-variable/geist-mono`, `sharp`. Dev dependencies: `@astrojs/check`, `typescript`, `prettier`, `prettier-plugin-astro`, `vitest`, `@types/node`.
  - Commit `pnpm-lock.yaml`.
- `astro.config.mjs`:
  - `site: "https://mmhy2003.github.io"`, `base: "/MegooFlags"`, `trailingSlash: "always"`.
  - `starlight({ title: "MegooFlags", logo: { light: "./src/assets/logo-light.svg", dark: "./src/assets/logo-dark.svg", replacesTitle: true }, social: [{ icon: "github", label: "GitHub", href: "https://github.com/mmhy2003/MegooFlags" }], editLink: { baseUrl: "https://github.com/mmhy2003/MegooFlags/edit/main/site/" }, customCss: ["@fontsource-variable/geist", "@fontsource-variable/geist-mono", "./src/styles/brand.css"], plugins: [starlightLinksValidator()], sidebar: [] })`. The sidebar starts empty; later steps fill it.
- `src/content.config.ts`: `docs: defineCollection({ loader: docsLoader(), schema: docsSchema() })`.
- `tsconfig.json`: extends `astro/tsconfigs/strict`.
- `.prettierrc.json`: `{"plugins": ["prettier-plugin-astro"]}`.
- `.prettierignore`: `dist`, `.astro`, `node_modules`, `pnpm-lock.yaml`, and the synced paths.
- `.gitignore`: `dist/`, `.astro/`, `node_modules/`.
- `vitest.config.ts`: `test.include: ["tests/**/*.test.ts"]`.
- `src/styles/brand.css`:
  - Two token blocks. `:root` holds the dark values and `:root[data-theme="light"]` the light values. Each declares `--mf-background`, `--mf-foreground`, `--mf-card`, `--mf-muted`, `--mf-muted-foreground`, `--mf-primary`, `--mf-secondary`, `--mf-accent`, `--mf-border`, `--mf-success`, `--mf-warning`, `--mf-destructive`, copied from `web/app/globals.css` (`.dark` and `:root`).
  - One mapping block on both selectors: `--sl-color-accent-low: var(--mf-muted); --sl-color-accent: var(--mf-primary); --sl-color-accent-high: var(--mf-primary); --sl-color-white: var(--mf-foreground); --sl-color-gray-1: var(--mf-foreground); --sl-color-gray-2: var(--mf-foreground); --sl-color-gray-3: var(--mf-muted-foreground); --sl-color-gray-4: var(--mf-muted-foreground); --sl-color-gray-5: var(--mf-border); --sl-color-gray-6: var(--mf-card); --sl-color-gray-7: var(--mf-muted); --sl-color-black: var(--mf-background); --sl-font: "Geist Variable", sans-serif; --sl-font-mono: "Geist Mono Variable", monospace;`.
- `src/assets/logo-light.svg` and `logo-dark.svg`: the app's diamond from `web/components/shell/brand.tsx` (outer `M16 2 30 16 16 30 2 16Z` in accent, inner `M16 8 24 16 16 24 8 16Z` in primary) plus a "MegooFlags" wordmark in the foreground colour, with literal hex values. Light uses `#7a1fd6`, `#c8006a` and `#1a1033`; dark uses `#b026ff`, `#ff2e97` and `#ede7ff`. Use `viewBox="0 0 168 32"`.
- `src/content/docs/index.mdx`: `template: splash` with a hero (title "MegooFlags", tagline "Open-source feature flags you host yourself: targeting, rollouts and OpenFeature SDKs.", one action "GitHub"; the Quick start action is added in step 11) and a `CardGrid` of four `Card`s (Self-hosted, OpenFeature SDKs, Release workflows, Multi-node).
- `tests/palette.test.ts`:
  - Parse `../web/app/globals.css` (`:root` gives light, `.dark` gives dark) and `src/styles/brand.css` (by selector).
  - Every `--mf-<name>` in each brand block equals `--<name>` in the matching globals block.
  - Every hex in both logo SVGs is one of the palette values of its theme.
  - Write the test first. Run `cd site && pnpm install && pnpm test`: it fails on missing files. Then add the files.

**AGENTS.md:**

- Layout table: add a row "`site/` | Docs site (Astro Starlight), published to GitHub Pages; the guides live in `site/src/content/docs/`".
- Package-manager list: add `site/` to pnpm.
- Section 9: add "# docs site" with `(cd site && pnpm install --frozen-lockfile && pnpm format:check && pnpm check && pnpm test && pnpm build)`.

**Run** the site checks and pass. **Commit:** `feat(site): Starlight docs site in the brand palette (MF-87)`.

### 7. Community files: README, CONTRIBUTING, code of conduct, SECURITY, ROADMAP, RFC process

**`README.md`** (keep the Development, Versioning and Repository layout sections; add `site/`, `perf/` and `rfcs/` to the layout block):

- At the top, a `<picture>` with `<source media="(prefers-color-scheme: dark)" srcset="site/src/assets/logo-dark.svg">` and an `<img src="site/src/assets/logo-light.svg" alt="MegooFlags" height="48">`.
- Badges, using `labelColor=1a1033` and `color=c8006a` where shields allows a colour:
  - CI: `https://img.shields.io/github/actions/workflow/status/mmhy2003/MegooFlags/ci.yml?branch=main&label=CI&labelColor=1a1033`, linked to the workflow.
  - Release: `https://img.shields.io/github/v/release/mmhy2003/MegooFlags?include_prereleases&labelColor=1a1033&color=c8006a`.
  - License: `https://img.shields.io/badge/license-Apache--2.0-007a87?labelColor=1a1033`.
  - Docs: `https://img.shields.io/badge/docs-mmhy2003.github.io%2FMegooFlags-7a1fd6?labelColor=1a1033`, linked to `https://mmhy2003.github.io/MegooFlags/`.
  - OpenFeature: `https://img.shields.io/badge/OpenFeature-providers-c8006a?labelColor=1a1033`, linked to `https://openfeature.dev`.
- The Quick start shrinks to four commands, the same as step 11, plus "Full quick start: https://mmhy2003.github.io/MegooFlags/start/quick-start/".
- A "Community" list linking `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `ROADMAP.md` and `rfcs/README.md`.
- The `docs/*.md` links stay until step 9.

**`CONTRIBUTING.md`:**

- Welcome; ways to help (issues, discussions, RFCs for big changes).
- Setup (prerequisites from README Development).
- Branches: start from `origin/develop`, pull requests target `develop`, merge commits only (AGENTS.md section 2).
- The checks to run: link AGENTS.md section 9 and list the per-area one-liners.
- TDD expectation; CHANGELOG rule (section 6); migration rules (section 7, expand/contract); regenerating the API client (section 8); commit message style `type(scope): summary`.
- "No CLA and no sign-off: by contributing you agree your contribution is licensed under Apache-2.0, like the rest of the project."
- Where releases are described (sections 10 to 12).

**`CODE_OF_CONDUCT.md`:**

- The full Contributor Covenant 2.1 text, unedited except for the Enforcement contact paragraph.
- That paragraph says: report through GitHub's "Report content" on the comment, issue or pull request, or privately through a security advisory at `https://github.com/mmhy2003/MegooFlags/security/advisories/new` (title it "Code of conduct report"). Maintainers review every report promptly and fairly. No email address.

**`SECURITY.md`:**

- Supported versions: the latest release; before 1.0.0, only the latest 0.Y.Z.
- Report privately through GitHub private vulnerability reporting (the advisories link). Do not open public issues.
- What to include; the expected first response, "within 7 days"; coordinated disclosure.
- A link to the site's security page (added in step 9). Use the absolute URL `https://mmhy2003.github.io/MegooFlags/deploy/security/`.

**`ROADMAP.md`:**

- From the PRD release plan (KB "PRD: MegooFlags — Open Source Feature Flag Platform", section 11; read it with `kb_search`/`kb_read`).
- "Done for GA": the GA row items. "Phase 2": the Phase 2 row. "How to propose items": an issue for small things, an RFC for big ones.
- Telemetry is not on the roadmap: MegooFlags collects no usage data.

**`rfcs/README.md`:**

- When an RFC is needed: new public API or SDK protocol changes, breaking changes, new deployment topologies, large UI flows.
- Process: copy `0000-template.md` to `rfcs/0000-short-title.md`; open a pull request into `develop` titled `RFC: ...`; discussion happens on the pull request, for at least 7 days; a maintainer decides, accepted or declined, and records it in the file's Status; on merge the maintainer gives it the next free number (the pull request number is fine) and renames the file.
- An index table: empty for now.

**`rfcs/0000-template.md`** has these sections: Summary, Motivation, Detailed design, Compatibility and migration, Alternatives, Unresolved questions, and a Status line (`Proposed | Accepted | Declined`).

**Test:** `scripts/tests/test_community_files.py`:

- Each of the files exists.
- `CODE_OF_CONDUCT.md` contains "Contributor Covenant" and "version 2.1", contains the advisories URL, and contains no `@` email address (regex `[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}`).
- README contains the five badge URLs and the site URL.

Write the test first; it fails because the files are missing. Add the files; it passes.

**Run** `uv run --frozen --project api pytest scripts/tests/test_community_files.py -q`. **Commit:** `docs: community files, RFC process and roadmap (MF-87)`.

### 8. Site: sync script and synced pages

**Files:** `site/scripts/sync-lib.mjs` (exports `rewriteLinks`, `stripTitle`, `frontMatter`, `loadManifest`), `site/scripts/sync.mjs` (replaces the stub), `site/sync.manifest.json`, `site/tests/sync.test.ts`, and `site/.gitignore`/`.prettierignore` entries.

**Tests first** (`site/tests/sync.test.ts`), with a fake manifest containing `sdks/js/README.md -> sdks/js.md` and `spec/evaluation.md -> reference/evaluation.md`:

- `rewriteLinks("[x](../js/README.md#bootstrap)", "sdks/react/README.md", m)` gives `[x](/MegooFlags/sdks/js/#bootstrap)`.
- `[x](../js)` from `sdks/react/README.md` gives `/MegooFlags/sdks/js/`.
- `[x](../../AGENTS.md)` from `sdks/go/README.md` gives `https://github.com/mmhy2003/MegooFlags/blob/main/AGENTS.md`.
- `[x](conformance/)` from `spec/README.md` gives `.../tree/main/spec/conformance`.
- `[x](LICENSE)` from `sdks/go/README.md` gives `.../blob/main/sdks/go/LICENSE`.
- `https://...` and `#anchor` links are unchanged.
- Links inside fenced code blocks are unchanged.
- `stripTitle` removes only the first `# ` line.
- Front matter contains `title`, `description` and `editUrl`.
- Running `sync.mjs` with a manifest entry whose source is missing exits 1 and names the path.
- Every `dest` in the real manifest is ignored: `git check-ignore -q site/src/content/docs/<dest>` exits 0.

**Run** `cd site && pnpm test`: it fails because the module is not found. **Implement**, then run again: pass.

**Real manifest** (dest, then title):

- `sdks/<lang>/README.md` for all 13 SDKs to `sdks/<lang>.md`, titled "Go", "Node.js", "Python", "Java", ".NET", "Ruby", "PHP", "JavaScript (browser)", "React", "React Native", "iOS (Swift)", "Android (Kotlin)", "Flutter".
- `spec/evaluation.md` to `reference/evaluation.md` ("Evaluation spec"); `spec/delivery.md` to `reference/delivery.md` ("Delivery protocol").
- `terraform/README.md` to `use/terraform.md` ("Terraform").
- `CHANGELOG.md` to `project/release-notes.md` ("Release notes").
- `ROADMAP.md` to `project/roadmap.md`; `CONTRIBUTING.md` to `project/contributing.md`; `rfcs/README.md` to `project/rfcs.md`; `CODE_OF_CONDUCT.md` to `project/code-of-conduct.md`; `SECURITY.md` to `project/security-policy.md`.

Add each dest to `.gitignore` and `.prettierignore`. Add the sidebar groups SDKs (13 synced pages; Overview comes in step 15), Reference (evaluation, delivery), Use (terraform) and Project (the six synced pages), in the order of "Shared contracts".

If the link validator flags a link inside a synced file whose target is a heading anchor that Starlight slugs differently, fix the source README's anchor, not the copy.

**Run** the site checks and `git status`; no synced copy shows as untracked. **Commit:** `feat(site): sync SDK, spec and project pages into the site (MF-87)`.

### 9. Site: move the guides from docs/ and update references

**Move with `git mv`, so history follows:**

- `docs/release-workflows.md` to `site/src/content/docs/use/release-workflows.md`
- `docs/flags-as-code.md` to `use/flags-as-code.md`
- `docs/integrations.md` to `use/integrations.md`
- `docs/cli.md` to `use/cli.md`
- `docs/multi-node.md` to `deploy/multi-node.md`
- `docs/multi-node-migration.md` to `deploy/multi-node-migration.md`
- `docs/security.md` to `deploy/security.md`
- `docs/importers.md` to `migrate/overview.md` (title "Migrate: overview and importers")

**In each moved file:**

- Replace the H1 with front matter (`title`, `description`).
- Links to another moved guide become `/MegooFlags/<slug>/`.
- Links to repository files (`examples/...`, `../deploy/...`) become `https://github.com/mmhy2003/MegooFlags/blob/main/<path>` (or `tree` for directories).

**New `docs/README.md`:** "The guides moved to the docs site: https://mmhy2003.github.io/MegooFlags/. Their sources are in `site/src/content/docs/`. This folder keeps `examples/` (proxy, git-sync and multi-node samples), `superpowers/` (design history) and `ga-checklist.md`."

**References to update.** Run `git grep -n -E 'docs/(cli|flags-as-code|importers|integrations|multi-node|multi-node-migration|release-workflows|security)\.md'` and fix every hit outside `docs/superpowers/` (old plans stay as history):

- `README.md`, `AGENTS.md` (layout table rows for `deploy/` and `cli/`), `cli/README.md` (site URLs), `deploy/.env.example` (comments to site URLs), `scripts/cluster/lb.yml` (comment).
- `CHANGELOG.md` `[Unreleased]` lines only (`see docs/cli.md` becomes `see https://mmhy2003.github.io/MegooFlags/use/cli/`); released sections stay.
- Moved pages use site paths in the repository (`site/src/content/docs/...`) when other docs point at their source.

**Test** `scripts/tests/test_docs_moved.py`:

- `git grep` for the regex above, excluding `docs/superpowers`, returns nothing.
- None of the eight old files exist.
- `docs/README.md` names the site URL.

Write it first: it fails, because the files exist and are referenced.

**Sidebar:** add the moved pages under Deploy, Use and Migrate.

**Run** the site checks and the new test. **CHANGELOG** `### Changed`: "The guides moved from `docs/` to the docs site at https://mmhy2003.github.io/MegooFlags/." **Commit:** `docs: move the guides into the docs site (MF-87)`.

### 10. Site: REST API reference from openapi.json

1. Add `starlight-openapi` (a version whose peer range matches the pinned Starlight). Configure `starlightOpenAPI([{ base: "reference/api", label: "REST API", schema: "../api/openapi.json" }])` and put `...openAPISidebarGroups` into the Reference sidebar group, before the evaluation and delivery pages.
2. Run `pnpm build`. If the plugin rejects the 3.1 spec, or the build fails in the plugin and one targeted fix (a plugin option or version) does not solve it, use the **fallback**: remove the plugin and create `src/pages/reference/api.astro`. It reads `../api/openapi.json` at build time and renders, inside Starlight's `StarlightPage` component (title "REST API"), one section per tag with each operation's method, path, summary, parameters (name, in, required, schema type) and response codes. Add `{ label: "REST API", link: "/reference/api/" }` to the sidebar.
3. **Test** `site/tests/openapi.test.ts`. For the plugin: the built `dist/reference/api/` contains a page for the operation `GET /api/system/version`. For the fallback: a unit test of its operation-listing helper (`src/lib/openapi.ts`, `listOperations(spec)`) finds `get /api/system/version` with tag `system`. Write it first and see it fail.
4. Record which path was taken in the commit body.

**Run** the site checks. **Commit:** `feat(site): REST API reference generated from openapi.json (MF-87)`.

### 11. Site: Start here pages and the privacy page

**Before writing,** read `deploy/docker-compose.yml`, `deploy/.env.example`, `routers/setup.py`, `routers/projects.py`, `routers/flags.py`, `routers/flag_environments.py`, `routers/sdk_keys.py`, `routers/ofrep.py` and `scripts/propagation_test.py`, so every command and field name is real.

**`start/introduction.md`:** what MegooFlags is; what is in the box (single node bundle with Caddy, web, API, PostgreSQL and Valkey; multi-node; 13 OpenFeature SDKs; CLI and Terraform); "MegooFlags collects no usage data" linking to Privacy; where to go next.

**`start/quick-start.mdx`** (Starlight `Steps`, `Tabs` and `TabItem`):

1. Download the release bundle: `curl -fsSLO` for `docker-compose.yml`, `Caddyfile` and `.env.example` from `https://github.com/mmhy2003/MegooFlags/releases/latest/download/<file>`.
2. `cp .env.example .env` and set the secret key: `sed -i "s/^MEGOOFLAGS_SECRET_KEY=.*/MEGOOFLAGS_SECRET_KEY=$(openssl rand -hex 32)/" .env`. Adjust to what `.env.example` really contains; add the line if absent.
3. `docker compose up -d --wait`.
4. Open `http://localhost/setup` and create the admin.
5. In the UI: create a project, create a boolean flag `new-checkout`, turn it on in the default environment, and create a server SDK key under API & SDK keys.
6. Evaluate with curl: `curl -s -X POST http://localhost/ofrep/v1/evaluate/flags/new-checkout -H "Authorization: Bearer $MF_SDK_KEY" -H 'Content-Type: application/json' -d '{"context":{"targetingKey":"user-1"}}'`, with the expected JSON shown.
7. SDK tabs for Node.js, Python, Go and Java, using the snippet from each SDK README.

End with "Next" links to Deploy pages that exist now (Multi-node, Security) and to the SDKs.

**`start/concepts.md`:** projects, environments, flags and variations, targeting rules, segments, rollouts, prerequisites, SDK keys (server and client), local versus remote evaluation, streaming, change requests and approvals, roles. Each is one paragraph, checked against `api/src/megooflags_api/models`.

**`project/privacy.md`** (committed):

- No telemetry, no instance ID, no pings.
- The update check: what it requests (the GitHub URL), what it sends (only a `User-Agent` with the version), what GitHub sees (the IP), how often, and how to turn it off (`MEGOOFLAGS_UPDATE_CHECK=false`).
- Evaluation context is not stored unless `MEGOOFLAGS_PERSIST_CONTEXT=true`; retention with `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS`.
- Audit log and sessions are kept in your database.

**`index.mdx`:** add a "Quick start" primary action.

**Sidebar:** Start here group first; privacy in Project.

**Run** the site checks. **Commit:** `docs(site): introduction, quick start, concepts and privacy (MF-87)`.

### 12. Quick start test script and CI job

**`scripts/quickstart_test.sh`** (bash, `set -euo pipefail`, header comment "Runs the commands of site/src/content/docs/start/quick-start.mdx against megooflags-{api,web}:ci"):

1. Create a temporary directory. Copy `deploy/docker-compose.yml`, `deploy/Caddyfile` and `deploy/.env.example` into it, standing in for the release download. Copy `deploy/compose.ci.yml` as `compose.ci.yml`.
2. Run the documented `cp` and `sed` lines verbatim. Set `MEGOOFLAGS_HTTP_PORT=8080` (in `.env`, so port 80 is not needed on CI).
3. Record `start=$(date +%s)`. Run `docker compose -p mf-quickstart -f docker-compose.yml -f compose.ci.yml up -d --wait` (the documented command plus the CI override).
4. Do the UI steps through the API, as `smoke.sh` does: `POST /api/setup`, a token, `POST /api/projects`, create flag `new-checkout`, turn it on in the default environment, create a server SDK key.
5. Run the documented curl (host `localhost:8080`) and assert `"value":true`.
6. Compute `elapsed=$(( $(date +%s) - start ))`. Print `quick start: ${elapsed}s`. Fail if it is 300 or more.
7. Trap teardown with `down -v`.

**`scripts/tests/test_quickstart_doc.py`:** extract the shell lines of the quick start's `cp`, `sed`, `docker compose up` and OFREP `curl` from the MDX and assert each appears in the script, allowing only the port and the `-p/-f` additions. Use helper `doc_commands(text) -> list[str]` and a normalising compare that drops `-p mf-quickstart -f docker-compose.yml -f compose.ci.yml` and `:8080`. Write it first; it fails because the script is missing.

**CI:** a new job `quickstart` in `ci.yml`, `needs: images`, with the same image-loading steps as `smoke`, then `run: bash scripts/quickstart_test.sh`.

**AGENTS.md section 9:** add `bash scripts/quickstart_test.sh` under "images and smoke".

**Run** the test, the script locally (after `docker build` of both images) and `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest`. **Commit:** `test: quick start runs in under five minutes, checked in CI (MF-87)`.

### 13. Site: Deploy pages and the configuration doc test

**Pages** (all committed, checked against `deploy/` and `settings.py`):

- `deploy/single-node.md`: requirements (2 vCPU / 2 GB; ports), the bundle's services, `.env` essentials, backups (`pg_dump` from the `db` service), logs.
- `deploy/tls.md`: the two modes chosen by `MEGOOFLAGS_SITE_ADDRESS` (empty means plain HTTP behind your proxy; a domain means automatic HTTPS), ports 80 and 443, ACME needs, the `caddy_data` volume, HSTS only over HTTPS, `MEGOOFLAGS_PUBLIC_URL`.
- `deploy/upgrades.md`: image tags (`X.Y.Z`, `X.Y`, `X` from 1.0, `latest`, rc); pin `MEGOOFLAGS_VERSION`; the upgrade procedure (read the release notes, back up, change the version, `docker compose pull && docker compose up -d`; migrations run automatically through the `migrate` service; expand/contract allows rolling upgrades of replicas); multi-node order (control plane first, then workers); SDK, CLI and Terraform versions are independent; how the update notice works (link Privacy).
- `deploy/configuration.md`: one table per area (Core, Database and Valkey, TLS and proxy, Security and sessions, SDK delivery, Scheduler and update check, Integrations, Multi-node, Observability) with variable, default and meaning, for every `MEGOOFLAGS_*` setting in `settings.py` plus the compose-only variables in `deploy/.env.example` and `.env.worker.example` (`MEGOOFLAGS_VERSION`, `MEGOOFLAGS_HTTP_PORT`, `MEGOOFLAGS_TRUSTED_PROXIES`, `POSTGRES_PASSWORD`, `OTEL_*`, `FORWARDED_ALLOW_IPS` and others).
- `deploy/observability.md`: `/healthz`, `/readyz`, Prometheus metrics (`megooflags_http_requests_total`, `megooflags_http_request_duration_seconds`, `megooflags_http_requests_in_progress`, `megooflags_build_info`, and the scheduler and worker metrics found in `metrics.py`), the scrape config from `.env.example`, OpenTelemetry variables, JSON logs.

**Test first** (`scripts/tests/test_configuration_doc.py`):

- `settings_env_names()` gives `{"MEGOOFLAGS_" + name.upper() for name in Settings.model_fields}`. Import via `sys.path` from `api/src`, as other script tests import the API; run with `uv run --project api`.
- `example_env_names()` collects every `NAME=` and `# NAME=` key in `deploy/.env.example` and `deploy/.env.worker.example`.
- Assert each name appears in `site/src/content/docs/deploy/configuration.md` in backticks.

It fails because the page is missing.

**Sidebar:** add these pages under Deploy in the final order (Single node, TLS modes, [reverse proxy comes in step 14], Multi-node, Move to multi-node, Upgrades and version pinning, Configuration, Observability, Security).

**Run** the test and the site checks. **Commit:** `docs(site): deployment, TLS, upgrade, configuration and observability guides (MF-87)`.

### 14. Reverse proxy samples, page and CI test

**Samples** in `docs/examples/proxies/`. Each terminates TLS on 443 with `/etc/megooflags-proxy/fullchain.pem` and `privkey.pem` (Caddy: automatic TLS for `{$MF_DOMAIN}`) and proxies to the bundle at `http://127.0.0.1:8080` (the bundle runs with `MEGOOFLAGS_HTTP_PORT=8080`, `MEGOOFLAGS_SITE_ADDRESS=` empty):

- `nginx.conf`: `proxy_http_version 1.1`, `proxy_buffering off`, `proxy_cache off`, `proxy_read_timeout 3600s`, `proxy_set_header Host $host`, `X-Forwarded-For`, `X-Forwarded-Proto $scheme`, `Connection ""`.
- `Caddyfile`: `reverse_proxy 127.0.0.1:8080 { flush_interval -1 }`.
- `traefik.yml` (static, with the entrypoint `websecure` on 443 and a file provider) and `traefik-dynamic.yml` (router, service, TLS cert; `respondingTimeouts` and `readTimeout` of 0 or 3600 s on the entrypoint).
- `haproxy.cfg`: `mode http`, `timeout tunnel 1h`, `timeout server 1h`, `option forwardfor`, `http-request set-header X-Forwarded-Proto https`.
- `README.md`: one line per file.

**`scripts/proxy_stream_check.py`** (stdlib only; import `parse_sse` from `propagation_test`):

- Arguments: `--proxy https://localhost`, `--api http://localhost:8080`, `--token`, `--insecure`.
- It creates a project, a flag and a server key (reuse the request shapes from `propagation_test.py`), and checks `GET <proxy>/sign-in` returns 200 with "MegooFlags".
- It opens `GET <proxy>/sdk/v1/stream` (Accept `text/event-stream`) and waits for the first `put`, then holds the stream for 20 s (longer than the 15 s heartbeat) and checks that it is still open (at least one heartbeat comment has arrived).
- It toggles the flag through `--api`, expects a `put` with the new state through the proxy within 5 s, and prints `proxy stream OK (<s> s)`.
- Function `heartbeat_seen(lines) -> bool` is unit-tested.

**`scripts/tests/test_proxy_stream_check.py`** (first, it fails on import): `heartbeat_seen` recognises `:` comment lines; `parse_args` defaults.

**`scripts/proxy_samples_test.sh`:**

1. Start the bundle as `smoke.sh` does, but with `MEGOOFLAGS_HTTP_PORT=8080`, `MEGOOFLAGS_TRUSTED_PROXIES=private_ranges` and `MEGOOFLAGS_PUBLIC_URL=https://localhost`. Make the admin token.
2. Create a self-signed certificate for `localhost` in a temporary directory.
3. For each proxy in turn, run its official image (`nginx:1.27`, `caddy:2`, `traefik:v3`, `haproxy:3.0`) with `--network host`, mounting the sample and the certificate (Caddy with `MF_DOMAIN=localhost`, which uses its internal CA). Run `python3 scripts/proxy_stream_check.py --insecure ...`, then remove the container.
4. Print `proxies OK`.

**`deploy/reverse-proxy.md`:**

- When to use it (TLS terminated elsewhere: set `MEGOOFLAGS_SITE_ADDRESS=` empty).
- The SSE rules: no buffering, idle timeout above the 15 s heartbeat (`MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`), forwarded headers, `MEGOOFLAGS_TRUSTED_PROXIES` for a public proxy address, and `MEGOOFLAGS_PUBLIC_URL`.
- Each sample inline (`<Code>` imported with `?raw`, or copied) with a GitHub link.
- Notes:
  - Cloudflare: proxied SSE works; the 100 s idle limit is covered by heartbeats; set the origin to Full (strict) TLS; add Cloudflare's IP ranges to the trusted proxies.
  - AWS ALB: set the idle timeout to 120 s or more; target port 80; health check `/readyz`.
  - Coolify: run the bundle with the domain on Coolify's Traefik and Caddy in plain-HTTP mode; nothing to buffer-tune.

**CI:** job `proxies` (`needs: images`, image steps as `smoke`, `run: bash scripts/proxy_samples_test.sh`).

**AGENTS.md section 9:** add the command.

**Run** the unit test, the script locally, actionlint and the site checks. **Commit:** `docs: reverse proxy samples with SSE notes, tested in CI (MF-87)`.

### 15. Site: SDK overview and migration guides

**`sdks/overview.md`:**

- A table of all 13 SDKs: language, package name, server or client, evaluation mode, status (0.1.0 Alpha).
- How to choose (server SDKs evaluate locally; client SDKs evaluate remotely through `/sdk/v1/evaluate`).
- OpenFeature usage; key prefixes (`mf_server_` for server SDKs).
- Offline start from a bootstrap file (`megooflags ruleset pull`).
- Take the data from `sdks/README.md` and each README.

**`migrate/unleash.md`, `migrate/flagsmith.md`, `migrate/launchdarkly.md`.** Each has:

1. A concept map table (that tool's project, environment, feature or flag, strategy or segment, variants, against MegooFlags concepts). Take the mapping from `cli/internal/importers/<tool>*.go`.
2. The steps: install the CLI, `megooflags import <tool> (--url U | --file F) --project P --out DIR` (exact flags from `cli/cmd/megooflags/import.go`), read `import-report.md`, then `megooflags diff` and `megooflags apply`.
3. What is reported instead of translated (from `migrate/overview.md`).
4. Switching SDKs through OpenFeature: keep the OpenFeature calls, replace the provider (before and after code for Node.js), roll out per service, and keep both systems during a rollout window.

Update `migrate/overview.md` to link the three guides.

**Sidebar:** SDKs Overview first in SDKs; Migrate group complete.

**Run** the site checks. **Commit:** `docs(site): SDK overview and migration guides from Unleash, Flagsmith and LaunchDarkly (MF-87)`.

### 16. Site CI job and GitHub Pages workflow

**`ci.yml` job `site`:**

- `runs-on: ubuntu-24.04`, `defaults.run.working-directory: site`.
- Steps: checkout, `pnpm/action-setup@v4` with `package_json_file: site/package.json`, `actions/setup-node@v4` with `node-version: ${{ env.NODE_VERSION }}`, `cache: pnpm` and `cache-dependency-path: site/pnpm-lock.yaml`.
- Then run `pnpm install --frozen-lockfile`, `pnpm format:check`, `pnpm check`, `pnpm test` (palette, sync and openapi tests) and `pnpm build`. The link validator runs inside the build.

**`.github/workflows/docs.yml`:**

- `name: Docs`; `on: push: branches: [main]` with `paths: [site/**, sdks/*/README.md, spec/*.md, terraform/README.md, CHANGELOG.md, ROADMAP.md, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, rfcs/**, perf/REPORT.md, api/openapi.json, web/app/globals.css, .github/workflows/docs.yml]`, plus `workflow_dispatch`.
- `permissions: contents: read, pages: write, id-token: write`; `concurrency: group: pages, cancel-in-progress: false`.
- Job `build`: the same setup as `site`, `pnpm install --frozen-lockfile && pnpm build`, `actions/configure-pages@v5`, then `actions/upload-pages-artifact@v3` with `path: site/dist`.
- Job `deploy`: `needs: build`, `environment: { name: github-pages, url: ${{ steps.deployment.outputs.page_url }} }`, `actions/deploy-pages@v4` with `id: deployment`.

**Test** `scripts/tests/test_workflows.py`. Parse YAML with `yaml` if it is available in the api environment; otherwise check by text.

- `ci.yml` has the jobs `site`, `quickstart` and `proxies`.
- `docs.yml` triggers on `main` and `workflow_dispatch` and uploads `site/dist`.

Write it first and see it fail.

**Run** actionlint on both files and the test. **Commit:** `ci: docs site checks and GitHub Pages deployment (MF-87)`.

### 17. perf: Go module with stats, rate scheduler and result format

**Files:** `perf/go.mod` (`module github.com/mmhy2003/MegooFlags/perf`, `go 1.26.0`, standard library only), `perf/internal/stats/stats.go` and `stats_test.go`, `perf/internal/rate/rate.go` and `rate_test.go`, `perf/internal/result/result.go` and `result_test.go`, and a `perf/cmd/mfload/main.go` skeleton (subcommand dispatch with `flag.NewFlagSet`; `version` prints `mfload dev`).

**Contracts:**

```go
// stats
func Percentile(samples []time.Duration, q float64) time.Duration // nearest-rank, same rule as scripts/latency.py; panics on empty
type Summary struct{ Count, Errors int; P50, P95, P99, Max time.Duration }
func Summarize(samples []time.Duration, errors int) Summary
// rate: open loop. Request i is due at start + i/rps; latency is measured from the due time.
type Result struct{ Latency time.Duration; Err error }
func Run(ctx context.Context, rps float64, d time.Duration, concurrency int, do func(context.Context) error) []Result
// result
type Metrics map[string]float64
type Scenario struct{ Metrics Metrics `json:"metrics"`; Errors int `json:"errors"` }
func (s Summary) Metrics() Metrics // count, errors, p50_ms, p95_ms, p99_ms, max_ms
func Write(path string, s Scenario) error
```

**Tests first:**

- `TestPercentileNearestRank`: 1..100 ms gives p99 = 99 ms and p50 = 50 ms; a single sample.
- `TestRunIsOpenLoop`: at `rps=100` for 1 s with a `do` that sleeps 50 ms and concurrency 2, latencies grow past 50 ms (queueing counted) and the count is 100 ± 1.
- `TestRunCountsErrors`.
- `TestScenarioJSON`: round trip with the keys from "Shared contracts".

**Run** `cd perf && go test ./...`. It fails because the packages are missing. Implement, run again, then `gofmt -l .` (empty) and `go vet ./...`.

**AGENTS.md:** layout row "`perf/` | Load test tool `mfload` (Go) and committed results; run with `scripts/perf.sh`"; Go modules list gains `perf/`; section 9 gains `(cd perf && gofmt -l . && go vet ./... && go test -race ./...)`.

**CI:** add job `perf` (setup-go from `perf/go.mod`, gofmt, vet, `go test -race ./...`), as the `cli` job does.

**Commit:** `test(perf): mfload percentiles, open-loop rate scheduler and result format (MF-87)`.

### 18. perf: SSE reader and the `streams` command

**Files:** `perf/internal/sse/sse.go` and `sse_test.go`, `perf/internal/api/api.go` (small management-API client: `New(base, token, insecure)`, `ToggleFlag(ctx, project, env, flag, on)`; request shapes from `scripts/propagation_test.py`), and `perf/cmd/mfload/streams.go`.

**Contracts:**

```go
type Event struct{ Name, Data, ID string }
func Read(ctx context.Context, r io.Reader, onEvent func(Event), onComment func()) error // SSE framing as parse_sse in scripts/propagation_test.py
```

`mfload streams --target URL [--target URL ...] --key K --streams N --ramp-seconds S --hold SECONDS --flip-every SECONDS --api URL --token T --project P --env E --flag F [--insecure] --out FILE`:

- It opens N streams to `<target>/sdk/v1/stream`, round-robin over targets, at a ramp rate, each in its own goroutine with its own connection (`Transport.MaxConnsPerHost = 0`, `DisableKeepAlives = false`).
- Every `--flip-every` seconds it toggles the flag through `--api` and records the write time. For each stream it records the time until a `put` shows the new state. Propagation samples are per flip and per stream.
- Metrics: `streams_opened`, `streams_dropped` (closed or erroring before the end), `flips`, `prop_p50_ms`, `prop_p95_ms`, `prop_max_ms`, `missed` (a stream that did not see a flip within 30 s).
- `--ramp-until-error` mode (for capacity finding): keep opening until an open fails or `--max` is reached, and report `streams_opened` as `capacity`.

**Tests first** (`httptest` servers):

- `TestReadParsesEventsAndComments`: multiline data, `id:`, `:` comments, CRLF.
- `TestStreamsMeasuresPropagation`: a fake server holds three streams and emits a `put` 50 ms after the fake toggle endpoint is called. `prop_p95_ms` lies between 40 and 500, `streams_dropped == 0`.
- `TestStreamsCountsDrops`: the server closes one stream early, so `streams_dropped == 1`.

**Run** `cd perf && go test -race ./...`: it fails, then passes after implementing. **Commit:** `test(perf): mfload streams measures propagation over many SSE streams (MF-87)`.

### 19. perf: `seed` and `evaluate` commands

**Files:** `perf/internal/seed/seed.go` and `seed_test.go`, `perf/cmd/mfload/seed.go`, `perf/cmd/mfload/evaluate.go`, and `api.go` additions.

`mfload seed --api URL --token T --project P --flags N [--eval-flags 100] --out FILE`:

- It creates project P (via `POST /api/projects`; the default environments come with it) and N boolean flags `flag-00000`… with one targeting rule each (an attribute clause plus a 50 % rollout), enabled in the first environment. Concurrency is 16, with retry on 429/5xx.
- It creates a server SDK key for the environment that holds all N flags, and a second environment (or project, if environments cannot hold a different flag set) where only `--eval-flags` flags are on, for the 100-flag `/sdk/v1/evaluate` case. Read `routers/flag_environments.py` and choose the simplest way; document it in the code.
- It writes `{"project", "env", "server_key", "eval_key", "flag"}` JSON to `--out`. It also measures `ruleset_bytes`, `ruleset_download_ms` (`GET /sdk/v1/ruleset`) and `flag_list_ms` (`GET /api/projects/P/flags?limit=100`), and reports them as metrics.

`mfload evaluate --target URL --key K --mode ofrep|evaluate --flag F --rps R --duration D --warmup W --users 1000000 --concurrency C [--insecure] --out FILE`:

- It uses `rate.Run`. Each request's context is `{"targetingKey": "user-<rand(users)>", "plan": <one of 3>}`. OFREP is `POST /ofrep/v1/evaluate/flags/F`; evaluate is `POST /sdk/v1/evaluate` with the context.
- A non-2xx answer counts as an error. Warm-up results are discarded. Metrics are the Summary plus `rps`.

**Tests first:**

- `TestSeedCreatesFlags`: a fake API counts flag creations (N=25) and returns the keys.
- `TestEvaluateBodies`: OFREP and evaluate request shapes, with a random user within range.
- `TestEvaluateErrorsCounted`: the fake answers 500 every tenth request.

**Run** `go test -race ./...`: it fails, then passes. **Commit:** `test(perf): mfload seed and evaluate (MF-87)`.

### 20. Scripts: move the compose helpers to `scripts/cluster/lib.sh`

This step is a refactor with no behaviour change.

1. Move `cp_compose`, `worker_compose`, `lb_compose`, `join`, `cp_up`, `worker_up`, `wait_streams` and `mem_mib` from `scripts/cluster_test.sh` into `scripts/cluster/lib.sh`. It needs `root`, `network`, `workers` and `MF_CLUSTER_STATE`, which the caller sets before sourcing.
2. Add `bootstrap_admin <base> <curl-tls-args...>`: setup plus admin token, echoing the token, taken from the current inline code.
3. `cluster_test.sh` sources it with `. "$root/scripts/cluster/lib.sh"` and uses `bootstrap_admin`.

**Test** `scripts/tests/test_cluster_lib.py`:

- `bash -n scripts/cluster/lib.sh` and `bash -n scripts/cluster_test.sh` succeed.
- `cluster_test.sh` contains `scripts/cluster/lib.sh` and no longer defines `cp_compose()`.
- `bash -c '. scripts/cluster/lib.sh; type cp_compose worker_compose bootstrap_admin mem_mib'` succeeds.

Write it first and see it fail.

**Run** the test and `bash scripts/cluster_test.sh` (prints `cluster OK`). **Commit:** `refactor(scripts): share the cluster compose helpers (MF-87)`.

### 21. `scripts/perf.sh`, size-limited overrides and the perf-smoke CI job

**Files:**

- `scripts/perf.sh [full|tiny]`. `tiny` is the default in CI.
- `scripts/perf/single.override.yml`: `deploy.resources.limits` of caddy 0.25 CPU / 128M, web 0.25 / 256M, api 1.0 / 768M, db 0.4 / 640M, valkey 0.1 / 128M (sum 2 vCPU / 1.875 GiB), `MEGOOFLAGS_API_WORKERS: 2`, plus `ulimits.nofile: 65536`.
- `scripts/perf/remote.override.yml`: api 1 replica, no limit, `MEGOOFLAGS_API_WORKERS` defaulting to `${MF_PERF_API_WORKERS:-4}`.
- `scripts/perf/worker.override.yml`: worker caddy 0.2 / 96M and api 0.8 / 416M (sum 1 vCPU / 512M), `ulimits.nofile: 1048576`, `MEGOOFLAGS_API_WORKERS: 1`.
- `scripts/perf/local_eval.sh`: runs each server SDK's p99 guard (the commands in AGENTS.md section 9) in its language image (`golang:1.26`, `node:22` plus corepack, `ghcr.io/astral-sh/uv:python3.12-bookworm`, `maven:3-eclipse-temurin-21`, the `mcr.microsoft.com/dotnet/sdk` tag matching `sdks/dotnet/global.json`, `ruby` matching `.ruby-version` or the gemspec, `composer:2`). It records pass or fail and the p99 if printed (regex `p99[^0-9]*([0-9.]+) ?(ns|µs|us|ms)`), and writes `local_eval_<sdk>.json`.
- `scripts/perf_report.py`, `merge` subcommand only for now (render comes in step 22).

**`perf.sh` flow:**

1. **Host check.** Read `nproc`, `MemTotal` and `docker version`. Write `host.json`. Compute what fits:
   - The remote scenario needs ≥ 6 cores for 2k RPS plus the load generator.
   - The 100k scenario needs `workers × (1 CPU, 512 MiB)` plus the control plane plus the generator.
   - A scenario that does not fit runs at the largest size that does, with `status: reduced` and `reduced_to` set.
2. **Build mfload** into an image: `golang:1.26` builds a static binary, mounted into a `debian:stable-slim` container on the compose network with `--ulimit nofile=1048576` and `--sysctl net.ipv4.ip_local_port_range="1024 65535"`.
3. **Remote evaluation:** the single-node compose plus `remote.override.yml`. Seed 10k flags. Run `evaluate --mode ofrep --rps 2000 --duration 60 --warmup 15`, then `--mode evaluate` with the 100-flag key. Write `remote_ofrep.json` and `remote_evaluate.json`; `flags_10k.json` holds the seed metrics plus SDK start time (`ruleset_download_ms`).
4. **Propagation, replicas:** the single-node compose scaled to `api=3`. Run `streams --streams 30 --flip-every 5 --hold 100` (20 flips).
5. **Propagation, cluster:** source `scripts/cluster/lib.sh`, start the control plane plus 3 workers, and stream to the workers while flipping through the control plane.
6. **100k streams:**
   - Start one worker with `worker.override.yml` and run `streams --ramp-until-error --max 60000` against it, stopping once its memory (`mem_mib`) reaches 90 % of 512 MiB. This gives `capacity`.
   - Then `workers = ceil(100000 / (0.8 × capacity))`. Start that many workers, plus the control plane, and run `streams --streams 100000 --hold 600 --flip-every 30` across all worker targets.
   - Record `mem_max_mib` per worker and restart counts (`docker inspect -f '{{.RestartCount}} {{.State.OOMKilled}}'`).
7. **Single-node footprint:** `single.override.yml` with 100 flags. Run concurrently for 600 s: `streams --streams 20 --flip-every 1` (one write per second) and `evaluate --rps 50`. Record restarts and OOM kills.
8. Run `local_eval.sh`.
9. Run `python3 scripts/perf_report.py merge --dir <tmp> --version $(cat VERSION) --profile <p> --out perf/results/<version>.json` (`tiny` writes to `perf/results/tiny.json`, git-ignored). Each scenario's limits and notes come from a `meta.json` that `perf.sh` writes per scenario.

**`tiny` profile:** 100 flags, `evaluate --rps 50 --duration 10 --warmup 2`, propagation with 3 flips, `streams` with 200 streams over one worker, no local_eval, footprint for 30 s. No gates.

**Tests first** (`scripts/tests/test_perf_report.py`): `merge` builds the document from a directory with `host.json`, two scenario files and `meta.json`; a missing scenario file is an error. Also `bash -n scripts/perf.sh scripts/perf/local_eval.sh`.

**CI:** job `perf-smoke` (`needs: images`; image steps as `smoke`; setup-go from `perf/go.mod`; `run: bash scripts/perf.sh tiny`; `timeout-minutes: 30`).

**Remove** `scripts/bench_sdk_evaluate.py` and `scripts/tests/test_bench.py`. Update `scripts/latency.py`'s docstring to "shared by the propagation and cluster tests". `git grep bench_sdk_evaluate` outside `docs/superpowers` must be empty.

**AGENTS.md section 9:** `bash scripts/perf.sh tiny`, and a note that `bash scripts/perf.sh full` is run on the build machine for releases.

**Run** the tests, `bash scripts/perf.sh tiny` and actionlint. **Commit:** `test(perf): load test harness with size-limited stacks (MF-87)`.

### 22. Report renderer and the "Performance and sizing" page

**`scripts/perf_report.py render --results perf/results/<version>.json --out perf/REPORT.md`.** The output:

1. A front paragraph: what was measured, on which machine, profile and date.
2. An NFR table, one row per gate: NFR, target, measured, verdict (`pass`, `fail`, `reduced` or `not run`).
3. Reported-only values: the 10k-flag ruleset size and download, flag list latency, SDK start time, whole-environment evaluation.
4. A host and limits table.
5. "Sizing guidance": single node up to the measured small-team load on 2 vCPU / 2 GB; one worker's measured stream capacity in 1 vCPU / 512 MB; how many workers for N streams; the API worker count per container.
6. "How to reproduce": `bash scripts/perf.sh full`.

Gates live in `GATES`, a list of `(nfr, target_text, scenario, check)` with `check(scenario) -> bool`:

- `local_eval_*` exit 0 (p99 < 1 ms).
- `remote_ofrep.p99_ms < 50 and errors == 0` and `remote_evaluate.p99_ms < 50 and errors == 0` (at `rps >= 2000`).
- `propagation_replicas.prop_p95_ms < 5000` and `propagation_cluster.prop_p95_ms < 5000`.
- `streams_100k`: `streams_opened >= 100000`, `streams_dropped == 0`, `prop_p95_ms < 5000`, and `mem_max_mib <= 512` for each worker.
- `footprint_single`: `restarts == 0 and oom_killed == 0`, plus its prop and p99 gates.

A scenario with `status: reduced` renders `reduced (<reduced_to>)` and never `pass`.

**Tests first** (`scripts/tests/test_perf_report.py`):

- `render` of a fixture with all gates passing shows "pass" in every row.
- A failing p99 shows "fail".
- A reduced 100k run shows "reduced (40000 streams, 3 workers)" and not "pass".
- Missing scenarios show "not run".
- The output starts with `# Performance and sizing`.

**Site:** add `{"source": "perf/REPORT.md", "dest": "deploy/performance.md", "title": "Performance and sizing", ...}` to the manifest, `.gitignore` and `.prettierignore`, and add the page to the Deploy sidebar last. Commit a `perf/REPORT.md` rendered from the tiny fixture with a top note "Placeholder until the full run in this release". The next step replaces it.

**Run** the tests and the site checks. **Commit:** `feat(perf): render the load test report and show it on the docs site (MF-87)`.

### 23. Full load test run on the build machine and the committed report

This step runs on the build machine; it is not a CI job.

1. Build both images (`docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web`). Run `bash scripts/perf.sh full`. Then run `python3 scripts/perf_report.py render --results perf/results/$(cat VERSION).json --out perf/REPORT.md`.
2. **If a gate fails,** you may tune only settings and defaults: `MEGOOFLAGS_API_WORKERS` per container, the limits split inside the same totals, proxy and `ulimits` settings, and `MEGOOFLAGS_SSE_HEARTBEAT_SECONDS` / `STREAM_RECHECK_SECONDS` if justified. Document each change in the report's notes and, where it changes a shipped default, in `deploy/` files plus the CHANGELOG.
3. **If a target still fails after tuning, stop and call `ask_human`** with the measured numbers and the options. Do not rewrite code. The gap becomes a blocker in step 24.
4. Scenarios marked `reduced` because the host is too small stay `reduced`. List them for the checklist.
5. Commit `perf/results/<version>.json` and `perf/REPORT.md`. Run the site build to see the page.

**Commit:** `test(perf): load test results against the NFRs (MF-87)`.

### 24. GA checklist

**File:** `docs/ga-checklist.md`, built from the KB PRD (`kb_search` "PRD: MegooFlags", `kb_read`, revision 2) and verified against the code. Sections:

1. **P0 requirements.** Every P0 row in PRD sections 6 and 8 and every item of the GA row in section 11: ID, requirement (short), status (done, partial or missing), and evidence (a file path, test name or CI job, for example `api/tests/test_ofrep.py`, the `cluster` job). Check each by reading the code or running the named test; do not copy statuses from older plans.
2. **NFR verdicts.** The verdict table from `perf/REPORT.md`, linked, plus the measured quick start time from the `quickstart` CI job log or a local run of `scripts/quickstart_test.sh`.
3. **Competitor matrix.** PRD section 5 re-checked cell by cell against each competitor's current public docs (use the web-fetch agent). Each cell has the value, the source URL and the date checked, with changed cells marked. An unreachable source is "not verified", which is a blocker.
4. **Differences from the PRD.** Telemetry was removed, so "monthly active self-hosted instances" cannot be measured. The bundle also runs Valkey. List any others found.
5. **Maintainer steps:**
   - Enable GitHub Pages (source: GitHub Actions).
   - Enable private vulnerability reporting and content reports.
   - Decide the SDK, CLI and Terraform versions for GA.
   - Update the PRD matrix in the Knowledge Base.
   - Run `docs.yml` once (README docs links 404 until then).
   - Bump to 1.0.0 per AGENTS.md section 5, open the release pull request, tag `v1.0.0`.
6. **Open blockers:** failed or reduced NFRs, P0 gaps, unverified matrix cells. "None" if there are none.

**Test** `scripts/tests/test_ga_checklist.py`: the file exists and has the six headings; every evidence path in backticks that looks like a repository path exists (`Path(ROOT / p).exists()`). Write it first; it fails.

**Run** the test. **Commit:** `docs: GA checklist for 1.0.0 (MF-87)`.

## Final verification

```bash
python3 scripts/check_version.py
uv run --frozen --project api pytest scripts/tests
(cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest)
bash scripts/check_migrations.sh
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api && git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build && pnpm e2e)
(cd site && pnpm install --frozen-lockfile && pnpm format:check && pnpm check && pnpm test && pnpm build)
(cd perf && gofmt -l . && go vet ./... && go test -race ./...)
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web
bash scripts/smoke.sh http && bash scripts/smoke.sh https && bash scripts/cluster_test.sh
bash scripts/quickstart_test.sh && bash scripts/proxy_samples_test.sh && bash scripts/perf.sh tiny
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
git status   # no synced pages, dist, caches or tiny results untracked
```

**Done when:**

- `perf/REPORT.md` shows the NFR verdicts from the full run.
- `docs/ga-checklist.md` lists the maintainer steps (Pages, reporting, the version bump) and any blockers.
- No `VERSION` change was made.

## Requirement coverage

| Design requirement | Steps |
|---|---|
| Docs site, Starlight, brand palette, palette test, logo | 6 |
| Synced pages and link rewriting | 8, 22 |
| Moved guides and reference updates | 9 |
| REST API reference (plugin or fallback) | 10 |
| Quick start, concepts, privacy | 11, 12 |
| Deploy guides, configuration, upgrades, TLS | 13 |
| Reverse proxy samples with SSE notes (DEP-5) | 14 |
| SDK reference and migration guides | 8, 15 |
| Publishing and site CI | 16 |
| README, CONTRIBUTING, CoC, SECURITY, ROADMAP, RFCs | 7 |
| Update check, storage, setting, endpoint | 1–4 |
| Footer and pill | 5 |
| Load tool, harness, report, full run | 17–23 |
| GA checklist | 24 |
| AGENTS.md and CHANGELOG updates | 3, 4, 6, 9, 12, 14, 17, 21 |
