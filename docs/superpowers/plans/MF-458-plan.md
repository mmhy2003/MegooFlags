# MF-458 implementation plan: project pages (segments, environments, project settings, projects) and Automation on the flag page

## Goal
Replace the Segments and Environments placeholders, add Project settings with project create and delete, and add an **Automation** tab on the flag page for scheduled changes and progressive rollouts. Everything works from 280 px and with touch at 344 px, and the work closes GA blocker 5 and the web part of T-4, UI-14, F-10 and T-6.

## Architecture
- **API (steps 1–3):** three backward-compatible additions with no migration: `flag_count` on segment list items (`SegmentListItem`), `GET …/segments/{segment}/usage`, and `PUT /projects/{project}/environment-order`. Each step regenerates `api/openapi.json` and `web/lib/api/schema.d.ts` and adds a CHANGELOG line in the same commit.
- **Web (steps 4–20):** client components on the generated `api` client, `useApiData` and `must`, like `components/tokens/service-token-list.tsx`. Pure logic lives in `web/lib/<area>/*.ts` with Vitest tests. Routes are thin server components wrapping a client component in `<Suspense>`, like `app/(app)/projects/[project]/keys/page.tsx`. Lists use `DataList` (a table from 768 px, cards below), and forms use `ResponsiveDialog` (full screen below 768 px). Search text, tab and environment live in URL params (`router.replace(…, { scroll: false })`).
- **Tests:** pytest first for the API. Vitest for helpers. Each page step writes its Playwright spec before the UI. Step 21 adds the matrix entries on the seeded `gov` project.

## Conventions (every step)
- Branch: the current `megoo/MF-458-web-ui-project-pages-segments-environmen`. Do not push.
- Commit message: `<type>(api|web|docs): <summary> (MF-458)`, a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.megoocolab/`, `web/e2e/.auth/`, `web/test-results/`, `web/playwright-report/` or `.pnpm-store/`.
- Match the surrounding style:
  - Short doc comments on exported things, and `"use client"` components.
  - `aria-hidden` icons and 44 px targets (`min-h-11 min-w-11`). Inputs use `text-base` (16 px).
  - `toast` for outcomes, and the `showError` pattern from `service-token-list.tsx`.
  - Danger triggers use `DANGER_TRIGGER` from `components/flags/delete-flag-dialog.tsx`.
- Every absolute or relative time is rendered inside `<time dateTime={iso}>`, so the matrix can mask it.

### Commands
- **API-TEST** `<files>`: `megoocolab-runner services run db -- bash -c 'cd api && uv sync --frozen && uv run pytest <files> -q'`
- **API-LINT**: `cd api && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy`
- **REGEN** (from the repository root): `uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api`
- **UNIT** `<file>`: `cd web && pnpm exec vitest run <file>`
- **WEB-GATE**: `cd web && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test`. Run `pnpm format` first if format:check fails.
- **E2E** `<spec>`: Postgres and Valkey come from the `db` set. The API runs on :8000, as AGENTS.md §9 requires. Run `cd web && pnpm exec playwright install chromium` once first. The trap stops the API, which otherwise outlives the runner and holds :8000.
  ```
  megoocolab-runner services run db -- bash -c 'set -e
  cd api && uv sync --frozen && uv run alembic upgrade head
  MEGOOFLAGS_SECRET_KEY=e2e-local-secret-key-0123456789abcdef MEGOOFLAGS_PUBLIC_URL=http://localhost:3000 MEGOOFLAGS_API_WORKERS=1 MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true MEGOOFLAGS_ROLE=control-plane uv run python -m megooflags_api.serve &
  API_PID=$!; trap "pkill -P $API_PID; kill $API_PID" EXIT
  cd ../web && pnpm install --frozen-lockfile && pnpm build && pnpm exec playwright test <spec>'
  ```
- **SNAPSHOTS**: the E2E command with its last line replaced by `cd ../web && pnpm e2e:docker --update-snapshots`. This uses the pinned Playwright image; never take baselines from a local browser. Afterwards:
  - Fix ownership of the root-owned `web/test-results`, `web/.next` and `web/node_modules`.
  - Delete `.pnpm-store/`.

## Contracts shared across steps

### C-API: API after steps 1–3
`api/src/megooflags_api/schemas/segments.py`
```python
class SegmentListItem(SegmentSummary):
    """`flag_count`: flags whose rules use the segment, in the environments the caller can see."""
    flag_count: int

class SegmentUsage(ApiModel):
    flag: str
    environment: str

class SegmentUsageOut(ApiModel):
    """Ordered by flag, then environment; only environments the caller can see."""
    used_by: list[SegmentUsage]
```
`api/src/megooflags_api/schemas/projects.py`
```python
class EnvironmentOrderIn(ApiModel):
    """Every environment of the project exactly once, in the new order."""
    environments: list[Key] = Field(min_length=1)
```
`api/src/megooflags_api/services/usage.py`
- `segment_dependents(session, segment_id, visible: set[uuid.UUID] | None = None)`: when `visible` is not None, add `.where(Environment.id.in_(visible))`. `delete_segment` keeps calling it without `visible`.
- New `segment_flag_counts(session, segment_ids: list[uuid.UUID], visible: set[uuid.UUID] | None) -> dict[uuid.UUID, int]`. One grouped query:
  - `select(ClauseSegment.segment_id, func.count(distinct(FlagEnvironment.flag_id)))`
  - joined `Clause → FlagRule → FlagEnvironment`, with `ClauseSegment.segment_id.in_(segment_ids)`
  - when `visible` is not None, also `FlagEnvironment.environment_id.in_(visible)`
  - `.group_by(ClauseSegment.segment_id)`
  - Return `{}` without a query when `segment_ids` is empty.

`api/src/megooflags_api/services/segments.py`
- `list_segments(session, project, *, q, cursor, limit, visible: set[uuid.UUID] | None = None) -> tuple[list[SegmentListItem], str | None]`: after `page_slice`, one `segment_flag_counts` call; a missing id counts 0.

`api/src/megooflags_api/services/environments.py`
- `reorder_environments(session, project, keys: list[str]) -> tuple[list[str], list[Environment]]`. It returns the old key order and the rows in the new order.
  - First it takes `select(Project.id).where(Project.id == project.id).with_for_update()`, as `create_environment` does.
  - It loads the project's environments with `list_environments(session, project)`.
  - If `len(set(keys)) != len(keys)` or `set(keys) != {e.key for e in rows}`, it raises `validation_error("environments must list every environment of the project exactly once", "environments")`.
  - Otherwise it sets `sort_order = keys.index(e.key)` on each row, flushes, and returns `(old_keys, rows sorted by the new sort_order)`.

Routes:
- `GET /api/projects/{project}/segments` → `Page[SegmentListItem]` (schema `Page_SegmentListItem_`). It passes `visible=authz.authorizer.visible_environment_ids(project.id)`.
- `GET /api/projects/{project}/segments/{segment}/usage` → `SegmentUsageOut` (`project.read`, 404 for an unknown segment).
- `PUT /api/projects/{project}/environment-order` → `list[EnvironmentOut]` in the new order (`environment.manage`). It writes one audit entry:
  - action `environment.reorder`, type `"project"`, key `project.key`, `project_id=project.id`
  - `before={"environments": old_keys}`, `after={"environments": new_keys}`
  - Then it commits and calls `bus.publish(project.id)`.

### C-WEB-LIB: helper modules (exact exports)
`web/lib/segments/segment-draft.ts` (step 5)
```ts
import type { ClauseDraft, DraftProblem } from "@/lib/flags/config-draft";
type S = components["schemas"];
export type SegmentRuleDraft = { id: string; description: string; clauses: ClauseDraft[] };
export type SegmentDraft = { included: string[]; excluded: string[]; rules: SegmentRuleDraft[] };
export const MAX_KEY_LENGTH = 256;
export function toDraft(s: S["SegmentOut"]): SegmentDraft;
export function toConfigIn(d: SegmentDraft): S["SegmentConfigIn"];        // ids stripped
export function isDirty(base: SegmentDraft, d: SegmentDraft): boolean;  // JSON of toConfigIn
export function setKeys(d: SegmentDraft, list: "included" | "excluded", keys: string[]): SegmentDraft; // trimmed, empty lines dropped, deduped, order kept
export function addRule(d: SegmentDraft): SegmentDraft;                 // one clause: newClause()
export function updateRule(d: SegmentDraft, ruleId: string, patch: { description: string }): SegmentDraft;
export function removeRule(d: SegmentDraft, ruleId: string): SegmentDraft;
export function moveRule(d: SegmentDraft, ruleId: string, by: -1 | 1): SegmentDraft; // no-op at the ends
export function addClause(d: SegmentDraft, ruleId: string): SegmentDraft;
export function updateClause(d: SegmentDraft, ruleId: string, clauseId: string, patch: Partial<Omit<ClauseDraft, "id">>): SegmentDraft;
export function removeClause(d: SegmentDraft, ruleId: string, clauseId: string): SegmentDraft; // never a rule's last clause
export function validateDraft(d: SegmentDraft): DraftProblem[];
//  excluded[j]: "<key> is both included and excluded"; included[i] / excluded[j]: "Keys can be at most 256 characters";
//  rules[i].clauses[j].values: "Add at least one value". Order: included, excluded, rules.
export function problemField(path: string | null): string | null;      // "included", "excluded", "rules[i].clauses[j]", "rules[i]", else null
export function usageByFlag(usedBy: S["SegmentUsage"][]): { flag: string; environments: string[] }[]; // input order kept
export function usedByText(flagCount: number): string;                 // 0 "Not used", 1 "1 flag", n "n flags"
```
`web/lib/environments/order.ts` (step 9)
```ts
export function moveKey(keys: string[], key: string, by: -1 | 1): string[]; // a copy; unchanged at the ends or for an unknown key
export function approvalsLabel(required: number): string; // 0 "Direct changes", 1 "Needs 1 approval", n "Needs n approvals"
```
`web/lib/flags/rollout-plan.ts` (step 14)
```ts
type S = components["schemas"];
export type WaitUnit = "minutes" | "hours" | "days";
export const UNIT_MINUTES: Record<WaitUnit, number>;      // 1, 60, 1440
export const WAIT_UNITS: { value: WaitUnit; label: string }[]; // Minutes, Hours, Days
export const MAX_STEPS = 20;                              // including the final 100 %
export const MAX_WAIT_MINUTES = 43_200;                   // 30 days
/** A step before the final 100 %: its percent and the wait before the next step, as typed. */
export type StepDraft = { id: string; percent: string; wait: string; unit: WaitUnit };
export type RolloutPlan = { from: string; to: string; steps: StepDraft[] }; // the final 100 % is implicit
export function defaultFrom(fallthrough: S["Serve"], variations: string[]): string; // its variation, else the largest weight, else variations[0]
export function defaultPlan(from: string, variations: string[]): RolloutPlan; // to = first variation ≠ from; steps 10, 25, 50 each "1" hours
export function addStep(p: RolloutPlan): RolloutPlan;     // percent "" , wait "1", unit "hours"
export function removeStep(p: RolloutPlan, id: string): RolloutPlan;
export function updateStep(p: RolloutPlan, id: string, patch: Partial<Omit<StepDraft, "id">>): RolloutPlan;
export function planProblems(p: RolloutPlan): { path: string; message: string }[];
//  "to": "Choose a variation other than From"; "steps": "At most 20 steps";
//  "steps[i].percent": "Enter a percentage above 0 and below 100" | "Each step must be higher than the one before";
//  "steps[i].wait": "Enter a whole number" | "Wait between 1 minute and 30 days"
export function waitMinutes(s: StepDraft): number;        // Number(wait) * UNIT_MINUTES[unit]
export function toRolloutIn(p: RolloutPlan): S["ProgressiveRolloutIn"]; // percent via fromPercent; last { percentage: 100000 }
export function totalMinutes(p: RolloutPlan): number;     // sum of waitMinutes
export function durationText(minutes: number): string;   // 0 "now", 90 "1 hour 30 minutes", 1500 "1 day 1 hour", 60 "1 hour"
export function currentPercent(r: S["ProgressiveRolloutOut"]): number; // completed → 100; else steps[current_step].percentage / 1000
export type StepState = { percent: number; state: "done" | "current" | "next"; waitMinutes: number };
export function stepStates(r: S["ProgressiveRolloutOut"]): StepState[];
export function rolloutPill(status: S["RolloutStatus"]): { status: Status; label: string };
//  active pending "Running", paused stale "Paused", completed on "Completed", aborted off "Aborted", failed error "Failed", cancelled off "Cancelled"
export function variationText(key: string | null): string; // null → "deleted variation"
```
`web/lib/flags/schedules.ts` (step 15)
```ts
type S = components["schemas"]; type Schedule = S["ScheduleOut"];
export type AutomationSnapshot = { rollout: S["RolloutView"] | null; schedules: Schedule[] | null };
export const SCHEDULE_ACTIONS: { value: S["ScheduledAction"]; label: string }[];
//  enable "Turn on", disable "Turn off", set_rollout "Set the default rule to a rollout"
export function scheduleText(s: Pick<Schedule, "action" | "weights">): string;
//  "Turn on" | "Turn off" | "Set the default rule to true 25 %, false 75 %" (weights in variation order given)
export function schedulePermission(action: S["ScheduledAction"]): S["Permission"]; // set_rollout flag.config.write, else flag.toggle
export function upcoming(list: Schedule[]): Schedule[]; // pending, run_at ascending
export function past(list: Schedule[]): Schedule[];     // not pending, (executed_at ?? run_at) descending
export function schedulePill(status: S["ScheduleStatus"]): { status: Status; label: string };
//  pending pending "Pending", done on "Applied", failed error "Failed", cancelled off "Cancelled"
export function defaultRunAt(now: Date): string;        // local "YYYY-MM-DDTHH:mm", one hour ahead rounded up to 5 minutes
export function localToIso(local: string): string | null; // new Date(local) → toISOString(); null when invalid or empty
export function runAtProblem(local: string, now: Date): string | undefined;
//  "Pick a date and time" | "Pick a time in the future" | "Pick a time within 365 days"
export function utcText(iso: string): string;           // "2026-12-01 08:30 UTC"
export function relativeText(iso: string, now: Date): string; // Intl.RelativeTimeFormat("en"): "in 3 hours", "2 days ago"
export function automationCount(a: AutomationSnapshot): number; // pending schedules + (rollout.current is active or paused ? 1 : 0)
export function needsPolling(a: AutomationSnapshot): boolean;   // rollout.current?.status === "active" or any pending schedule
export function wasApplied(before: AutomationSnapshot, after: AutomationSnapshot): boolean;
//  true when a schedule pending before is "done" after, or the same rollout's current_step grew, or a rollout live before is no longer live
```

### C-WEB-UI: shared components (exact props)
- `web/components/flags/key-list-field.tsx` (step 4): `KeyListField({ label, keys, readOnly, error, showCount = false, onApply }: { label: string; keys: string[]; readOnly: boolean; error?: string; showCount?: boolean; onApply: (keys: string[]) => void })`.
  - This is the `TargetsField` of `rules-editor.tsx`, moved out unchanged. It keeps one key per line, applies on blur, and resyncs from `keys`.
  - It adds `aria-invalid`/`aria-describedby` with the error, and a `"n keys"` line under the label when `showCount`.
- `ClauseRow` (step 4) gains `allowSegments?: boolean` (default `true`); when false, `is in segment` is left out of the operator list. `segments` becomes optional (default `[]`).
- `config-draft.ts` (step 4) exports its existing `newId()` and `newClause()`.
- `UnsavedBar` (step 4) gains `actionLabel?: string`, which replaces the Review / "Review & request change" text.
- `web/components/patterns/search-field.tsx` (step 4): `SearchField({ label, placeholder, value, onChange })`. It is the debounced field of `members-tab.tsx`, moved out with its `SEARCH_DELAY_MS = 200`; `members-tab.tsx` uses it with label "Search members".
- `web/components/patterns/typed-delete-dialog.tsx` (step 11): `TypedDeleteDialog({ open, onOpenChange, title, description, typedKey, confirmLabel, onDelete }: { …; onDelete: () => Promise<void> })`.
  - It is an alert `ResponsiveDialog` with "Type <typedKey> to confirm" (an input with `autoComplete="off"`).
  - The confirm button is disabled until the input equals `typedKey`, and while sending.
  - When `onDelete` rejects, the dialog stays open and is enabled again (the caller shows the error). It resets the text on close.
- `web/components/flags/weights-editor.tsx` (step 16): `WeightsEditor({ type, weights, onChange, readOnly, error })`.
  - It holds the rollout part of `ServeEditor`, moved out: `RolloutSlider` for boolean, a `WeightInput` per variation otherwise, "Total: n %", and the error.
  - `ServeEditor` uses it and keeps "Bucket by".
- `web/components/flags/request-fields.tsx` (step 16): `RequestFields({ title, onTitle, description, onDescription })`. These are the Title (max 200) and Description fields of `ToggleRequestDialog`, moved out; `ToggleRequestDialog` uses it.

### C-ROUTES
- `PROJECT_SECTIONS` gains `"settings"` (last).
- New `segmentPath(project, segment)` → `/projects/<p>/segments/<encoded segment>`.
- `nav.ts`:
  - `NavKey = Exclude<ProjectSection, "settings"> | "project-settings" | "nodes" | "settings"`.
  - `PROJECT_ITEMS` entries become `{ key: NavKey; section: ProjectSection; label; visible? }`, and `href` uses `section`.
  - A last entry `{ key: "project-settings", section: "settings", label: "Project settings", visible: (p) => !!p.project?.includes("project.manage") }`.
  - `NAV_ICONS["project-settings"] = SlidersHorizontal`.

## File map
| File | Responsibility |
|---|---|
| `api/src/megooflags_api/schemas/segments.py` | `SegmentListItem`, `SegmentUsage`, `SegmentUsageOut` |
| `api/src/megooflags_api/schemas/projects.py` | `EnvironmentOrderIn` |
| `api/src/megooflags_api/services/usage.py` | visibility filter, `segment_flag_counts` |
| `api/src/megooflags_api/services/segments.py` | `list_segments` with counts |
| `api/src/megooflags_api/services/environments.py` | `reorder_environments` |
| `api/src/megooflags_api/routers/segments.py`, `routers/environments.py` | the new and changed routes |
| `api/tests/test_segments.py`, `test_environments.py`, `test_rbac_routes.py`, `test_audit_coverage.py`, `test_openapi.py` | API tests |
| `web/lib/segments/segment-draft.ts`, `web/lib/environments/order.ts`, `web/lib/flags/rollout-plan.ts`, `web/lib/flags/schedules.ts` | pure helpers (+ `web/tests/unit/*.test.ts`) |
| `web/lib/routes.ts`, `web/lib/nav.ts`, `web/components/shell/nav.tsx`, `project-switcher.tsx`, `app-shell.tsx` | routes, the menu item, New project |
| `web/components/segments/*` | list, new dialog, detail, rules, details dialog, used by, delete dialog |
| `web/components/environments/*` | list, create/edit dialog, delete dialog |
| `web/components/projects/*` | project settings, new project dialog and button |
| `web/components/flags/*` (new) | key-list-field, weights-editor, request-fields, use-flag-automation, automation-panel, rollout-card, rollout-dialog, rollout-notice, schedule-list, schedule-dialog |
| `web/components/patterns/search-field.tsx`, `typed-delete-dialog.tsx` | shared patterns; `placeholder-page.tsx` is deleted |
| `web/app/(app)/projects/[project]/segments/page.tsx`, `segments/[segment]/page.tsx`, `environments/page.tsx`, `settings/page.tsx`, `web/app/(app)/page.tsx` | routes |
| `web/e2e/segments.spec.ts`, `environments.spec.ts`, `project-settings.spec.ts`, `flag-automation.spec.ts`, `matrix.spec.ts`, `global-setup.ts`, `support/api.ts`, `shell.spec.ts` | Playwright |
| `docs/ga-checklist.md`, `CHANGELOG.md` | docs |

## Steps

### 1. API: segment usage endpoint
**Files:** `schemas/segments.py`, `services/usage.py`, `routers/segments.py`, `api/tests/test_segments.py`, `api/tests/test_rbac_routes.py`, `api/tests/test_openapi.py`, `api/openapi.json`, `web/lib/api/schema.d.ts`, `CHANGELOG.md`.

**Tests first:**
- In `test_segments.py`, add the helper `async def use_segment(api, flag: str, env: str, segment: str = "beta") -> None`. It GETs `/api/projects/demo/flags/{flag}/environments/{env}` for the version, then PUTs `{"version": v, "enabled": False, "rules": [{"clauses": [{"operator": "in_segment", "values": [segment]}], "variation": "true"}], "fallthrough": {"variation": "false"}}` and asserts 200.
- `test_usage_lists_flags_and_environments_in_order`: flags `b-flag` (production and development) and `a-flag` (staging) use beta. GET `…/segments/beta/usage` → `{"used_by": [{"flag": "a-flag", "environment": "staging"}, {"flag": "b-flag", "environment": "development"}, {"flag": "b-flag", "environment": "production"}]}`.
- `test_usage_unused_is_empty`: `{"used_by": []}`.
- `test_usage_unknown_segment_404`: `not_found`.
- `test_rbac_routes.py::test_segment_usage_shows_only_visible_environments`: the `api` fixture puts beta in new-ui's staging and production rules (same PUT body). `client_as(("editor", "demo", "production"))` sees only production.
- `test_openapi.py`: add `"/api/projects/{project}/segments/{segment}/usage"` to the path set.

**Run:** API-TEST `tests/test_segments.py tests/test_rbac_routes.py tests/test_openapi.py`. Expected: 404 on `/usage`, and the path is missing.

**Implement:** follow C-API (schemas, the `visible` parameter of `segment_dependents`, and the route after `read_segment`).

**Pass:** the same API-TEST, plus API-LINT. Then REGEN, and CHANGELOG `### Added`: "`GET /api/projects/{project}/segments/{segment}/usage` lists the flags and environments whose rules use a segment."

**Commit:** `feat(api): segment usage endpoint (MF-458)`

### 2. API: flag_count on the segment list
**Files:** `schemas/segments.py`, `services/usage.py`, `services/segments.py`, `routers/segments.py`, `test_segments.py`, `test_rbac_routes.py`, the generated files, `CHANGELOG.md`.

**Tests first:**
- `test_list_paginated`: the expected key set gains `"flag_count"`.
- `test_list_flag_count`: segments `beta` (new-ui in staging and production, other-ui in production) and `unused` → list gives beta 2 and unused 0. One flag in two environments counts once.
- `test_list_flag_count_is_one_query`: with `count_statements(app.state.engine)` around a list of 3 segments, the statements that mention `clause_segment` number exactly 1.
- `test_rbac_routes.py::test_segment_flag_count_counts_only_visible_environments`: new-ui uses beta in staging and `other-ui` uses it in production. The admin sees 2, and `editor` at demo/production sees 1.

**Run:** API-TEST `tests/test_segments.py tests/test_rbac_routes.py` → KeyError `flag_count`.

**Implement:** C-API `segment_flag_counts`, `list_segments(..., visible=...)`, and the route returning `Page[SegmentListItem]`. Keep `SegmentOut` unchanged, so the audit snapshots keep their shape.

**Pass:** API-TEST, API-LINT, REGEN. CHANGELOG `### Added`: "Segment list items have `flag_count`: how many flags use the segment in the environments you can see."

**Commit:** `feat(api): flag_count on segment list items (MF-458)`

### 3. API: reorder environments in one call
**Files:** `schemas/projects.py`, `services/environments.py`, `routers/environments.py`, `test_environments.py`, `test_rbac_routes.py`, `test_audit_coverage.py`, `test_openapi.py`, `web/lib/audit/actions.ts`, the generated files, `CHANGELOG.md`.

**Tests first** (`ORDER = "/api/projects/demo/environment-order"`):
- `test_reorder_sets_sort_order`: PUT `["production", "development", "staging"]` → 200 with those keys in that order and `sort_order` 0, 1, 2. GET environments returns the same order.
- `test_reorder_refuses_a_wrong_list` (parametrized: a missing key, an unknown key `qa`, a repeated key, `[]`) → `assert_error(…, 422, "validation_error")`, and the order is unchanged.
- `test_reorder_writes_one_audit_entry`: `audit_entries(app, "environment.reorder")` has one entry with `resource_type == "project"`, `resource_key == "demo"`, `before == {"environments": ["development", "staging", "production"]}` and `after` the new order.
- `test_rbac_routes.py::test_reorder_needs_environment_manage`: an org `viewer` gets 403.
- `test_audit_coverage.py`: `sample("PUT", f"{P}/environment-order", "environment.reorder", project, {"environments": ["production", "staging", "development"]})` after the environment samples.
- `test_openapi.py`: add `"/api/projects/{project}/environment-order"`.

**Run:** API-TEST `tests/test_environments.py tests/test_rbac_routes.py tests/test_audit_coverage.py tests/test_openapi.py` → 404/405, and the coverage test complains.

**Implement:** C-API `EnvironmentOrderIn`, `reorder_environments` and the route. In `web/lib/audit/actions.ts`, add the label `"environment.reorder": "Environments reordered"` (keep LABELS sorted; `audit-actions.test.ts` checks that).

**Pass:** API-TEST, API-LINT, REGEN, UNIT `tests/unit/audit-actions.test.ts`. CHANGELOG `### Added`: "`PUT /api/projects/{project}/environment-order` reorders all environments of a project in one call."

**Commit:** `feat(api): reorder a project's environments in one call (MF-458)`

### 4. Web: shared parts for segments
**Files:** `web/components/flags/key-list-field.tsx` (new), `rules-editor.tsx`, `clause-row.tsx`, `unsaved-bar.tsx`, `web/lib/flags/config-draft.ts`, `web/components/patterns/search-field.tsx` (new), `web/components/settings/members-tab.tsx`, `web/tests/unit/flags-config-draft.test.ts`.

**Test first:** in `flags-config-draft.test.ts`, check that `newClause()` returns `{attribute: "key", operator: "in", values: [], negate: false}` with a fresh id, and that two `newId()` calls differ. Run UNIT → fails (not exported).

**Implement** C-WEB-UI: `KeyListField` (replace `TargetsField` in `rules-editor.tsx` with it, label `Context keys served ${v}`), `ClauseRow.allowSegments`, the `newId`/`newClause` exports, `UnsavedBar.actionLabel`, and `SearchField` (members-tab uses it). This is a refactor: no visible change on the flag page or the members tab.

**Pass:** UNIT; WEB-GATE; E2E `e2e/flag-detail.spec.ts e2e/settings.spec.ts` (unchanged behaviour).

**Commit:** `refactor(web): shared key list, search field and clause options (MF-458)`

### 5. Web: segment draft helpers
**Files:** `web/lib/segments/segment-draft.ts`, `web/tests/unit/segments-draft.test.ts`.

**Test first** (every export of C-WEB-LIB `segment-draft`):
- `toDraft`/`toConfigIn` round-trip a `SegmentOut` with two rules (ids stripped).
- `isDirty` is false for the same content and true after `setKeys`.
- `setKeys` trims, drops empty lines and dedupes in order.
- The rule and clause helpers, including `moveRule` at the ends and `removeClause` keeping a last clause.
- `validateDraft` finds a key in both lists (path `excluded[0]`), a 257-character key, and a clause without values.
- `problemField` maps `excluded[3]` → `excluded`, `rules[1].clauses[0].values` → `rules[1].clauses[0]`, `rules[2]` → `rules[2]`, and `version` → null.
- `usageByFlag` groups by flag; `usedByText(0|1|2)`.

**Run:** UNIT `tests/unit/segments-draft.test.ts` → the module is missing.

**Implement** with `newId`/`newClause` from `config-draft.ts`.

**Pass:** UNIT; WEB-GATE.

**Commit:** `feat(web): segment draft helpers (MF-458)`

### 6. Web: Segments list page
**Files:** `web/lib/routes.ts` (`segmentPath`), `web/tests/unit/routes.test.ts`, `web/components/segments/segment-list.tsx`, `new-segment-dialog.tsx`, `web/app/(app)/projects/[project]/segments/page.tsx`, `web/app/(app)/projects/[project]/segments/[segment]/page.tsx` (minimal now, finished in step 7), `web/e2e/segments.spec.ts`.

**Tests first:**
- `routes.test.ts`: `segmentPath("my shop", "beta testers")` → `/projects/my%20shop/segments/beta%20testers`.
- `e2e/segments.spec.ts` (shop, ``RUN = `e2e-${Date.now().toString(36)}` ``):
  - "create a segment": "New segment" with name `${RUN} Beta` → the key field shows `slugifyKey(name)`. Create → the URL is `segmentPath(shop, key)` and the heading is the name.
  - "search": after creating `${RUN}-alpha` and `${RUN}-omega` through the API, typing `${RUN}-al` leaves only alpha, and the URL has `q=`.
  - "used by in the list": beta-testers shows "Not used". After `putConfig` puts an `in_segment` rule in a test flag, it shows "1 flag".
  - "cards under 768 px": at 390 px, the list is an `ul` named "Segments" with `article`s, and the page has no horizontal scroll.
  - "viewer cannot create": as VIEWER, there is no "New segment".
  - `afterAll` deletes the RUN segments, removing the rule first.

**Run:** UNIT and E2E `e2e/segments.spec.ts` → the placeholder.

**Implement:**
- `SegmentList({ project })`:
  - `PageHeader` "Segments" (`projectTrail`), with "New segment" in its actions when `can(perms, "segment.write")`.
  - `SearchField` (label "Search segments") on `?q=`.
  - `useApiData` on `GET …/segments` with `{ q, limit: 50 }`, plus Load more as in `service-token-list.tsx`.
  - `DataList` caption "Segments" with these columns:
    - key: the name as a `Link` to `segmentPath`, with the key in `font-mono` below
    - main: description
    - main: "Used by" (`usedByText(flag_count)`)
    - extra: Updated (`<time>`)
  - `LoadingRows`, `LoadFailed`, and an `EmptyState` with no segments, or "No segments match" for a search.
- `NewSegmentDialog`:
  - Fields: name, key (follows `slugifyKey(name)` until edited; `KEY_PATTERN` check), description.
  - It POSTs `…/segments`; 409 `key_conflict` shows on Key and 422 on its path. Then `router.push(segmentPath(…))`.
- The `[segment]/page.tsx`: a server component that calls `serverApi().GET(…/segments/{segment})` and runs `notFound()` on 404. Until step 7 it renders `<PageHeader title={data.name} trail={…} />`.

**Pass:** UNIT; E2E `e2e/segments.spec.ts`; WEB-GATE.

**Commit:** `feat(web): Segments list with search, create and usage (MF-458)`

### 7. Web: a segment's page
**Files:** `web/components/segments/segment-detail.tsx`, `segment-rules.tsx`, `segment-details-dialog.tsx`, `used-by.tsx`, `segments/[segment]/page.tsx`, `e2e/segments.spec.ts`.

**E2E first** (append):
- "edit keys and rules": open a RUN segment, type `u1`, `u2` in Included keys and blur → "2 keys" and the "Unsaved changes" bar. Add rule → clause attribute `plan`, values `pro`, blur. The Operator select has no "is in segment". Save → the bar is gone, and after a reload the keys and the rule are still there.
- "a key in both lists": `u1` in Excluded → Save shows "u1 is both included and excluded" under Excluded, and nothing is sent.
- "unsaved guard": with a dirty draft, clicking the "Segments" crumb asks; dismissing stays on the page.
- "used by and confirmation": with a test flag using the segment in development (`putConfig`), Used by lists the flag link and "development". Save asks "Save changes to <name>?"; Confirm saves.
- "edit details": rename → the heading changes.
- "viewer read-only": the textareas are read-only, with no Add rule, Save or Edit details.
- "unknown segment": `/projects/shop/segments/nope` shows "Page not found".

**Run:** E2E `e2e/segments.spec.ts` → the new tests fail.

**Implement** `SegmentDetail({ project, segment })`:
- It loads `GET …/segments/{segment}` and `…/usage` through `useApiData`, and holds a `{loaded, base, draft}` state as `flag-detail.tsx` does. `useUnsavedGuard(dirty)`.
- `PageHeader` with title = name, description = key (`code`), and the crumbs project → Segments → name. Actions (with `segment.write`): "Edit details" and "Delete" (Delete is wired in step 8).
- Sections:
  - "Included keys" and "Excluded keys": `KeyListField` with `showCount`, through `setKeys`.
  - "Rules": `SegmentRules` with cards like `RuleCard`, without the serve part. They have a Description, `ClauseRow allowSegments={false}`, move up/down (focus follows), remove, Add clause and Add rule.
  - "Used by": `UsedBy` from `usageByFlag`, each flag a `Link` to `flagPath` with its environments, and the line "A saved change applies to these flags at once, in every environment listed, protected ones too." Without uses: "No flag uses this segment."
- Problems: client `validateDraft` after Save is pressed, plus an API 422 mapped through `problemField`, shown under the field. `UnsavedBar actionLabel="Save" needsApproval={false}`.
- Save sends `PUT` with `toConfigIn(draft)`. When usage is non-empty, a `ConfirmDialog` comes first. The response becomes the new base, and usage reloads.
- `SegmentDetailsDialog` PATCHes name and description.
- Read-only without `segment.write`.

**Pass:** E2E `e2e/segments.spec.ts`; WEB-GATE.

**Commit:** `feat(web): segment page with keys, rules and usage (MF-458)`

### 8. Web: delete a segment
**Files:** `web/components/segments/delete-segment-dialog.tsx`, `segment-detail.tsx`, `e2e/segments.spec.ts`.

**E2E first:**
- "delete blocked while in use": with a rule using it, Delete shows "<name> is used by these flags" with the flag and environment, and only Close.
- "delete after removing the rule": after `putConfig` removes the rule and the page reloads, Delete → Confirm → back on the list, and the segment is gone.
- "409 shows the API's list": `page.route` returns 409 `in_use` with `details.used_by` for DELETE → the dialog switches to the blocked list.

**Run:** E2E `e2e/segments.spec.ts`.

**Implement** `DeleteSegmentDialog({ project, segment, name, usedBy, open, onOpenChange })`:
- With uses: a `ResponsiveDialog` listing `usageByFlag(usedBy)` and a Close button.
- Otherwise a `ConfirmDialog`-like footer ("Delete segment", destructive) → `DELETE`.
  - A 409 `in_use` switches to the list from `failure.details.used_by`.
  - Success → toast "Segment deleted" and `router.push(projectPath(project, "segments"))`.

**Pass:** E2E; WEB-GATE.

**Commit:** `feat(web): delete a segment, blocked while flags use it (MF-458)`

### 9. Web: Environments list with reorder
**Files:** `web/lib/environments/order.ts`, `web/tests/unit/environments-order.test.ts`, `web/components/environments/environment-list.tsx`, `web/app/(app)/projects/[project]/environments/page.tsx`, delete `web/components/patterns/placeholder-page.tsx`, `web/e2e/environments.spec.ts`.

**Tests first:**
- UNIT: `moveKey` (middle, ends, unknown key) and `approvalsLabel(0|1|3)`.
- `e2e/environments.spec.ts`:
  - `beforeAll` creates project `${RUN}` (key `e2e-<base36>`) with flag `f1` through `adminApi()`; `afterAll` deletes it.
  - "lists in order with approvals": rows Development, Staging, Production; after PATCHing production `approvals_required: 2`, it shows "Needs 2 approvals"; the others show "Direct changes".
  - "reorder": "Move Production up" twice → the order is Production, Development, Staging; the focused button is still a Production move button; after a reload the order holds; on `flagPath(RUN, "f1")` the environment strip shows Production first.
  - "reorder refused": `page.route` answers PUT environment-order with 422 → a toast "The environments changed meanwhile. The list was reloaded." and the order is unchanged.
  - "viewer read-only": as VIEWER on `/projects/shop/environments`, there are no move, Edit or Delete buttons.
  - "cards under 768 px" at 390 px.

**Run:** UNIT; E2E → the placeholder.

**Implement** `EnvironmentList({ project })`:
- It loads `GET …/environments`; `canManage = can(perms, "environment.manage")`.
- `PageHeader` "Environments" with "New environment" (step 10) for managers.
- `DataList` caption "Environments" with these columns:
  - key: name, with the key in mono
  - status: `StatusPill`, `pending` with a Lock icon label `approvalsLabel(n)` when n > 0, else `off` "Direct changes"
  - main: description
  - main: actions (managers only) — "Move <name> up", "Move <name> down" (icon buttons, 44 px), Edit, Delete (steps 10–11)
- A move PUTs `environment-order` with `moveKey(keys, key, by)` and replaces the rows with the response. Focus follows the moved environment as `RulesEditor` does; a 422 → reload and the toast above.
- Delete `placeholder-page.tsx`; `rg -n PlaceholderPage web` is then empty.

**Pass:** UNIT; E2E `e2e/environments.spec.ts`; WEB-GATE.

**Commit:** `feat(web): Environments list with reorder (MF-458)`

### 10. Web: create and edit environments, with approvals
**Files:** `web/components/environments/environment-dialog.tsx`, `environment-list.tsx`, `e2e/environments.spec.ts`.

**E2E first:**
- "create": New environment, name `QA` → key `qa`; the dialog says every flag gets it turned off and that it is added last. Create → the row is last, and `GET …/flags/f1/environments/qa` returns `enabled: false`.
- "duplicate key": key `qa` again → "environment 'qa' already exists" under Key.
- "set and clear approvals": Edit Staging → tick "Changes need approval", set Approvals needed 2 → Save → "Needs 2 approvals"; on the flag page, the Staging card shows "Needs approval". Edit again → untick → the note "Open change requests stay open and still need one approval." shows; Save → "Direct changes".
- "approvals range": 11 → the field error "Enter 1 to 10" and Save is disabled.

**Run:** E2E.

**Implement** `EnvironmentDialog({ project, environment, open, onOpenChange, onSaved })`:
- **Create** (`environment === null`): name, key (`slugifyKey`, `KEY_PATTERN`), description, and the two notes → POST.
- **Edit**: name, description (the key is shown read-only), a "Changes need approval" checkbox, and "Approvals needed" (`type="number"`, 1–10, only when ticked) → PATCH with `approvals_required` = the number, or 0 when unticked.
  - The note shows when the environment had approvals and the box is now unticked.
- 409 `key_conflict` → Key; a 422 path → its field.

**Pass:** E2E; WEB-GATE.

**Commit:** `feat(web): create and edit environments with required approvals (MF-458)`

### 11. Web: delete an environment
**Files:** `web/components/patterns/typed-delete-dialog.tsx`, `web/components/environments/delete-environment-dialog.tsx`, `environment-list.tsx`, `e2e/environments.spec.ts`.

**E2E first:**
- "delete with the typed key": Delete QA → the dialog lists what goes with it; Delete stays disabled until `qa` is typed → the row is gone, and the flag page no longer has QA.
- "the last environment": in a second RUN project with one environment, its Delete button is disabled and "A project needs at least one environment." is shown.

**Run:** E2E.

**Implement:**
- `TypedDeleteDialog` (C-WEB-UI).
- `DeleteEnvironmentDialog` passes the description "This deletes <name> and, with it, every flag's state, targeting, schedules and rollouts there, its SDK keys, change requests and role assignments. It can't be undone." and `typedKey = env.key`. It calls DELETE, then `onDeleted` and the toast "Environment deleted".
- In the list, Delete is disabled with `aria-describedby` pointing at that sentence when `rows.length === 1`.

**Pass:** E2E; WEB-GATE.

**Commit:** `feat(web): delete an environment with its key typed (MF-458)`

### 12. Web: Project settings page and menu item
**Files:** `web/lib/routes.ts`, `web/lib/nav.ts`, `web/components/shell/nav.tsx`, `web/tests/unit/nav.test.ts`, `routes.test.ts`, `web/components/projects/project-settings.tsx`, `web/app/(app)/projects/[project]/settings/page.tsx`, `web/e2e/shell.spec.ts`, `web/e2e/project-settings.spec.ts`.

**Tests first:**
- `nav.test.ts`: the admin list becomes `[flags, segments, environments, change-requests, audit, keys, integrations, project-settings, settings]`, with href `/projects/demo/settings`. The org viewer from the existing test lacks `project-settings`.
- `routes.test.ts`: `switchProjectPath("/projects/a/settings", "b")` → `/projects/b/settings`.
- `shell.spec.ts`: add `"Project settings"` to `ADMIN_ITEMS` after `"Integrations"`. In the viewer test, `toHaveCount(1)` for Settings uses `{ name: "Settings", exact: true }`.
- `e2e/project-settings.spec.ts`:
  - `beforeAll` creates project `${RUN}-p`; `afterAll` deletes it if it still exists.
  - "rename and describe": change the name and description, Save → the toast "Project saved", and the project switcher shows the new name.
  - "stale after days": 0 and 366 → "Enter 1 to 365" and Save is disabled; 45 → saved, and `GET /api/projects/<key>` returns 45.
  - "viewer": on `/projects/shop/settings`, the fields are read-only, there is no Save or Danger zone, and the sidebar has no "Project settings".

**Run:** UNIT `tests/unit/nav.test.ts tests/unit/routes.test.ts`; E2E `e2e/shell.spec.ts e2e/project-settings.spec.ts`.

**Implement:**
- C-ROUTES.
- `ProjectSettings({ project })` loads `GET /api/projects/{project}`; `canManage = can(perms, "project.manage")`. `PageHeader` "Project settings". Two cards (`Card`, `h2`):
  - **Details**: name, key (read-only, mono), description, Save → PATCH `{name, description}`.
  - **Stale flags**: "Stale after (days)" (`type="number"`, 1–365, with the help "A flag no environment has evaluated for this many days is marked stale."), Save → PATCH `{stale_after_days}`.
- Each Save shows a toast, then `router.refresh()` (the switcher is server-loaded). A 422 path → its field. Read-only without `canManage`.

**Pass:** UNIT; E2E; WEB-GATE.

**Commit:** `feat(web): Project settings page (MF-458)`

### 13. Web: create and delete projects
**Files:** `web/components/projects/new-project-dialog.tsx`, `new-project-button.tsx`, `project-settings.tsx`, `web/components/shell/project-switcher.tsx`, `app-shell.tsx`, `web/app/(app)/page.tsx`, `e2e/project-settings.spec.ts`.

**E2E first:**
- "create from the switcher": the "Project: …" button → menuitem "New project" → name `${RUN} New` → key suggested; the dialog says it starts with Development, Staging and Production → Create → the URL is `/projects/<key>/flags` and the switcher shows it.
- "delete with the typed key": Project settings of that project → Danger zone → "Delete project" → type the key → the URL is `/projects/demo/flags` (via `/`), and `GET /api/projects/<key>` is 404.
- "created without access": `page.route` makes `GET /api/projects/<key>` return 404 after a create → the toast "Project created, but you have no access to it. Ask an admin for a role in it."
- "viewer": no "New project" menu item.

**Run:** E2E `e2e/project-settings.spec.ts`.

**Implement:**
- `NewProjectDialog({ open, onOpenChange })`:
  - Fields: name, key (`slugifyKey`), description, and the note. It POSTs `/api/projects` `{key, name, description}`. 409 → Key.
  - Then it GETs the project: 404 shows the no-access toast and `router.refresh()`; otherwise `router.push(projectPath(key))` and `router.refresh()`.
- `ProjectSwitcher` gains `canCreate: boolean`. When true, it adds `DropdownMenuSeparator` and a "New project" item (Plus icon) that opens the dialog, rendered outside the menu. `AppShell` passes `!!permissions?.org.includes("project.create")`.
- `NewProjectButton` (client): a button plus the dialog. The `/` page asks `GET /api/auth/me/permissions` (no project) on the server and renders it inside the "No projects yet" `EmptyState` when `org` has `project.create`.
- In `ProjectSettings`, a **Danger zone** card (only with `canManage`): "Delete project" (`DANGER_TRIGGER`) → `TypedDeleteDialog` with "This deletes <name> with all its flags, segments, environments, SDK keys, change requests and history. It can't be undone." → DELETE → `router.push("/")` and `router.refresh()`.

**Pass:** E2E; WEB-GATE.

**Commit:** `feat(web): create and delete projects (MF-458)`

### 14. Web: rollout plan helpers
**Files:** `web/lib/flags/rollout-plan.ts`, `web/tests/unit/flags-rollout-plan.test.ts`.

**Test first** (C-WEB-LIB `rollout-plan`):
- `defaultFrom` for a variation, a rollout and an empty fallthrough.
- `defaultPlan("false", ["true", "false"])` → to `true`, steps 10/25/50 at 1 hour each.
- `toRolloutIn` → `[{percentage: 10000, wait_minutes: 60}, …, {percentage: 100000}]`, and 12.5 % → 12500.
- `planProblems`: from === to; 0, 100 and falling percentages; a wait of "1.5"; 31 days; 20 intermediate steps (→ "At most 20 steps").
- `totalMinutes` and `durationText` (0, 60, 90, 1500, 43200).
- `currentPercent` (active step 1 → 25; completed → 100).
- `stepStates`, `rolloutPill` for every status, and `variationText(null)`.

**Run:** UNIT → the module is missing.

**Implement** with `fromPercent` from `lib/flags/weights.ts`.

**Pass:** UNIT; WEB-GATE.

**Commit:** `feat(web): progressive rollout plan helpers (MF-458)`

### 15. Web: schedule helpers
**Files:** `web/lib/flags/schedules.ts`, `web/tests/unit/flags-schedules.test.ts`.

**Test first** (C-WEB-LIB `schedules`), with a fixed `now`:
- `scheduleText` for each action.
- `schedulePermission`.
- `upcoming`/`past` sorting.
- `schedulePill`.
- `defaultRunAt(new Date(2026, 10, 24, 10, 2))` → `"2026-11-24T11:05"`.
- `localToIso` (valid, empty, garbage).
- `runAtProblem` (empty, past, 366 days).
- `utcText`, `relativeText`.
- `automationCount`, `needsPolling` and `wasApplied` (a schedule turning done; a current_step growing; a rollout no longer live; no change → false).

**Run:** UNIT → missing.

**Implement.**

**Pass:** UNIT; WEB-GATE.

**Commit:** `feat(web): scheduled change helpers (MF-458)`

### 16. Web: shared parts for automation
**Files:** `web/components/flags/weights-editor.tsx`, `serve-editor.tsx`, `request-fields.tsx`, `toggle-request-dialog.tsx`.

**Implement** C-WEB-UI `WeightsEditor` and `RequestFields` by moving the existing code. The labels, ids and behaviour stay the same, so this is a pure refactor.

**Check:** E2E `e2e/flag-detail.spec.ts e2e/flags-mobile.spec.ts` (rollout slider, weights, toggle request) passes unchanged; WEB-GATE.

**Commit:** `refactor(web): weights editor and request fields as their own components (MF-458)`

### 17. Web: Automation tab (load, count, polling, Targeting notice, read-only views)
**Files:** `web/components/flags/use-flag-automation.ts`, `automation-panel.tsx`, `rollout-card.tsx`, `schedule-list.tsx`, `rollout-notice.tsx`, `flag-detail.tsx`, `web/e2e/support/api.ts`, `web/e2e/flag-automation.spec.ts`.

**E2E first:**
- In `support/api.ts`, add helpers that POST and return the JSON:
  - `startRollout(api, flag, env, steps = [{percentage: 10000, wait_minutes: 60}, {percentage: 100000}], from = "false", to = "true")`
  - `scheduleChange(api, flag, env, body)`
- `e2e/flag-automation.spec.ts` (shop test flags):
  - "shows a running rollout": `startRollout` in development → the Automation tab label includes "1"; the tab shows "Running", "false → true", "10 %", and steps "10 %" (current) and "100 %" (next, with a `<time>`); Targeting shows the notice "A progressive rollout is running: the default rule serves true to 10 % now." with a link "Open Automation".
  - "lists schedules": `scheduleChange` enable at +1 day → "Upcoming": "Turn on" with a `<time>`; tab count 1; "Past changes" is folded.
  - "empty": a fresh flag → "No rollout running" and "No scheduled changes".
  - "viewer": as VIEWER, the panel shows but there is no Start, Pause, Schedule or Cancel.

**Run:** E2E `e2e/flag-automation.spec.ts` → there is no Automation tab.

**Implement:**
- `useFlagAutomation(project, flag, env, onApplied)` returns `{ data: AutomationSnapshot; failure; reload(): Promise<void> }`.
  - It loads `GET …/rollout` and `GET …/schedules` together when `env` changes, and keeps the last data during reloads (no skeleton flash; reset on an env change).
  - `useVisibleInterval(() => { if (needsPolling(data)) void reload(); }, 30_000)`.
  - After each load, when `wasApplied(prev, next)`, it calls `onApplied()`.
- In `flag-detail.tsx`:
  - `TABS` gains `{ value: "automation", label: "Automation" }` after targeting; the trigger shows a `Badge` with `automationCount` when > 0 (`aria-label` "Automation, n active").
  - `onApplied` = `flagData.reload()`, plus `config.reload()` when `!dirty`.
  - `RolloutNotice` goes above the `RulesEditor` when `rollout.current` is active or paused.
- `AutomationPanel({ project, flag: FlagOut, env, envName, enabled, perms, archived, automation })` holds two sections:
  - **"Progressive rollout"**: `RolloutCard` shows the pill, from → to (`variationText`), who started it, a `<time>`, the current percent with a bar (`role="progressbar"` with aria values), the steps from `stepStates` (the next one shows `next_step_at` as a `<time>`, or "n minutes left" from `remaining_seconds` when paused), "Recent rollouts" (status pill, error text), and the note "The default rule serves nobody until the flag is on in <env>." when `!enabled`.
  - **"Scheduled changes"**: `ScheduleList` with Upcoming (text, `<time>` local and relative, created_by, a `changeRequestPath` link when `change_request_id`) and `<details>` "Past changes" (pill, error).
  - A load failure shows `LoadFailed`.

**Pass:** E2E; E2E `e2e/flag-detail.spec.ts`; WEB-GATE.

**Commit:** `feat(web): Automation tab with rollout and schedule status (MF-458)`

### 18. Web: start, pause, resume and abort a rollout
**Files:** `web/components/flags/rollout-dialog.tsx`, `rollout-card.tsx`, `automation-panel.tsx`, `flag-detail.tsx`, `e2e/flag-automation.spec.ts`.

**E2E first:**
- "start": "Start a rollout" → the dialog has From `false` (the current default), To `true`, and steps 10/25/50 (1 hour) plus a fixed "100 %".
  - Change step 2 to 30 % and remove step 3; the summary reads "The first step (10 %) applies when you start. 100 % after 2 hours."
  - Start → the card shows Running at 10 %, and Targeting's default rule is a rollout with the slider at 10.
- "checks": To = From → "Choose a variation other than From" and Start is disabled.
- "pause, resume, abort": Pause → Paused with a wait left; Resume → Running; Abort → a confirm that says the default rule serves false to everyone again → "No rollout running"; Recent rollouts has Aborted, and Targeting serves `false`.
- "already running": `page.route` returns 409 `invalid_state` on start → the toast shows the API message and the card reloads.
- "protected production": in production the button reads "Request a rollout"; the dialog has Title and Description → "Change request created", and `GET …/change-requests?flag=<f>` has kind `rollout`.
- "approval_required meanwhile": `page.route` returns 409 `approval_required` on a development start → the dialog switches to the request form.

**Run:** E2E.

**Implement:**
- `RolloutDialog({ project, flag: FlagOut, env, envName, needsApproval, fallthrough, open, onOpenChange, onDone, onApprovalRequired })`:
  - From/To `NativeSelect`s.
  - A step list (per row: "Step n percent" input, Wait number + unit select, Remove), "Add step", and the fixed last row "100 %".
  - `planProblems` errors under their fields, the summary (`durationText(totalMinutes)`), and the note "Only the default rule changes. Rules and individual targets stay as they are."
  - Submit: direct → POST `…/rollout` with `toRolloutIn`; `needsApproval` → `RequestFields` (title default "Roll out <to> in <flag> in <envName>") and POST change-requests `{kind: "rollout", payload: toRolloutIn(plan)}`.
  - A 409 `approval_required` calls `onApprovalRequired()` (flag-detail reloads environments) and switches to the request form.
- `RolloutCard` actions (with `flag.config.write` in env, not archived): Pause (`…/rollout/pause`), Resume, and Abort (`ConfirmDialog`: "The default rule serves <from> to everyone again. Pause keeps the current split instead."). After each action: `automation.reload()` and `onApplied()`. A 409 `invalid_state` → toast `failure.message` and reload.

**Pass:** E2E; WEB-GATE.

**Commit:** `feat(web): start, pause, resume and abort progressive rollouts (MF-458)`

### 19. Web: schedule and cancel changes
**Files:** `web/components/flags/schedule-dialog.tsx`, `schedule-list.tsx`, `automation-panel.tsx`, `e2e/flag-automation.spec.ts`.

**E2E first:**
- "schedule on and off": "Schedule a change" → Turn on, When = a local time two days ahead (computed in the test as `YYYY-MM-DDTHH:mm`) → the dialog shows the browser time zone and "… UTC" → Schedule → Upcoming has "Turn on". Repeat with Turn off → both are listed soonest first.
- "schedule a rollout": "Set the default rule to a rollout" shows the slider for a boolean flag; set 25 → Upcoming "Set the default rule to true 25 %, false 75 %". For a string test flag it shows weight inputs per variation, and a total ≠ 100 % blocks Schedule.
- "past time": a time 1 hour ago → "Pick a time in the future".
- "cancel": Cancel on a row → confirm → it moves to Past changes as "Cancelled".
- "protected production": "Request a schedule" with Title → a change request of kind `schedule`; the form says "If it is approved after this time, it fails."

**Run:** E2E.

**Implement:**
- `ScheduleDialog({ project, flag: FlagOut, env, envName, needsApproval, perms, open, onOpenChange, onDone, onApprovalRequired })`:
  - The change as radios from `SCHEDULE_ACTIONS`. An option the caller lacks `schedulePermission` for in env is disabled.
  - `WeightsEditor` for set_rollout (starting at `evenWeights(variations)`), with "Weights must add up to 100 %" from `weightsValid`.
  - When: `Input type="datetime-local"` (`text-base`, default `defaultRunAt(now)`), the help "Your time zone: <Intl tz>" and `utcText(localToIso(v))`, and the `runAtProblem` error.
  - Direct → POST `…/schedules` `{action, run_at, weights?}`; request → change-requests `{kind: "schedule", payload}`; `approval_required` is handled as in step 18.
- `ScheduleList` Cancel (shown when `can(perms, schedulePermission(s.action), env)` and not archived) → `ConfirmDialog` → DELETE `…/schedules/{id}` → reload.

**Pass:** E2E; WEB-GATE.

**Commit:** `feat(web): schedule and cancel flag changes (MF-458)`

### 20. Web: automation at 344 px with touch
**Files:** `e2e/flag-automation.spec.ts`, and the automation components if fixes are needed.

**E2E first:** a `test.describe("automation at 344 px with touch")` with `test.use({ viewport: { width: 344, height: 800 }, hasTouch: true, isMobile: true })`. Every interaction uses `.tap()`, and `fits(page)` (no horizontal scroll, no small targets) runs after each screen:
- Start a rollout (the dialog is full screen; edit a step percent) → Running.
- Pause → Abort (confirm).
- Schedule Turn on (fill datetime-local) → Upcoming.
- Cancel it.

**Run:** E2E → fix every horizontal scroll, small target or hover-only control it finds (for example, step rows wrap with `flex-wrap`, and the unit select gets `min-h-11`).

**Pass:** E2E `e2e/flag-automation.spec.ts`; WEB-GATE.

**Commit:** `test(web): automation at 344 px with touch (MF-458)`

### 21. E2E: gov seed and the responsive/a11y matrix
**Files:** `web/e2e/global-setup.ts`, `web/e2e/matrix.spec.ts`, `web/e2e/matrix.spec.ts-snapshots/`.

**Seed:** `seedGovAutomation(admin)` is called at the end of `seedGov`. It is idempotent and never changes an existing item:
- Segment `gov-beta` "Gov beta testers" (`ensure`). When created, PUT `{included: ["user-1", "user-2"], rules: [{description: "Pro plan", clauses: [{attribute: "plan", operator: "in", values: ["pro"]}]}]}`.
- Flag `gov-rollout` "Gov rollout" (boolean). When created, PUT development `{version, enabled: true, rules: [{description: "Beta testers", clauses: [{operator: "in_segment", values: ["gov-beta"]}], variation: "true"}], fallthrough: {variation: "false"}}`.
- When `GET …/gov-rollout/environments/development/rollout` has `current === null`: start `{from_variation: "false", to_variation: "true", steps: [{percentage: 10000, wait_minutes: 60}, {percentage: 50000, wait_minutes: 60}, {percentage: 100000}]}`, then pause it.
- When development has no pending schedule: POST `{action: "disable", run_at: <now + 300 days ISO>}`.

**Matrix entries:**
- The tab list becomes `["targeting", "automation", "test", "history", "insights", "settings"]`, which gives `flag-automation` (demo, empty).
- New entries, all `mask: true`:
  - `flag-automation-live`: `/projects/gov/flags/gov-rollout?env=development&tab=automation`, `detail: true`, `ready: "Paused"`
  - `segments`: `/projects/gov/segments`, `ready: "gov-beta"`
  - `segment`: `/projects/gov/segments/gov-beta`, `ready: "gov-rollout"`
  - `environments`: `/projects/gov/environments`, `ready: "Needs 1 approval"`
  - `project-settings`: `/projects/gov/settings`

**Run first:** E2E `e2e/matrix.spec.ts` → the new entries fail on missing baselines. Fix every axe violation, horizontal scroll and small target at 280 px before taking baselines.

**Then:**
- Run SNAPSHOTS for the full suite, as the baselines come from full-suite runs.
- Expected changes: new PNGs for the new entries; `flag-*` (the new tab); and every page whose sidebar lists project items (the new "Project settings" item).
- Compare every other changed PNG with HEAD by pixels and `git checkout` those that are only data noise.
- Review the new PNGs by eye (both themes, 280 and 1440).
- E2E `e2e/matrix.spec.ts` passes from those baselines.

**Commit:** `test(web): project pages and Automation in the responsive and a11y matrix (MF-458)`

### 22. Docs: GA checklist and CHANGELOG
**Files:** `docs/ga-checklist.md`, `CHANGELOG.md`.

**Do:**
- `docs/ga-checklist.md`:
  - T-4 evidence: `api/tests/test_segments.py`, `web/e2e/segments.spec.ts` (drop "placeholder").
  - UI-14 evidence: add segments `web/e2e/segments.spec.ts` ("cards under 768 px") and drop "follows in MF-458".
  - GA "Scheduled changes" row → **done**, with `api/tests/test_schedules.py` and `web/e2e/flag-automation.spec.ts`.
  - The checklist lists only P0 rows and has no F-10 or T-6 row. Name them in that row's evidence: "F-10 scheduled changes and T-6 progressive rollouts: `web/e2e/flag-automation.spec.ts` (also at 344 px with touch)".
  - Remove blocker 5 from section 6 and renumber nothing else.
  - Check that `rg -n placeholder docs/ga-checklist.md` finds no web placeholder wording.
- `CHANGELOG.md` `[Unreleased]` → `### Added`, for users:
  - "Segments page: create segments, edit included and excluded keys and rules, see which flags use a segment; deleting is blocked while flags use it."
  - "Environments page: create, edit, reorder and delete environments, and require approvals (protected environments)."
  - "Project settings: name, description and stale-flag days; create a project from the project switcher and delete one with its key typed."
  - "Automation tab on the flag page: schedule turning a flag on or off or a new default rollout, and start, pause, resume or abort a progressive rollout. In an environment that needs approval these open change requests."
- `git ls-files web/e2e` lists every spec named.

**Pass:** `cd web && pnpm format:check` is unaffected; review the Markdown diff by hand.

**Commit:** `docs: project pages and automation close T-4, UI-14, F-10, T-6 and blocker 5 (MF-458)`

### 23. Final verification
Run every check in AGENTS.md §9 that this change touches. Fix failures with new `fix(api|web): … (MF-458)` commits; do not amend.
- `megoocolab-runner services run api -- bash -c 'cd api && uv sync --frozen && uv run pytest -q'` and API-LINT.
- `megoocolab-runner services run db -- bash scripts/check_migrations.sh` (no migration; it must still pass).
- REGEN, then `git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts`.
- `cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test`.
- E2E with all specs (empty `<spec>`), then the matrix in the pinned image (SNAPSHOTS without `--update-snapshots`).
- `rg -n PlaceholderPage web` is empty.
- `uv run --frozen --project api python scripts/check_version.py` (version unchanged).

## Final verification
1. `megoocolab-runner services run api -- bash -c 'cd api && uv run pytest -q'` plus API-LINT: all green, including `test_audit_coverage.py`, `test_route_permissions.py` and `test_openapi.py`.
2. No OpenAPI drift: `uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api && git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts`.
3. `cd web && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test`.
4. Playwright: `segments`, `environments`, `project-settings`, `flag-automation`, `shell`, `flag-detail` and `flags-mobile` specs, then the matrix in the pinned image (`pnpm e2e:docker`).
5. `rg -n PlaceholderPage web` is empty, and every project page renders real content.
6. `docs/ga-checklist.md` shows T-4, UI-14 and "Scheduled changes" with the new specs (F-10 and T-6 named there), and blocker 5 is gone.
