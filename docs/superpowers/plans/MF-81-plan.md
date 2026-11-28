# MF-81 implementation plan: web UI foundation

**Goal.** Give every signed-in page one shared app shell. It uses the PRD 6.8 cyberpunk neon theme in light, dark and system modes, works from 280 px wide up, and comes with a shared component kit. A Playwright + axe matrix in CI (6 widths × 2 themes) keeps all of this in place.

**Architecture.**
- **API.** `user` gets `theme` and `sidebar_collapsed`. `/api/auth/me` returns them, `PATCH /api/account/preferences` changes them, and `GET /api/auth/me/permissions?project=` reports the caller's rights through the existing `Authorizer`.
- **Web: root and proxy.**
  - The root layout wraps the body in `<Suspense>`. A server `RootProviders` reads the `mf_theme` cookie, which becomes the `next-themes` `defaultTheme`, and the request headers, which drive the plain-HTTP banner.
  - `proxy.ts`, which already calls `/me`, copies the stored theme into `mf_theme` when that cookie is missing (no flash in a new browser) and records the URL's project as `mf_project`.
- **Web: shell.** `app/(app)/layout.tsx` loads `/me`, the projects and the permissions on the server, then renders the client `AppShell`: shadcn `SidebarProvider`, `Sidebar` and `SidebarInset`, plus a sticky header. Layout changes are CSS-only. The only JS width checks are shadcn's mobile drawer and a hook that forces icon mode between 768 and 1023 px; that hook changes a prop and never remounts content.
- **Web: tests and CI.** Pure logic sits in `lib/*.ts` and has Vitest unit tests. Behaviour, axe, reflow, tap-target and visual checks are Playwright specs in `web/e2e/`. They run in a new `web-e2e` CI job, inside the pinned Playwright image, against the real API, Postgres and Valkey.

## Conventions (every step)
- **API:**
  - TDD with pytest, run from `api/` with Postgres and Valkey up (AGENTS.md §9).
  - Checks: `uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest <files>`.
  - Commit messages: `<type>(api): … (MF-81)`.
- **Web:**
  - Run from `web/`: `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build`. `pnpm test` exists from step 4 on.
  - From step 8 on, `pnpm e2e <spec>` needs the API running and the app built (Appendix E).
  - Commit messages: `<type>(web): … (MF-81)`.
- **API route or schema changes:** regenerate with `cd api && uv run python -m megooflags_api.export_openapi openapi.json && cd ../web && pnpm gen:api`, and commit both files.
- **Every commit:** ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.megoocolab/`, `e2e/.auth/`, `test-results/` or `playwright-report/`.
- **Colours:** never write a hex or `rgb(` colour outside `web/app/globals.css` (step 4's test enforces this). Use token classes only.
- **Tap targets:** every interactive element is at least 44×44 px, or sits inside an element marked `data-hit-area` that is. Inline links inside running text carry `data-inline-link` and are exempt.

## File map
- **API:**
  - `alembic/versions/0013_user_preferences.py` (new).
  - `models/enums.py` (`UserTheme`) and `models/identity.py` (two columns).
  - `schemas/auth.py` (`Preferences`, `PreferencesIn`, `MePermissions`, `MeOut.preferences`).
  - `routers/auth.py` (`/me`, `/me/permissions`), `routers/account.py` (PATCH), `auth/rbac.py` (`held`).
  - Tests: `tests/test_preferences.py` and `tests/test_me_permissions.py` (new); `tests/test_migrations.py` (head 0013); `tests/test_audit_coverage.py` (`NOT_AUDITED`).
  - Generated and docs: `openapi.json`, `web/lib/api/schema.d.ts`, `web/lib/api/check.ts`, `CHANGELOG.md`.
- **Web pure modules** (each unit-tested in `web/tests/unit/`): `lib/http-warning.ts`, `lib/nav.ts`, `lib/routes.ts`, `lib/theme.ts`, `lib/user.ts`, `lib/shortcuts.ts`, `lib/rollout.ts`, `lib/diff.ts`, `lib/kit.ts`.
- **Web server and data:** `lib/api/base.ts` (`apiBaseUrl`), `lib/api/server.ts` (`serverApi`), `lib/shell-data.ts` (`loadShell`), `lib/preferences.ts` (browser `savePreferences`).
- **Web theme and root:**
  - `app/globals.css`, `app/layout.tsx`, `components/root-providers.tsx`, `components/theme-provider.tsx`.
  - `app/icon.svg` (new); `app/favicon.ico` (deleted).
  - `next.config.ts` (rewrites), `proxy.ts` (cookies, matcher), `hooks/use-media-query.ts`.
- **shadcn primitives:** `components/ui/*`, added in step 6 and touch-sized.
- **Shell (`components/shell/`):** `app-shell`, `brand`, `nav`, `project-switcher`, `use-project-permissions`, `site-header`, `command-palette`, `theme-switcher`, `theme-sync`, `user-menu`, `http-warning`.
- **Kit (`components/patterns/`):** `page-header`, `empty-state`, `placeholder-page`, `status-pill`, `data-list`, `toggle`, `rollout-slider`, `responsive-dialog`, `confirm-dialog`, `copy-button`, `json-viewer`, `diff-viewer`.
- **Routes (`app/(app)/`):**
  - `layout.tsx`, `page.tsx`, `not-found.tsx`.
  - `projects/[project]/{layout,page}.tsx` plus placeholders for `flags`, `flags/[flag]`, `segments`, `environments`, `change-requests`, `audit`, `keys` and `integrations`.
  - `settings`, `account/tokens`, `account/security` (moved here from `app/account/security`), `dev/kit` (with `kit-samples.tsx`).
- **E2E:** `playwright.config.ts`, `e2e/global-setup.ts`, `e2e/support/{users,checks}.ts`, `e2e/{http-warning,smoke,shell,preferences,palette,kit,matrix,motion}.spec.ts`, `e2e/docker.sh`, and `e2e/matrix.spec.ts-snapshots/`.
- **Repo:** `.github/workflows/ci.yml` (`pnpm test` and the `web-e2e` job), `AGENTS.md` §9, `CHANGELOG.md`.

## Steps
1. API: user preference columns (migration 0013).
2. API: preferences on /me and PATCH /api/account/preferences.
3. API: GET /api/auth/me/permissions.
4. Web: Vitest and the pure helpers (HTTP warning, nav map, routes, theme, user, no-hex check).
5. Web: PRD 6.8 palette tokens, glow, reduced motion and safe-area CSS.
6. Web: shadcn primitives, touch sizes and 16 px inputs.
7. Web: root providers (next-themes, theme cookie, viewport, favicon, plain-HTTP banner, rewrites, proxy cookies).
8. Web: Playwright e2e harness and web-e2e CI job.
9. Web: app shell (layout, sidebar, nav, project switcher, placeholder routes).
10. Web: header controls (theme switcher, user menu) and preference sync.
11. Web: ⌘K / Ctrl+K command palette.
12. Web: kit part 1 (status pill, data list, toggle, rollout slider) and the /dev/kit gallery.
13. Web: kit part 2 (responsive dialog, confirm dialog, JSON and diff viewers).
14. Web: responsive and accessibility matrix, reduced motion, visual baselines, changelog.

The full detail of each step (files, failing tests first, commands, implementation, commit message) is in the structured `steps` list. It relies on the appendices below.

## Final verification
- **web/:** `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build && pnpm e2e`, then `pnpm e2e:docker` (visual baselines inside the pinned image).
- **api/:** ruff, format, mypy and the full pytest suite; `bash scripts/check_migrations.sh`; the OpenAPI drift check (AGENTS.md §9).
- **Workflows:** actionlint.
- **Image:** `docker build -t megooflags-web:ci web && bash scripts/smoke.sh http`.
- **CI:** jobs `web` and `web-e2e` are green.

---

## Appendix A: API contracts
- **Enum** (`models/enums.py`): `class UserTheme(StrEnum)` with `LIGHT="light"`, `DARK="dark"` and `SYSTEM="system"`.
- **`User` model:**
  - `theme: Mapped[UserTheme] = mapped_column(pg_enum(UserTheme, "user_theme"), default=UserTheme.SYSTEM, server_default=UserTheme.SYSTEM.value)`.
  - `sidebar_collapsed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())`.
- **Migration 0013** (down_revision 0012):
```python
USER_THEME = postgresql.ENUM("light", "dark", "system", name="user_theme", create_type=False)
def upgrade() -> None:
    USER_THEME.create(op.get_bind())
    op.add_column("user", sa.Column("theme", USER_THEME, server_default="system", nullable=False))
    op.add_column("user", sa.Column("sidebar_collapsed", sa.Boolean(), server_default=sa.false(), nullable=False))
def downgrade() -> None:
    op.drop_column("user", "sidebar_collapsed"); op.drop_column("user", "theme"); op.execute("DROP TYPE user_theme")
```
- **Schemas** (`schemas/auth.py`; import `Permission` from `auth.rbac`, as `schemas/tokens.py` does):
```python
class Preferences(ApiModel):
    theme: UserTheme
    sidebar_collapsed: bool
class PreferencesIn(ApiModel):
    """Only the fields given change."""
    theme: UserTheme | None = None
    sidebar_collapsed: bool | None = None
class MePermissions(ApiModel):
    """What the caller may do: org-wide, in `project` and in each of its visible environments."""
    org: list[Permission]
    project: list[Permission] | None
    environments: dict[str, list[Permission]]
# MeOut += preferences: Preferences | None   (None for API tokens)
```
- **`auth/rbac.py`:**
```python
def held(authorizer: Authorizer, levels: Iterable[ScopeKind], project_id: uuid.UUID | None = None,
         environment_id: uuid.UUID | None = None) -> list[Permission]:
    """The permissions of `levels` that `authorizer` holds there, in catalogue order."""
    wanted = set(levels)
    return [p for p in Permission if PERMISSION_LEVEL[p] in wanted and authorizer.can(p, project_id, environment_id)]
```
- **Route** (`routers/auth.py`, after `/me`). It uses `Caller = Annotated[Authz, Depends(authenticated())]`, so it is guarded and self-only. It is **not** added to `PUBLIC_ROUTES`.
```python
@router.get("/me/permissions", responses={404: {"model": ErrorBody}})
async def my_permissions(session: SessionDep, authz: Caller, project: str | None = None) -> MePermissions:
    a = authz.authorizer
    org = held(a, ScopeKind)
    if project is None:
        return MePermissions(org=org, project=None, environments={})
    row = await get_project(session, project)
    if not a.can(Permission.PROJECT_READ, row.id):
        raise not_found("project", project)
    envs = await environments.list_environments(session, row, a.visible_environment_ids(row.id))
    return MePermissions(org=org, project=held(a, [ScopeKind.PROJECT, ScopeKind.ENVIRONMENT], row.id),
                         environments={e.key: held(a, [ScopeKind.ENVIRONMENT], row.id, e.id) for e in envs})
```
- **`/me`:** sets `preferences` only when `principal.kind == PrincipalKind.USER` and the user exists; otherwise `None`.
- **PATCH** (`routers/account.py`, `SignedIn` dependency, not audited):
```python
@router.patch("/preferences")
async def update_preferences(session: SessionDep, authz: SignedIn, body: PreferencesIn) -> Preferences:
    """Your UI preferences (theme, sidebar). Personal settings: not audited."""
    user, _ = await _current(session, authz)
    if body.theme is not None: user.theme = body.theme
    if body.sidebar_collapsed is not None: user.sidebar_collapsed = body.sidebar_collapsed
    await session.commit()
    return Preferences(theme=user.theme, sidebar_collapsed=user.sidebar_collapsed)
```
- **`test_audit_coverage.py`:** add `("PATCH", "/api/account/preferences")` to `NOT_AUDITED`, with the comment `# Personal UI settings.`

## Appendix B: web pure-module contracts
```ts
// lib/theme.ts
export type Theme = "light" | "dark" | "system";
export const THEME_COOKIE = "mf_theme", PROJECT_COOKIE = "mf_project", SIDEBAR_COOKIE = "sidebar_state";
export function parseTheme(v: string | undefined | null): Theme | null;
export function themeCookieToSet(existing: string | undefined, stored: Theme | undefined | null): Theme | null; // stored when cookie missing, else null
export function sidebarOpen(cookie: string | undefined, collapsed: boolean | undefined): boolean; // "true"/"false" cookie wins, else !collapsed, else true
// lib/http-warning.ts — scheme = first comma-separated x-forwarded-proto (trimmed, lower-case) else "http";
// warn when scheme is http and host (port stripped) is not localhost, 127.0.0.1 or ::1. 127.0.0.2 warns. Null host: no warning.
export function shouldWarnPlainHttp(i: { forwardedProto: string | null; host: string | null }): boolean;
// lib/routes.ts
export const PROJECT_SECTIONS = ["flags","segments","environments","change-requests","audit","keys","integrations"] as const;
export type ProjectSection = (typeof PROJECT_SECTIONS)[number];
export function projectPath(project: string, section: ProjectSection = "flags"): string; // key URI-encoded
export function flagPath(project: string, flag: string): string;
export function projectFromPath(pathname: string): string | null;
export function switchProjectPath(pathname: string, project: string): string; // keeps a known section, else flags
export function homeTarget(projects: string[], last: string | null): string | null;
export type Crumb = { label: string; href: string };
export function projectTrail(project: string): Crumb[];
// lib/nav.ts
export type MePermissions = components["schemas"]["MePermissions"];
export type NavKey = ProjectSection | "settings";
export type NavItem = { key: NavKey; label: string; href: string };
export function navItems(project: string | null, perms: MePermissions | null): NavItem[];
// lib/user.ts:      initials(name, email): "Ada Admin"→"AA", "ada"→"A", else email[0] upper-case, else "?"
// lib/shortcuts.ts: isPaletteShortcut({key, metaKey, ctrlKey, target}): (meta|ctrl)+k, any case; false inside [data-code-editor]
// lib/rollout.ts:   clampRollout(n) clamps to 0..100 and rounds to 0.1; parseRollout(text) gives null for ""/NaN, else clampRollout
// lib/diff.ts:      type DiffLine = { kind: "add"|"remove"|"same"; text: string; oldNo: number|null; newNo: number|null }; lineDiff(before, after) built on diffLines
// lib/kit.ts:       kitEnabled(v) => v === "1"
```
**`navItems` rules.** Order: Flags, Segments, Environments, Change requests, Audit log, API & SDK keys, Integrations, Settings. Nodes is never listed.
- **With a project:**
  - always Flags, Segments, Environments and Change requests;
  - Audit log when `perms.project` has `audit.read`;
  - API & SDK keys when any `perms.environments[*]` has `sdk_key.read`;
  - Integrations when `perms.project` has `project.manage`.
- **Settings** (`/settings`) when `perms.org` has any of `org.manage`, `member.manage`, `role.manage` or `token.manage`.
- **When `perms` is null:** only the four always-visible items.
- **When `project` is null:** only Settings, if permitted.

## Appendix C: `globals.css` tokens
These replace the neutral `:root` and `.dark` blocks.
```css
:root {
  --radius: 0.625rem; --header-height: 3.5rem;
  --background: #FAF7FF; --foreground: #1A1033; --card: #FFFFFF; --card-foreground: #1A1033;
  --popover: #FFFFFF; --popover-foreground: #1A1033;
  --primary: #C8006A; --primary-foreground: #FFFFFF; --secondary: #007A87; --secondary-foreground: #FFFFFF;
  --accent: #7A1FD6; --accent-foreground: #FFFFFF; --muted: #F1ECFA; --muted-foreground: #5C5080;
  --success: #007A53; --success-foreground: #FFFFFF; --warning: #8A5E00; --warning-foreground: #FFFFFF;
  --destructive: #CC0033; --destructive-foreground: #FFFFFF;
  --border: #E3DAF5; --input: #E3DAF5; --ring: #007A87;
  --glow: color-mix(in oklch, var(--primary) 20%, transparent);
  --chart-1: #C8006A; --chart-2: #007A87; --chart-3: #7A1FD6; --chart-4: #007A53; --chart-5: #8A5E00;
  --sidebar: #FFFFFF; --sidebar-foreground: #1A1033; --sidebar-primary: #C8006A; --sidebar-primary-foreground: #FFFFFF;
  --sidebar-accent: #EFE7FC; --sidebar-accent-foreground: #1A1033; --sidebar-border: #E3DAF5; --sidebar-ring: #007A87;
}
.dark {
  --background: #0B0618; --foreground: #EDE7FF; --card: #150D2B; --card-foreground: #EDE7FF;
  --popover: #150D2B; --popover-foreground: #EDE7FF;
  --primary: #FF2E97; --primary-foreground: #0B0618; --secondary: #00F0FF; --secondary-foreground: #0B0618;
  --accent: #B026FF; --accent-foreground: #FFFFFF; --muted: #1E1538; --muted-foreground: #A79BD0;
  --success: #2DFF8A; --success-foreground: #0B0618; --warning: #FCEE0A; --warning-foreground: #0B0618;
  --destructive: #FF3B5C; --destructive-foreground: #0B0618;
  --border: #2A1F4D; --input: #2A1F4D; --ring: #00F0FF;
  --glow: color-mix(in oklch, var(--primary) 55%, transparent);
  --chart-1: #FF2E97; --chart-2: #00F0FF; --chart-3: #B026FF; --chart-4: #2DFF8A; --chart-5: #FCEE0A;
  --sidebar: #150D2B; --sidebar-foreground: #EDE7FF; --sidebar-primary: #FF2E97; --sidebar-primary-foreground: #0B0618;
  --sidebar-accent: #2A1F4D; --sidebar-accent-foreground: #EDE7FF; --sidebar-border: #2A1F4D; --sidebar-ring: #00F0FF;
}
@utility shadow-glow { box-shadow: 0 0 0 1px var(--glow), 0 0 14px var(--glow); }
/* @layer base: html { scroll-padding-top: calc(var(--header-height) + env(safe-area-inset-top) + 0.5rem); }
   @media (prefers-reduced-motion: reduce) { *, ::before, ::after { animation-duration: .01ms !important;
   animation-iteration-count: 1 !important; transition-duration: .01ms !important; scroll-behavior: auto !important; } } */
```
- **AA tuning:** the PRD's light success (`#00845A`) and warning (`#9A6A00`) are just under 4.5:1 on `#FAF7FF`, so they become `#007A53` and `#8A5E00`. Dark `--accent-foreground` is white, because the night-purple background colour on `#B026FF` is only 4.3:1. The design allows tuning for AA.
- **`@theme inline` additions:** `--color-success`, `--color-success-foreground`, `--color-warning`, `--color-warning-foreground`, `--color-destructive-foreground` and `--color-glow`.
- **Contrast pairs** that `tokens.test.ts` checks in both themes, each at least 4.5:1:
  - foreground on background and on card; popover-foreground on popover;
  - muted-foreground on background, card and muted;
  - each `X-foreground` on `X`, for X = primary, secondary, accent, success, warning and destructive;
  - primary, secondary, success, warning and destructive as text on background and on card;
  - sidebar-foreground on sidebar; sidebar-accent-foreground on sidebar-accent.

## Appendix D: `web-e2e` CI job
- **Runner and container:** `runs-on: ubuntu-24.04`, `container: { image: mcr.microsoft.com/playwright:v<PW>-noble, options: --ipc=host }`. `<PW>` is the exact `@playwright/test` version.
- **Services:** `postgres` (postgres:16 with the same env and health check as `api-test`, no ports) and `valkey` (valkey/valkey:8 with a health check).
- **Env:**
  - `MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://megooflags:megooflags@postgres:5432/megooflags`
  - `MEGOOFLAGS_VALKEY_URL=redis://valkey:6379/0`
  - `MEGOOFLAGS_SECRET_KEY=ci-e2e-secret-key-not-used-anywhere-else-0123456789`
  - `MEGOOFLAGS_PUBLIC_URL=http://localhost:3000` (the browser's Origin passes the API's CSRF check)
  - `MEGOOFLAGS_API_URL=http://localhost:8000`
  - `MEGOOFLAGS_API_WORKERS=1`
  - `MEGOOFLAGS_UI_KIT=1`
  - `HOME=/root`
- **Steps:**
  1. Checkout.
  2. `apt-get update && apt-get install -y --no-install-recommends build-essential libxml2-dev libxslt1-dev libxmlsec1-dev pkg-config`.
  3. `astral-sh/setup-uv@v6` (Python `${{ env.PYTHON_VERSION }}`, cache `api/uv.lock`).
  4. `pnpm/action-setup@v4` (`package_json_file: web/package.json`).
  5. `actions/setup-node@v4` (`${{ env.NODE_VERSION }}`, pnpm cache).
  6. In `api`: `uv sync --frozen && uv run alembic upgrade head`.
  7. In `api`: `nohup uv run python -m megooflags_api.serve > /tmp/api.log 2>&1 &`.
  8. In `web`: `pnpm install --frozen-lockfile && pnpm build`, then `pnpm e2e`.
  9. On failure, `actions/upload-artifact@v4` uploads `web/playwright-report`, `web/test-results` and `/tmp/api.log`.
- **`web` job:** also gains `- run: pnpm test` after `pnpm typecheck`.

## Appendix E: e2e harness
- **Scripts:** `"test": "vitest run"`, `"e2e": "playwright test"`, `"e2e:docker": "bash e2e/docker.sh"`.
- **`playwright.config.ts`:**
  - `testDir: "e2e"`, `globalSetup: "./e2e/global-setup.ts"`, `workers: 1`, `fullyParallel: false`, `retries: CI ? 1 : 0`, html reporter (`open: "never"`) plus list reporter.
  - `use: { baseURL: "http://localhost:3000", storageState: "e2e/.auth/admin.json", trace: "retain-on-failure" }`.
  - `expect.toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: "disabled" }`.
  - One project: chromium (Desktop Chrome).
  - `webServer: { command: "pnpm start -H 0.0.0.0 -p 3000", url: "http://localhost:3000/sign-in", reuseExistingServer: !CI, env: { MEGOOFLAGS_UI_KIT: "1", MEGOOFLAGS_API_URL } }`.
- **`e2e/support/users.ts`:**
  - `PASSWORD = "e2e correct horse battery staple"`.
  - `ADMIN`: `admin@e2e.test`, "Ada Admin", `e2e/.auth/admin.json`.
  - `VIEWER`: `viewer@e2e.test`, "Vic Viewer", org `viewer` role, `e2e/.auth/viewer.json`.
  - `PREFS`: `prefs@e2e.test`, "Pat Prefs", org `admin` role, `e2e/.auth/prefs.json`.
- **`e2e/global-setup.ts`** (idempotent):
  1. Poll `${MEGOOFLAGS_API_URL}/readyz` for up to 90 s.
  2. Use `request.newContext({ baseURL: "http://localhost:3000", extraHTTPHeaders: { Origin: "http://localhost:3000" } })`, so calls go through the Next rewrites.
  3. If the setup status reports `needs_setup`, call `POST /api/setup` for ADMIN; otherwise sign in.
  4. Ensure projects `demo` (environments development, staging, production) and `other` exist.
  5. Ensure flags `new-checkout` (boolean), `dark-mode` (boolean) and `pricing-copy` (string) exist in `demo`.
  6. For VIEWER and PREFS:
     - if `GET /api/users?q=` finds nothing, call `POST /api/users`;
     - take the token from the last path segment of `invite.url` and call `POST /api/invitations/{token}` with `{ password }`;
     - call `POST /api/bindings` with `{ subject_kind: "user", subject_id, role, scope: "org" }`.
  7. For each user:
     - sign in from a fresh context;
     - `PATCH /api/account/preferences` to `{ theme: "system", sidebar_collapsed: false }`;
     - `storageState({ path })`.
- **`e2e/support/checks.ts`:**
  - `noHorizontalScroll(page)`: `scrollWidth <= clientWidth` on `documentElement`.
  - `axeViolations(page)`: `AxeBuilder` with the tags `wcag2a`, `wcag2aa`, `wcag21aa`; returns `.violations`.
  - `smallTargets(page)`: checks visible `a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button|switch|slider|menuitem|option|tab|checkbox]`. It skips `[data-inline-link]` and anything inside `[aria-hidden=true]` or `[inert]`. It measures `closest("[data-hit-area]") ?? el` and returns a description of each element under 43.5 px in either dimension.
- **`e2e/docker.sh`:**
  - `PW_VERSION="<PW>"`.
  - `docker run --rm --network host --ipc=host -v "$repo:/repo" -w /repo/web -e CI=1 -e MEGOOFLAGS_UI_KIT=1 -e MEGOOFLAGS_API_URL=${MEGOOFLAGS_API_URL:-http://localhost:8000} mcr.microsoft.com/playwright:v$PW_VERSION-noble bash -c "corepack enable && pnpm install --frozen-lockfile && pnpm build && pnpm e2e $*"`.
- **Running the API locally:**
  - Postgres and Valkey as in AGENTS.md §9.
  - `cd api && uv run alembic upgrade head`.
  - `MEGOOFLAGS_SECRET_KEY=<32+ chars> MEGOOFLAGS_PUBLIC_URL=http://localhost:3000 MEGOOFLAGS_API_WORKERS=1 uv run python -m megooflags_api.serve`.
- **Ignore files:** add `/e2e/.auth`, `/test-results` and `/playwright-report` to `.gitignore`, `.prettierignore` and the ESLint `globalIgnores`. Don't use `test.extend` fixtures: their `use` callback trips `react-hooks/rules-of-hooks`.

## Appendix F: shell data flow
- **`proxy.ts`**, after `/me` succeeds and the session-state checks pass:
  - It calls `themeCookieToSet(cookie mf_theme, me.preferences?.theme)` and `projectFromPath(pathname)`.
  - For each value that is set, it calls `request.cookies.set(...)` and returns `NextResponse.next({ request: { headers: request.headers } })`, with `response.cookies.set(name, value, { path: "/", sameSite: "lax", maxAge: 31536000 })`.
  - The matcher also excludes `icon.svg`.
- **`loadShell()`:**
  - Calls `/me`.
  - If `session_state !== "active"`, it returns `projects: null, permissions: null`.
  - Otherwise it calls `GET /api/projects?limit=500` and `GET /api/auth/me/permissions?project=<mf_project if it is in the list, else the first project, else omitted>`.
  - Each failure becomes `null` and is logged with `console.error`.
  - It returns `{ me, projects, permissions, permissionsProject, sidebarOpen: sidebarOpen(cookie sidebar_state, me.preferences?.sidebar_collapsed) }`.
- **`AppShell` (client):**
  - The current project is `useParams().project ?? permissionsProject`.
  - `useProjectPermissions` returns the initial permissions while the project matches. Otherwise it fetches `/api/auth/me/permissions?project=` in the browser; while that loads it shows the always-visible items.
  - `isDesktop = useMediaQuery("(min-width: 1024px)", true)`.
  - `SidebarProvider open={isDesktop ? userOpen : false}`, and `onOpenChange` does nothing below lg; otherwise it calls `setUserOpen(v)` and `savePreferences({ sidebar_collapsed: !v })`.
  - When a save fails, it shows the toast "Couldn't save this preference. It applies in this browser only."
