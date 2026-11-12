# Release workflows

MegooFlags can hold changes to protected environments for approval, run flag changes at a set time,
step a rollout up automatically and track each flag's lifecycle. Everything here works through the
management API under `/api`; the web pages come later.

The examples use a service or personal API token (`POST /api/tokens` or `/api/account/tokens`) and
these shell variables:

```sh
export MF=https://flags.example.com   # your MEGOOFLAGS_PUBLIC_URL
export TOKEN=mf_...                   # an API token
export P=shop                         # project key
FE="$MF/api/projects/$P/flags/new-checkout/environments/production"
```

Errors use the usual body, `{"code": "...", "message": "...", "details": ...}`. The codes added by
these workflows are `approval_required` (409), `self_approval` (403) and `invalid_state` (409, an
action on a closed change request or a finished rollout).

## Protecting an environment

Set how many approvals an environment needs (0 to 10; 0, the default, means not protected). This
needs `environment.manage`.

```sh
curl -X PATCH "$MF/api/projects/$P/environments/production" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"approvals_required": 2}'
```

In a protected environment these writes return **409 `approval_required`** and must go through a
change request:

- `PUT …/environments/{env}` (replace the flag's config);
- `POST …/environments/{env}/toggle`;
- `POST /api/projects/{project}/flags/{flag}/copy` when any target environment is protected;
- `POST …/environments/{env}/schedules` (create a scheduled change);
- `POST …/environments/{env}/rollout` (start a progressive rollout).

These stay direct, because they stop or undo a change: the kill switch (`POST …/kill`), pausing,
resuming and aborting a rollout, and cancelling a scheduled change.

Changing `approvals_required` leaves open change requests open; the new count applies to the next
approval.

## Change requests

A change request (CR) carries one change to one flag in one environment. Its `payload` is the body
of the direct endpoint it stands in for:

| `kind` | `payload` | Permission to create |
|---|---|---|
| `config` | the config `PUT` body (`version`, `enabled`, `targets`, `rules`, `fallthrough`, `prerequisites`) | `flag.config.write` |
| `toggle` | `{"enabled": true}` | `flag.toggle` |
| `schedule` | a scheduled change, see below | `flag.toggle` for enable and disable, `flag.config.write` for `set_rollout` |
| `rollout` | a progressive rollout, see below | `flag.config.write` |

The payload is checked by a dry run when the CR is created, so an invalid change is refused at once
(422, with `details.path` starting with `payload`). A `config` payload's `version` must match the
flag environment's current version (409 `version_conflict`).

```sh
curl -X POST "$MF/api/projects/$P/change-requests" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
        "environment": "production",
        "flag": "new-checkout",
        "kind": "toggle",
        "title": "Turn on the new checkout",
        "description": "Verified in staging.",
        "payload": {"enabled": true}
      }'
```

List, read and discuss them:

```sh
curl "$MF/api/projects/$P/change-requests?status=open&env=production&flag=new-checkout" \
  -H "Authorization: Bearer $TOKEN"
curl "$MF/api/projects/$P/change-requests/$CR" -H "Authorization: Bearer $TOKEN"
curl -X POST "$MF/api/projects/$P/change-requests/$CR/comments" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"body": "Can we wait until after the sale?"}'
```

The list is newest first and paginated with `cursor` and `limit`. Reading a CR returns its reviews
and comments, and for a `config` CR the live config (`current`) next to the `proposed` one.

### Review rules

- Approving and rejecting need the `change.approve` permission in the CR's environment. The
  built-in Admin and Editor roles have it; add it to custom roles that should review.
- The author can neither approve nor reject their own CR (403 `self_approval`). A user and their
  personal tokens count as one person.
- Each reviewer reviews once; a second review is 409 `invalid_state`.
- One rejection closes the CR. The comment is required.
- The approval that reaches the environment's `approvals_required` (at least 1, so a CR in an
  unprotected environment needs one approval) applies the change in the same request. The response
  then has `status: "applied"`.
- The author, or anyone with `project.manage`, can cancel an open CR.

```sh
curl -X POST "$MF/api/projects/$P/change-requests/$CR/approve" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"comment": "LGTM"}'
curl -X POST "$MF/api/projects/$P/change-requests/$CR/reject" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"comment": "Not during the sale."}'
curl -X POST "$MF/api/projects/$P/change-requests/$CR/cancel" -H "Authorization: Bearer $TOKEN"
```

### When applying fails

A CR records the flag environment's version when it is created. If the flag changed in that
environment before the last approval (someone used the kill switch, a schedule ran, another CR was
applied), the approval is still recorded but the CR becomes `failed` with an `error` such as
"the flag changed since this request was made (version 4, now 5)", and nothing is applied. The
same happens when the change is no longer valid, for example a variation it names was deleted.
Open a new CR against the current config.

Every step (create, comment, approve, reject, cancel, apply, fail) is in the audit log, and the
change itself is recorded as `flag.config.update` or `flag.toggle` with the CR's id.

## Scheduled changes

A scheduled change enables or disables a flag, or sets its fallthrough rollout, at `run_at`:

```sh
curl -X POST "$FE/schedules" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action": "enable", "run_at": "2026-12-01T09:00:00Z"}'

curl -X POST "$FE/schedules" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action": "set_rollout", "run_at": "2026-12-02T09:00:00Z",
       "weights": [{"variation": "on", "weight": 25000}, {"variation": "off", "weight": 75000}]}'
```

- `action` is `enable`, `disable` or `set_rollout`. `weights` is required for `set_rollout` and
  refused otherwise; it uses the same units as rollouts (0–100000, adding up to 100000) and keeps
  the fallthrough's current `bucket_by`.
- `run_at` must have a time zone, be in the future and be at most 365 days ahead.
- `GET "$FE/schedules"` lists them; `DELETE "$FE/schedules/$ID"` cancels a pending one.
- A schedule runs within a few seconds of `run_at`. If the scheduler was down at that time, the
  missed schedule runs late, as soon as it is back; it is never skipped.
- A schedule that cannot be applied (for example a variation was deleted) becomes `failed` with an
  `error`. Archiving the flag cancels its pending schedules.

## Progressive rollouts

A progressive rollout moves the fallthrough from one variation to another in steps:

```sh
curl -X POST "$FE/rollout" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"from_variation": "off", "to_variation": "on",
       "steps": [{"percentage": 5000, "wait_minutes": 60},
                 {"percentage": 25000, "wait_minutes": 240},
                 {"percentage": 100000}]}'
```

- `percentage` is the "to" variation's share in thousandths of a percent: 0–100000 units, so 5000
  is 5 % and 100000 is 100 %. Percentages must strictly increase and end at 100000 (1 to 20 steps).
- `wait_minutes` (1–43200, default 60) is the wait after a step; the last step's is ignored.
- Step 0 is applied at once. At the last step the fallthrough serves the "to" variation and the
  rollout is `completed`. Only one rollout can run per flag environment.
- `GET "$FE/rollout"` returns the running rollout (`current`) and the 10 most recent finished ones.

Control it with:

```sh
curl -X POST "$FE/rollout/pause"  -H "Authorization: Bearer $TOKEN"
curl -X POST "$FE/rollout/resume" -H "Authorization: Bearer $TOKEN"
curl -X POST "$FE/rollout/abort"  -H "Authorization: Bearer $TOKEN"
```

- **Pause** keeps the current weights and the wait that was left; **resume** restarts the clock
  with that remaining wait.
- **Abort** sends 100 % back to the "from" variation.
- The **kill switch** pauses a running rollout in that environment.
- A **manual edit** of the fallthrough while the rollout runs (a config `PUT`, a CR, a schedule)
  makes the next step fail the rollout with "fallthrough changed manually"; the edit is kept.
- Deleting one of its variations fails the rollout. Archiving the flag cancels it.

## Lifecycle and stale detection

Every flag has a `lifecycle`: `new`, `active`, `permanent`, `stale` or `archived`, and a
`last_evaluated_at` taken from the SDK evaluation counts of all the project's environments.

- `new` becomes `active` once the flag has been evaluated.
- `new` or `active` becomes `stale` when no environment has evaluated it for the project's
  `stale_after_days` (1–365, default 30), counted from creation if it was never evaluated.
- A `stale` flag that is evaluated again becomes `active`.
- `permanent` flags (kill switches, long-lived configuration) are never stale. Archived flags show
  `archived` and are left alone.

The scheduler's lifecycle sweep updates these states every 15 minutes, so a change shows up within
that time. Each state change is audited as `flag.lifecycle.update`.

```sh
# Mark a flag permanent, or clear the mark (back to active or new)
curl -X PUT "$MF/api/projects/$P/flags/new-checkout/lifecycle" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"permanent": true}'

# Change the stale threshold of a project
curl -X PATCH "$MF/api/projects/$P" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"stale_after_days": 60}'

# List stale flags (lifecycle=archived lists archived flags)
curl "$MF/api/projects/$P/flags?lifecycle=stale" -H "Authorization: Bearer $TOKEN"
```

## The scheduler

Scheduled changes, rollout steps and the lifecycle sweep are run by one background scheduler.
Every `api` process starts one, but only one per cluster is the leader: the one that holds a
session-level Postgres advisory lock. The others retry every few seconds and take over if the
leader's database connection drops. Each job claims its rows with `FOR UPDATE SKIP LOCKED` and
changes their status in the same transaction, so a job never runs twice, even during a failover.
Changes the scheduler makes reach SDKs like manual ones and are audited with the actor `system`
(`scheduler`).

Settings (in `deploy/.env.example`):

| Setting | Default | Meaning |
|---|---|---|
| `MEGOOFLAGS_SCHEDULER_ENABLED` | `true` | Set to `false` to stop this process from running the scheduler. If no process runs it, nothing scheduled happens. |
| `MEGOOFLAGS_SCHEDULER_INTERVAL_SECONDS` | `5` | Seconds between the leader's checks for due work. |

Metrics on `:8000/metrics`:

- `megooflags_scheduler_leader`: 1 in the process that is the leader. Summed over the cluster it
  should be 1; 0 means nothing is running scheduled work.
- `megooflags_scheduler_jobs_total{job, outcome}`: rows handled per job (`schedules`, `rollouts`,
  `lifecycle`) and outcome (`ok`, `failed`, or `error` when the job itself crashed).
