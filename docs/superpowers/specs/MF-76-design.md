# MF-76 — Core data model and management REST API

## Goal
Give MegooFlags its data: projects, environments, flags with typed variations, reusable segments and per-environment targeting (allow/deny lists, rules, percentage rollouts, prerequisites). Expose all of it through a management REST API under `/api/*` that covers every UI action, with the spec exported to `api/openapi.json` and checked in CI.

## Decisions
- **Auth: none for now** (your answer). `/api/*` is open until the governance task adds users, RBAC and tokens. Every router depends on one `get_actor()` dependency that returns `None` today, so the governance task can plug in real auth in one place. The CHANGELOG warns that the management API is unauthenticated in this release. Rejected: an admin token from an env var, and an explicit insecure opt-in.
- **Targeting storage: fully normalized tables** (your answer). Targets, rules, clauses, rollout weights, prerequisites and segment references each get their own table, linked by real foreign keys. Rejected: a typed JSONB document, and a JSONB + link-table hybrid. The one exception is a clause's list of literal values (`values jsonb`, a JSON array of scalars). Those values are data, not references, so they don't need their own table.
- **Editing targeting: whole-config PUT plus small actions** (your answer).
  - One `PUT` replaces a flag's whole config in one environment, in one transaction.
  - It is guarded by an integer `version`. A stale version returns 409.
  - Small endpoints handle the frequent one-click actions: toggle, kill switch and copy.
  - Rejected: fine-grained sub-resources, and a list of patch operations.
- **Archive, then hard delete** (your answer).
  - Archive hides a flag from default lists. It stays restorable.
  - `DELETE` works only on an archived flag and removes it for good. The key can then be reused.
  - Archive and delete are both refused (409) while another flag uses it as a prerequisite.
  - Rejected: archive and delete being independent, and soft delete.
- **Keys in URLs.** Projects, environments, flags, variations and segments are addressed by a stable key. Keys match `^[a-z0-9][a-z0-9._-]{0,99}$` and can't change after creation (SDKs reference flag keys). Primary keys are UUIDv4.
- **Owner is free text** (an email or team name). There is no users table yet. The governance task can move it to a user reference using expand/contract.
- **The flag type is fixed after creation.** Every variation value is validated against it.
- **The off variation is set per flag**, not per environment. The PRD asks for one default (off) variation per flag.

## Architecture
The API is a FastAPI app (`api/src/megooflags_api`) and follows the patterns from MF-74 and MF-75. New pieces:
- `db.py`: adds an `async_sessionmaker` on `app.state` and a `get_session()` dependency (one transaction per request).
- `models/`: SQLAlchemy 2 typed models, one module per area: `project.py`, `flag.py`, `targeting.py`, `segment.py`. They share `Base` plus a `TimestampMixin`.
- `schemas/`: Pydantic v2 request and response models (`extra="forbid"`).
- `services/`: domain logic with no HTTP in it:
  - checking variation values against the flag type
  - checking rollout weight sums
  - checking that references stay inside the project (variations belong to the flag; segments and prerequisite flags belong to the same project)
  - detecting prerequisite cycles
  - replacing the targeting config
  - copying config between environments
- `routers/`: `projects.py`, `environments.py`, `flags.py`, `flag_environments.py`, `segments.py`, all included under the `/api` prefix in `main.py`. Caddy already routes `/api/*` to the API.
- `errors.py`: one error body, `{"code": "...", "message": "...", "details": ...}`, for 404, 409 and 422. Its codes include `not_found`, `key_conflict`, `version_conflict`, `in_use`, `not_archived` and `invalid_reference`.
- Alembic revision `0002_core_model` with a full `downgrade()`, including dropping the enum types.

## Data model (PostgreSQL)
All tables have a UUID `id`, plus `created_at`/`updated_at` where useful. FKs to a parent use `ON DELETE CASCADE`. Cross-references (a variation, a segment or a prerequisite flag) use `RESTRICT`, so the database refuses to delete something still in use.

| Table | Columns (main) | Constraints / indexes |
|---|---|---|
| `project` | key, name, description | unique `key` |
| `environment` | project_id, key, name, description, sort_order | unique `(project_id, key)` |
| `flag` | project_id, key, name, description, type enum[boolean,string,number,json], tags `text[]`, owner, off_variation_id, archived_at | unique `(project_id, key)`; btree `(project_id, archived_at, key)` for paging; GIN on `tags`; btree `(project_id, owner)` |
| `variation` | flag_id, key, name, description, value `jsonb`, sort_order | unique `(flag_id, key)` |
| `flag_environment` | flag_id, environment_id, enabled bool (default false), version int, fallthrough_variation_id null, fallthrough_bucket_by null | unique `(flag_id, environment_id)`; index on `environment_id` |
| `flag_target` | flag_environment_id, variation_id, context_key | unique `(flag_environment_id, context_key)` (a key appears in one list only) |
| `flag_rule` | flag_environment_id, position, description, variation_id null, bucket_by null | unique `(flag_environment_id, position)` |
| `rollout_weight` | flag_rule_id null, flag_environment_id null (fallthrough), variation_id, weight int 0..100000 | CHECK exactly one owner; unique `(owner, variation_id)` |
| `clause` | flag_rule_id null, segment_rule_id null, position, attribute, operator enum, values jsonb, negate bool | CHECK exactly one owner |
| `clause_segment` | clause_id, segment_id (RESTRICT) | PK `(clause_id, segment_id)`; used by the `in_segment` operator |
| `flag_prerequisite` | flag_environment_id, prerequisite_flag_id (RESTRICT), variation_id (RESTRICT), position | unique `(flag_environment_id, prerequisite_flag_id)` |
| `segment` | project_id, key, name, description | unique `(project_id, key)` |
| `segment_target` | segment_id, context_key, kind enum[included, excluded] | unique `(segment_id, context_key)` |
| `segment_rule` | segment_id, position, description | unique `(segment_id, position)` |

**Semantics**
- **Rule outcome:** a rule (and the fallthrough) serves either one fixed variation or a percentage rollout.
- **Rollout weights:**
  - A rollout is a set of `rollout_weight` rows. Weights are in thousandths of a percent and must add up to 100000.
  - `bucket_by` sets the bucketing attribute (T-5). It defaults to `key`.
  - The bucketing algorithm itself belongs to the evaluation-engine task.
- **Clause operators (T-3):** `in`, `not_in`, `contains`, `starts_with`, `ends_with`, `matches` (regex, compiled when saved to validate it), `lt`, `lte`, `gt`, `gte`, `semver_eq`, `semver_lt`, `semver_gt`, `before`, `after`, `in_segment`.
  - `equals` is `in` with one value.
  - Values are checked for each operator: numbers for numeric operators, valid semver for semver operators, RFC 3339 dates for date operators.
- **Allow/deny lists (T-2):** a target list maps context keys to a variation. For boolean flags, the UI's allow list is the `true` variation and its deny list is `false`.
- **Boolean flags** always get exactly the variations `true` and `false`, with `false` as the off variation. Other types need at least one variation, and the off variation is one of them.
- **Rows created automatically:**
  - Creating a flag creates a disabled `flag_environment` row in every environment of the project.
  - Creating an environment fills its rows for every flag with one `INSERT … SELECT`. This stays cheap at 10k flags.
- **Scale (10k flags per project):**
  - Lists use keyset pagination on `key` (`limit` defaults to 50, max 500, with a `cursor`).
  - The flag list loads only flag columns and the per-environment `enabled` summary. It never loads targeting.

## Interfaces (all JSON, under `/api`)
- **Projects:** `GET/POST /projects`, `GET/PATCH/DELETE /projects/{project}`.
  - `POST` takes an optional `environments` list. If it's missing, it creates `development`, `staging` and `production`.
  - `DELETE` cascades.
- **Environments:** `GET/POST /projects/{p}/environments`, `GET/PATCH/DELETE /projects/{p}/environments/{env}`.
- **Flags:**
  - `GET /projects/{p}/flags` takes `q` (key or name), `tag` (repeatable), `owner`, `archived` (default false), `type`, `cursor` and `limit`.
  - `POST /projects/{p}/flags` takes the variations and the off variation key in its body.
  - `GET/PATCH/DELETE /projects/{p}/flags/{flag}`. `PATCH` covers name, description, tags, owner and off variation.
  - `POST …/flags/{flag}/archive`, `POST …/flags/{flag}/restore`.
- **Variations:** `POST /…/flags/{flag}/variations`, `PATCH/DELETE /…/flags/{flag}/variations/{variation}`.
  - Delete is refused with `in_use` while the variation is the off variation or is used by targets, rules, rollouts or prerequisites.
  - Boolean flag variations can't be added or deleted.
- **Per-environment config:**
  - `GET /…/flags/{flag}/environments` returns a summary per environment.
  - `GET /…/flags/{flag}/environments/{env}` returns the full config: `enabled`, `version`, `targets[]`, `rules[]` with `clauses[]` and rollout, `fallthrough`, `prerequisites[]`.
  - `PUT /…/flags/{flag}/environments/{env}` takes the full config plus `version`. It returns 409 `version_conflict` if the version is stale. On success it replaces all child rows in one transaction and bumps `version`.
  - Every reference in the body uses keys (variation, segment, prerequisite flag), and the service resolves them to ids.
- **Toggle:** `POST /…/flags/{flag}/environments/{env}/toggle` with `{enabled, version?}`.
- **Kill switch (F-6):** `POST /…/flags/{flag}/environments/{env}/kill` sets `enabled=false` without needing a version, so it always wins. It bumps `version` and returns the new state. Pushing the change to SDKs belongs to the SDK delivery task.
- **Copy (F-9):** `POST /…/flags/{flag}/copy` with `{from, to: [env…], include_enabled: false}`. It copies targets, rules, rollouts and prerequisites in one transaction and bumps each target environment's version. By default it doesn't copy the on/off state, so prod isn't switched on by accident.
- **Segments (T-4):** `GET/POST /projects/{p}/segments`, `GET/PUT/PATCH/DELETE /projects/{p}/segments/{segment}`.
  - `PUT` replaces included/excluded keys and rules, like the flag config.
  - `DELETE` returns 409 `in_use` with the flags that reference the segment.
  - Segment rules can't use `in_segment`, so segments never nest.

The OpenAPI spec is regenerated into `api/openapi.json` and the web client into `web/lib/api/schema.d.ts`. CI already regenerates both and fails on any drift, so this task commits both files.

## Error handling and edge cases
- **Duplicate key:** 409 `key_conflict`. The service maps the unique-violation error from Postgres, so concurrent creates are covered too.
- **Unknown references:**
  - A key in the URL that doesn't exist returns 404.
  - Unknown keys inside a body (variation, segment, prerequisite flag) return 422 `invalid_reference` with the JSON path.
  - A prerequisite flag from another project counts as unknown.
- **Prerequisites:** a self-reference, or a cycle through the prerequisite graph in that environment, returns 422. An archived prerequisite flag returns 422.
- **Weights:** a sum other than 100000, or a variation listed twice, returns 422.
- **Positions:** taken from the array order in the PUT body, never sent by the client.
- **Delete:** a flag that isn't archived returns 409 `not_archived`, and a flag that is a prerequisite returns 409 `in_use`. The DB-level `RESTRICT` FKs are a safety net, and their violations map to the same `in_use` error.
- **Kill switch:** idempotent. Killing a flag that is already off still returns 200 with its current state.

## Testing
- pytest against Postgres, the same way CI already runs it. A session fixture runs `alembic upgrade head`, and each test runs on cleaned tables.
- CRUD tests for every entity: projects, environments, flags, variations, segments, and per-environment config. Each covers create, read, list, update and delete, plus the 404, 409 and 422 paths above.
- Behaviour tests:
  - creating a flag creates a disabled row in every environment, and adding an environment backfills existing flags
  - optimistic-version conflict
  - the kill switch wins over a stale version
  - copy between environments, with and without `include_enabled`
  - archive, restore and delete rules
  - segment and prerequisite in-use blocks
  - cycle detection
  - value and operator validation for each flag type
- Scale test: seed 10k flags in one project. Check that paging the flag list and filtering by tag issue a fixed number of SQL statements (no N+1). Check that adding an environment finishes within a generous time bound.
- Migration tests: `test_single_head` expects `0002`, and `check_migrations.sh` already walks upgrade, downgrade and upgrade.
- The existing `test_openapi` and CI drift checks cover the generated spec and client.

## Out of scope
- Authentication, users, RBAC, API tokens and the audit log (governance task). `owner` stays free text until then.
- The evaluation engine and bucketing algorithm, SDK ruleset and snapshot endpoints, SSE propagation of changes such as the kill switch (SDK delivery and evaluation tasks).
- Lifecycle states and stale detection (F-7), scheduled changes (F-10), progressive rollouts (T-6) and change requests (release-workflow task).
- Web UI screens, multi-tenancy, and a version bump. A CHANGELOG `[Unreleased]` entry is included.
