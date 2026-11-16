# MF-85: Developer tooling and integrations

## Goal
Teams can manage MegooFlags without the web UI and hear about changes where they work:
- a `megooflags` CLI for flag CRUD, promotion between environments and local development;
- flags as code: a YAML directory with `export`, `apply` and `diff`, synced with Git from CI;
- importers from Unleash, Flagsmith and LaunchDarkly;
- a Terraform provider;
- webhooks, Slack and Microsoft Teams notifications, managed on the project's Integrations page (mockup v1).

Everything ships in one pull request into `develop`, with tests against the running stack and documentation.

## Decisions
- **Scope (your answer): all five pieces in one design and one pull request.** *Rejected:* CLI first with follow-up tasks.
- **Go CLI with a hand-written API client that the provider reuses (your answer).** *Rejected:* a client generated from `openapi.json` (OpenAPI 3.1 is only partly supported by Go generators); a Python CLI.
- **Git sync: Git resolves conflicts (your answer).** The server never talks to Git. CI runs `export` on a branch and opens a pull request with the UI changes; merging to main runs `apply`. *Rejected:* a state file with automatic two-way merge; Git always wins.
  - One correction to what I described in that question: the export job runs on a schedule and on a manual trigger. A MegooFlags webhook cannot start a GitHub workflow directly, because GitHub's dispatch API needs its own auth header; the docs show how to do it through a small relay of your own.
- **Local development: bootstrap file and evaluate (your answer).** `ruleset pull` and `evaluate`, plus one new read-only API endpoint. *Rejected:* a local flag server; evaluate only.
- **Importers read the other tool's API or a saved file (your answer),** and write the flags-as-code directory plus a report; they never write to MegooFlags directly. CI uses recorded files. *Rejected:* files only; real Unleash and Flagsmith containers in CI.
- **Terraform provider: GitHub release files only (your answer).** No Terraform Registry and no split repository. Users install through a filesystem mirror. *Rejected:* Registry through a split repository; build only.
- **Notification events (your answer):** four groups per integration: Flags, Targeting and state, Segments, Change requests. *Rejected:* flags only; every audit entry.
- **Webhook targets: public addresses only by default (your answer).** `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true` lifts it. *Rejected:* private networks allowed; any address.
- **Integrations page layout: mockup v1 (your answer).**
- **My choices, for you to check:**
  - The task says every tool uses only the REST API and tokens. That holds for the CLI, flags as code, importers and the provider. Webhooks need server code, and `ruleset pull` needs one new endpoint.
  - `apply` never bypasses approvals. In an environment that requires approval it reports "needs approval" and exits with code 4; with `--change-requests` it opens a config change request instead. Terraform fails there with a clear error.
  - `apply` never deletes by default. `--prune` archives flags and deletes segments that are missing from the files. Environments are never deleted.
  - The YAML uses the API's field names and units (weights in thousandths of a percent), so there is one format to learn and no conversion bugs.
  - The CLI and the provider have their own versions (0.1.0), like the SDKs. Tags are `cli/vX.Y.Z` and `terraform/vX.Y.Z`; only a maintainer pushes them.
  - No automatic pausing of a failing integration; it shows "Failing" and keeps retrying each event up to 6 times.

## Architecture

### 1. API (`api/`)
- **Ruleset endpoint** in `routers/evaluate.py`: returns what `/sdk/v1/ruleset` returns, built by `services/ruleset.build_ruleset`, to an API token.
- **Integrations**, new package `services/integrations/`:
  - `events.py`: the audit actions in each group, and `build_event(entry)`, which turns an audit entry into the event payload.
  - `outbox.py`: `services/audit.record` and `record_system` add one `integration_outbox` row in the same transaction when the entry has a project and its action is in a group. A rolled-back change therefore sends nothing, and no committed change is missed.
  - `fanout.py`: a new scheduler job (`default_jobs`). It claims outbox rows with `FOR UPDATE SKIP LOCKED`, creates one pending delivery per matching active integration, and deletes the rows.
  - `worker.py`: a background task started in `main.background_jobs`. It works only while this process is the scheduler leader, so deliveries never delay scheduled changes. It sends due deliveries (at most 10 at once) and sets the next attempt.
  - `net.py`: the outbound client and the address check (see Error handling).
  - `format.py`: pure functions `summarise(event)`, `slack_body` and `teams_body`.
  - A retention job deletes deliveries older than the retention setting.
- `routers/integrations.py`, `schemas/integrations.py`, `models/integration.py`, migration `0014_integrations` with a working downgrade.
- Regenerate `api/openapi.json` and `web/lib/api/schema.d.ts`; CHANGELOG entries.

### 2. Web (`web/`)
- `app/(app)/projects/[project]/integrations/page.tsx` replaces the placeholder with `<IntegrationList>`.
- `components/integrations/`: `integration-list` (`DataList`, cards below 768 px), `integration-form` (`ResponsiveDialog`; type picker, name, URL, event groups, environments, Active), `delivery-log` (expandable rows with `JsonViewer`, "Send again"), `secret-reveal` (shown once, `CopyButton`).
- `lib/integrations/`: pure helpers for event group labels, status (Active, Failing, Paused) and form to API mapping, tested with Vitest.
- Uses `useApiData`, `must`, `useProjectPermissions` and `sonner` toasts as the flags pages do. The page joins the visual and axe matrix.

### 3. CLI (`cli/`, module `github.com/mmhy2003/MegooFlags/cli`)
| Package | Purpose |
|---|---|
| `client` (public) | Typed client for the management API on `net/http`: Bearer token, cursor pagination, `*client.Error{Status, Code, Message, Details}`, retry of GETs on 429 and 5xx |
| `cmd/megooflags` | The binary; commands built with cobra |
| `internal/config` | URL, token, project and output format from flags, then `MEGOOFLAGS_URL`, `MEGOOFLAGS_API_TOKEN`, `MEGOOFLAGS_PROJECT`, then `~/.config/megooflags/config.yaml` (mode 0600, written by `login`) |
| `internal/flagfiles` | The YAML types, strict loading with file and line in errors, and canonical writing (fixed key order, sorted files) |
| `internal/sync` | `Snapshot` (server state), `Plan` (ordered changes between files and server) and `Apply` |
| `internal/importers/{unleash,flagsmith,launchdarkly}` | `Fetch` (API or file) and `Convert` to `flagfiles` plus a `Report` |

Commands:
- `login`, `logout`, `whoami`, `version`, `completion`.
- `project`, `env`, `segment`: `list`, `get`, `create`, `update`, `delete`.
- `flag`: `list`, `get`, `create`, `update`, `archive`, `restore`, `delete`, `variation set|remove`, `config get|set -f file`, `on`, `off`, `kill`.
- `promote <flag…|--all> --from staging --to production [--include-state] [--change-requests]`: the copy endpoint; with `--change-requests` it opens config change requests for targets that need approval.
- `evaluate [flag…] --env dev --context ctx.json` or `-c key=value`: value, variant and reason.
- `ruleset pull --env dev -o ruleset.json`: a bootstrap file for the SDKs' offline mode.
- `export`, `apply`, `diff` (flags as code) and `import unleash|flagsmith|launchdarkly`.
- Output is a table, or `-o json|yaml`. Exit codes: 0 done, 1 error, 2 usage, 3 `diff` found drift, 4 `apply` left changes that need approval.

### 4. Flags as code
```
megooflags.yaml        # apiVersion: megooflags/v1, project and its environments
segments/<key>.yaml    # name, description, included, excluded, rules
flags/<key>.yaml       # name, description, type, tags, owner, variations, off_variation,
                       # permanent, archived, and environments.<env>: enabled, targets,
                       # rules, fallthrough, prerequisites
```
- Not in the files: versions, timestamps, lifecycle state, schedules, running progressive rollouts, SDK keys, integrations, members.
- `export` writes what the token can see. `--env` limits all three commands to chosen environments; an environment missing from a flag's file is not managed.
- `diff` compares meaning, not text, and prints the plan. `apply --dry-run` is the same.
- `apply` order: project and environments, segments, flags and new variations, per-environment configs (with the current `version`, one retry on `version_conflict`), then removed variations, then `--prune`.
- Git sync: `docs/examples/git-sync/` holds three GitHub Actions workflows: `diff` on pull requests, `apply` on merge to main, and `export` on a schedule, which rebuilds a `megooflags/export` branch and opens or updates its pull request.

### 5. Importers
`megooflags import <tool> (--url … --token … | --file …) --project shop --out ./flags [--env-map prod=production] [--strict]` writes the directory and `import-report.md`.

| Tool | Becomes | Reported, not translated |
|---|---|---|
| Unleash | Feature: boolean flag, or a multivariate flag when it has variants. Each strategy: one rule (constraints become clauses, `flexibleRollout` a rollout, `userWithId` targets). Segments: segments. Dependencies: prerequisites | Case-insensitive constraints, IP ranges, random stickiness, custom strategies |
| Flagsmith | Feature: boolean flag, or a flag typed by its value, with multivariate options as variations. Segment overrides: rules with `in_segment` in priority order. Segment rules ALL, ANY, NONE: clauses, several rules, negate | Identity overrides, percentage split, `IS_SET`, modulo, rules nested too deep |
| LaunchDarkly | Almost one to one: variations, targets, rules and clauses, rollouts (same weight unit), fallthrough, off variation, prerequisites. Segments are per environment there: one segment when equal everywhere, else `<key>-<env>` | Context kinds other than user, experiments, big segments, migration flags |

Keys that do not fit MegooFlags' key pattern are lowercased and listed in the report. The report always states that percentage rollouts bucket users differently than the old tool.

### 6. Terraform provider (`terraform/`)
Built on terraform-plugin-framework; its `go.mod` requires the `cli` module with a `replace` to `../cli`. Configured with `url` and `token` (or the same environment variables as the CLI).

| Resource | Notes |
|---|---|
| `megooflags_project` | Includes its environments as a nested list (key, name, description, `approvals_required`) |
| `megooflags_flag` | Details, variations, off variation; changing key or type replaces it; destroy deletes it |
| `megooflags_flag_environment` | Enabled, targets, rules, fallthrough, prerequisites for one environment; destroy turns the flag off there and clears its targeting |
| `megooflags_segment` | Details, included, excluded, rules |
| `megooflags_sdk_key` | `secret` is sensitive; destroy revokes the key |

Data sources: `megooflags_project`, `megooflags_flag`. All resources support `terraform import`. Docs are generated into `terraform/docs/` with tfplugindocs.

### 7. CI, releases and docs
- `ci.yml`: `cli` and `terraform` (gofmt, `go vet`, `go test -race`, build, docs up to date), `cli-e2e` and `terraform-e2e` on the `sdk-e2e-stack` action.
- `release.yml`: tags `cli/vX.Y.Z[-rc.N]` and `terraform/vX.Y.Z[-rc.N]`. Each job checks the tag against the version constant (as the Go SDK job does) and that it is on `main`, runs the tests, builds linux, macOS and Windows for amd64 and arm64, and creates a GitHub release with the archives and `SHA256SUMS`. The release is created with `--latest=false`, so the quick start's "latest release" link keeps pointing at the server. Provider archives use Terraform's names (`terraform-provider-megooflags_X.Y.Z_<os>_<arch>.zip`).
- Docs: `docs/cli.md`, `docs/flags-as-code.md`, `docs/importers.md`, `docs/integrations.md` (payload, signature check examples), `cli/README.md`, `terraform/README.md` (filesystem mirror install). README layout, AGENTS.md sections 1, 9 and 10, `.gitignore` and CHANGELOG are updated.

## Data and interfaces

**New endpoints**
| Endpoint | Permission |
|---|---|
| `GET /api/projects/{project}/environments/{env}/ruleset` (with `ETag`) | `project.read`, environment visible |
| `GET`, `POST /api/projects/{project}/integrations` | `project.manage` |
| `GET`, `PATCH`, `DELETE …/integrations/{id}` | `project.manage` |
| `POST …/integrations/{id}/rotate-secret` (webhook only) | `project.manage` |
| `POST …/integrations/{id}/test`: sends a `ping` now and returns the delivery | `project.manage` |
| `GET …/integrations/{id}/deliveries` (`cursor`, `limit`, newest first) | `project.manage` |
| `POST …/integrations/{id}/deliveries/{delivery}/redeliver` | `project.manage` |

Create and rotate return the signing secret (`mf_whsec_` plus 32 characters) once. The URL is write-only; reads return a masked `url_hint`. Each mutating route writes an audit entry (`integration.*`), which is in no event group.

**Tables**
- `integration`: project, `kind` (`webhook`, `slack`, `teams`), name, `url_encrypted` and `secret_encrypted` (`SecretBox`), `url_hint`, `event_groups`, `all_environments`, `enabled`, last delivery time and result.
- `integration_environment`: the chosen environments (rows go when an environment is deleted).
- `integration_outbox`: audit entry id and project.
- `integration_delivery`: integration, audit entry (empty for tests), event id and type, payload, `status` (`pending`, `delivered`, `failed`), attempts, next attempt time, last status code or error, first 2 KB of the response, duration.

**Event groups** (the event `type` is the audit action)
| Group | Actions |
|---|---|
| Flags | `flag.create`, `.update`, `.archive`, `.restore`, `.delete`, `.variation.*`, `.lifecycle.update` |
| Targeting and state | `flag.config.update`, `.toggle`, `.kill`, `.copy`, `.schedule.*`, `.rollout.*` |
| Segments | `segment.create`, `.update`, `.config.update`, `.delete` |
| Change requests | `change_request.create`, `.approve`, `.reject`, `.apply`, `.fail`, `.cancel` |

An event with an environment is sent only to integrations that include it; an event without one is always sent.

**Webhook request**
```json
{"id": "evt_…", "type": "flag.toggle", "occurred_at": "2026-11-15T14:02:10Z",
 "project": "shop", "environment": "production",
 "resource": {"type": "flag", "key": "new-checkout"},
 "actor": {"kind": "user", "label": "mona@example.com"},
 "before": {…}, "after": {…}, "url": "https://flags.example.com/projects/shop/flags/new-checkout?env=production"}
```
- `before` and `after` are the audit snapshots, already redacted. `url` is null without `MEGOOFLAGS_PUBLIC_URL`.
- Headers: `X-MegooFlags-Event`, `X-MegooFlags-Delivery`, and `X-MegooFlags-Signature: t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>">`.
- Slack receives `{text, blocks}` at its incoming webhook URL; Teams receives an Adaptive Card message at its workflow URL. Both carry one sentence (who did what to which flag, where) and a link.

**Settings** (in `settings.py`, `docker-compose.yml` and `.env.example`): `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE` (false), `MEGOOFLAGS_WEBHOOK_TIMEOUT_SECONDS` (10), `MEGOOFLAGS_WEBHOOK_RETENTION_DAYS` (7). New metric `megooflags_integration_deliveries_total{kind, outcome}`.

## Error handling and edge cases
- **Delivery:** any 2xx is success. Network errors, timeouts, 408, 429 and 5xx are retried: 6 attempts in all, after 30 s, 2 min, 10 min, 1 h and 6 h. Other 4xx fail at once. "Send again" queues a new delivery with the same event id.
- **At least once:** a leader change in mid-send can repeat a delivery; receivers dedupe on `id`. Notifications leave within about 10 s of the change.
- **Address check:** `https` only (`http` only with the allow-private setting). The host is resolved, every address must be public, and the connection goes to the checked address, so a DNS change between check and connect cannot redirect it. No redirects, no proxy settings from the environment, response read capped at 64 KB. Checked on save and again on every send. A Slack URL must be on `hooks.slack.com`.
- **Secrets:** the URL and signing secret are encrypted at rest, never returned after creation, and never written to the audit log or to logs.
- **Deleted things:** deleting a project or integration removes its deliveries. An outbox row whose project has no matching integration is dropped.
- **Rollout steps** send two events (`flag.rollout.step` and `flag.config.update`), as the audit log records two entries.
- **CLI:** API errors print the `code` and message; `forbidden` names the missing permission. The token is never printed and is read from a prompt or the environment, not from an argument.
- **`apply`:** the whole directory is validated before the first write (unknown fields, missing variation or segment, weights not adding to 100000). It continues past a failed flag, lists every failure and exits 1. A flag's prerequisites are created before its config.
- **Sync while a change request is open:** the server still differs from Git, so the export job proposes a pull request that would undo the Git change. It disappears by itself once the request is approved; the docs explain this.
- **Importers:** nothing untranslatable is dropped silently; it goes into the report, and `--strict` exits 1 when the report is not empty. Key collisions after lowercasing get a suffix and a report line.
- **Terraform:** a 404 on read removes the resource from state; `version_conflict` is retried once; `approval_required` is an error that says to lower the approval setting or use change requests outside Terraform.

## Testing (TDD)
- **API (pytest):**
  - Ruleset endpoint: body equals the SDK one, ETag, permissions, hidden environment 404.
  - Integrations: CRUD, permissions, masked URL, secret shown once, rotation, audit samples in `test_audit_coverage`.
  - Outbox: written with the change, absent after a rollback, absent for other actions.
  - Fan-out: groups, environments, paused integrations.
  - Worker with `httpx.MockTransport`: signature, retry schedule, 4xx against 5xx, test send, redeliver, retention, leader only.
  - Address check: private, loopback, link-local, IPv4-mapped IPv6, changed DNS answer, redirect.
  - Slack and Teams bodies for each event type. Migration check and OpenAPI drift.
- **Web:** Vitest for `lib/integrations`. Playwright `integrations.spec.ts`: the test starts a local HTTP receiver, adds a webhook in the UI, copies the secret, toggles a flag, and checks the received request's signature and the delivery log; then a failing receiver, "Send again", edit, pause, delete, Slack and Teams forms, a Viewer without the menu item, and the phone layout at 344 px. The page joins the matrix at all six widths in both themes. The e2e API runs with `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true`.
- **CLI unit tests:** client against `httptest`, config precedence, YAML load and canonical write round trip, plan ordering, each importer against recorded files with golden YAML and report.
- **CLI end to end** (`cli/e2e`, build tag `e2e`, compose stack): the built binary does flag CRUD, on, off, kill, promote (direct and by change request), evaluate, and `ruleset pull`, whose file must validate against `spec/ruleset.schema.json` and start the Go SDK offline. Then export to a directory, `apply` it to a second project, `diff` exits 0, change a flag through the API, `diff` exits 3. Each importer's recorded file is converted, applied to the stack and checked with `evaluate`.
- **Terraform:** unit tests for the model mapping; acceptance tests (terraform-plugin-testing) against the compose stack: create, update, import and destroy for every resource, and the approval error.
- **Workflows:** `actionlint`; `scripts/tests` for any script change.

## Out of scope
- Terraform Registry and OpenTofu Registry publishing, GPG signing, a Homebrew tap, a CLI Docker image.
- A local flag server in the CLI; a server-side Git integration; CI templates for systems other than GitHub Actions.
- Automatic pausing of failing integrations, custom webhook headers, email notifications, Slack or Teams apps with buttons.
- Importing identity overrides, experiments, users, roles and audit history from other tools.
- A Terraform resource for integrations, roles, teams or tokens.
- Code references (D-6) and the observability integrations (D-7).
- A server version bump: the CHANGELOG `[Unreleased]` entries are included, the release is a maintainer decision.