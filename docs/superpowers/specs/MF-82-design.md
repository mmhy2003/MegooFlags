# MF-82 (part 1): Flags list and flag detail pages

## Goal
Replace the Flags placeholder pages from MF-81 with working ones. People can find, create, edit, target, test, toggle and kill flags from the web UI, at every width down to 280 px. The PRD 6.7 mobile tasks for flags (toggle, kill switch, rollout, search) work fully at 344 px. The web app holds no business logic and calls the API only through the generated client.

## Decisions
- **Scope split (human):** this task is the flags part only. Segments, Environments, Change requests, Scheduled and progressive rollouts, Audit log, API & SDK keys and Settings become follow-up web tasks. Integrations (webhooks, Slack, Teams) becomes its own API and UI task, because no API for it exists yet. "Approve or reject at 344 px" belongs to the Change requests follow-up.
- **Create and edit flags are in scope (human).** This covers a New flag form, editing details and variations, archive, restore and delete (F-1 to F-3).
- **Bulk actions (human):** turn on or off in a chosen environment, add or remove a tag, and archive or restore. The UI sends one existing API call per flag, at most 4 at a time, and shows a result for each flag (done, or failed with the reason, such as "needs approval" or a 403). No bulk endpoint. *Rejected:* a new bulk API endpoint (more API work), and archive only (too limited).
- **Editor saving (human): draft, review the diff, then save.** Edits stay in a local draft, and a sticky "unsaved changes" bar offers Discard and Review. The Review dialog shows a before/after `DiffViewer`, then sends one `PUT …/environments/{env}` with the loaded `version`. In an environment with `approvals_required > 0`, the button becomes **Review & request change**: it asks for a title and description and creates a `config` change request with the same payload. *Rejected:* saving each section on its own, which creates many audit entries and many change requests.
- **Layout (human): layout A, an environment strip plus page tabs.** A card for each visible environment shows its state (icon and label), a toggle and a kill switch; selecting a card picks the environment being edited. Below the strip are the tabs **Targeting · Test · History · Insights · Settings**. The selected environment and tab live in the URL (`?env=…&tab=…`). *Rejected:* one long stacked page per environment.
- **Test this flag (human): saved config only.** The panel uses the existing `POST …/environments/{env}/evaluate`. While a draft is unsaved, it notes that the result reflects the saved config. *Rejected:* a new endpoint that evaluates a draft.
- **Toggle in an approval environment** opens a short dialog that creates a `toggle` change request. The **kill switch** always goes straight through (`POST …/kill` skips approval), behind a confirmation dialog that names the flag and environment and says that any running progressive rollout is paused.
- **Evaluation chart:** a small stacked-bar SVG component that uses the `--chart-1…5` tokens, with a "Show as table" toggle for accessibility. No chart library.

## Architecture (web)
- **Routes:** `app/(app)/projects/[project]/flags/page.tsx` (list), `flags/new/page.tsx` (create; step by step below 768 px, one form above), and `flags/[flag]/page.tsx` (detail). They are client components fed by the browser `api` client. Filters and the cursor live in URL search params, so a refresh or a fold change keeps them (UI-13).
- **Pure helpers in `web/lib/flags/`,** each unit-tested with Vitest:
  - `filters.ts`: URL params ↔ API query.
  - `config-draft.ts`: `FlagConfigOut` → editable draft → `FlagConfigIn`, with move, add and remove for rules, clauses, targets and prerequisites, and a dirty check.
  - `weights.ts`: thousandths ↔ percent with a sum of 100 000.
  - `operators.ts`: labels and value parsing for each `Operator`, including `in_segment`, semver and dates.
  - `explain.ts`: an `EvaluationResult` → plain words ("Rule 2", "Individual target", "Prerequisite payments-v2 failed", "Default rule", "Flag is off", "Error: …").
  - `bulk.ts`: the per-flag runner with a concurrency limit and collected results.
  - `history.ts`: audit actions → labels.
- **Components in `web/components/flags/`:**
  - `flag-list`: `DataList` table, stacked cards below 768 px, row selection and a bulk bar.
  - `flag-filters`: search debounced at 200 ms; tag, owner, lifecycle state, and environment status (on/off in an environment). On phones the filters sit in a sheet.
  - `env-strip`: `Toggle`, a kill button, `StatusPill`, and a lock badge for approval environments.
  - `rules-editor`, with sections Prerequisites, Individual targets, Rules (clause rows with attribute, operator, values and negate; segment picker; serve a variation or a rollout) and Default rule (`RolloutSlider` for boolean flags, a weight input for each variation on multivariate flags).
  - `review-dialog`, `copy-dialog`, `test-panel` (JSON context editor that remembers the last context in sessionStorage, and the result with `JsonViewer`), `history-list` (audit entries with `DiffViewer`), `evaluation-chart`, `flag-settings` (details, variations, off variation, permanent mark, archive, restore and delete), and `bulk-results`.
- **Permissions:** `use-project-permissions` hides or disables actions: `flag.create`, `flag.config.write`, `flag.toggle`, `flag.delete` and so on. A Viewer sees everything read-only.
- **Reuses the MF-81 kit:** ResponsiveDialog, ConfirmDialog, DataList, Toggle, RolloutSlider, StatusPill, DiffViewer, JsonViewer, EmptyState and PageHeader. The Flags `PlaceholderPage` usages are removed. `routes.ts` gains `newFlagPath`.
- **Touch and accessibility:** targets are at least 44 px, inputs use 16 px text, and nothing depends on hover. Rules are reordered with up and down buttons, not drag and drop. Code and JSON scroll inside their own boxes.

## Data and interfaces (API additions)
All three are small, read-only and backward compatible. Each one regenerates `api/openapi.json` and `web/lib/api/schema.d.ts` and adds an `[Unreleased]` CHANGELOG entry.
1. **`GET /api/projects/{project}/flags` gains `env` and `enabled`.** With both set, it lists only flags whose state in `env` matches `enabled`. `enabled` without `env` is a 422. An unknown or hidden environment is a 404. The query count stays fixed.
2. **`GET /api/audit` (and `/export`) gains `resource_key`.** Flag history calls `?project=&resource_type=flag&resource_key=<flag>`, optionally with `env`.
3. **New `GET /api/projects/{project}/flags/{flag}/environments/{env}/evaluations?range=24h|7d|30d`** (project.read; the environment must be visible). It reads `evaluation_count` and returns `{interval: "hour"|"day", variants: [str], points: [{start, counts: {variant: int}}], total}`, with empty buckets filled in. Hourly for 24h, daily otherwise.

## Error handling and edge cases
- **409 version conflict on save:** the dialog shows "Someone saved a newer version", reloads the latest config, and shows it next to the user's draft so they can re-apply and save. The draft is never lost silently.
- **409 `approval_required`:** shown as a prompt to create a change request.
- **422:** validation errors are mapped onto the field (unknown segment, prerequisite cycle, weights not adding to 100 %). Weights are also checked client-side before Review.
- **Copy to another environment:** source and targets are chosen in a dialog, with an "Include on/off state" option. Approval environments are disabled with an explanation, because the API refuses copies into them.
- **Bulk actions** report partial failure for each flag and never stop on the first error. The list refreshes afterwards.
- **Delete** requires typing the flag key. Archive and restore use a confirmation.
- **Leaving with an unsaved draft** asks for confirmation. Resizing or folding the device keeps the draft and any open dialog.
- **Empty states:** no flags, no rules, no history, and no evaluations ("No evaluations in this range").
- **A 404 flag** shows the not-found page.

## Testing
- **API (pytest, TDD):** the env/enabled filter (match, 422, hidden environment 404, query count); the audit `resource_key` filter, including export; and the evaluations endpoint (bucketing, empty fill, range validation, permissions, hidden environment). The OpenAPI drift test is regenerated.
- **Web unit (Vitest):** every helper in `lib/flags/`.
- **Playwright e2e:** global-setup seeds a project with dev, staging and a production that needs 1 approval, plus a segment, several flags and an SDK key. Evaluations are driven through OFREP so the chart has data. New specs:
  - `flags-list.spec.ts`: search, each filter, pagination, and bulk on/off, tag and archive with a partial failure.
  - `flag-create.spec.ts`: boolean and multivariate, and the stepper at 344 px.
  - `flag-detail.spec.ts`: rules editor round trip, review diff, version conflict, change request created in production, toggle, kill switch with confirmation, copy, the Test panel explaining a rule, target, default and off result, history diffs, chart and table, settings, archive, restore and delete, and a Viewer seeing it read-only.
  - `flags-mobile.spec.ts`: toggle, kill switch, rollout and search at 344 px.
- **Matrix:** the flag list, flag detail (each tab) and new flag pages join the existing axe and visual matrix at 280, 344, 390, 880, 1024 and 1440 px in both themes, with no horizontal page scroll.
- **Final checks:** lint, format, typecheck, test, build, e2e and e2e:docker, plus the API checks (ruff, mypy, pytest, migrations unchanged).

## Out of scope
- Segments, Environments, Change requests (approve and reject), Scheduled and progressive rollouts UI (including on the flag page), Audit log page, API & SDK keys, Integrations, and Settings (members, teams, roles, SSO). These are follow-up tasks.
- Evaluating an unsaved draft, a bulk API endpoint, drag-and-drop rule reordering, and the Nodes page.