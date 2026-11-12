# MF-80 plan: release workflows

**Goal:** through the management API, teams can do four things, and one scheduler that is safe across replicas runs the timed work:
- require approvals for protected environments;
- schedule flag changes;
- step a fallthrough rollout up automatically;
- see flag lifecycle states, with stale detection.

**Architecture:**
- **Scheduler:** `services/scheduler.py` holds a `Scheduler`. It keeps a dedicated connection with the session-level `pg_try_advisory_lock(7808002)`, and the leader calls `tick()` every `scheduler_interval_seconds`. Each job locks the `flag_environment` row, claims the due row with `FOR UPDATE SKIP LOCKED` and the status condition, applies the change and sets the status in that transaction. Changed projects are then published on the `ChangeBus`.
- **Services:** each workflow is a service that writes its own audit entries through an `AuditSink`. Routes pass the request `Auditor`; jobs pass a `SystemAuditor` (actor kind `system`, label `scheduler`). Every config change goes through `services/targeting.py` (`replace_config`, `set_enabled`, and the new `set_fallthrough_rollout` and `set_fallthrough_variation`).
- **Gate:** only routes call `approvals.ensure_direct_write(env)`, so CR apply and jobs bypass it by construction.

## Conventions (every step)
- **Branch and commits:** branch `megoo/MF-80-release-workflows-change-requests-schedu`. Commit as `<type>(api): <summary> (MF-80)`, then a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.megoocolab/`. Only step 13 edits `CHANGELOG.md`.
- **TDD:** write the test, see it fail, implement, see it pass. From `api/`, keep these green: `uv run ruff check . ../scripts`, `uv run ruff format --check . ../scripts`, `uv run mypy`, and the full `uv run pytest`, with no warnings. Postgres and Valkey run as in AGENTS.md §9.
- **Route or schema change:** run `uv run python -m megooflags_api.export_openapi openapi.json` in `api/`, then `pnpm --dir web gen:api` at the root. Commit both generated files with the step.
- **Repo-wide route tests:**
  - Every new mutating `/api` route gets an `AUDIT_SAMPLES` entry in `tests/test_audit_coverage.py`.
  - Every new mutating `/api/projects…` route goes into `WRITES` (if it publishes) or `EXCLUDED` (if it does not) in `tests/test_changes.py`.
- **Time:** services take `now` (aware UTC). Routes pass `datetime.now(UTC)`; the scheduler passes its clock. Tests use `FakeClock`.

## Details this plan fixes
1. **The person behind a call:** `Principal.actor_id` is `user_id`, or the token id for a service token. Author, reviewer and `created_by_id` use it, so a user's session and their personal token count as one person.
2. **Lock order:** the `flag_environment` row (`FOR UPDATE`) is locked first, then schedule or rollout rows.
   - Jobs read due ids without locks. Per id, they lock the flag environment, then re-select the row with `FOR UPDATE SKIP LOCKED`, the status condition and `populate_existing`. A second leader therefore finds the row already done.
   - The kill switch, pause and abort follow the same order. Archive locks the flag row, then updates schedule and rollout rows.
   - CR approve locks the CR row, then the flag environment. Nothing locks them the other way round.
3. **Failure isolation:** each job row is applied inside `session.begin_nested()`.
   - An `ApiError` or any other exception (logged) rolls the savepoint back, marks the row `failed` with the message (`"internal error"` otherwise) and commits.
   - A job handles at most 100 rows per tick.
4. **Scheduled `set_rollout`:** the input is `weights: list[WeightIn]` (stored in the `weights` jsonb). It sets a fallthrough rollout and keeps the existing `fallthrough_bucket_by`, or `"key"`.
5. **Rollout units:**
   - `percentage` is the "to" share in 0–100000 units (1–100000); step weights are `{from: 100000-p, to: p}`.
   - `wait_minutes` (1–43200, default 60) is the wait after a step; the last step's value is ignored.
   - A rollout whose only step is 100000 completes at start.
   - After a late step, the next one is due `now + wait`.
6. **Direct actions:** resuming a rollout and cancelling a schedule stay direct; the design gates only creation and start.
7. **Archive:** it cancels pending schedules (`flag.schedule.cancel`) and live rollouts. A rollout gets status `cancelled` and the new audit action `flag.rollout.cancel`.
8. **Deleted variations:** the rollout variation FKs are nullable with `ON DELETE SET NULL`, and a null variation fails the rollout ("variation was deleted"). A schedule naming a missing variation fails with the `invalid_reference` message.
9. **Lifecycle storage:**
   - The sweep uses a Core `UPDATE ... updated_at = flag.updated_at`, so it never bumps `updated_at`.
   - `last_evaluated_at` = max `evaluation_count.hour` over the project's environments, joined by flag key.
   - The `lifecycle` filter wins over `archived`: `lifecycle=archived` lists archived flags, and other values list live flags in that state.
10. **Stale rule:**
    - `reference = last_evaluated_at or created_at`, `cutoff = now - stale_after_days`.
    - `new` or `active` with `reference < cutoff` becomes `stale`.
    - Otherwise `new` with an evaluation becomes `active`.
    - `stale` with `reference >= cutoff` becomes `active`, or `new` if never evaluated.
    - Permanent and archived flags never change state; permanent flags still get `last_evaluated_at`.
11. **Change requests:**
    - Approve and reject both need `change.approve`, and the author can do neither (403 `self_approval`); authors cancel instead.
    - One review per reviewer; a second is 409 `invalid_state`.
    - Actions on closed CRs are 409 `invalid_state`.
    - `base_version` is the flag environment's version at creation. For `config`, the payload `version` must match (409 `version_conflict`).
    - Creation dry-runs the apply in a savepoint and rolls it back, so an invalid payload is a 4xx.
    - The list is newest first, with a keyset on `(created_at, id)`. The cursor is `encode_cursor(f"{created_at.isoformat()}|{id}")`.
    - CRs in hidden environments are 404.
12. **Publishing:**
    - These publish: lifecycle `PUT`, rollout start and abort, and a CR approve that applied.
    - Schedule create and cancel, rollout pause and resume, and CR create, comment, reject and cancel do not.
    - Jobs publish the projects they changed.

## File map
**New:**
- `api/alembic/versions/0012_release_workflows.py`
- `models/release.py`
- Services: `services/scheduler.py`, `lifecycle.py`, `schedules.py`, `rollouts.py`, `approvals.py`, `change_requests.py`
- Schemas: `schemas/schedules.py`, `rollouts.py`, `change_requests.py`
- Routers: `routers/schedules.py`, `rollouts.py`, `change_requests.py`
- Tests: `test_scheduler.py`, `test_lifecycle.py`, `test_schedules.py`, `test_rollouts.py`, `test_approvals_gate.py`, `test_change_requests.py`
- Docs: `docs/release-workflows.md`

All Python paths are under `api/src/megooflags_api/` or `api/tests/`.

**Modified:**
- Models: `models/enums.py`, `flag.py`, `project.py`, `__init__.py`.
- Auth: `auth/rbac.py`, `auth/principal.py`.
- Services: `services/audit.py`, `targeting.py`, `flags.py`, `projects.py`, `environments.py`.
- Schemas: `schemas/flags.py`, `projects.py`.
- Routers: `routers/flags.py`, `flag_environments.py`.
- Core: `errors.py`, `settings.py`, `metrics.py`, `main.py`.
- Tests: `tests/helpers.py`, `test_migrations.py`, `test_models_migration.py`, `test_rbac_catalogue.py`, `test_audit_coverage.py`, `test_changes.py`, `test_audit_record.py`, `test_principal.py`, `test_projects.py`, `test_environments.py`, `test_openapi.py`.
- `CHANGELOG.md`, `deploy/.env.example`, `api/openapi.json`, `web/lib/api/schema.d.ts`.

## Steps
1. Release-workflow schema: migration 0012, models, enums and the change.approve permission
2. Shared helpers: system audit actor, Actor, record_flag_change, state_of, invalid_state, FakeClock
3. Leader-elected background scheduler with advisory lock, metrics and settings
4. Lifecycle in the API: flag fields, lifecycle filter, permanent mark, project stale_after_days
5. Lifecycle sweep job: last_evaluated_at and stale detection
6. Scheduled changes service and the due-schedule job
7. Scheduled change routes, and archiving cancels pending schedules
8. Progressive rollout service and the rollout step job
9. Progressive rollout routes; kill switch pauses, archive cancels
10. Protected environments: approvals_required and the approval_required gate
11. Change requests: create (with dry run), list, read, comment, cancel
12. Change requests: approve, reject and apply on approval
13. Docs, CHANGELOG, OpenAPI coverage and final verification

## Appendix A: schema (migration "0012", down "0011")
**Enums** (in this order):
- `FlagLifecycle` (new, active, permanent, stale)
- `ScheduledAction` (enable, disable, set_rollout)
- `ScheduleStatus` (pending, done, failed, cancelled)
- `RolloutStatus` (active, paused, completed, aborted, failed, cancelled)
- `ChangeRequestKind` (config, toggle, schedule, rollout)
- `ChangeRequestStatus` (open, applied, rejected, cancelled, failed)
- `ReviewDecision` (approve, reject)

The pg type names are the snake_case names: `flag_lifecycle`, `scheduled_action`, `schedule_status`, `rollout_status`, `change_request_kind`, `change_request_status`, `review_decision`.

**Columns:**
- `environment.approvals_required` int, default 0, check `BETWEEN 0 AND 10` (`approvals_required_range`).
- `project.stale_after_days` int, default 30, check `BETWEEN 1 AND 365` (`stale_after_days_range`).
- `flag.lifecycle`, default `'new'`. Create the type before `add_column`.
- `flag.last_evaluated_at` timestamptz NULL.

**Tables** (create `change_request` first):
- `change_request`:
  - `id`, `project_id`/`environment_id`/`flag_id` (CASCADE)
  - `kind`, `payload` jsonb, `base_version` int, `title` varchar(200), `description` text default ''
  - `status` default open, `error` NULL
  - `author_id` uuid NULL, `author_label` text
  - `created_at`/`updated_at`, `closed_at` NULL
  - Index (`project_id`, `status`, `created_at`).
- `change_request_review`: `id`, `change_request_id` (CASCADE), `reviewer_id`, `reviewer_label`, `decision`, `comment` NULL, `created_at`. Unique (`change_request_id`, `reviewer_id`).
- `change_request_comment`: `id`, `change_request_id` (CASCADE, indexed), `author_id` NULL, `author_label`, `body` text, `created_at`.
- `scheduled_change`:
  - `id`, `flag_environment_id` (CASCADE, indexed)
  - `action`, `weights` jsonb NULL, `run_at`
  - `status` default pending, `error` NULL
  - `created_by_id` NULL, `created_by_label`, `change_request_id` (SET NULL, NULL)
  - `created_at`, `executed_at` NULL
  - Index (`status`, `run_at`).
- `progressive_rollout`:
  - `id`, `flag_environment_id` (CASCADE, indexed)
  - `from_variation_id`/`to_variation_id` (SET NULL, NULL)
  - `steps` jsonb, `current_step` default 0, `next_step_at` NULL, `remaining_seconds` NULL
  - `status` default active, `last_applied_weights` jsonb NULL, `error` NULL
  - `created_by_id` NULL, `created_by_label`
  - `created_at`/`updated_at`, `finished_at` NULL
  - Index (`status`, `next_step_at`).
  - Partial unique `uq_progressive_rollout_live` (`flag_environment_id`) `WHERE status IN ('active','paused')`.

**Data:** append `'change.approve'` to the builtin `admin` and `editor` roles. The downgrade runs `array_remove` on all roles, drops everything and runs `DROP TYPE` for all seven enums.

## Appendix B: shared helpers
- `Principal.actor_id` (property): `user_id if user_id is not None else id`.
- `services/audit.py`:
  - `Actor(id: UUID | None, label: str)` with `Actor.of(principal)`, and `SCHEDULER = Actor(None, "scheduler")`.
  - Protocol `AuditSink.record(action, resource_type, resource_key, **kw)`.
  - `record_system(session, action, resource_type, resource_key, *, label="scheduler", project_id=None, environment_id=None, before=None, after=None)` writes actor_kind `system`, no actor_id, ip, user agent or request id, and redacted snapshots.
  - `SystemAuditor(session, label="scheduler")`.
  - `record_flag_change(audit, action, flag, env, before, after, **context)` writes resource ("flag", key) with project and env ids; `context` (schedule_id, rollout_id, change_request_id, created_by, reason) is merged into `after`.
- `targeting.state_of(fe, env_key)`, moved from `routers/flag_environments._state`.
- `errors.invalid_state(msg)` (409).
- `tests/helpers.FakeClock(now=None)`: callable, with `.now` and `.advance(**timedelta_kw)`.

## Appendix C: scheduler
- `SCHEDULER_LOCK_ID = 7_808_002`, `MAX_PER_TICK = 100`.
- Protocol `Publisher.publish(project_id)`.
- `JobResult(ok=0, failed=0, projects=set())`.
- `Job(name, run: Callable[[sessionmaker, datetime], Awaitable[JobResult]], every: timedelta | None = None)`.
- `default_jobs()` returns, in order, schedules (step 6), rollouts (step 8) and lifecycle every 15 min (step 5).
- `Scheduler(engine, sessionmaker, bus, *, interval=5.0, jobs=None, clock=now_utc, lock_id=SCHEDULER_LOCK_ID)` has `is_leader` and `backend_pid`, and these methods:
  - `ensure_leader()` connects, runs `pg_try_advisory_lock` and `pg_backend_pid()`, commits, and closes the connection if it did not get the lock. The leader runs `SELECT 1` and commits. On `DBAPIError`, `OSError` or `TimeoutError` it invalidates the connection, closes it and sets `is_leader=False`. It sets the gauge.
  - `tick()` skips a periodic job that ran less than `every` ago. Otherwise it runs the job and counts the outcomes `ok`, `failed` and `error` (an exception, logged). It publishes the sorted projects.
  - `run()` loops forever on `ensure_leader` then `tick`, logging errors and sleeping `interval`, with `close()` in `finally`.
  - `close()` unlocks, closes the connection and sets the gauge to 0.
- Metrics:
  - `Gauge("megooflags_scheduler_leader", multiprocess_mode="livesum")`.
  - `Counter("megooflags_scheduler_jobs", labels job and outcome)`, exported as `_total`.
- Settings: `scheduler_enabled=True`, `scheduler_interval_seconds=5` (gt 0).
- `main.py`:
  - `app.state.scheduler` is created in `create_app`.
  - `background_jobs` adds `scheduler.run()` when enabled.
  - The lifespan awaits `scheduler.close()` before `engine.dispose()`.
- Test helper: `make_scheduler(app, clock)`.

## Appendix D: schemas
- **Flags and projects:**
  - `LifecycleState = Literal["new","active","permanent","stale","archived"]`; `LifecycleIn{permanent: bool}`.
  - `FlagSummary` (and so `FlagOut`) gains `lifecycle` and `last_evaluated_at`.
  - `ProjectUpdate`/`ProjectSummary.stale_after_days` (1–365).
  - `EnvironmentUpdate`/`EnvironmentOut.approvals_required` (0–10).
- **Schedules:**
  - `ScheduleIn{action, run_at: AwareDatetime, weights: list[WeightIn] | None}`; `weights` is required for set_rollout and refused otherwise.
  - `ScheduleOut{id, environment, action, weights, run_at, status, error, created_by, change_request_id, created_at, executed_at}`.
- **Rollouts:**
  - `RolloutStepIn{percentage 1..100000, wait_minutes=60 (1..43200)}`.
  - `ProgressiveRolloutIn{from_variation: Key, to_variation: Key, steps (1..20)}`. Validation: from != to, percentages strictly increase, and the last is 100000.
  - `ProgressiveRolloutOut{id, environment, from_variation, to_variation, steps, current_step, next_step_at, remaining_seconds, status, last_applied_weights: list[WeightIn] | None, error, created_by, created_at, updated_at, finished_at}`.
  - `RolloutView{current, recent (10 newest finished)}`.
- **Change requests:**
  - `ChangeRequestCreate{environment, flag, kind, title: Name, description="", payload: dict[str, JsonValue]}`.
  - `CommentIn{body 1..10000}`, `ApproveIn{comment: str | None}`, `RejectIn{comment 1..10000}`.
  - `ReviewOut{reviewer, decision, comment, created_at}`, `CommentOut{id, author, body, created_at}`.
  - `ChangeRequestSummary{id, environment, flag, kind, title, status, author, approvals, approvals_required, created_at, closed_at}`.
  - `ChangeRequestOut(Summary){description, payload, base_version, error, reviews, comments, current: FlagConfigOut | None, proposed: FlagConfigIn | None}`; `current` and `proposed` are set for config CRs only.
  - Payload per kind: `config` → `FlagConfigIn`, `toggle` → `ToggleIn`, `schedule` → `ScheduleIn`, `rollout` → `ProgressiveRolloutIn`. Pydantic errors become `validation_error(msg, "payload.<loc>")`.

## Appendix E: job pattern
1. In one session, `select(id).where(status==PENDING, run_at<=now).order_by(run_at).limit(100)`.
2. For each id, in a new session:
   - Read `flag_environment_id`, then lock the fe with `select(FlagEnvironment)...with_for_update()`.
   - Claim the row with `select(Row).where(id==.., status==PENDING).with_for_update(skip_locked=True).execution_options(populate_existing=True)`. If there is none, skip it.
   - Apply with `SystemAuditor` inside `begin_nested()`, marking it done or failed, and commit.

Rollouts use the same pattern with `status==ACTIVE and next_step_at<=now`.

## Appendix F: routes (under /api; FE = /projects/{project}/flags/{flag}/environments/{env})
| Route | Permission | Audit | Publishes |
|---|---|---|---|
| PUT /projects/{project}/flags/{flag}/lifecycle | flag.update | flag.lifecycle.update | WRITES |
| GET, POST FE/schedules (201) | project.read, then flag.toggle (enable/disable) or flag.config.write; POST gated | flag.schedule.create | EXCLUDED |
| DELETE FE/schedules/{id} (200) | project.read, then the action's permission | flag.schedule.cancel | EXCLUDED |
| POST FE/rollout (201) | flag.config.write; gated | flag.rollout.start, flag.config.update | WRITES |
| GET FE/rollout | project.read | — | — |
| POST FE/rollout/pause, resume | flag.config.write | flag.rollout.pause/resume | EXCLUDED |
| POST FE/rollout/abort | flag.config.write | flag.rollout.abort, flag.config.update | WRITES |
| GET, POST /projects/{project}/change-requests (201) | project.read, then the payload's permission at env | change_request.create | EXCLUDED |
| GET …/{id}; POST …/{id}/comments (201) | project.read, env visible | change_request.comment | EXCLUDED |
| POST …/{id}/approve | change.approve at env | change_request.approve, .apply/.fail, plus the applied change's actions | when applied; EXCLUDED |
| POST …/{id}/reject | change.approve at env | change_request.reject | EXCLUDED |
| POST …/{id}/cancel | author or project.manage | change_request.cancel | EXCLUDED |

Jobs audit as `system`/`scheduler`:
- Schedules: `flag.schedule.run`/`.fail`, plus `flag.toggle` or `flag.config.update`.
- Rollouts: `flag.rollout.step`/`.complete`/`.fail`, plus `flag.config.update`.
- Lifecycle: `flag.lifecycle.update`.

## Final verification
From `api/`:
- `uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest`

From the root:
- `bash scripts/check_migrations.sh`
- `uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api && git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts`
- `(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build)`
- `python3 scripts/check_version.py`
