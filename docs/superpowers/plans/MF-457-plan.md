# MF-457 implementation plan: web governance pages

## Goal
Replace the five governance placeholder pages (change requests, audit log, API & SDK keys, personal tokens, Settings) with working, permission-aware, responsive pages, backed by five small read-only API additions, and close UI-14 (audit log), UI-15, G-1, G-2, G-3, G-5 and the GA "Change requests" row.

## Architecture
- **API (steps 1–5):** backward-compatible additions with no migration: `can_approve`/`can_cancel` on `ChangeRequestOut`, `GET /api/tokens?project=` (newest first), `GET /api/permissions`, set-up URLs on `SsoProviderOut`, and `invitation_expires_at` on `UserOut` plus invite-purpose links from `reset-link`. Each step regenerates `api/openapi.json` and `web/lib/api/schema.d.ts` in the same commit.
- **Web (steps 6–27):** client components fed by the generated `api` client, `useApiData` and `must`, like `components/integrations/integration-list.tsx`. Pure logic lives in `web/lib/<area>/*.ts` with Vitest tests. Filters, tabs and the selected environment live in URL search params (`router.replace(..., { scroll: false })`, as in `flag-list.tsx` and `flag-detail.tsx`). Lists use `DataList` (table from 768 px, cards below it); forms use `ResponsiveDialog` (full screen under 768 px).
- **Tests:** pytest first for the API; Vitest for helpers; one Playwright spec per page, written before the UI in each step; matrix entries at six widths and two themes against a seeded `gov` project (step 28).

## Conventions (every step)
- Branch: the current `megoo/MF-457-web-ui-governance-pages-change-requests`. Do not push.
- Commit message: `<type>(api|web|docs): <summary> (MF-457)`, a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Match the surrounding style: short doc comments on exported things, `"use client"` components, `aria-hidden` icons, 44 px targets (`min-h-11 min-w-11`), `toast` for outcomes, `showError` pattern from `integration-list.tsx`.
- Never use `PlaceholderPage` in the five routes. It stays for Segments and Environments.

### Commands
- **API-TEST** `<files>`: `megoocolab-runner services run api -- bash -c 'cd api && uv run pytest <files> -q'`
- **API-LINT**: `cd api && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy`
- **REGEN** (from the repository root): `uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api`
- **UNIT** `<file>`: `cd web && pnpm exec vitest run <file>`
- **WEB-GATE**: `cd web && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test` (run `pnpm format` first if format:check fails)
- **E2E** `<spec>` (Postgres and Valkey from the `db` set; the API on :8000 as AGENTS.md §9 requires; run `cd web && pnpm exec playwright install chromium` once):
  ```
  megoocolab-runner services run db -- bash -c 'set -e
  cd api && uv sync --frozen && uv run alembic upgrade head
  MEGOOFLAGS_SECRET_KEY=e2e-local-secret-key-0123456789abcdef MEGOOFLAGS_PUBLIC_URL=http://localhost:3000 MEGOOFLAGS_API_WORKERS=1 MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true MEGOOFLAGS_ROLE=control-plane uv run python -m megooflags_api.serve &
  cd ../web && pnpm install --frozen-lockfile && pnpm build && pnpm exec playwright test <spec>'
  ```
- **SNAPSHOTS**: the same E2E command, with its last line replaced by `cd ../web && pnpm e2e:docker e2e/matrix.spec.ts --update-snapshots` (the pinned Playwright image; never take baselines from a local browser).

## Contracts shared across steps

### C-API: API shapes after steps 1–5
- `ChangeRequestOut` gains `can_approve: bool` and `can_cancel: bool`.
- `GET /api/tokens` gains the optional query `project` (a project key; 404 if unknown). The response stays `Page[TokenOut]`, newest first (cursor `created_at|id`).
- `GET /api/permissions` → `PermissionOut[]` with `{ permission: Permission, level: "org" | "project" | "environment" }`, in catalogue order (`Permission` enum order). Any signed-in caller.
- `SsoProviderOut` gains `redirect_uri`, `acs_url`, `entity_id` and `metadata_url`, all `string | null`. OIDC sets only `redirect_uri` (`<public>/api/auth/sso/<key>/callback`). SAML sets `acs_url` (`<public>/api/auth/sso/<key>/saml/acs`), `metadata_url` (`<public>/api/auth/sso/<key>/saml/metadata`) and `entity_id` (`saml.sp_entity_id` or `metadata_url`). All are null without `MEGOOFLAGS_PUBLIC_URL`.
- `UserOut` gains `invitation_expires_at: datetime | null`: the expiry of the newest unused `invite`-purpose link (also once it has passed); null once it is used or superseded. `POST /api/users/{id}/reset-link` issues purpose `invite` when the user has no password and `last_login_at` is null; otherwise `reset`.

### C-WEB-LIB: helper modules (exact exports)
`web/lib/access/permissions.ts`
```ts
export type Permission = components["schemas"]["Permission"];
export type Level = components["schemas"]["ScopeKind"]; // "org" | "project" | "environment"
export type PermissionInfo = components["schemas"]["PermissionOut"];
export const PERMISSION_LABELS: Record<Permission, string>;
export const LEVEL_TEXT: Record<Level, { label: string; where: string }>;
export function worksAt(level: Level, scope: Level): boolean; // rank(scope) <= rank(level); org 0, project 1, environment 2
export function groupByLevel(catalogue: PermissionInfo[]): { level: Level; permissions: Permission[] }[];
export function heldAt(perms: MePermissions, scope: Level, env?: string): Permission[];
export function grantableAt(catalogue: PermissionInfo[], held: readonly Permission[], scope: Level): Permission[];
```
Labels: org.manage "Manage organization settings and SSO", member.manage "Manage members and teams", role.manage "Manage roles", token.manage "Manage service tokens", project.create "Create projects", audit.read "Read the audit log", project.read "See the project", project.manage "Manage the project", environment.manage "Manage environments", flag.create "Create flags", flag.update "Edit flags and variations", flag.archive "Archive, restore and delete flags", segment.write "Edit segments", flag.config.write "Edit targeting", flag.toggle "Turn flags on and off", change.approve "Approve change requests", evaluate "Evaluate flags", sdk_key.read "See SDK keys", sdk_key.manage "Manage SDK keys".
Level text: org {"Organization", "Works only when granted for the whole organization."}; project {"Project", "Works for the whole organization or for one project."}; environment {"Environment", "Works for the organization, a project or one environment."}.
`heldAt`: org → `perms.org`; project → `perms.project ?? []`; environment → `perms.environments[env] ?? []`.

`web/lib/access/bindings.ts`
```ts
export type ScopeValue = { scope: Level; project: string | null; env: string | null };
export type AccessRow = ScopeValue & { role: string };
export function scopeText(s: ScopeValue): string;            // "Organization" | "Project shop" | "shop / production"
export function bindingText(b: { role: string } & ScopeValue): string; // "viewer · Organization"
export function scopeBody(s: ScopeValue): { scope: Level; project?: string; env?: string };
export function isFromSso(b: { source: string }): boolean;   // source !== "manual"
export function reachesProject(b: ScopeValue, project: string): boolean; // org scope, or b.project === project
export function usedByText(f: ApiFailure): string;           // "This role is still used by: 2 bindings, SSO provider okta, group devs of okta"
export function formFailureText(f: ApiFailure): string;      // 403 and exceeds_owner_rights: f.message plus details.permissions; in_use: usedByText; else failureText(f)
```

`web/lib/keys/status.ts`
```ts
export type Credential = { expires_at: string | null; revoked_at: string | null };
export type CredentialState = { kind: "active" | "expiring" | "expired" | "revoked"; label: string };
export function credentialState(c: Credential, now: Date): CredentialState; // revoked > expired > "Expires in 3 hours" > "Active"
export function credentialPill(s: CredentialState): { status: Status; label: string }; // active on, expiring pending, expired off, revoked error
export function isInactive(c: Credential, now: Date): boolean;
export function visibleCredentials<T extends Credential>(rows: T[], showInactive: boolean, now: Date): T[];
export const SDK_KIND_TEXT: Record<"server" | "client", { label: string; help: string }>;
```
`web/lib/tokens/expiry.ts`
```ts
export type ExpiryChoice = "never" | "30d" | "90d" | "1y" | "date";
export type ExpiryValue = { choice: ExpiryChoice; date: string }; // date "YYYY-MM-DD"
export const EXPIRY_CHOICES: { value: ExpiryChoice; label: string }[]; // Never, 30 days, 90 days, 1 year, On a date
export const PERSONAL_EXPIRY_DEFAULT: ExpiryValue; // { choice: "90d", date: "" }
export const SERVICE_EXPIRY_DEFAULT: ExpiryValue;  // { choice: "never", date: "" }
export function expiresAt(v: ExpiryValue, now: Date): string | null; // ISO; "date" = the end (23:59:59.999 local) of that day
export function expiryError(v: ExpiryValue, now: Date): string | undefined; // "Pick a date" | "Pick a date in the future"
export const OVERLAP_CHOICES: { seconds: number; label: string }[]; // 0 "Revoke the old one now", 3600 "1 hour", 86400 "24 hours", 604800 "7 days", 2592000 "30 days"
export const DEFAULT_OVERLAP = 86400;
```
`web/lib/settings/tabs.ts`
```ts
export const SETTINGS_TABS = ["members", "teams", "roles", "tokens", "sso", "security", "audit"] as const;
export type SettingsTab = (typeof SETTINGS_TABS)[number];
export const TAB_LABELS: Record<SettingsTab, string>; // Members, Teams, Roles, Service tokens, SSO, Security, Audit log
export function visibleTabs(org: readonly Permission[]): SettingsTab[]; // members: member.manage; teams, roles: always; tokens: token.manage; sso and security: org.manage; audit: audit.read
export function pickTab(raw: string | null, visible: SettingsTab[]): SettingsTab; // unknown or hidden → visible[0]
```
`web/lib/settings/members.ts`
```ts
export type MemberState = { kind: "active" | "invited" | "invite-expired" | "disabled"; label: string };
export function memberState(u: components["schemas"]["UserOut"], now: Date): MemberState; // disabled > invited (expiry in future) > invite-expired > active
export function memberPill(s: MemberState): { status: Status; label: string }; // active on, invited pending, invite-expired stale, disabled off
export function linkKind(u: components["schemas"]["UserOut"]): "invite" | "reset"; // !has_password && last_login_at === null → invite
```
`web/lib/change-requests/status.ts`
```ts
type CR = components["schemas"]["ChangeRequestSummary"];
export const KIND_LABELS: Record<CR["kind"], string>; // config "Targeting", toggle "On/off", schedule "Schedule", rollout "Rollout"
export function crPill(status: CR["status"]): { status: Status; label: string }; // open pending "Open", applied on "Applied", rejected killed "Rejected", cancelled off "Cancelled", failed error "Failed"
export function approvalsText(cr: Pick<CR, "approvals" | "approvals_required">): string; // "1 of 2 approvals"
export function isLastApproval(cr: Pick<CR, "approvals" | "approvals_required">): boolean; // approvals + 1 >= approvals_required
export function isStale(cr: { status: CR["status"]; base_version: number }, currentVersion: number | null): boolean;
```
`web/lib/change-requests/summary.ts`
```ts
export function proposedSummary(kind: CR["kind"], payload: Record<string, unknown>, formatDate?: (iso: string) => string): string;
export function currentSummary(kind: CR["kind"], config: components["schemas"]["FlagConfigOut"]): string;
```
Toggle: "Turn the flag on"/"Turn the flag off". Schedule: "Turn on at <date>", "Turn off at <date>", "Change the rollout at <date> to a 50%, b 50%" (weights are thousandths of a percent: 50000 → 50%). Rollout: "Roll out from <from> to <to> in 3 steps: 10%, 50%, 100%". Current: "Currently on"/"Currently off", plus ", serving <variation>" or ", serving a 50%, b 50%" from `fallthrough` for rollout and schedule.
`web/lib/change-requests/filters.ts`
```ts
export type CrFilters = { status: CR["status"] | "all"; env: string; flag: string };
export function crFiltersFromParams(p: URLSearchParams): CrFilters; // missing or invalid status → "open"
export function crFiltersToParams(f: CrFilters): URLSearchParams;   // omits status=open, empty env and flag
export function crListQuery(f: CrFilters, cursor?: string): { status?: CR["status"]; env?: string; flag?: string; cursor?: string; limit: number }; // limit 50
export function canComment(perms: MePermissions | null, env: string, isAuthor: boolean): boolean; // author, or flag.toggle / flag.config.write / change.approve in env
```
`web/lib/audit/filters.ts`
```ts
export type AuditScope = { kind: "project"; project: string } | { kind: "org" };
export type AuditFilterValues = { action: string; actor: string; resource_type: string; resource_key: string; project: string; env: string; from: string; to: string }; // dates "YYYY-MM-DD"
export const EMPTY_AUDIT_FILTERS: AuditFilterValues;
export function auditFiltersFromParams(p: URLSearchParams): AuditFilterValues;
export function auditFiltersToParams(f: AuditFilterValues, base: URLSearchParams): URLSearchParams; // keeps other params (tab), drops empty values
export function auditQuery(f: AuditFilterValues, scope: AuditScope): Record<string, string>; // project scope forces project; env only with a project; from → local midnight ISO; to → next local midnight ISO (exclusive)
export function exportUrl(f: AuditFilterValues, scope: AuditScope, format: "csv" | "ndjson"): string; // "/api/audit/export?<query>&format=csv"
export function activeFilterCount(f: AuditFilterValues, scope: AuditScope): number;
```
`web/lib/audit/actions.ts`
```ts
export const AUDIT_ACTIONS: readonly string[];   // every action the API records, sorted
export const RESOURCE_TYPES: readonly string[];  // every resource_type the API records, sorted
export function actionLabel(action: string): string; // known map, else humanized ("team.member.add" → "Team member add")
export function resourceHref(e: components["schemas"]["AuditEntryOut"]): string | null; // flag → flagPath(+?env=), change_request → changeRequestPath, needs e.project
export function snapshotText(v: Record<string, unknown> | null): string; // JSON, 2 spaces; null → ""
```

### C-WEB-UI: shared components
- `components/patterns/secret-reveal.tsx`: `SecretReveal({ secret: string | null; title: string; description?: ReactNode; copyLabel: string; onClose: () => void; children?: ReactNode })`. An alertdialog (`ResponsiveDialog alert`), Done is the only way out; `children` show under the copy button.
- `components/patterns/list-state.tsx`: `LoadFailed({ title, failure, onRetry })` (the destructive `Alert` with Try again from `integration-list.tsx`) and `LoadingRows({ label, rows = 3 })` (the `role="status"` skeleton list).
- `components/access/use-catalogue.ts`: `usePermissionCatalogue(): PermissionInfo[] | null`, `useRoles(): RoleOut[] | null` (`GET /api/roles?limit=500`), `useProjectKeys(): string[] | null` (`GET /api/projects`; read its response shape in `schema.d.ts`), `useEnvironmentKeys(project: string | null): string[] | null`.
- `components/access/scope-picker.tsx`: `ScopePicker({ value: ScopeValue; onChange; levels?: Level[]; lockedProject?: string; idPrefix: string })`. Native selects (`components/ui/native-select.tsx`) for level, project and environment.
- `components/access/access-rows.tsx`: `AccessRows({ value: AccessRow[]; onChange; lockedProject?: string; idPrefix: string })`, used by create forms.
- `components/access/access-editor.tsx`: `AccessEditor({ subjectKind: "user" | "team" | "service_token"; subjectId: string; readOnly: boolean; lockedProject?: string; onChanged?: () => void })`. Lists `GET /api/bindings?subject_kind&subject_id&limit=500`; SSO bindings (`isFromSso`) are read-only; adds through `POST /api/bindings`; removes through `DELETE /api/bindings/{id}` after a `ConfirmDialog`; errors through `formFailureText`.
- `components/keys/rotate-dialog.tsx`: `RotateDialog({ open; onOpenChange; name: string; onRotate: (overlapSeconds: number) => Promise<void> })`, a radio group of `OVERLAP_CHOICES`.
- `components/tokens/expiry-field.tsx`: `ExpiryField({ value: ExpiryValue; onChange; idPrefix: string; error?: string })`.

### C-E2E: test data rules
- New e2e user `REVIEWER` (`reviewer@e2e.test`, "Rae Reviewer", org `editor`, `e2e/.auth/reviewer.json`) in `web/e2e/support/users.ts` and `USERS`; `global-setup.ts` calls `ensureUser(admin, REVIEWER)` (step 13).
- Each spec defines `const RUN = \`e2e-${Date.now().toString(36)}\`` and names everything it creates with it; `afterAll` deletes, revokes or disables those.
- Specs create change requests, keys and project-scoped tokens only in `shop`. Users invited by specs use the domain `@invite.e2e.test`. Specs never create org-wide service tokens or personal tokens for the admin.
- The `gov` project and the `gov-*` team, role, token and SSO provider belong to the visual matrix (step 28): no spec touches them.

## File map
**API (modify)**
- `api/src/megooflags_api/schemas/change_requests.py`, `services/change_requests.py`, `routers/change_requests.py`: `can_approve`, `can_cancel`.
- `api/src/megooflags_api/services/api_tokens.py`, `routers/api_tokens.py`: project filter, newest-first paging.
- `api/src/megooflags_api/schemas/identity.py`, `routers/roles.py`: `PermissionOut`, `GET /permissions`; `UserOut.invitation_expires_at`.
- `api/src/megooflags_api/schemas/sso.py`, `auth/sso/saml.py`, `services/sso_providers.py`, `routers/sso.py`: set-up URLs.
- `api/src/megooflags_api/services/users.py`, `services/teams.py`, `routers/users.py`: invitation expiry, invite-purpose links.
- Tests: `api/tests/test_change_requests.py`, `test_service_tokens.py`, `test_roles_bindings.py`, `test_sso_providers.py`, `test_users_invitations.py`.
- Generated: `api/openapi.json`, `web/lib/api/schema.d.ts`.

**Web (create)**
- `lib/access/{permissions,bindings}.ts`, `lib/keys/status.ts`, `lib/tokens/expiry.ts`, `lib/settings/{tabs,members}.ts`, `lib/change-requests/{status,summary,filters}.ts`, `lib/audit/{filters,actions}.ts`, each with `tests/unit/<area>-<file>.test.ts`.
- `components/patterns/{secret-reveal,list-state}.tsx` (secret-reveal moved from `components/integrations/`).
- `components/access/{use-catalogue.ts,scope-picker.tsx,access-rows.tsx,access-editor.tsx}`.
- `components/change-requests/{change-request-list,change-request-detail,proposed-change,review-actions,comments}.tsx`.
- `components/audit/{audit-log,audit-filters,audit-entry-details,export-dialog}.tsx`.
- `components/keys/{keys-page,sdk-key-list,new-sdk-key-dialog,rotate-dialog}.tsx`.
- `components/tokens/{service-token-list,new-service-token-dialog,token-access-dialog,personal-token-list,new-personal-token-dialog,expiry-field}.tsx`.
- `components/settings/{settings-page,members-tab,invite-dialog,member-dialog,teams-tab,team-dialog,roles-tab,role-dialog,sso-tab,provider-form,setup-urls-dialog,mappings-dialog,security-tab}.tsx`.
- `app/(app)/projects/[project]/change-requests/[id]/page.tsx`.
- `e2e/{change-requests,audit,keys,account-tokens,settings}.spec.ts`.

**Web (modify)**
- `app/(app)/projects/[project]/{change-requests,audit,keys}/page.tsx`, `app/(app)/account/tokens/page.tsx`, `app/(app)/settings/page.tsx`.
- `lib/routes.ts` (`changeRequestPath`), `lib/nav.ts`, `lib/flags/errors.ts`, `components/integrations/integration-list.tsx`.
- `tests/unit/{nav,routes,flags-errors}.test.ts`, `e2e/shell.spec.ts`, `e2e/support/users.ts`, `e2e/global-setup.ts`, `e2e/matrix.spec.ts`, `e2e/matrix.spec.ts-snapshots/`.

**Docs:** `docs/ga-checklist.md`, `CHANGELOG.md`.

## Steps

### 1. API: can_approve and can_cancel on change requests
**Files:** `api/src/megooflags_api/schemas/change_requests.py`, `services/change_requests.py`, `routers/change_requests.py`, `api/tests/test_change_requests.py`, generated files, `CHANGELOG.md`.

**Tests first** (append to `api/tests/test_change_requests.py`, reusing `color_flag`, `protect`, `created`, `reviewer`, `approve`, `token_client`, `C`):
- `test_can_approve_and_can_cancel_follow_the_caller`: `protect(api, approvals=2)`, `cr = await created(api)`. GET `{C}/{id}` as `api` (the author): `can_approve` False, `can_cancel` True. As `reviewer(app)` (editor, which holds `project.manage`): True, True. As `token_client(app, "viewer", "project")`: False, False. After the reviewer approves, the reviewer's GET gives `can_approve` False; `reviewer(app, name="second")` still gets True.
- `test_closed_requests_cannot_be_approved_or_cancelled`: cancel as `api`; the cancel response and a reviewer's GET give False and False.

**Run:** API-TEST `tests/test_change_requests.py -k "can_approve or closed_requests"`. Expected failure: `KeyError: 'can_approve'`.

**Implement:**
- `ChangeRequestOut`: add `can_approve: bool` and `can_cancel: bool`; extend the docstring with the rules from C-API.
- `services/change_requests.py`: `@dataclass(frozen=True) class Viewer: actor_id: uuid.UUID; may_approve: bool; may_manage: bool`. `out(session, cr, viewer: Viewer)` computes, from the reviews it already loads, `reviewed = any(r.reviewer_id == viewer.actor_id ...)`. Then `can_approve = open and viewer.may_approve and viewer.actor_id != cr.author_id and not reviewed`, and `can_cancel = open and (viewer.actor_id == cr.author_id or viewer.may_manage)`.
- `routers/change_requests.py`: `def _viewer(authz: Authz, cr: ChangeRequest) -> change_requests.Viewer` from `authz.principal.actor_id`, `authz.authorizer.can(Permission.CHANGE_APPROVE, project.id, cr.environment_id)` and `authz.authorizer.can(Permission.PROJECT_MANAGE, project.id)`. Pass it to all five `out` calls.
- REGEN. CHANGELOG `### Added`: "Change requests returned by the API say whether you may approve (`can_approve`) or cancel (`can_cancel`) them."

**Pass:** API-TEST `tests/test_change_requests.py tests/test_approvals_gate.py tests/test_openapi.py`, then API-LINT.

**Commit:** `feat(api): can_approve and can_cancel on change requests (MF-457)`

### 2. API: list service tokens by project, newest first
**Files:** `api/src/megooflags_api/services/api_tokens.py`, `routers/api_tokens.py`, `api/tests/test_service_tokens.py`, generated files, `CHANGELOG.md`.

**Tests first** (in `test_service_tokens.py`; create tokens with `POST /api/tokens` through the `api` fixture, whose own token "tests" is the oldest):
- `test_tokens_are_listed_newest_first`: create `a`, `b`, `c` → names in the order `c, b, a, tests`. Walking with `limit=1` and `next_cursor` gives the same order.
- `test_tokens_filter_by_project`: `create_project(api, "other")` (from `helpers`). Create `org` (viewer at org), `proj` (viewer at project demo), `env` (viewer at environment demo/production), `elsewhere` (viewer at project other) and `none` (no bindings). `?project=demo` gives exactly `env, proj, org, tests`.
- `test_tokens_filter_by_unknown_project`: `assert_error(r, 404, "not_found")`.
- Update any existing assertion that relied on id order.

**Run:** API-TEST `tests/test_service_tokens.py`. Expected: the order and filter assertions fail.

**Implement:**
- `list_service_tokens(session, cursor, limit, project_id: uuid.UUID | None = None)`. Order by `ApiToken.created_at.desc(), ApiToken.id.desc()`. The cursor is `f"{created_at.isoformat()}|{id}"`, decoded like `services/change_requests._cursor_key` (copy it as a private helper). Apply it with `tuple_(created_at, id) < tuple_(...)`. With `project_id`, add `ApiToken.id.in_(select(RoleBinding.subject_id).where(RoleBinding.subject_kind == SubjectKind.SERVICE_TOKEN, or_(RoleBinding.scope == ScopeKind.ORG, RoleBinding.project_id == project_id)))`. Update the docstring.
- Router: `project: Annotated[str | None, Query(description="A project key: tokens with a binding in it, in one of its environments, or at org scope.")] = None`, resolved with `services.lookup.get_project`.
- REGEN. CHANGELOG `### Added`: "`GET /api/tokens?project=<key>` lists the service tokens that can act in a project. The list is now newest first."

**Pass:** API-TEST `tests/test_service_tokens.py tests/test_openapi.py`, then API-LINT.

**Commit:** `feat(api): filter service tokens by project, newest first (MF-457)`

### 3. API: GET /api/permissions
**Files:** `api/src/megooflags_api/schemas/identity.py`, `routers/roles.py`, `api/tests/test_roles_bindings.py`, generated files, `CHANGELOG.md`.

**Tests first:**
- `test_permissions_list_the_catalogue_with_levels`: `seed_user(app, "viewer@example.com", role="viewer")`, `sign_in(anon, "viewer@example.com")`, GET `/api/permissions` → 200 and equal to `[{"permission": p.value, "level": PERMISSION_LEVEL[p].value} for p in Permission]`. Spot checks: `org.manage` is `org`, `audit.read` is `project`, `change.approve` is `environment`.
- `test_permissions_need_a_sign_in`: `anon` without signing in → 401.

**Run:** API-TEST `tests/test_roles_bindings.py -k permissions`. Expected: 404 for the route.

**Implement:**
- `class PermissionOut(ApiModel)` with `permission: Permission` and `level: ScopeKind`. Docstring: "A permission and the most specific scope it can be granted at. Granted below that scope it does nothing."
- In `routers/roles.py`: `@router.get("/permissions", tags=["roles"]) async def list_permissions(authz: Authenticated) -> list[PermissionOut]`, returning `[PermissionOut(permission=p, level=PERMISSION_LEVEL[p]) for p in Permission]`.
- REGEN. CHANGELOG `### Added`: "`GET /api/permissions` lists every permission with the level (organization, project, environment) where it works."

**Pass:** API-TEST `tests/test_roles_bindings.py tests/test_route_permissions.py tests/test_openapi.py`, then API-LINT.

**Commit:** `feat(api): list permissions with their level (MF-457)`

### 4. API: set-up URLs on SSO providers
**Files:** `api/src/megooflags_api/schemas/sso.py`, `auth/sso/saml.py`, `services/sso_providers.py`, `routers/sso.py`, `api/tests/test_sso_providers.py`, generated files, `CHANGELOG.md`.

**Tests first** (in `test_sso_providers.py`; reuse its existing OIDC and SAML create bodies):
- Add a `public_api` fixture modelled on `conftest.app` and `conftest.api`, but with `create_app(Settings(public_url="https://flags.example.org/"))`.
- `test_oidc_provider_shows_its_redirect_uri`: on create, GET one, the list and PATCH, `redirect_uri == "https://flags.example.org/api/auth/sso/<key>/callback"`, and the three SAML fields are None.
- `test_saml_provider_shows_acs_entity_and_metadata_urls`: `acs_url` ends in `/api/auth/sso/<key>/saml/acs`, `metadata_url` ends in `/saml/metadata`, and `entity_id == metadata_url`; with `sp_entity_id` set, `entity_id` equals it. `redirect_uri` is None.
- `test_setup_urls_are_null_without_a_public_url`: the default `api` fixture gives all four None.

**Run:** API-TEST `tests/test_sso_providers.py -k "redirect_uri or acs or setup_urls"`. Expected: `KeyError`.

**Implement:**
- `SsoProviderOut`: `redirect_uri`, `acs_url`, `entity_id`, `metadata_url: str | None = None`, with a comment that they are what the identity provider's admin copies, and null without `MEGOOFLAGS_PUBLIC_URL`.
- `auth/sso/saml.py`: a public `def sp_urls(key: str, sp_entity_id: str | None, public_url: str) -> tuple[str, str, str]` returning `(acs_url, entity_id, metadata_url)`; `_sp_settings` uses it.
- `services/sso_providers.py`: `def callback_url(public_url: str, key: str) -> str` and `def with_setup_urls(out: SsoProviderOut, public_url: str | None) -> SsoProviderOut` (`model_copy(update=...)`).
- `routers/sso.py`: `_callback_url` uses `callback_url`. A helper `_setup_base(request) -> str | None` returns None when `settings.public_url` is None, else `public_base_url(request)`. Apply `with_setup_urls` to the list, create, read and update responses, after the audit records, so snapshots do not change.
- REGEN. CHANGELOG `### Added`: "SSO providers returned by the API include the redirect URI (OIDC) or the ACS URL, entity ID and metadata URL (SAML) to enter at the identity provider."

**Pass:** API-TEST `tests/test_sso_providers.py tests/test_sso_oidc.py tests/test_sso_saml.py tests/test_openapi.py`, then API-LINT.

**Commit:** `feat(api): SSO set-up URLs on providers (MF-457)`

### 5. API: invitation state on users and invite links for people who never joined
**Files:** `api/src/megooflags_api/schemas/identity.py`, `services/users.py`, `services/teams.py`, `routers/users.py`, `api/tests/test_users_invitations.py`, generated files, `CHANGELOG.md`.

**Tests first** (in `test_users_invitations.py`):
- `test_a_new_user_shows_the_invitation_expiry`: `POST /api/users` → `user.invitation_expires_at == invite.expires_at`, and the same from `GET /api/users/{id}` and `GET /api/users?q=`.
- `test_the_invitation_expiry_stays_after_it_passes`: move the row's `Invitation.expires_at` into the past through `app.state.sessionmaker` → GET returns that past time.
- `test_accepting_clears_the_invitation_expiry`: `POST /api/invitations/{token}` with a password → null.
- `test_reset_link_for_someone_who_never_joined_is_an_invite`: a created user → `POST /api/users/{id}/reset-link` → `GET /api/invitations/<token>` has `purpose == "invite"`, and the user's `invitation_expires_at` equals the new link's expiry.
- `test_reset_link_for_a_user_with_a_password_is_a_reset`: `seed_user` → purpose `reset`, `invitation_expires_at` null.
- `test_reset_link_for_an_sso_user_who_signed_in_is_a_reset`: a created user with `last_login_at` set in the database → `reset`.
- The token is the last path segment of `url`.

**Run:** API-TEST `tests/test_users_invitations.py`. Expected: `KeyError` or wrong purpose.

**Implement:**
- `UserOut`: `invitation_expires_at: datetime | None = None`, with a comment.
- `services/users.py`:
  - `user_out(user, invitation_expires_at: datetime | None = None)`.
  - `async def invitation_expiry(session, user_ids: Collection[uuid.UUID]) -> dict[uuid.UUID, datetime]`: `select(Invitation.user_id, func.max(Invitation.expires_at))`, filtered on purpose INVITE, `used_at IS NULL` and the ids, grouped by user.
  - `async def users_out(session, rows: Sequence[User]) -> list[UserOut]` and `async def full_user_out(session, user) -> UserOut`. `list_users` uses `users_out`.
- `routers/users.py`: create, read, update, disable and enable use `full_user_out`, for their snapshots too. `reset_link` picks `InvitationPurpose.INVITE if user.password_hash is None and user.last_login_at is None else InvitationPurpose.RESET`. Docstring: "A link to set a password: an invitation for someone who has no password and has never signed in, otherwise a reset. Older links stop working."
- `services/teams.py`: build member `UserOut`s with `invitation_expiry` (one query per page).
- REGEN. CHANGELOG `### Added`: "Users returned by the API show when their unused invitation link expires (`invitation_expires_at`)." `### Changed`: "A new link for someone who never joined is an invitation again, not a password reset."

**Pass:** API-TEST `tests/test_users_invitations.py tests/test_users_service.py tests/test_teams.py tests/test_audit_coverage.py tests/test_openapi.py`, then API-LINT, then the full API-TEST `tests` as the API gate.

**Commit:** `feat(api): invitation expiry on users, invite links for people who never joined (MF-457)`

### 6. Web: shared secret reveal, list states and failure texts
**Files:** move `web/components/integrations/secret-reveal.tsx` to `web/components/patterns/secret-reveal.tsx` (`git mv`); create `web/components/patterns/list-state.tsx`; modify `web/components/integrations/integration-list.tsx`, `web/lib/flags/errors.ts`, `web/tests/unit/flags-errors.test.ts`.

**Tests first** (`tests/unit/flags-errors.test.ts`):
- `toFailure` keeps object `details` (`failure.details` equals the body's `details`), and validation arrays still give `path`.
- `failureText` maps:
  - `last_admin` → "This would leave the organization without an admin."
  - `key_inactive` → "This key is no longer active"
  - `self_approval` → "You can't review your own change request"
  - `public_url_required` → "Set MEGOOFLAGS_PUBLIC_URL first"
  - `exceeds_owner_rights` → `"<message>: flag.toggle, change.approve"` (from `details.permissions`)
- Existing cases still pass.

**Run:** UNIT `tests/unit/flags-errors.test.ts`. Expected: the new cases fail.

**Implement:**
- `ApiFailure` gains `details?: unknown`. `toFailure` sets it from `body.details` (null when absent). Add the codes to `failureText`, before the 403 and 404 fallbacks.
- `SecretReveal` gets the props in C-WEB-UI (`title`, `description` defaulting to "Copy it now. You won't see it again.", `copyLabel`, `children`). Update the Integrations import to pass `title="Signing secret"` and `copyLabel="Copy secret"`.
- `list-state.tsx`: `LoadFailed` and `LoadingRows`, extracted from the markup in `integration-list.tsx` (leave that file's own markup as it is).

**Pass:** UNIT `tests/unit/flags-errors.test.ts`; WEB-GATE; E2E `e2e/integrations.spec.ts` (the secret dialog still works).

**Commit:** `refactor(web): shared secret reveal and list states, more failure texts (MF-457)`

### 7. Web: navigation for governance pages
**Files:** `web/lib/routes.ts`, `web/lib/nav.ts`, `web/tests/unit/routes.test.ts`, `web/tests/unit/nav.test.ts`, `web/e2e/shell.spec.ts`.

**Tests first:**
- `routes.test.ts`: `changeRequestPath("my shop", "abc")` → `/projects/my%20shop/change-requests/abc`. `switchProjectPath("/projects/a/change-requests/abc", "b")` still → `/projects/b/flags` (deeper pages fall back).
- `nav.test.ts`:
  - `org: ["audit.read"]` shows `settings`; `org: []` does not.
  - `org: ["token.manage"]` with no `sdk_key.read` in any environment shows `keys`.
  - Replace the "viewer … not … settings" case with a viewer holding org `audit.read`, who now sees settings but not integrations.
- `e2e/shell.spec.ts`: rename "a viewer doesn't see Settings or Integrations" to "a viewer sees Settings but not Integrations", and expect the Settings link count to be 1.

**Run:** UNIT `tests/unit/routes.test.ts tests/unit/nav.test.ts`. Expected: missing export and visibility failures.

**Implement:**
- `export function changeRequestPath(project: string, id: string): string` → `${projectPath(project, "change-requests")}/${encodeURIComponent(id)}`.
- `nav.ts`: Settings shows when `perms.org` holds any `SETTINGS_PERMISSIONS` or `"audit.read"`. The `keys` item shows when any environment holds `sdk_key.read` or `perms.org` holds `token.manage`.

**Pass:** UNIT for both files; WEB-GATE; E2E `e2e/shell.spec.ts`.

**Commit:** `feat(web): Settings for org audit readers, keys for token managers (MF-457)`

### 8. Web: access helpers (permissions, levels, bindings)
**Files:** create `web/lib/access/permissions.ts`, `web/lib/access/bindings.ts`, `web/tests/unit/access-permissions.test.ts`, `web/tests/unit/access-bindings.test.ts`. Exports exactly as in C-WEB-LIB.

**Tests first:**
- permissions:
  - `PERMISSION_LABELS` has a label for every value in the `ALL` list (copy it from `nav.test.ts`).
  - `worksAt`: (`environment`, `org`) true; (`org`, `project`) false; (`project`, `environment`) false.
  - `groupByLevel` over a sample catalogue keeps the order org, project, environment and the catalogue order within each, and drops empty groups.
  - `heldAt` for org, project (null project → `[]`) and environment.
  - `grantableAt(catalogue, ["org.manage","audit.read","flag.toggle"], "project")` → `["audit.read","flag.toggle"]`; at `"environment"` → `["flag.toggle"]`; at `"org"` → all three.
- bindings:
  - `scopeText` for the three levels.
  - `bindingText({role:"viewer",scope:"org",project:null,env:null})` → "viewer · Organization".
  - `scopeBody` drops nulls.
  - `isFromSso({source:"sso:okta"})` true; `"manual"` false.
  - `reachesProject` for org, the same project and another project.
  - `usedByText` on a failure whose `details.used_by` has two `binding` entries, an `sso_provider` "okta" and an `sso_mapping` okta/devs → "This role is still used by: 2 bindings, SSO provider okta, group devs of okta".
  - `formFailureText`: 403 forbidden with message "cannot grant x" → that message; `exceeds_owner_rights` → message plus permissions; `last_admin` → the last-admin sentence.

**Run:** UNIT `tests/unit/access-permissions.test.ts tests/unit/access-bindings.test.ts`. Expected: module not found.

**Implement** the two modules. `Level` and `PermissionInfo` come from the regenerated schema (`ScopeKind`, `PermissionOut` from step 3). `MePermissions` comes from `@/lib/nav`. `formFailureText` delegates to `failureText` from `@/lib/flags/errors`.

**Pass:** the same UNIT command, then WEB-GATE.

**Commit:** `feat(web): access helpers for permissions, levels and bindings (MF-457)`

### 9. Web: key, token and settings helpers
**Files:** create `web/lib/keys/status.ts`, `web/lib/tokens/expiry.ts`, `web/lib/settings/tabs.ts`, `web/lib/settings/members.ts`, and the tests `web/tests/unit/keys-status.test.ts`, `tokens-expiry.test.ts`, `settings-tabs.test.ts`, `settings-members.test.ts`. Exports exactly as in C-WEB-LIB.

**Tests first** (fixed `now = new Date("2026-11-22T12:00:00Z")`):
- `credentialState`:
  - revoked wins over an expiry → "Revoked".
  - Past expiry → "Expired".
  - +3 h → kind `expiring`, label "Expires in 3 hours" (use `Intl.RelativeTimeFormat("en", { numeric: "always" })` with the largest whole unit: minutes, hours or days).
  - No expiry → "Active".
- `credentialPill` mapping; `visibleCredentials` hides revoked and expired unless `showInactive`.
- `expiresAt`: never → null; `30d` → now + 30 days; `date` "2026-12-01" → the end of that local day; `expiryError` for an empty date and a past date.
- `OVERLAP_CHOICES` seconds are `[0, 3600, 86400, 604800, 2592000]`; `DEFAULT_OVERLAP` is 86400.
- `visibleTabs`:
  - `[]` → `["teams","roles"]`.
  - Viewer `["audit.read","project.read"]` → `["teams","roles","audit"]`.
  - Every permission → all seven in order.
- `pickTab`: "sso" when hidden → first visible; null → first.
- `memberState`: disabled; invited (future expiry); invite expired; active. `linkKind`.

**Run:** UNIT for the four files. Expected: module not found.

**Implement** the modules. `SDK_KIND_TEXT`: server {"Server", "For backend SDKs. Keep it secret: it downloads every flag's rules."}; client {"Client", "For browser and mobile apps. It can only evaluate flags, so it can ship in an app."}. Check the wording against `site/src/content/docs` (grep "client key") and keep it consistent.

**Pass:** the same UNIT command, then WEB-GATE.

**Commit:** `feat(web): helpers for key status, token expiry and settings tabs (MF-457)`

### 10. Web: change request helpers
**Files:** create `web/lib/change-requests/status.ts`, `summary.ts`, `filters.ts`, and the tests `web/tests/unit/change-requests-status.test.ts`, `change-requests-summary.test.ts`, `change-requests-filters.test.ts`. Exports exactly as in C-WEB-LIB.

**Tests first:**
- status:
  - `crPill` for all five statuses.
  - `approvalsText({approvals:1, approvals_required:2})` → "1 of 2 approvals".
  - `isLastApproval` at 1/2 → true and 0/2 → false.
  - `isStale`: open with current 5 and base 4 → true; equal versions → false; closed → false; null version → false.
- summary (pass `formatDate = (iso) => iso.slice(0, 10)`):
  - toggle on and off.
  - schedule enable, disable, and set_rollout with weights `[{variation:"a",weight:50000},{variation:"b",weight:50000}]` → "Change the rollout at 2026-12-01 to a 50%, b 50%".
  - rollout with steps 10000/50000/100000 → "Roll out from a to b in 3 steps: 10%, 50%, 100%".
  - `currentSummary` for toggle (on, off) and for rollout with a fallthrough variation and a fallthrough rollout. Read the `Serve` and `WeightIn` shapes in `web/lib/api/schema.d.ts`.
- filters:
  - Missing status → "open"; "all" stays "all"; "bogus" → "open".
  - `crFiltersToParams` omits defaults.
  - `crListQuery` drops `status` for "all" and always sets `limit: 50`.
  - `canComment`: author true; `flag.toggle` in env true; viewer false; null perms and not author false.

**Run:** UNIT for the three files. Expected: module not found.

**Implement** the modules. Types come from `components["schemas"]["ChangeRequestSummary"]`, `["ChangeRequestOut"]` and `["FlagConfigOut"]`; `Status` from `@/components/patterns/status-pill`; `can` from `@/lib/flags/permissions`.

**Pass:** the same UNIT command, then WEB-GATE.

**Commit:** `feat(web): change request helpers (MF-457)`

### 11. Web: audit log helpers
**Files:** create `web/lib/audit/filters.ts`, `web/lib/audit/actions.ts`, `web/tests/unit/audit-filters.test.ts`, `web/tests/unit/audit-actions.test.ts`. Exports exactly as in C-WEB-LIB.

**Build the lists:** `AUDIT_ACTIONS` and `RESOURCE_TYPES` hold every action and resource type the API records. Collect them from `api/tests/test_audit_coverage.py` and with `rg -o 'record(_system)?\(\s*"[a-z_.]+"' api/src` (also the `action=`/string constants in `auth/` and `routers/auth.py`, such as `auth.sign_in.failed`). Sort them. `actionLabel` has readable labels for at least the auth, flag, change_request, user, binding, role, team, token, sdk_key, sso_provider, settings and node actions. Examples: "flag.config.update" → "Targeting changed", "auth.sign_in.failed" → "Sign-in failed", "change_request.approve" → "Change request approved". Unknown actions are humanized.

**Tests first:**
- filters:
  - `auditFiltersFromParams` round-trips with `auditFiltersToParams`, which keeps an unrelated `tab=audit` param and drops empty values.
  - `auditQuery` with a project scope forces `project`, keeps `env`, and ignores `f.project`. With the org scope it drops `env` when `project` is empty.
  - `from`/`to` "2026-11-01"/"2026-11-02" → `new Date(2026,10,1).toISOString()` and `new Date(2026,10,3).toISOString()`.
  - `exportUrl` → starts with `/api/audit/export?` and ends with `format=ndjson`.
  - `activeFilterCount`.
- actions:
  - `AUDIT_ACTIONS` is sorted, has no duplicates, and includes `flag.config.update`, `auth.sign_in.failed` and `change_request.apply`.
  - `actionLabel` for a known and an unknown action.
  - `resourceHref` for a flag with an environment → `/projects/shop/flags/f?env=production`, a change request → `changeRequestPath`, a user → null, and a flag without a project → null.
  - `snapshotText(null)` → "".

**Run:** UNIT for both files. Expected: module not found.

**Implement** both modules.

**Pass:** the same UNIT command, then WEB-GATE.

**Commit:** `feat(web): audit log helpers (MF-457)`

### 12. Web: access components (catalogue hooks, scope picker, access editor)
**Files:** create `web/components/access/use-catalogue.ts`, `scope-picker.tsx`, `access-rows.tsx`, `access-editor.tsx` with the props in C-WEB-UI.

There is no page to drive these yet, so this step is checked by the type checker and the kit page. Add an "Access" sample to `web/app/(app)/dev/kit/kit-samples.tsx`, next to the other samples there: an `AccessRows` with one row and a `ScopePicker`. `e2e/kit.spec.ts` and the matrix `kit` entry then cover it. Refresh the `kit-*` baselines in step 28.

**Behaviour:**
- `ScopePicker`:
  - A level select (only `levels`). For project and environment, a project select from `useProjectKeys` (fixed and disabled when `lockedProject` is set), and for environment an environment select from `useEnvironmentKeys`.
  - Changing the level resets the deeper fields.
  - Labels use `LEVEL_TEXT`, and the description under the level is `LEVEL_TEXT[level].where`.
- `AccessRows`: per row, a role select from `useRoles` and a `ScopePicker`; Add access and Remove (44 px icon button with `aria-label="Remove access <n>"`).
- `AccessEditor`:
  - Each binding shows `bindingText` and a "From SSO" badge when `isFromSso`.
  - Remove (not for SSO bindings, not when `readOnly`) opens a `ConfirmDialog`.
  - An add form (role + `ScopePicker` + Add) calls `POST /api/bindings` with `{subject_kind, subject_id, role, ...scopeBody(scope)}`.
  - Failures show inline with `role="alert"` and `formFailureText`. Uses `LoadingRows` and `LoadFailed`; `onChanged` runs after each change.

**Run before:** `cd web && pnpm typecheck` fails until the files exist (imports in kit-samples).

**Pass:** WEB-GATE; E2E `e2e/kit.spec.ts`.

**Commit:** `feat(web): scope picker and access editor (MF-457)`

### 13. Web: Change requests list page
**Files:** `web/components/change-requests/change-request-list.tsx`, `web/app/(app)/projects/[project]/change-requests/page.tsx`, `web/e2e/change-requests.spec.ts`, `web/e2e/support/users.ts`, `web/e2e/global-setup.ts`.

**E2E first** (`e2e/change-requests.spec.ts`):
- Add `REVIEWER` (C-E2E) to `users.ts` and `USERS`, and call `ensureUser(admin, REVIEWER)` in `globalSetup`.
- A helper `openRequest(api, kind, flag, title)`: `createTestFlag(api, { prefix: RUN })`, then `POST /api/projects/shop/change-requests` in `production`. Config payloads use the environment's current `version` from `GET .../flags/<key>/environments/production` and `{version, enabled: true, fallthrough: {variation: "true"}}`; toggle payloads use `{enabled: true}`.
- Test "lists open requests and filters them": two requests on different flags. The page opens on Open and shows both, with the status pill and "0 of 1 approvals". Filtering by the flag key leaves one, and the URL has `flag=`. Status "All" and the environment filter work. A title links to the detail page.

**Run:** E2E `e2e/change-requests.spec.ts`. Expected: fails on the placeholder ("Coming soon").

**Implement:**
- The page: the `Suspense` + `Content` pattern from the Integrations page, rendering `<ChangeRequestList project=… />`.
- `ChangeRequestList`:
  - `PageHeader` "Change requests" with `projectTrail`.
  - Filters: a status native select (Open, Applied, Rejected, Cancelled, Failed, All), an environment select from `GET /api/projects/{project}/environments`, and a flag key input (debounced 300 ms). They are kept in the URL through `crFiltersFromParams`/`crFiltersToParams` and `router.replace`.
  - `useApiData` key from the query. A `DataList` with these columns: title (key, `Link` to `changeRequestPath`), status (`StatusPill` from `crPill`), flag + environment + `KIND_LABELS` (main), approvals (main), author and `<time>` created (extra).
  - "Load more" appends pages with `next_cursor`.
  - `LoadingRows`, `LoadFailed`, and an `EmptyState` ("No open change requests" / "No change requests match").

**Pass:** E2E `e2e/change-requests.spec.ts`; WEB-GATE.

**Commit:** `feat(web): change requests list (MF-457)`

### 14. Web: Change request detail
**Files:** `web/app/(app)/projects/[project]/change-requests/[id]/page.tsx`, `web/components/change-requests/change-request-detail.tsx`, `proposed-change.tsx`, `comments.tsx`, `web/e2e/change-requests.spec.ts`.

**E2E first** (append):
- "shows the diff of a targeting request": the config request's page shows `DiffViewer` (group "Proposed change") whose After pane holds the new fallthrough.
- "summarises a toggle request": "Turn the flag on" and "Currently off".
- "comments": the admin (author) adds a comment, and it shows with the author and the time. As VIEWER (`test.use({ storageState: VIEWER.storageState })`) there is no comment box.
- "unknown request": `/projects/shop/change-requests/00000000-0000-0000-0000-000000000000` shows the not-found page (heading from `app/(app)/not-found.tsx`).

**Run:** E2E `e2e/change-requests.spec.ts`. Expected: 404 route.

**Implement:**
- The page: `Suspense` + `Content`, rendering `<ChangeRequestDetail project id />`.
- `ChangeRequestDetail`:
  - Loads `GET .../change-requests/{id}` and calls `notFound()` from `next/navigation` on a 404 failure.
  - Header: `PageHeader` (title = request title, trail = projectTrail + "Change requests"), `StatusPill`, `approvalsText`.
  - A `dl` with flag (`Link` to `flagPath(project, flag)?env=`), environment, `KIND_LABELS`, author and created `<time>`; the description; and `error` in a destructive `Alert` when Failed.
  - The stale warning (`Alert`) when `isStale(cr, config.version)`: "The flag changed after this request was made. Approving it will fail."
- `ProposedChange`:
  - config → `DiffViewer` of `configText(toDraft(cr.current, keys))` against `configText(toDraft({...cr.current, ...cr.proposed} as FlagConfigOut, keys))`, with variation keys from `GET .../flags/{flag}`.
  - Other kinds → `proposedSummary` plus `currentSummary` from `GET .../flags/{flag}/environments/{env}`, which the stale check also uses (for config, use `cr.current.version`).
- Reviews: a list of reviewer, decision and comment.
- `Comments`: a list, plus a `Textarea` and Comment button when `canComment(perms, cr.environment, me.label === cr.author)` and status is open. `me` comes from `GET /api/auth/me`; `perms` from `useProjectPermissions(project, null, null)`.

**Pass:** E2E `e2e/change-requests.spec.ts`; WEB-GATE.

**Commit:** `feat(web): change request detail with diff and comments (MF-457)`

### 15. Web: Approve, reject and cancel
**Files:** `web/components/change-requests/review-actions.tsx`, `change-request-detail.tsx`, `web/e2e/change-requests.spec.ts`.

**E2E first** (append; the reviewer runs in `test.describe` with `storageState: REVIEWER.storageState`):
- "approve applies the change": the reviewer approves with a comment. The dialog says it will apply at once. Status becomes Applied, and the flag's production config (via API) has the new fallthrough.
- "reject needs a reason": the Reject submit stays disabled until a reason is typed; then the status is Rejected.
- "the author cancels": the admin sees Cancel request (and no Approve); confirm → Cancelled.
- "a stale request fails": two config requests at the same version; approving the first, then the second, shows Failed with the API's error text.
- "someone else closed it": open the page, cancel through the API, click Approve → the page reloads and shows "Someone else closed this request".
- "a viewer sees no actions": VIEWER sees no Approve, Reject or Cancel buttons.
- "approve and reject at 344 px": `setViewportSize({width: 344, height: 740})`. The bar's buttons are inside the viewport without scrolling (`toBeInViewport()`), `smallTargets(page)` is `[]`, `noHorizontalScroll(page)` is true, and approve works.

**Run:** E2E `e2e/change-requests.spec.ts`. Expected: no Approve button.

**Implement** `ReviewActions({ project, cr, onChanged, onReload })`:
- A bar rendered when `cr.can_approve || cr.can_cancel`: `sticky bottom-0` with `pb-[max(0.75rem,env(safe-area-inset-bottom))]`, `bg-background`, a border on top and `min-h-11` buttons.
- Approve: `ResponsiveDialog` with an optional comment. The description is "This is the last approval needed, so the change is applied at once." when `isLastApproval`. It sends `POST .../approve {comment?}`; the 200 response may be `failed`, and `onChanged` shows it.
- Reject: `ResponsiveDialog` with a required reason; the submit is disabled while it is empty. `POST .../reject {comment}`.
- Cancel request: `ConfirmDialog` (destructive), then `POST .../cancel`.
- 409 `invalid_state`: `onReload()` and `toast("Someone else closed this request")`. Other errors: `toast.error(failureText)`.
- Toasts: "Change request approved" / "Change applied" / "Change request rejected" / "Change request cancelled".

**Pass:** E2E `e2e/change-requests.spec.ts`; WEB-GATE.

**Commit:** `feat(web): approve, reject and cancel change requests, usable at 344 px (MF-457)`

### 16. Web: Audit log list and filters
**Files:** `web/components/audit/audit-log.tsx`, `audit-filters.tsx`, `web/app/(app)/projects/[project]/audit/page.tsx`, `web/e2e/audit.spec.ts`.

**E2E first** (`e2e/audit.spec.ts`; data: `createTestFlag(api, {prefix: RUN})` and a toggle in development through the API):
- "filters by action, actor, resource and environment": `/projects/shop/audit`, filter action `flag.create` + resource key = the flag key → one entry, labelled "Flag created", with the raw action under it, and the URL holds both filters. Actor `admin@e2e.test` keeps it. Resource type `flag`, environment `development` + action `flag.toggle` → the toggle entry. From/to = today keeps it; a past range empties the list (empty state "No entries match").
- "the flag links to its page": clicking the resource opens `/projects/shop/flags/<key>`.
- "cards under 768 px": at 390 px the `list` named "Audit log" is visible and the table is hidden, and at 1024 the table is visible. The filters open in a sheet under 768 px.

**Run:** E2E `e2e/audit.spec.ts`. Expected: placeholder.

**Implement:**
- The page: `PageHeader` "Audit log" with `projectTrail`, then `<AuditLog scope={{kind:"project", project}} />` (`Suspense` + `Content`).
- `AuditFilters({ scope, value, onChange })`:
  - Action: `Input` with a `<datalist>` of `AUDIT_ACTIONS`, any text accepted.
  - Actor: "Email or token name".
  - Resource type: a select from `RESOURCE_TYPES`.
  - Resource key.
  - Environment select (project scope, from the project's environments) or project select (org scope, `useProjectKeys`) with environment.
  - From and To: `type="date"`.
  - Inline from 768 px. Below it, a "Filters (n)" button opens a `Sheet`, as `components/flags/flag-filters.tsx` does.
  - Text inputs are debounced 300 ms.
- `AuditLog({ scope })`:
  - Filters from the URL (`auditFiltersFromParams`/`auditFiltersToParams` + `router.replace`).
  - Loads `GET /api/audit` with `auditQuery` and `limit: 50`; "Load more" uses the cursor.
  - `DataList` caption "Audit log" with these columns: time (`<time>`, status slot), action (key: `actionLabel` above a `font-mono text-sm` raw action), actor (`actor.label`), resource (`resource_type` + key, a `Link` when `resourceHref`), environment (project scope) or project (org scope).
  - `LoadingRows`, `LoadFailed`, `EmptyState`.

**Pass:** E2E `e2e/audit.spec.ts`; WEB-GATE.

**Commit:** `feat(web): audit log with filters, cards under 768 px (MF-457)`

### 17. Web: Audit entry details and export
**Files:** `web/components/audit/audit-entry-details.tsx`, `export-dialog.tsx`, `audit-log.tsx`, `web/e2e/audit.spec.ts`.

**E2E first** (append):
- "details show the diff": filter to the toggle entry, open "Details" → a `DiffViewer` group whose Before has `"enabled": false` and After `"enabled": true`, plus request ID, IP address and user agent labels.
- "exports CSV and NDJSON": open Export, choose CSV, `page.waitForEvent("download")` → `suggestedFilename()` matches `/^audit-.*\.csv$/`, and the file contains the flag key. NDJSON is the same, with each line parsing as JSON with `resource_key`.
- "a sign-in entry shows its method": filter action `auth.sign_in` → Details shows "Sign-in method".

**Run:** E2E `e2e/audit.spec.ts`. Expected: no Details control.

**Implement:**
- `AuditEntryDetails({ entry })`: a disclosure (`Collapsible` from `components/ui/collapsible.tsx`, trigger "Details", 44 px) with `DiffViewer label="Before and after" before={snapshotText(entry.before)} after={snapshotText(entry.after)}` when either is set, and a `dl` of sign-in method (`actor.auth_method`), IP address, user agent ("as recorded") and request ID, each only when set. Add it as an extra `main` column "Details" in `AuditLog`.
- `ExportDialog({ filters, scope })`: `ResponsiveDialog` "Export audit log", a CSV/NDJSON radio and the description "Exports every entry that matches the current filters." The footer has a plain `<a href={exportUrl(...)} download>` styled as a button (`buttonVariants`), so the browser downloads it with the session cookie. The Export button sits next to the filters.

**Pass:** E2E `e2e/audit.spec.ts`; WEB-GATE.

**Commit:** `feat(web): audit entry details and CSV/NDJSON export (MF-457)`

### 18. Web: SDK keys on the API & SDK keys page
**Files:** `web/components/keys/keys-page.tsx`, `sdk-key-list.tsx`, `new-sdk-key-dialog.tsx`, `rotate-dialog.tsx`, `web/app/(app)/projects/[project]/keys/page.tsx`, `web/e2e/keys.spec.ts`.

**E2E first** (`e2e/keys.spec.ts`; `/projects/shop/keys?env=staging`):
- "create a server and a client key": New SDK key, name `${RUN} server`, kind Server (the help text shows) → an alertdialog "SDK key" with the secret and Copy key. Escape does not close it; Done does. The row shows Active and the key's first characters, never the full secret. The same for Client.
- "rotate with an overlap": Rotate the server key with "1 hour" → a new secret is shown. The old row shows "Expires in", the new one Active. "Revoke now" on the old row → it disappears; ticking "Show revoked and expired" shows it as Revoked.
- "revoke": confirm → hidden.
- "the environment is kept in the URL": switching the environment tab updates `?env=`, and a reload keeps it.
- "a viewer is read-only": VIEWER sees the keys but no New SDK key, Rotate or Revoke.
- `afterAll`: revoke the keys whose name starts with RUN.

**Run:** E2E `e2e/keys.spec.ts`. Expected: placeholder.

**Implement:**
- The page renders `<KeysPage project />`: `PageHeader` "API & SDK keys", then a section "SDK keys" holding `SdkKeyList`. Step 19 adds a second section.
- `SdkKeyList({ project })`:
  - `useProjectPermissions`. Environments are the keys of `perms.environments` holding `sdk_key.read`; `?env=` selects one (fallback: the first); tabs from `components/ui/tabs.tsx`.
  - Loads `GET .../environments/{env}/sdk-keys`, filtered by `visibleCredentials` with a "Show revoked and expired" `Checkbox`.
  - `DataList` columns: name (key), status (`credentialPill(credentialState)`), kind (`SDK_KIND_TEXT`), `prefix` in mono (main), created `<time>` (extra), actions.
  - Actions only with `can(perms, "sdk_key.manage", env)`: Rotate (`RotateDialog`, `POST .../rotate {overlap_seconds}`), Revoke (`ConfirmDialog`, DELETE), and "Revoke now" for a key that is expiring after a rotation (DELETE).
  - 409 `key_inactive` → toast + reload.
- `NewSdkKeyDialog`: name and a kind radio with `SDK_KIND_TEXT` help → `POST`, then `SecretReveal title="SDK key" copyLabel="Copy key"`.
- `RotateDialog`: as in C-WEB-UI, defaulting to `DEFAULT_OVERLAP`. The response's `key` goes to `SecretReveal`.

**Pass:** E2E `e2e/keys.spec.ts`; WEB-GATE.

**Commit:** `feat(web): SDK keys with rotation and one-time secrets (MF-457)`

### 19. Web: Service tokens (shared list) on the keys page
**Files:** `web/components/tokens/service-token-list.tsx`, `new-service-token-dialog.tsx`, `token-access-dialog.tsx`, `expiry-field.tsx`, `web/components/keys/keys-page.tsx`, `web/e2e/keys.spec.ts`.

**E2E first** (append to `keys.spec.ts`):
- "create a service token from the project": in the "Service tokens" section, New service token, name `${RUN} ci`. Expiry defaults to Never. The access row is pre-set to project `shop` (project select locked) with role viewer. → `SecretReveal` "Service token". The row shows the first characters, Active, "viewer · Project shop", and last used "Never".
- "change its access": Access → add `editor` in shop / development → the row lists both. Remove the first → one left.
- "rotate and revoke": Rotate with "Revoke the old one now" → a new secret, and the list keeps one active row. Revoke → hidden.
- "org-wide tokens are marked": a token bound at org scope shows the "Org-wide" badge. Create it through the API in `beforeAll` as `${RUN} org`, bound to viewer at org, and revoke it in `afterAll`, so the gov matrix page never shows it.
- "hidden without token.manage": VIEWER sees no Service tokens section.
- `afterAll`: revoke the RUN tokens.

**Run:** E2E `e2e/keys.spec.ts`. Expected: no section.

**Implement:**
- `ServiceTokenList({ project?: string })`:
  - `GET /api/tokens` with `project` and `limit: 50`, plus "Load more".
  - `visibleCredentials` with "Show revoked and expired".
  - Columns: name, status, `prefix`, access (each binding through `bindingText`, plus an "Org-wide" `Badge` when `project` is set and a binding has org scope), last used, expiry (`<time>` or "Never"), actions (Access, Rotate, Revoke).
- `NewServiceTokenDialog({ project?, open, onOpenChange, onCreated })`: name, `ExpiryField` (`SERVICE_EXPIRY_DEFAULT`) and `AccessRows` (initial row `{role:"viewer", scope: project ? "project" : "org", project: project ?? null, env: null}`, `lockedProject={project}`) → `POST /api/tokens {name, expires_at, bindings}`. Errors go through `formFailureText` (403 refusals).
- `TokenAccessDialog`: `AccessEditor subjectKind="service_token"`.
- Rotate reuses `RotateDialog` → `POST /api/tokens/{id}/rotate`.
- In `KeysPage`, the section renders only when `perms.org.includes("token.manage")`.

**Pass:** E2E `e2e/keys.spec.ts`; WEB-GATE.

**Commit:** `feat(web): service tokens on the project keys page (MF-457)`

### 20. Web: Personal API tokens page
**Files:** `web/components/tokens/personal-token-list.tsx`, `new-personal-token-dialog.tsx`, `web/app/(app)/account/tokens/page.tsx`, `web/e2e/account-tokens.spec.ts`.

**E2E first** (`e2e/account-tokens.spec.ts`, as VIEWER via `test.use({ storageState: VIEWER.storageState })`, so the admin's list stays empty for the matrix):
- "create a scoped token": New token, name `${RUN} laptop`, "Where it works" = One project → shop. The permission list shows only "See the project", "Evaluate flags", "See SDK keys" and "Read the audit log", and no "Manage members and teams" or "Edit targeting". Tick two. Expiry defaults to 90 days. → the secret is shown once; the row shows "Project shop", the two permission labels and "Expires in 3 months".
- "one environment narrows the list": scope One environment → shop/production lists only environment-level permissions (Evaluate flags, See SDK keys).
- "revoke": confirm → hidden; "Show revoked and expired" shows it as Revoked.
- `afterAll`: revoke the RUN tokens via `DELETE /api/account/tokens/{id}` with the viewer's storage state.

**Run:** E2E `e2e/account-tokens.spec.ts`. Expected: placeholder.

**Implement:**
- The page: `PageHeader` "API tokens" (`Suspense`), rendering `<PersonalTokenList />`.
- The list: `GET /api/account/tokens` (an array), `visibleCredentials` and the checkbox. Columns: name, status, `prefix`, where (`scopeText`), permissions (labels, extra), last used, expiry, and Revoke (`DELETE /api/account/tokens/{id}`).
- `NewPersonalTokenDialog`:
  - "Where it works": Everything / One project / One environment, through `ScopePicker` (`idPrefix="token"`).
  - Permissions: checkboxes of `grantableAt(catalogue, heldAt(perms, scope, env), scope)` with `PERMISSION_LABELS`. `perms` comes from `GET /api/auth/me/permissions?project=<project>`, or without `project` for Everything.
  - Ticked permissions that are no longer grantable after a scope change are dropped.
  - `ExpiryField` defaults to `PERSONAL_EXPIRY_DEFAULT`.
  - → `POST /api/account/tokens {name, permissions, project?, env?, expires_at}`, then `SecretReveal title="API token"`.
  - Submit is disabled without a name or permissions. 422 errors go through `formFailureText`.

**Pass:** E2E `e2e/account-tokens.spec.ts`; WEB-GATE.

**Commit:** `feat(web): personal API tokens page (MF-457)`

### 21. Web: Settings page with tabs; Service tokens, Security and Audit log tabs
**Files:** `web/components/settings/settings-page.tsx`, `security-tab.tsx`, `web/app/(app)/settings/page.tsx`, `web/e2e/settings.spec.ts`, `web/e2e/audit.spec.ts`.

**E2E first:**
- `settings.spec.ts`:
  - "tabs follow the URL": `/settings?tab=tokens` selects Service tokens; clicking SSO sets `?tab=sso`; `?tab=bogus` falls back to Members.
  - "a viewer sees Teams, Roles and Audit log": the VIEWER's tablist has exactly those three; `?tab=members` shows Teams.
  - "Require two-factor": `page.route("**/api/settings", …)` fulfils the PUT with `{require_mfa: true}` without reaching the API. Toggling the switch sends `{require_mfa: true}` (capture the request body) and toasts "Two-factor sign-in is now required".
- `audit.spec.ts` "the organization tab shows org entries": `/settings?tab=audit&action=auth.sign_in` lists sign-in entries with the Project column. Filtering by project `shop` shows only shop entries.

**Run:** E2E `e2e/settings.spec.ts e2e/audit.spec.ts`. Expected: placeholder.

**Implement:**
- The page: `PageHeader` "Settings" + `<SettingsPage />` in `Suspense`.
- `SettingsPage`:
  - `useApiData("me-permissions", GET /api/auth/me/permissions)`; `visibleTabs(perms.org)`; `pickTab(params.get("tab"), visible)`.
  - `Tabs` from `components/ui/tabs.tsx`, scrollable horizontally under 768 px (`overflow-x-auto`, with no page scroll).
  - Changing the tab replaces `?tab=` and drops the other tab's params.
  - Panels: members, teams, roles, sso → `null` placeholders until steps 22–27 fill them (render nothing yet; the tabs exist). tokens → `<ServiceTokenList />`. security → `<SecurityTab />`. audit → `<AuditLog scope={{kind:"org"}} />`.
- `SecurityTab`: `GET /api/settings`, a `Switch` "Require two-factor sign-in" with the text "People who sign in with a password must set up a second factor. SSO users rely on their identity provider." → `PUT /api/settings`.

**Pass:** E2E `e2e/settings.spec.ts e2e/audit.spec.ts`; WEB-GATE.

**Commit:** `feat(web): Settings tabs with service tokens, security and the org audit log (MF-457)`

### 22. Web: Members tab and invitations
**Files:** `web/components/settings/members-tab.tsx`, `invite-dialog.tsx`, `settings-page.tsx`, `web/e2e/settings.spec.ts`.

**E2E first** (append):
- "invite a member with a role": Invite member with email `${RUN}@invite.e2e.test`, name and role viewer → a `SecretReveal` "Invite link" with the URL. Done → the row shows "Invited" and role viewer. Open the URL in a fresh `browser.newContext()`, set a password and submit → a later reload of Members shows Active.
- "search": typing the RUN email filters to that row and sets `?q=`.
- "role refused": `page.route` makes `POST /api/bindings` return 403 → the link is still shown, with the warning "The role was not assigned: <message>".
- "duplicate email": inviting `admin@e2e.test` shows the conflict on the email field.
- `afterAll`: disable the RUN users through `POST /api/users/{id}/disable`.

**Run:** E2E `e2e/settings.spec.ts`. Expected: empty Members panel.

**Implement:**
- `MembersTab`:
  - `q` search input (debounced, kept in `?q=`) → `GET /api/users?q&limit=50` + Load more.
  - Org roles from `GET /api/bindings?subject_kind=user&scope=org&limit=500`, mapped by `subject_id`.
  - `DataList` caption "Members": name + email (key), status (`memberPill(memberState(u, now))`; for invited, also "link expires <time>"), organization role(s) (main), last sign-in `<time>` or "Never" (extra), and a Manage button (step 23 wires it; render it disabled until then).
- `InviteDialog`: email, name, role select (`useRoles`, with "No role" allowed). It calls `POST /api/users`, then, with a role, `POST /api/bindings {subject_kind:"user", subject_id, role, scope:"org"}`. Then `SecretReveal title="Invite link" copyLabel="Copy link"`, with the text "No email is sent. Share this link; it works once and expires <date>." and the role warning as `children` when the binding failed.
- `key_conflict` and 422 go to the email field.

**Pass:** E2E `e2e/settings.spec.ts`; WEB-GATE.

**Commit:** `feat(web): members list and invitations (MF-457)`

### 23. Web: Manage a member
**Files:** `web/components/settings/member-dialog.tsx`, `members-tab.tsx`, `web/e2e/settings.spec.ts`.

**E2E first** (append; use a member invited by the spec):
- "assign and remove roles": Manage → Access shows "viewer · Organization". Add editor in shop → listed. Remove it → gone.
- "rename": change the name → the list shows it.
- "new invite link": "New invite link" for an invited member shows the link (the dialog title says Invite link). For an active member, the button reads "Password reset link".
- "reset two-factor": confirm → toast "Two-factor reset" (works for a member without factors).
- "disable and enable": Disable → status Disabled; Enable → Active.
- "last admin": `page.route` makes `POST /api/users/*/disable` return 409 `{code:"last_admin", message:"…"}` → "This would leave the organization without an admin." shows.

**Run:** E2E `e2e/settings.spec.ts`. Expected: Manage disabled.

**Implement** `MemberDialog({ user, open, onOpenChange, onChanged })`, a `ResponsiveDialog` titled with the name:
- A name field and Save (`PATCH /api/users/{id}`).
- `AccessEditor subjectKind="user"` (SSO roles read-only through `isFromSso`).
- A link button labelled by `linkKind(user)` ("New invite link" or "Password reset link") → `POST /api/users/{id}/reset-link` → `SecretReveal`.
- "Reset two-factor" (`ConfirmDialog`, `DELETE /api/users/{id}/mfa`).
- Disable/Enable (`ConfirmDialog` for disable: "They are signed out everywhere.").
- Errors go through `formFailureText` in a `role="alert"`. Wire Manage in `MembersTab`.

**Pass:** E2E `e2e/settings.spec.ts`; WEB-GATE.

**Commit:** `feat(web): manage members (roles, links, two-factor, disable) (MF-457)`

### 24. Web: Teams tab
**Files:** `web/components/settings/teams-tab.tsx`, `team-dialog.tsx`, `settings-page.tsx`, `web/e2e/settings.spec.ts`.

**E2E first** (append):
- "a team with members and a role": New team, name `${RUN} Web` → the key is suggested as `slugifyKey(name)` and is editable. Open it → add member `viewer@e2e.test` (picked by email search), source "Added by hand". Add role viewer at project shop. The list shows member count 1 and "viewer · Project shop". Rename it. Remove the member. Delete the team (confirm).
- "read-only without member.manage": VIEWER opens the gov matrix team `gov-team` (seeded in step 28; if absent, `test.skip`) and sees no add, remove, rename or delete controls.
- `afterAll`: delete the RUN teams.

**Run:** E2E `e2e/settings.spec.ts`. Expected: empty panel.

**Implement:**
- `TeamsTab({ canManage })`: `GET /api/teams?limit=500`. `DataList` columns: name + key, members (`member_count`), roles (from `GET /api/bindings?subject_kind=team&limit=500`, through `bindingText`), Open. New team when `canManage` (`perms.org.includes("member.manage")`).
- `TeamDialog`:
  - Create mode: name + key (`slugifyKey` from `@/lib/flags/flag-form`) → `POST /api/teams`.
  - Open mode: rename (`PATCH`); members from `GET /api/teams/{key}/members` (source "manual" → "Added by hand", `sso:<p>` → "From SSO (<p>)"); add a member through an email search (`GET /api/users?q=`) → `POST /api/teams/{key}/members {user_id}`; remove → `DELETE`; `AccessEditor subjectKind="team"`; delete (`ConfirmDialog`).
  - `last_admin` and `key_conflict` go through `formFailureText` / the key field.

**Pass:** E2E `e2e/settings.spec.ts`; WEB-GATE.

**Commit:** `feat(web): teams with members and roles (MF-457)`

### 25. Web: Roles tab
**Files:** `web/components/settings/roles-tab.tsx`, `role-dialog.tsx`, `settings-page.tsx`, `web/e2e/settings.spec.ts`.

**E2E first** (append):
- "create, edit and delete a custom role": New role, name `${RUN} Toggler` (key suggested) with permissions "See the project" and "Turn flags on and off". The dialog groups them under Organization, Project and Environment headings, each with its `LEVEL_TEXT.where` sentence. Save → listed as Custom. Edit: add "Edit targeting" → saved. Delete → gone.
- "a role in use": create a role, bind it to a RUN team through the API, then Delete → the message starts "This role is still used by: 1 binding". Clean up through the API.
- "built-in roles are read-only": opening Admin shows the permissions as read-only text, with no Save or Delete.
- "a viewer sees roles read-only": as VIEWER, opening `gov-role` shows no Save.

**Run:** E2E `e2e/settings.spec.ts`. Expected: empty panel.

**Implement:**
- `RolesTab({ canManage })`: `useRoles` (reload on change). `DataList`: name + key, Built-in/Custom `Badge`, permission count, description (extra), Open.
- `RoleDialog`:
  - Mode create/edit/view. Read-only when `role.builtin` or `!canManage`.
  - Fields: name, key (create only, `slugifyKey`), description.
  - Permission checkboxes from `groupByLevel(usePermissionCatalogue())`, under the `LEVEL_TEXT[level].label` heading and `where` text.
  - → `POST /api/roles` or `PATCH /api/roles/{key}`. Delete → `DELETE /api/roles/{key}`; 409 `in_use` → `usedByText`.

**Pass:** E2E `e2e/settings.spec.ts`; WEB-GATE.

**Commit:** `feat(web): roles with permissions grouped by level (MF-457)`

### 26. Web: SSO providers
**Files:** `web/components/settings/sso-tab.tsx`, `provider-form.tsx`, `settings-page.tsx`, `web/e2e/settings.spec.ts`.

**E2E first** (append):
- "add, edit, disable and delete an OIDC provider": Add provider, protocol OIDC, key `${RUN}-oidc`, name, issuer `https://idp.example.test`, client ID, client secret. New users are created on first sign-in (JIT, default on); default role viewer. → listed with OIDC, Enabled and "New users join as viewer". Edit: the secret field shows "Saved" and stays empty; changing the name keeps the secret (`has_client_secret` stays true through the API). Disable → Disabled. Delete (confirm) → gone.
- "SAML fields": switching the protocol to SAML shows metadata URL or XML, SP entity ID, the attribute names and an optional SP key pair. An unreachable metadata URL (`https://127.0.0.1:9/metadata`) shows the API error on the metadata URL field.
- `afterAll`: delete the RUN providers.

**Run:** E2E `e2e/settings.spec.ts`. Expected: empty panel.

**Implement:**
- `SsoTab`: `GET /api/sso/providers?limit=500`. `DataList`: name + key, protocol, status (Enabled `on` / Disabled `off`), new users (`jit_enabled` → "New users join as <default_role or 'no role'>", else "Existing users only"), and an actions menu (Edit, Set-up URLs, Group mappings, Enable/Disable through `PATCH {enabled}`, Delete through `ConfirmDialog` with `last_admin` handling). Set-up URLs and Group mappings come in step 27; leave them out of the menu until then.
- `ProviderForm({ provider | null })`:
  - Protocol radio (create only), key (create only), name, enabled.
  - OIDC: issuer, client ID, scopes (comma text, default "openid, email, profile"), groups claim, client secret (`type="password"`; in edit mode the placeholder is "Saved — leave empty to keep it" when `has_client_secret`).
  - SAML: a metadata source radio (URL / XML) with a field or textarea, SP entity ID, email/name/groups attributes, and the SP private key + certificate pair.
  - JIT, "Link by verified email", default role (`useRoles`).
  - Sends only the changed or non-empty secrets. 422 `path` → the matching field; the metadata fetch failure → the metadata URL field.

**Pass:** E2E `e2e/settings.spec.ts`; WEB-GATE.

**Commit:** `feat(web): SSO providers (OIDC and SAML) (MF-457)`

### 27. Web: SSO set-up URLs and group mappings
**Files:** `web/components/settings/setup-urls-dialog.tsx`, `mappings-dialog.tsx`, `sso-tab.tsx`, `web/e2e/settings.spec.ts`.

**E2E first** (append):
- "set-up URLs": for a RUN OIDC provider, Set-up URLs shows the redirect URI `http://localhost:3000/api/auth/sso/<key>/callback` (the e2e API runs with `MEGOOFLAGS_PUBLIC_URL=http://localhost:3000`) with a Copy button. A SAML provider created through the API with inline metadata XML (copy the fixture from `api/tests/sso_fixtures.py`) shows ACS URL, entity ID and metadata URL.
- "no public URL": `page.route` returns the provider with the four fields null → the dialog explains "Set MEGOOFLAGS_PUBLIC_URL to this site's address, then restart the API."
- "group mappings": Group mappings → add group `devs` → team (a RUN team), and group `ops` → role viewer at project shop. Both are listed ("devs → team <key>", "ops → viewer · Project shop"). Remove one.

**Run:** E2E `e2e/settings.spec.ts`. Expected: no menu items.

**Implement:**
- `SetupUrlsDialog({ provider })`: a `dl` of the non-null URLs, each with `CopyButton` (`components/patterns/copy-button.tsx`) labelled "Copy redirect URI" and so on. When all four are null, an `Alert` with the explanation.
- `MappingsDialog({ provider })`: `GET /api/sso/providers/{key}/mappings`; an add form with group, a target radio (Team: select from `GET /api/teams`; Role: `useRoles` + `ScopePicker`) → `POST` with `SsoMappingIn`; remove → `DELETE /api/sso/providers/{key}/mappings/{id}`. Check the exact paths in `schema.d.ts`.
- Add both to the SSO actions menu.

**Pass:** E2E `e2e/settings.spec.ts`; WEB-GATE.

**Commit:** `feat(web): SSO set-up URLs and group mappings (MF-457)`

### 28. E2E: gov seed and the responsive/a11y matrix
**Files:** `web/e2e/global-setup.ts`, `web/e2e/matrix.spec.ts`, `web/e2e/matrix.spec.ts-snapshots/` (new baselines; delete the old `settings-*` placeholder baselines and refresh `kit-*`).

**Seed** `seedGov(admin, reviewer)` in `global-setup.ts`, called after `ensureUser(admin, REVIEWER)` with a REVIEWER context signed in with `signIn`. It is idempotent: create each item only when missing, and never change gov after that.
- Project `gov` (environments development and production; production `approvals_required: 1`), with boolean flag `gov-flag`.
- When the project has no change requests: a config request "Turn on targeting" (open) and a toggle request "Turn it on" that the reviewer rejects with "Not now".
- SDK keys in development: "gov server" (server) and "gov client" (client).
- Service token `gov-token` (viewer at project gov) unless `GET /api/tokens?project=gov` lists it.
- Team `gov-team` with VIEWER as member and viewer at project gov.
- Custom role `gov-role` (project.read, flag.toggle).
- Disabled OIDC provider `gov-oidc` (issuer `https://idp.example.test`, client ID `megooflags`, secret `e2e-secret`, `enabled: false`).

**Matrix entries** (replace `{ name: "settings", path: "/settings" }`):
- `change-requests` `/projects/gov/change-requests?status=all` (ready "Turn on targeting").
- `change-request` (ready "Turn on targeting", `maskText: [/^[0-9a-f-]{36}$/]`). Add an optional `resolve?: () => Promise<string>` to the PAGES type: it looks up the id with `adminApi()` and returns `/projects/gov/change-requests/<id>`.
- `audit` `/projects/gov/audit?action=change_request.create`.
- `keys` `/projects/gov/keys?env=development` (`maskText: [/^mf_/]`, ready "gov server").
- `account-tokens` `/account/tokens`.
- `settings-members` `/settings?tab=members&q=%40e2e.test`.
- `settings-teams`, `settings-roles`, `settings-tokens` (ready "gov-token", `maskText: [/^mf_api_/]`), `settings-sso` (ready "gov-oidc"), `settings-security`.
- `settings-audit` `/settings?tab=audit&action=sso_provider.create&resource_key=gov-oidc`.
- All with `mask: true`.

**Run first:** E2E `e2e/matrix.spec.ts` → the new entries fail with missing baselines. Fix every axe violation, horizontal scroll and small target the new pages show at 280 px before taking baselines.

**Then:** SNAPSHOTS, and review the new PNGs by eye (both themes, 280 and 1440). E2E `e2e/matrix.spec.ts` passes from those baselines.

**Commit:** `test(web): governance pages in the responsive and a11y matrix (MF-457)`

### 29. Docs: GA checklist and CHANGELOG
**Files:** `docs/ga-checklist.md`, `CHANGELOG.md`.

**Do:**
- `docs/ga-checklist.md`:
  - G-1 → **done** with `web/e2e/settings.spec.ts`.
  - G-2 → **done** with `web/e2e/audit.spec.ts`.
  - G-3 → **done** with `web/e2e/settings.spec.ts` (SSO tab).
  - G-5 → **done** with `web/e2e/keys.spec.ts` and `web/e2e/account-tokens.spec.ts`.
  - UI-14: the audit log part done (`web/e2e/audit.spec.ts`); Segments stays with MF-458. Keep the status as the row's wording requires: **done** for the audit log, with "Segments: MF-458" in evidence.
  - UI-15 → **done** with `web/e2e/flags-mobile.spec.ts` and `web/e2e/change-requests.spec.ts`.
  - The GA "Change requests" row → **done** with `web/e2e/change-requests.spec.ts`.
  - Remove "the web … page is a placeholder" wording from those rows.
  - Section 6, blocker 5: narrow it to Segments and Environments (MF-458).
- `CHANGELOG.md` under `[Unreleased]` → `### Added`, for users:
  - "Change requests page: review a change with its diff, comment, approve, reject or cancel, also on a phone."
  - "Audit log page with filters, before/after details and CSV or NDJSON export, plus an organization audit log in Settings."
  - "API & SDK keys page: create, rotate (with an overlap) and revoke SDK keys, and manage the service tokens that can act in the project."
  - "API tokens page for personal tokens limited to a project or environment."
  - "Settings: members and invitations, teams, custom roles, service tokens, SSO providers with set-up URLs and group mappings, and the require two-factor setting."
- Verify the referenced paths exist: `git ls-files web/e2e`.

**Pass:** `cd site && pnpm format:check` is not affected; run `cd web && pnpm format:check` and, from the root, `npx --yes prettier --check docs/ga-checklist.md CHANGELOG.md` only if the repository formats Markdown (check `.prettierrc`/CI). Otherwise review the diff by hand.

**Commit:** `docs: governance pages close UI-14, UI-15, G-1, G-2, G-3, G-5 (MF-457)`

### 30. Final verification
Run every check in AGENTS.md §9 that this change touches, and fix what fails, in the step it belongs to (amend nothing; add `fix(...)` commits):
- API: `cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy`; API-TEST `tests` (full suite with Keycloak); `megoocolab-runner services run db -- bash scripts/check_migrations.sh` (no migration expected; it must still pass).
- Drift: REGEN, then `git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts`.
- Web: `cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test`.
- E2E: the full suite, E2E with `<spec>` empty (all specs), then the matrix from the pinned image (SNAPSHOTS without `--update-snapshots`).
- `rg -n PlaceholderPage web/app` lists only `segments/page.tsx` and `environments/page.tsx`.
- `python3 scripts/check_version.py` (unchanged version).

**Commit:** only if fixes were needed: `fix(web|api): <what> (MF-457)`.

## Final verification
1. `megoocolab-runner services run api -- bash -c 'cd api && uv run pytest -q'`, plus API-LINT.
2. `uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api && git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts`.
3. `cd web && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test`.
4. E2E (all specs), and the matrix in the pinned Playwright image (`pnpm e2e:docker`).
5. `rg -n PlaceholderPage web/app` shows only Segments and Environments.
6. `docs/ga-checklist.md` shows UI-14 (audit log), UI-15, G-1, G-2, G-3, G-5 and the GA "Change requests" row as done.
