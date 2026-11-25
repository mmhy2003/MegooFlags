# MF-458: Web project pages, and automation on the flag page

## Goal
People can manage segments, environments (including protected environments) and project settings from the web UI, create and delete projects, and schedule changes and run progressive rollouts from the flag page, at every width from 280 px and with touch at 344 px. No project page renders `PlaceholderPage` any more. This closes GA blocker 5 and the web part of the checklist rows T-4, UI-14, F-10 and T-6.

## Decisions
Answers from the human:
- **Scope: everything in one design and one pull request**, as MF-457 did. *Rejected:* two phases (project pages first, or flag page first).
- **Schedules and rollouts get a new flag tab** next to Targeting, for the selected environment, with two sections. Targeting shows a notice while a rollout runs. *Rejected:* more sections at the bottom of Targeting (they act at once, while Targeting is a draft with a Review bar), and two separate tabs.
- **Project settings are a new "Project settings" item** at the end of the project menu, shown with `project.manage`. *Rejected:* a card on the Environments page, a dialog from the project switcher.
- **A segment opens on its own page** (`segments/<key>`) with the unsaved-changes bar; creating one is a small dialog. *Rejected:* editing in a dialog over the list.
- **Two small API additions**: read-only segment usage, and one call that reorders all environments (see "Data and interfaces"). *Rejected:* no API change (the web app would read every flag's config to find usage, and reorder with several PATCH calls that can stop half-way).
- **Creating and deleting projects is included**: "New project" in the project switcher, "Delete project" in the project settings. *Rejected:* a follow-up task.
- **Layout: mockup v1**, approved as shown.

Decisions made in the design (all shown in the mockup and accepted with it):
- The tab is called **Automation** (`?tab=automation`), placed after Targeting. It carries a count for the selected environment: pending schedules plus one for a live rollout.
- **Environments are reordered with up and down buttons** (44 px, as rules are), not drag and drop. Each press saves at once.
- **Approvals are set in an environment's Edit dialog**, not when creating it, so `EnvironmentCreate` stays as it is.
- **Saving a segment that flags use asks for confirmation**, because the change applies at once in every environment, protected ones too. An unused segment saves directly.
- **Typed-key confirmation** for deleting an environment or a project. The UI refuses to delete a project's last environment (the flag page needs one).
- **Rollout steps:** a percentage, then a wait typed as a number plus minutes, hours or days. The last step is always 100 %. The dialog opens with 10 %, 25 %, 50 % (1 hour each), then 100 %.
- **In an environment that needs approval**, "Start a rollout" and "Schedule a change" become "Request…", add a title and description, and open a `rollout` or `schedule` change request. Pause, resume, abort and cancel act at once, as the API allows.
- The Delete project dialog names what is deleted without the counts the mockup shows for illustration (the API has no counts).

## Architecture (web)
All pages are client components on the generated `api` client with `useApiData`, like Keys and Integrations. Search text and the selected tab and environment stay in URL search params.

**Routes**
- `projects/[project]/segments/page.tsx`: the list (replaces the placeholder).
- `projects/[project]/segments/[segment]/page.tsx` (new): one segment. An unknown key shows the not-found page.
- `projects/[project]/environments/page.tsx`: the list (replaces the placeholder).
- `projects/[project]/settings/page.tsx` (new): project settings.
- `lib/routes.ts`: `PROJECT_SECTIONS` gains `settings`; new `segmentPath`. `lib/nav.ts`: "Project settings" shows with `project.manage`.
- `components/patterns/placeholder-page.tsx` is deleted: nothing uses it.

**Segments** (`components/segments/`, `lib/segments/`)
- `segment-list`: a search field (`q`), then a `DataList` (table from 768 px, cards below, UI-14) with name and key (a link), description, "Used by" (`2 flags` or "Not used") and updated time. "Load more" follows the cursor. "New segment" (name, key suggested from the name, description) shows with `segment.write` and opens the new segment's page.
- `segment-detail`: header with name, key and description; "Edit details" (name and description, `PATCH`) and "Delete". Below it:
  - **Included keys** and **Excluded keys**: one key per line, applied on blur, with a count. They reuse the key-list field of the rules editor, extracted from `rules-editor.tsx` into its own component.
  - **Rules**: cards with a description and clauses, with move up, move down and remove, and "Add rule" / "Add clause". They reuse `ClauseRow`, which gains a prop to leave out "is in segment" (the API refuses it in a segment).
  - **Used by**: each flag (a link to its page) with its environments, and a line saying that a saved change applies to them at once.
  - The existing `UnsavedBar` pattern with Discard and Save, and `useUnsavedGuard`. Save sends `PUT` with the whole config. When the segment is in use, a confirmation comes first.
- `delete-segment-dialog`: when the segment is in use it lists the flags and environments and offers only Close; otherwise it confirms and deletes, then returns to the list.
- `lib/segments/segment-draft.ts`: the draft (included, excluded, rules of `ClauseDraft`), `toDraft`, `toConfigIn`, `isDirty`, rule and clause helpers, and `validateDraft` (a key in both lists, a key over 256 characters, a clause without values).
- Without `segment.write` the page is read-only.

**Environments** (`components/environments/`)
- `environment-list`: a `DataList` in `sort_order` with move up and down, name and key, description, a pill ("Needs 2 approvals" with a lock, or "Direct changes"), Edit and Delete.
- `environment-dialog`: create (name, key suggested from the name, description; it says every flag gets the environment turned off, and that it is added last) and edit (name, description, a "Changes need approval" checkbox and "Approvals needed", 1 to 10; unticked sends 0).
- `delete-environment-dialog`: says what goes with it (every flag's state, targeting, schedules and rollouts there, its SDK keys, change requests and role assignments), and needs the key typed. Disabled for the last environment.
- Write controls need `environment.manage`; without it the list is read-only.

**Projects** (`components/projects/`)
- `project-settings`: three cards. *Details*: name, key (read-only), description. *Stale flags*: "Stale after (days)", 1 to 365. *Danger zone*: Delete project (typed key), then go to `/`. After a save, `router.refresh()` updates the switcher. Without `project.manage` the fields are read-only and there is no danger zone.
- `new-project-dialog`: name, key suggested from the name, description; it says the project starts with Development, Staging and Production. Opened from a "New project" item at the end of the project switcher, and from a button on the "No projects yet" page. Both show with the organization permission `project.create`. After creating, it opens the new project.

**Flag page: Automation** (`components/flags/`, `lib/flags/`)
- `flag-detail.tsx`: `TABS` gains `automation`. It loads the selected environment's rollout (`GET …/rollout`) and schedules (`GET …/schedules`) once, for the tab, its count and the Targeting notice. With `useVisibleInterval` it reloads them every 30 seconds while a rollout is running or a schedule is pending, and after every action. When a reload shows that a step or a schedule was applied, it reloads the flag (the environment strip) and, if the Targeting draft is clean, the config; a dirty draft keeps the existing version-conflict flow.
- `rollout-card`: 
  - No live rollout: a short explanation and "Start a rollout".
  - Running or paused: status pill, from → to, who started it, the current percentage with a bar, the steps (done, current, next with its time or the wait left when paused), and Pause or Resume, and Abort (confirmation: the default rule serves "from" to everyone again; Pause keeps the current split).
  - "Recent rollouts": the ten the API returns, with status and error.
- `rollout-dialog`: From and To variations (From defaults to what the default rule serves now), the steps, a summary (when the first step applies and how long until 100 %), and a note that only the default rule changes.
- `schedule-list`: upcoming changes soonest first (what, when in local time with a relative time, who, a link when it came from a change request, Cancel with confirmation), and "Past changes" folded (Applied, Failed with the error, Cancelled).
- `schedule-dialog`: the change (turn on, turn off, set the default rule to a rollout), the weights for a rollout (`RolloutSlider` for a boolean flag, one weight per variation otherwise, from `ServeEditor`'s parts), and When: a `datetime-local` input (16 px, native picker) with the browser's time zone and the UTC time below it.
- `request-fields`: the title and description fields of `ToggleRequestDialog`, moved out so the rollout and schedule dialogs use them too.
- Targeting shows a notice while a rollout is live: it serves N % now, and saving another default rule makes the rollout fail at its next step, with a link to Automation.
- `lib/flags/rollout-plan.ts`: steps to and from `ProgressiveRolloutIn` (percent to thousandths, wait units to minutes), checks (percentages rise, at most 20 steps, wait 1 minute to 30 days, From differs from To), total duration, current percentage. `lib/flags/schedules.ts`: the text of a change, local time to ISO, checks (in the future, at most 365 days), upcoming and past, status pills.
- Permissions per environment: rollout actions need `flag.config.write`; scheduling on or off needs `flag.toggle`, a rollout `flag.config.write`; Cancel needs the permission of that schedule's action. An archived flag is read-only.

## Data and interfaces (API additions)
Backward compatible, no migration. Each regenerates `api/openapi.json` and `web/lib/api/schema.d.ts` in the same commit and adds a CHANGELOG entry.
1. **`GET /projects/{project}/segments` items gain `flag_count`**: how many flags have a rule that uses the segment, in the environments the caller can see. A new `SegmentListItem(SegmentSummary)` carries it, so `SegmentOut` and the audit snapshots keep their shape. One grouped query per page.
2. **New `GET /projects/{project}/segments/{segment}/usage`** (`project.read`): `{used_by: [{flag, environment}]}`, ordered by flag then environment, limited to environments the caller can see. It reuses `usage.segment_dependents`, which gains an optional visibility filter.
3. **New `PUT /projects/{project}/environment-order`** (`environment.manage`): body `{environments: [key, …]}` with every environment of the project exactly once; anything else is 422 on `environments`. It takes the project row lock that environment creation takes, sets `sort_order` to each key's position, writes one `environment.reorder` audit entry (before and after order) and returns the environments in the new order. `lib/audit/actions.ts` gains its label. The path is outside `/environments/{env}` so that it cannot be confused with an environment named `order`.

Existing endpoints used as they are: segments (create, read, `PUT`, `PATCH`, delete), environments, `PATCH` and `DELETE /projects/{project}`, `POST /projects`, schedules, rollout (start, read, pause, resume, abort), and `POST …/change-requests` with kind `schedule` or `rollout`.

## Error handling and edge cases
- **Segment in use on delete** (409 `in_use`), also when the usage was hidden from the caller or changed meanwhile: the dialog shows the list from `details.used_by`.
- **Segment validation** (422): a key in both lists and bad clause values show on their field, through the same path-to-field mapping as the rules editor.
- **Segments have no version**: two people saving the same segment means the last save wins. Stated here, not solved (see Out of scope).
- **Duplicate key** (409 `key_conflict`) for a segment, an environment or a project shows on the Key field.
- **Reorder refused** (422, an environment was added or deleted meanwhile): the list reloads with a message.
- **Turning approval off** leaves open change requests open; the Edit dialog says they still need one approval.
- **A rollout is already running** (409 `invalid_state`) on start, or **none is running** on pause, resume or abort: the card reloads and shows the API's message.
- **The environment became protected meanwhile** (409 `approval_required`): environments reload and the dialog switches to its request form.
- **A change request approved after the scheduled time** fails; the request form says so.
- **A failed rollout or schedule** shows the API's error text ("fallthrough changed manually", "flag … is archived", "variation was deleted").
- **A deleted variation** in a rollout shows as "deleted variation".
- **The flag is off** in the environment: the rollout card notes that the default rule serves nobody until it is on.
- **Kill switch** pauses a running rollout (API behaviour); the card shows Paused after the next reload.
- **A creator who cannot read the new project** (a role with only `project.create`): a message says it was created but they have no access.
- **Deleting the current project** goes to `/`, which picks another project or the empty page.
- Every list has a loading skeleton, an error with Try again, and an empty state. `failureText` gains any missing codes.

## Testing
- **API (pytest, written first):** `flag_count` (unused; used by two flags; one flag in two environments counts once; hidden environments), the usage endpoint (order, visibility, 404), environment order (happy path, missing, unknown and repeated keys, permission, the audit entry), plus the route permission and OpenAPI tests.
- **Web unit (Vitest):** `segment-draft`, `rollout-plan`, `schedules`, and the `nav` and `routes` changes.
- **Playwright, against the real API:**
  - `segments.spec.ts`: create, search, edit keys and rules and save, the unsaved guard, used-by after a flag rule uses it, delete blocked while in use and allowed after, Viewer read-only, cards under 768 px.
  - `environments.spec.ts`: create (flags get it), edit, set and clear approvals (the flag page shows "Needs approval"), reorder (the flag page follows), delete with the typed key, the last one cannot be deleted, Viewer read-only.
  - `project-settings.spec.ts`: rename, description, stale-after-days with its limits, create a project from the switcher, delete it with the typed key; the menu item is hidden without `project.manage`.
  - `flag-automation.spec.ts`: start a rollout (the default rule shows the first step), pause, resume, abort; the Targeting notice; schedule on, off and a rollout, cancel; a protected environment opens `rollout` and `schedule` change requests; Viewer read-only; and start, pause, abort, schedule and cancel **at 344 px with touch** (UI-15, UI-16).
- **Matrix (`matrix.spec.ts`):** segments, a segment, environments, project settings, and the Automation tab both empty (`demo`) and filled, at the six widths in both themes, with the axe, horizontal-scroll and target-size checks. `global-setup.ts` adds to the unchanged `gov` project a segment, and a flag that uses it and has a paused rollout and a pending schedule; times are masked. Baselines come from `pnpm e2e:docker --update-snapshots`. Expected changes to existing baselines: every flag detail tab (the new tab) and every project page that shows the sidebar (the new menu item); other diffs are reverted.
- **Final checks:** the web and API commands in AGENTS.md section 9, including the OpenAPI drift check.

## Documentation
- `docs/ga-checklist.md`: T-4, UI-14 (Segments part), F-10, T-6 and the GA "Scheduled changes" row point to the new specs; blocker 5 is removed.
- `CHANGELOG.md`: entries under `[Unreleased]` for the pages, the Automation tab, project create and delete, and the three API additions.

## Out of scope
- Guarded rollouts (T-7), and progressive rollouts of a rule (the API moves only the default rule).
- Editing a pending schedule or a running rollout's steps (cancel or abort, then create again), and recurring schedules.
- Rollout and schedule indicators on the other environment cards and in the flag list.
- A version or conflict check for segments, and approval for segment changes.
- Setting approvals while creating an environment, choosing a new project's environments in the dialog, and changing any key.
- Drag and drop reordering.
- CLI and Terraform support for the two new endpoints.
