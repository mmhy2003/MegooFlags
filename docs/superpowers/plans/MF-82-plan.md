# MF-82 (part 1) plan: Flags list and flag detail pages

**Goal:** replace the MF-81 Flags placeholders with working pages. Users can find, create, edit, target, test, toggle, kill, copy and inspect flags from 280 px up, with Playwright, axe and visual coverage.

**Architecture:** three small read-only API additions: a flag list `env`/`enabled` filter, an audit `resource_key` filter, and an evaluations series endpoint. On the web side, pure logic lives in `web/lib/flags/*.ts` and is unit-tested with Vitest. Client components in `web/components/flags/` call the API only through the generated `api` client. Routes in `app/(app)/projects/[project]/flags/` are thin server components that wrap a client component in `<Suspense>`, as in MF-81. List filters and the cursor live in the URL, and so do `?env=&tab=` on the detail page.

## Conventions (every step)
- Branch `megoo/MF-82-web-ui-pages-flags-rules-editor-test-thi`. Test first, watch it fail, implement, watch it pass, then commit. Commit messages use `<type>(api|web): <summary> (MF-82)` and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.megoocolab/`, `web/e2e/.auth/`, `test-results/` or `playwright-report/`.
- **API** (from `api/`): Postgres and Valkey as in AGENTS.md §9, with `MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags` and `MEGOOFLAGS_VALKEY_URL=redis://localhost:6379/0`. Checks: `uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest -q <files>`. After a route or schema change, regenerate: `uv run python -m megooflags_api.export_openapi openapi.json && (cd ../web && pnpm gen:api)`, and commit both files.
- **Web** (from `web/`): `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test`. Unit tests go in `web/tests/unit/*.test.ts`.
- **E2E** (from `web/`): start the API as in AGENTS.md §9, then `pnpm build && pnpm e2e e2e/<spec>.ts`. Refresh baselines only with `pnpm e2e:docker --update-snapshots e2e/matrix.spec.ts`.
- **E2E data:**
  - Flag specs use only the `shop` project, which global setup recreates on every run. Tests that change a flag first create their own with `createTestFlag`, so retries are safe.
  - `demo` is never changed, because the visual matrix depends on it.
  - With default filters the list adds no query string, because shell.spec and palette.spec assert `/flags$`.
- Times are always rendered in `<time dateTime>`, so the matrix can mask them.
- The accessible names in Appendix D are the contract between components and specs.
- Colours come only from tokens (`no-hex.test.ts`).
- Targets are at least 44 px (`min-h-11 min-w-11`, or a `data-hit-area` wrapper), and inputs use `text-base`.
- Nothing depends on hover. Rules reorder with up and down buttons.
- **Known limitation:** a flag keyed `new` can't be opened at `/flags/new`, because the static route wins. This follows from the approved routes.

## File map
- **API:**
  - `routers/flags.py` and `services/flags.py` (list filter).
  - `routers/audit.py` and `services/audit.py` (`resource_key`).
  - new `services/evaluation_counts.py`; `schemas/evaluation.py` (`EvaluationPoint`, `EvaluationSeries`).
  - `routers/flag_environments.py` (the evaluations route).
  - Tests: `test_flags.py`, `test_audit_query.py`, `test_rbac_routes.py`, `test_openapi.py`, and the new `test_evaluation_counts.py`.
  - Regenerated `api/openapi.json` and `web/lib/api/schema.d.ts`, plus `CHANGELOG.md`.
- **Web helpers** in `web/lib/flags/`, each with `tests/unit/flags-<name>.test.ts`:
  - `filters.ts`, `errors.ts`, `permissions.ts`, `flag-form.ts`;
  - `weights.ts`, `operators.ts`, `config-draft.ts`;
  - `explain.ts`, `history.ts`, `bulk.ts`;
  - plus `newFlagPath` in `web/lib/routes.ts`.
- **Web UI:**
  - `components/ui/native-select.tsx` and `checkbox.tsx` (Appendix E); `hooks/use-api-data.ts`.
  - `components/flags/`: `flag-list`, `flag-filters`, `bulk-bar`, `bulk-results`, `flag-form`, `flag-detail` (orchestrator), `env-strip`, `toggle-request-dialog`, `rules-editor`, `serve-editor`, `clause-row`, `unsaved-bar`, `review-dialog`, `use-unsaved-guard`, `copy-dialog`, `test-panel`, `history-list`, `evaluation-chart`, `insights-panel`, `flag-settings`, `delete-flag-dialog`.
  - Routes `flags/page.tsx` and `flags/[flag]/page.tsx` (rewrite), and `flags/new/page.tsx` (new).
- **E2E:**
  - `e2e/support/api.ts` (new); `e2e/global-setup.ts` (seeds `shop`).
  - New specs `flags-list`, `flag-create`, `flag-detail`, `flags-mobile`.
  - `matrix.spec.ts` and its snapshots.

## Steps
1. API: filter the flag list by state in an environment
2. API: filter the audit log by resource key
3. API: evaluation counts series for a flag in an environment
4. Web: flag helpers part 1 (routes, filters, errors, permissions, flag form)
5. Web: flag helpers part 2 (weights, operators, config draft)
6. Web: flag helpers part 3 (explain, history, bulk)
7. E2E: seed the shop project and add the API support helpers
8. Web: flag list page with search, filters and pagination
9. Web: bulk actions on the flag list
10. Web: New flag page (one form on desktop, stepper on phones)
11. Web: flag detail shell, environment strip, toggle and kill switch
12. Web: rules editor part 1 (draft, targets, prerequisites, default rule, review and save)
13. Web: rules editor part 2 (rules, clauses, segments, reordering)
14. Web: copy configuration to other environments
15. Web: Test this flag panel
16. Web: History tab
17. Web: Insights tab with the evaluation chart
18. Web: Settings tab (details, variations, lifecycle, archive, restore, delete)
19. Web: Viewer read-only checks and the 344 px mobile tasks
20. Web: responsive and visual matrix, CHANGELOG, final checks

Each step's full detail (files, failing tests, commands, implementation, commit) is in the structured steps.

## Final verification
- `api/`: ruff, format, mypy and the full `uv run pytest`; `bash scripts/check_migrations.sh` (no new migration).
- Drift: export `openapi.json`, run `pnpm --dir web gen:api`, then `git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts`.
- `web/`: `pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build && pnpm e2e && pnpm e2e:docker`.
- `docker build -t megooflags-web:ci web && bash scripts/smoke.sh http`.

---
## Appendix A: API contracts
**A1. `GET /api/projects/{project}/flags`** gains:
- `env: str | None` ("An environment key; with `enabled`, only flags in that state there").
- `enabled: bool | None` ("Needs `env`").

Behaviour:
- `enabled` without `env` raises `validation_error("enabled needs env", "enabled")`.
- With `env`: `get_environment` returns 404 when it is unknown, and `not authz.authorizer.can_see_environment(project.id, env.id)` raises `not_found("environment", env)`.
- `env` alone only validates. With `enabled`, pass `env_state=(env.id, enabled)`.
- `services.flags.list_flags(…, env_state: tuple[uuid.UUID, bool] | None = None)` adds `exists().where(FlagEnvironment.flag_id == Flag.id, FlagEnvironment.environment_id == env_id, FlagEnvironment.enabled.is_(on))`. It is still one page statement.

**A2. Audit:** add `AuditFilters.resource_key: str | None = None`. `get_filters` gains `resource_key` ("An exact resource key, such as a flag key."), and `_select` adds `AuditEntry.resource_key == …`. Export inherits it.

**A3. Evaluations**
```python
class EvaluationPoint(ApiModel):
    start: datetime            # UTC bucket start
    counts: dict[str, int]     # every name in variants, 0 when none
class EvaluationSeries(ApiModel):
    """Variant "" counts evaluations with no variant."""
    interval: Literal["hour", "day"]
    variants: list[str]        # sorted
    points: list[EvaluationPoint]  # oldest first, empty buckets included
    total: int
# services/evaluation_counts.py
EvaluationRange = Literal["24h", "7d", "30d"]
def series_window(range_, now) -> tuple[Literal["hour","day"], list[datetime]]  # 24h: 24 hour starts ending at now's hour; 7d/30d: UTC midnights ending today
def fill_series(interval, starts, rows: Iterable[tuple[datetime, str, int]]) -> EvaluationSeries  # rows (hour, variant, count); rows before starts[0] ignored
async def evaluation_series(session, environment_id, flag_key, range_, now) -> EvaluationSeries  # ONE query: hour, variant, sum(count) WHERE env, flag_key, hour >= starts[0] GROUP BY hour, variant
```
The route goes in `routers/flag_environments.py`: `GET /projects/{project}/flags/{flag}/environments/{env}/evaluations`.
- Permission `ReadProject`, with `range: EvaluationRange = "24h"`.
- `get_flag` returns 404 for an unknown flag.
- It calls `evaluation_series(session, _environment(authz).id, flag_row.key, range, datetime.now(UTC))`.
- A hidden env is a 404 through `require`; a bad range is a 422.
- Reuse `truncate_hour` from `services/events.py`.

## Appendix B: web helper contracts
`S = components["schemas"]`
```ts
// routes.ts
newFlagPath(project): string // projectPath(project,"flags") + "/new"
// filters.ts
type Lifecycle = S["FlagSummary"]["lifecycle"];
type FlagFilters = { q: string; tags: string[]; owner: string; lifecycle: Lifecycle | ""; env: string; enabled: "" | "on" | "off"; cursor: string };
EMPTY_FILTERS; PAGE_SIZE = 50;
filtersFromParams(p: URLSearchParams): FlagFilters // repeated `tag`; invalid lifecycle/enabled -> ""
filtersToParams(f): URLSearchParams // non-defaults only: q, tag*, owner, lifecycle, env, enabled, cursor
withFilter(f, patch): FlagFilters // resets cursor unless patch sets it
filtersToQuery(f) // list query: empties omitted; env+enabled only if both (on->true); limit PAGE_SIZE
activeFilterCount(f): number // tags, owner, lifecycle, env-status; not q
// errors.ts
type ApiFailure = { status: number; code: string; message: string; path: string | null };
toFailure(status, body): ApiFailure // ErrorBody; details.path; request-validation loc (drop "body"/"query") -> "rules[0].clauses[1].values[0]"; else code "unknown", "HTTP <status>"
failureText(f): string // approval_required "Needs approval"; 403 "You don't have permission"; version_conflict "Someone saved a newer version"; 404 "Not found"; else message
class ApiError extends Error { failure: ApiFailure }
must<T>(call: Promise<{data?: T; error?: unknown; response: Response}>): Promise<T> // throws ApiError
// permissions.ts
can(perms: MePermissions | null, p: S["Permission"], env?: string): boolean // env -> perms.environments[env]; else perms.project; null -> false
// flag-form.ts
type VariationRow = { key: string; name: string; value: string };
type FlagForm = { key; keyEdited: boolean; name; description; type: S["FlagType"]; tags: string[]; owner; variations: VariationRow[]; offVariation };
KEY_PATTERN = /^[a-z0-9][a-z0-9._-]{0,99}$/;
slugifyKey(name) // lowercase; runs of other chars -> "-"; trim leading "-._"; max 100
parseTags(text) // comma split, trim, no empties, dedupe
parseVariationValue(type, text) -> {ok:true,value}|{ok:false,error} // number "Enter a number"; json "Enter valid JSON"; string as is
formatVariationValue(type, value) // string raw, else JSON
formErrors(f): Record<string,string> // key, name, variations[i].key|value, offVariation
toFlagCreate(f): S["FlagCreate"] // boolean: no variations/off_variation; owner "" -> null
// weights.ts
WEIGHT_TOTAL = 100_000; toPercent(w) = w/1000; fromPercent(p) = clamp(round(p*1000));
weightsSum(ws); weightsValid(ws) // each 0..total, sum === total
booleanWeights(pctTrue) // [{true,w},{false,total-w}]
percentOf(ws, v); evenWeights(keys) // remainder to first
// operators.ts
type OperatorKind = "list"|"text"|"regex"|"number"|"semver"|"date"|"segment";
OPERATORS: {value, label, kind}[] // in "is one of", not_in "is not one of" (list); contains, starts_with, ends_with (text); matches "matches regex"; lt "<" lte "≤" gt ">" gte "≥" (number); semver_eq/lt/gt "version =,<,>"; before/after "before date"/"after date"; in_segment "is in segment"
operatorKind(op); formatValues(values) // one per line
parseValues(op, text) -> {ok,values}|{ok:false,error} // trims, drops empties; none -> "Add at least one value"; SEMVER/RFC3339 regexes copied from api/.../evaluation/operators.py; regex must compile
// config-draft.ts
type ServeDraft = {mode:"variation"; variation} | {mode:"rollout"; bucketBy; weights: S["WeightIn"][]};
type ClauseDraft = {id; attribute; operator; values: Scalar[]; negate};
type RuleDraft = {id; description; clauses: ClauseDraft[]; serve: ServeDraft};
type PrereqDraft = {id; flag; variation};
type ConfigDraft = {version; enabled; targets: Record<string,string[]>; rules: RuleDraft[]; fallthrough: ServeDraft; prerequisites: PrereqDraft[]};
toDraft(c: S["FlagConfigOut"], variationKeys); toConfigIn(d): S["FlagConfigIn"] // empty target lists dropped, ids stripped
configText(d) // JSON (2 spaces) of toConfigIn without version/enabled
isDirty(base, d); withState(d, {enabled, version})
addRule(d, firstVariation) // one clause {attribute:"key", operator:"in", values:[]}
updateRule, removeRule, moveRule(d, id, -1|1) /* no-op at ends */, addClause, updateClause, removeClause /* keeps a rule's last clause */
setTargets(d, variation, keys) // trim, dedupe, remove keys from other variations
addPrerequisite, updatePrerequisite, removePrerequisite, setFallthrough
validateDraft(d): {path, message}[] // "rules[i].clauses[j].values" empty; "...rollout.weights" "Weights must add up to 100 %"; prerequisite incomplete
// all edits are immutable; ids from a module counter
// explain.ts
explain(r: S["EvaluationResult"]) // ERROR "Error: <message|code>"; DISABLED "Flag is off"; PREREQUISITE_FAILED "Prerequisite <metadata.prerequisite> failed"; TARGETING_MATCH + rule_index n "Rule n+1"; TARGETING_MATCH "Individual target"; DEFAULT/SPLIT/STATIC "Default rule"
// history.ts
actionLabel({action, after}) // flag.create Created; flag.update Details edited; flag.config.update Targeting changed; flag.toggle Turned on/off (after.enabled); flag.kill Kill switch used; flag.copy Config copied here; flag.archive Archived; flag.restore Restored; flag.lifecycle.update Lifecycle mark changed; flag.schedule.<x> "Scheduled change: <x>"; flag.rollout.<x> "Progressive rollout: <x>"; else action
entryText(value | null) // "" or JSON with keys sorted recursively
// bulk.ts
type BulkResult = {key; ok:true} | {key; ok:false; reason};
runBulk(keys, action, limit = 4, onDone?) // worker pool; input order; ApiError -> failureText; other -> String(e)
tagsAfter(tags, tag, "add"|"remove"): string[] | null // null = no change
// hooks/use-api-data.ts
useApiData<T>(key: string | null, load: () => Promise<T>): {data; failure: ApiFailure|null; loading; reload}
// reruns when key changes; load kept in a ref; stale responses ignored; non-ApiError -> {status:0, code:"network", message:"Couldn't reach the server"}
```

## Appendix C: E2E seed
- `e2e/support/api.ts` exports `APP`, `API` and `ok` (moved from global-setup), plus `SHOP = "shop"` and:
  - `adminApi()`: baseURL APP, ADMIN storageState and the Origin header.
  - `createTestFlag(api, {prefix?, type?: "boolean"|"string", tags?})`: a unique key `t-<base36 time>-<rand>` in shop. String flags get variations a/b with off a.
  - `putConfig(api, flag, env, mutate)`: GET, then PUT with that version.
- `seedShop(admin)` in global setup:
  1. DELETE `/api/projects/shop` (ignore 404), then create it with development, staging and production, and PATCH production to `{approvals_required:1}`.
  2. Segment `beta-testers` with included `["user-beta"]` (see `schemas/segments.py`).
  3. Flags:
     - `checkout-v2` (boolean, tags web/checkout, owner team-web);
     - `search-ranking` (string classic/ml, off classic, tag search, owner team-search);
     - `banner-text` (string a/b);
     - `legacy-header` (boolean, archived);
     - `load-01`…`load-52` (tag load), created 4 at a time.
  4. Configure `checkout-v2`:
     - development: enabled, target true:[user-1], rule country in [NL] serving true, fallthrough false;
     - staging: toggled on.
  5. A server SDK key in development. Then 20 calls to `${API}/ofrep/v1/evaluate/flags/checkout-v2` with `Authorization: Bearer <key>` and context `{targetingKey:"user-<i>", country: i%2 ? "NL" : "DE"}`. The counts flush within 10 s, so specs poll.

## Appendix D: accessible names
- **List:**
  - "Search flags"; "Filters (n)" button; fields "Tags", "Owner", "Lifecycle", "Environment", "State in environment"; "Clear filters".
  - "New flag", "Next page", "First page".
  - "Select <key>", "Select all on this page".
  - Region "Bulk actions" with "<n> selected", "Turn on", "Turn off", "Add tag", "Remove tag", "Archive", "Restore", "Clear selection". Dialog fields "Environment" / "Tag" and the button "Apply".
  - Dialog "Bulk results", with items "<key>: Done" or "<key>: Failed, <reason>".
- **New flag:** "Name", "Key", "Description", "Type", "Tags", "Owner"; "Variation <n> key|name|value"; "Add variation", "Remove variation <n>"; "Off variation"; "Create flag". The phone stepper shows "Step i of 3", "Next" and "Back".
- **Detail strip:**
  - Region "Environments", with one group per environment named after it.
  - Toggle "<flag> in <Env>"; "Kill switch in <Env>"; "Edit <Env>" (aria-pressed); badge "Needs approval".
  - Kill dialog "Kill <flag> in <Env>?" with the confirm button "Kill switch".
  - Toggle request dialog "Request change in <Env>" with "Title", "Description" and "Request change".
  - Tabs "Targeting", "Test", "History", "Insights", "Settings".
- **Editor:**
  - Headings "Prerequisites", "Individual targets", "Rules", "Default rule".
  - Prerequisites: "Add prerequisite", "Prerequisite <n> flag|variation", "Remove prerequisite <n>".
  - Targets: "Context keys served <variation>".
  - Rules: "Add rule"; group "Rule <n>" (id rule-<n>) with "Description", "Move rule <n> up|down", "Remove rule <n>", "Add clause".
  - Clauses: "Attribute", "Operator", "Values" or "Segment <key>", "Not", "Remove clause".
  - Serve: radios "Serve a variation" / "Serve a rollout"; "Variation"; "Bucket by"; slider "Percentage serving true"; inputs "Weight of <v> (%)".
  - Unsaved bar: status "Unsaved changes" with "Discard" and "Review" or "Review & request change".
  - Dialog "Review changes" with "Save" or "Request change". On conflict: the alert "Someone saved a newer version" and "Save over latest".
  - "Copy to…" opens dialog "Copy configuration" with "From", "To <Env>", "Include on/off state" and "Copy".
- **Test:** "Context (JSON)", "Evaluate"; region "Result" with Variation, Value and Why; the note "Results use the saved config".
- **History:** list "Change history"; "Show diff"; checkbox "This environment only"; "Load more".
- **Insights:**
  - Select "Range" with Last 24 hours, Last 7 days and Last 30 days.
  - Figure "Evaluations by variation"; button "Show as table" / "Show as chart"; table caption "Evaluations".
  - Empty text "No evaluations in this range".
- **Settings:**
  - Fields "Name", "Description", "Tags", "Owner" and "Save details"; "Off variation"; toggle "Permanent flag".
  - "Archive flag" (confirm "Archive"), "Restore flag" (confirm "Restore"), "Delete flag", whose dialog has "Type <key> to confirm" and "Delete".

## Appendix E: primitives
```tsx
// components/ui/native-select.tsx
import { cn } from "@/lib/utils";
export function NativeSelect({ className, ...props }: React.ComponentProps<"select">) {
  return <select data-slot="native-select" className={cn("h-11 w-full min-w-0 rounded-md border border-input bg-transparent px-3 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30", className)} {...props} />;
}
// components/ui/checkbox.tsx (render inside <label data-hit-area className="inline-flex min-h-11 min-w-11 items-center gap-2">)
import { cn } from "@/lib/utils";
export function Checkbox({ className, ...props }: Omit<React.ComponentProps<"input">, "type">) {
  return <input type="checkbox" data-slot="checkbox" className={cn("size-5 shrink-0 accent-primary", className)} {...props} />;
}
```
