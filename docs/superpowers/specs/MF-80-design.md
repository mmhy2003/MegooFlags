# MF-80 — Release workflows: change requests, scheduled changes, progressive rollouts, flag lifecycle and stale flags

## Goal
Through the management API, teams can:
- require approvals before changes reach protected environments (G-4);
- schedule a flag to turn on or off, or change its rollout, at a set time (F-10);
- step a rollout up automatically, with pause and abort (T-6);
- see each flag's lifecycle state, with stale flags detected from evaluation counts (F-7).

One background scheduler that is safe across API replicas runs all the timed work. The UI pages belong to the web UI task.

## Decisions (with the human's answers)
- **One design, built in ordered steps:** scheduler, then lifecycle, scheduled changes, progressive rollouts, change requests, and finally the docs and generated files. One branch, one PR.
- **What a protected environment gates.** In an environment with approvals turned on, these writes return **409 `approval_required`** and must go through a change request (CR): config PUT, toggle, copy *into* that environment, creating a scheduled change, and starting a progressive rollout. The **kill switch stays direct**. Pausing and aborting a rollout also stay direct, because they are safety actions. Rejected options: gating everything including the kill switch, and an audited bypass permission.
- **Approval rule:** each environment sets N required approvals (`approvals_required`, 0 = not protected, default 1 when turned on). The author can never approve their own CR. A single reject closes the CR. Approving needs a new environment-level permission `change.approve`. Rejected options: exactly one approval, and allowing self-approval.
- **Scheduler:** one leader per cluster, chosen with a session-level Postgres advisory lock. The leader looks for due jobs every 5 s. Rejected options: every replica polling with SKIP LOCKED, and a Valkey lock.
- **Progressive rollout steps only the fallthrough**, moving traffic from one variation to another. Rule rollouts are not stepped.
- **Stale:** a flag is stale when no environment has evaluated it for N days. N is a project setting (`stale_after_days`, default 30). Permanent flags are never stale. A new evaluation makes a stale flag active again.
- My own calls, which you can change at review:
  - Missed jobs run late after downtime instead of being skipped.
  - Aborting a rollout sends 100 % back to the "from" variation.
  - The kill switch pauses an active rollout in that environment.
  - Archiving a flag cancels its pending schedules and running rollouts.

## Architecture
New modules follow the existing router → service → model layout:

- `services/scheduler.py`: the `Scheduler` class, added to `background_jobs()` in `main.py`.
  - It holds a dedicated connection and tries `pg_try_advisory_lock(<constant key>)` on it.
  - Without the lock it retries every 5 s. With the lock it runs one tick every `scheduler_interval_seconds` (default 5) and pings the connection on each tick. If the connection drops, the lock is released, another replica takes over, and this one goes back to retrying.
  - Each tick runs these jobs, each in its own transaction:
    - due scheduled changes;
    - due rollout steps;
    - the lifecycle sweep, at most every 15 min.
  - Due rows are claimed with `FOR UPDATE SKIP LOCKED` and their status changes in the same transaction. A rare double leader during failover can therefore never run a job twice.
  - After each commit the scheduler calls `ChangeBus.publish(project_id)`, so SDK streams update as they do for a manual change.
  - The setting `MEGOOFLAGS_SCHEDULER_ENABLED` (default true) lets tests drive ticks by hand with `await scheduler.tick()`.
  - Prometheus metrics: `megooflags_scheduler_leader` (gauge) and `megooflags_scheduler_jobs_total{job,outcome}`.
- `services/lifecycle.py`, `services/schedules.py`, `services/rollouts.py` and `services/change_requests.py` hold the logic. Routers and the scheduler call them.
- **Applying changes reuses the existing code in `services/targeting`** (`replace_config`, `set_enabled`, plus a new `set_fallthrough_rollout`). Direct routes, CR apply and scheduler jobs therefore share the same validation, locking order (env lock, then `flag_environment` `FOR UPDATE`), version bump and audit entries.
- **Gate:** a helper `ensure_direct_write(env)` raises `approval_required`. It is called by `replace_flag_config`, `toggle_flag`, `copy_flag_config` (for any protected target), schedule creation and rollout start.
- **Audit:** every action is recorded with the existing `audit.record`. The scheduler records as a new actor kind `system`, labelled `scheduler`. Its `after` snapshot carries the schedule, rollout or CR id and the original creator.

## Data and interfaces
The migration is `0012_release_workflows`, with a full downgrade.

**Changed tables**
- `environment.approvals_required int not null default 0`, range 0–10. It is set through the existing environment PATCH and needs `environment.manage`.
- `project.stale_after_days int not null default 30`, range 1–365. It is set through the project PATCH.
- `flag.lifecycle` uses the enum `flag_lifecycle`: `new`, `active`, `permanent`, `stale`. `flag.last_evaluated_at timestamptz null` is also added.
  - The API reports `archived` whenever `archived_at` is set.
  - `FlagOut` gains `lifecycle` and `last_evaluated_at`.
  - The flag list gains a `lifecycle` filter.

**New tables**
- `scheduled_change`:
  - `id`, `flag_environment_id` (FK, on delete cascade)
  - `action` (`enable`, `disable`, `set_rollout`), plus `weights` (jsonb, for `set_rollout`)
  - `run_at`, `status` (`pending`, `done`, `failed`, `cancelled`)
  - `error`, `created_by_id`, `created_by_label`, `change_request_id`, `created_at`, `executed_at`
  - Index on `(status, run_at)`.
- `progressive_rollout`:
  - `id`, `flag_environment_id`, `from_variation_id`, `to_variation_id`
  - `steps` (jsonb `[{percentage, wait_minutes}]`): percentages strictly increase and the last is 100. Weights use the existing 0–100000 units.
  - `current_step`, `next_step_at`, `remaining_seconds` (kept while paused)
  - `status` (`active`, `paused`, `completed`, `aborted`, `failed`, `cancelled`)
  - `last_applied_weights`, `error`, `created_by_*`, timestamps
  - A partial unique index allows only one active or paused rollout per `flag_environment`.
- `change_request`:
  - `id`, `project_id`, `environment_id`, `flag_id`
  - `kind` (`config`, `toggle`, `schedule`, `rollout`), `payload` (jsonb: the same input schema as the direct endpoint)
  - `base_version`, `title`, `description`
  - `status` (`open`, `applied`, `rejected`, `cancelled`, `failed`)
  - `error`, `author_id`, `author_label`, `created_at`, `closed_at`
- `change_request_review`: CR id, reviewer id and label, `decision` (`approve`, `reject`), `comment`, `created_at`. Unique on `(cr, reviewer)`.
- `change_request_comment`: id, CR id, author, `body` (max 10k), `created_at`.

**Endpoints** (all under `/api`; paths use `{env}`)
- Lifecycle:
  - `PUT /projects/{project}/flags/{flag}/lifecycle` with `{permanent: bool}` marks a flag permanent or clears the mark (back to `active` or `new`). Needs `flag.update`.
- Schedules:
  - `GET` and `POST /projects/{project}/flags/{flag}/environments/{env}/schedules`.
  - `DELETE …/schedules/{id}` cancels a pending one.
  - `enable` and `disable` need `flag.toggle`; `set_rollout` needs `flag.config.write`.
  - `run_at` must be in the future and at most one year ahead.
- Progressive rollout:
  - `POST …/environments/{env}/rollout` starts it and applies step 0 at once.
  - `GET …/rollout` returns the current rollout plus recent ones.
  - `POST …/rollout/pause`, `…/resume` and `…/abort`.
  - All need `flag.config.write`.
- Change requests:
  - `GET` and `POST /projects/{project}/change-requests`. The list filters by `status`, `env` and `flag` and uses the existing cursor pagination.
  - `GET /projects/{project}/change-requests/{id}` returns the CR with its reviews and comments, and for `config` CRs the current and proposed config.
  - `POST …/{id}/comments`, `POST …/{id}/approve` (optional comment), `POST …/{id}/reject` (comment required).
  - `POST …/{id}/cancel` is for the author or `project.manage`.
  - Creating a CR needs the permission of the action it carries. CRs are allowed in unprotected environments too, where 1 approval applies them.
- **RBAC:** a new permission `change.approve` at environment level. The migration adds it to the seeded admin and editor roles.
- **New error codes:** `approval_required` (409), `self_approval` (403), `invalid_state` (409, for an action on a closed CR or a finished rollout).
- **Audit actions:**
  - Change requests: `change_request.create`, `.comment`, `.approve`, `.reject`, `.cancel`, `.apply`, `.fail`.
  - Schedules: `flag.schedule.create`, `.cancel`, `.run`, `.fail`.
  - Rollouts: `flag.rollout.start`, `.step`, `.pause`, `.resume`, `.abort`, `.complete`, `.fail`.
  - Lifecycle: `flag.lifecycle.update`.
  - The existing `flag.config.update` and `flag.toggle` entries are also written when a CR or a job applies a change.

## Behaviour
- **CR apply:** the approval that reaches the required count (`max(1, approvals_required)`, read at approval time) applies the CR in the same transaction.
  - It locks the flag environment, checks `base_version`, validates the payload again and applies it.
  - On a version conflict or a validation failure the approval is still recorded, the CR becomes `failed` with the reason, and the author resubmits.
  - A `schedule` or `rollout` CR creates the schedule or starts the rollout when it is applied.
- **Rollout steps:** each step first checks that the fallthrough still equals `last_applied_weights`.
  - If someone edited it by hand, the rollout becomes `failed` ("fallthrough changed manually") and nothing is overwritten.
  - At the last step the fallthrough serves the "to" variation directly and the rollout is `completed`.
  - A deleted variation also fails the rollout or the schedule with a clear error.
- **Lifecycle sweep:**
  - It reads `max(hour)` from `evaluation_count`, joined by flag key across the project's environments, and stores it in `last_evaluated_at`.
  - `new` becomes `active` once the flag has been evaluated.
  - `new` or `active` becomes `stale` when the last evaluation (or creation, if never evaluated) is more than N days ago.
  - `stale` becomes `active` again after a newer evaluation.
  - Archived and permanent flags are skipped. Each state change is audited.
- Other edge cases:
  - Archiving a flag cancels its pending schedules and running rollouts.
  - The kill switch pauses an active rollout.
  - A change to `approvals_required` leaves open CRs open.

## Testing
Tests are written first and must fail before the code is added, as AGENTS.md requires.
- **Scheduler:** two `Scheduler` instances against one database, where exactly one becomes leader. Failover when the leader's connection closes. A job claimed concurrently runs once. Overdue jobs run on the next tick.
- **Each workflow:** service and route tests that drive `tick()` with a controllable clock. They cover:
  - schedule run, cancel and failure;
  - rollout steps, pause and resume timing, abort rollback, failure after a manual edit, completion, and the kill switch pausing it;
  - lifecycle transitions from seeded `evaluation_count` rows, and the permanent mark;
  - CR create, comment, approve to the threshold and apply, self-approval 403, reject, cancel, version-conflict failure, and the `approval_required` gate on each gated route with the kill switch staying open.
- **Shared checks:**
  - Audit entries exist for every action (extend `test_audit_coverage`).
  - Route permissions (extend `test_route_permissions`).
  - `ChangeBus.publish` is called after each job.
  - Migrations upgrade and downgrade, and the single-head test expects `0012`.
  - `test_openapi` passes and the regenerated client is up to date.

## Out of scope
- Web UI pages.
- Webhook, Slack and email notifications for CRs or stale flags (D-5).
- Code references (D-6).
- Guarded or metric-based rollouts (T-7).
- Stepping rule rollouts.
- Multi-flag or multi-environment CRs.
- Cleanup reminders beyond the `stale` state.
- A version bump. A CHANGELOG `[Unreleased]` entry is included.
