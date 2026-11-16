# MF-85 implementation plan: developer tooling and integrations

## Goal
Teams can manage MegooFlags without the web UI and hear about changes where they work. This adds a `megooflags` Go CLI (CRUD, promotion, local development, flags as code, importers), a Terraform provider, and webhook, Slack and Teams integrations. It all ships in one pull request into `develop`.

## Architecture
- **API.** One new read-only endpoint returns an environment's ruleset to API tokens. A new `services/integrations/` package works like this:
  - `services/audit.record` and `record_system` write an outbox row in the same transaction as the audit entry.
  - A scheduler job fans each outbox row out into delivery rows.
  - A worker runs only while this process is the scheduler leader. It sends the deliveries through an outbound client that checks addresses.
- **Web.** The project's Integrations page replaces the placeholder (mockup v1).
- **CLI and provider.** `cli/` is a Go module with a public `client` package; `terraform/` reuses it through a `replace ../cli`. Flags as code is a YAML directory, compared and applied by `internal/sync`. The importers turn other tools' exports into the same directory plus a report.

All paths below are relative to the repository root. Commit format: `<type>(api|web|cli|terraform|ci|docs): <summary> (MF-85)`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Every step leaves the build and tests green. Any step that changes API routes or schemas regenerates `api/openapi.json` and `web/lib/api/schema.d.ts` in the same commit (AGENTS.md section 8).

## File map

### API (`api/`)
| File | Responsibility |
|---|---|
| `src/megooflags_api/routers/evaluate.py` (modify) | `GET /api/projects/{project}/environments/{env}/ruleset` |
| `src/megooflags_api/settings.py` (modify) | `webhook_allow_private`, `webhook_timeout_seconds`, `webhook_retention_days` |
| `src/megooflags_api/models/enums.py` (modify) | `IntegrationKind`, `DeliveryStatus`, `EventGroup` |
| `src/megooflags_api/models/integration.py` (new), `models/__init__.py` (modify) | `Integration`, `IntegrationEnvironment`, `IntegrationOutbox`, `IntegrationDelivery` |
| `alembic/versions/0014_integrations.py` (new) | Tables, enums, indexes; working downgrade |
| `src/megooflags_api/services/integrations/__init__.py` (new) | Package marker |
| `…/integrations/events.py` | `GROUP_ACTIONS`, `group_of(action)`, `build_event(...)`, `ping_event(...)` |
| `…/integrations/outbox.py` | `enqueue(session, entry)` called from `services/audit.py` |
| `…/integrations/net.py` | `UnsafeUrl`, `validate_url`, `is_allowed_ip`, `CheckedBackend`, `SafeTransport`, `post_json` |
| `…/integrations/format.py` | `summarise`, `slack_body`, `teams_body`, `render(kind, event)` |
| `…/integrations/crud.py` | Create, list, get, update, delete, rotate; `url_hint`, `new_secret` |
| `…/integrations/fanout.py` | `run_integration_fanout` scheduler job |
| `…/integrations/worker.py` | `DeliveryWorker`, `sign`, `RETRY_DELAYS`, `MAX_ATTEMPTS`, `run_delivery_retention` job |
| `src/megooflags_api/schemas/integrations.py` (new) | Pydantic in/out models |
| `src/megooflags_api/routers/integrations.py` (new), `main.py` (modify) | Routes and wiring |
| `src/megooflags_api/services/scheduler.py` (modify) | `default_jobs(settings)` adds fan-out and retention; `Scheduler(settings=…)` |
| `src/megooflags_api/metrics.py` (modify) | `INTEGRATION_DELIVERIES` counter |
| `tests/test_ruleset_endpoint.py`, `tests/test_integrations_net.py`, `tests/test_integrations_outbox.py`, `tests/test_integrations_format.py`, `tests/test_integrations_api.py`, `tests/test_integrations_fanout.py`, `tests/test_integrations_worker.py` (new) | Tests |
| `tests/test_audit_coverage.py`, `tests/test_migrations.py`, `tests/helpers.py` (modify) | Audit samples, head `0014`, `make_scheduler` passes settings |
| `deploy/docker-compose.yml`, `deploy/.env.example` (modify) | The three new settings |

### Web (`web/`)
| File | Responsibility |
|---|---|
| `lib/integrations/groups.ts`, `status.ts`, `form.ts` (new) | Labels, status, draft validation and API mapping |
| `tests/unit/integrations-groups.test.ts`, `integrations-status.test.ts`, `integrations-form.test.ts` (new) | Vitest |
| `components/integrations/integration-list.tsx`, `integration-form.tsx`, `delivery-log.tsx`, `secret-reveal.tsx` (new) | UI |
| `app/(app)/projects/[project]/integrations/page.tsx` (modify) | Renders `<IntegrationList project=… />` |
| `e2e/integrations.spec.ts` (new), `e2e/support/receiver.ts` (new), `e2e/matrix.spec.ts` (modify) and snapshots | Playwright |

### CLI (`cli/`, module `github.com/mmhy2003/MegooFlags/cli`, `go 1.26.0`)
| Path | Responsibility |
|---|---|
| `go.mod`, `go.sum`, `README.md`, `LICENSE` (copy of the root one) | Module |
| `internal/buildinfo/version.go` | `const Version = "0.1.0"` (the release job checks the tag against it) |
| `client/` (public) | `client.go`, `errors.go`, `types.go`, `projects.go`, `environments.go`, `segments.go`, `flags.go`, `configs.go`, `evaluate.go`, `sdkkeys.go` and `*_test.go` |
| `internal/config/` | Resolution order: flags, then env, then file; `Load`, `Save`, `DefaultPath` |
| `internal/flagfiles/` | YAML types, strict `Load`, `Validate`, canonical `Write` |
| `internal/sync/` (package `sync`) | `Snapshot`, `Diff` (into a `Plan`), `Apply` |
| `internal/importers/` | `report.go` (`Report`, `Options`, `NormalizeKey`, `ParseEnvMap`) |
| `internal/importers/{unleash,flagsmith,launchdarkly}/` | `fetch.go`, `convert.go`, `testdata/export.json`, `testdata/golden/…` |
| `cmd/megooflags/` (package `main`) | `main.go`, `root.go` (`run(...) int`), `exit.go`, `output.go`, `auth.go`, `project.go`, `env.go`, `segment.go`, `flag.go`, `promote.go`, `evaluate.go`, `ruleset.go`, `sync.go`, `import.go` and tests |
| `e2e/` (separate module `github.com/mmhy2003/MegooFlags/cli/e2e`, with `replace` to `..` and `../../sdks/go`) | `e2e_test.go` (build tag `e2e`); keeps the Go SDK out of the published CLI module |

### Terraform (`terraform/`, module `github.com/mmhy2003/MegooFlags/terraform`)
| Path | Responsibility |
|---|---|
| `go.mod` (`require github.com/mmhy2003/MegooFlags/cli v0.0.0` and `replace … => ../cli`; `tool github.com/hashicorp/terraform-plugin-docs/cmd/tfplugindocs`) | Module |
| `main.go` | `providerserver.Serve(…, Address: "registry.terraform.io/mmhy2003/megooflags")` |
| `internal/provider/version.go` | `const Version = "0.1.0"` |
| `internal/provider/provider.go`, `project_resource.go`, `project_data_source.go`, `flag_resource.go`, `flag_data_source.go`, `flag_environment_resource.go`, `segment_resource.go`, `sdk_key_resource.go`, `convert.go` (model mapping), `errors.go` | Provider |
| `internal/provider/*_test.go` | Unit tests; `*_acc_test.go` acceptance tests with `TF_ACC=1` |
| `examples/…`, `templates/…`, `docs/` | tfplugindocs inputs and generated docs |
| `README.md`, `LICENSE` | Filesystem mirror install |

### CI, release, docs
| Path | Responsibility |
|---|---|
| `.github/workflows/ci.yml` | Jobs `cli`, `cli-e2e`, `terraform`, `terraform-e2e`; `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE` in `web-e2e` |
| `.github/workflows/release.yml` | Tags `cli/v*` and `terraform/v*`; jobs `cli-release` and `terraform-release` |
| `scripts/go_release.py`, `scripts/tests/test_go_release.py` | Cross-builds archives and writes `SHA256SUMS` |
| `docs/cli.md`, `docs/flags-as-code.md`, `docs/importers.md`, `docs/integrations.md` | User docs |
| `docs/examples/git-sync/megooflags-diff.yml`, `megooflags-apply.yml`, `megooflags-export.yml` | Git sync workflows to copy |
| `README.md`, `AGENTS.md`, `.gitignore`, `CHANGELOG.md` | Updated |

## Shared contracts

### A. Integration models (`models/integration.py`)
```python
class Integration(Base):            # __tablename__ = "integration"; eager_defaults
    id = uuid_pk()
    project_id: UUID  FK project.id ON DELETE CASCADE, index
    kind: IntegrationKind            pg_enum(IntegrationKind, "integration_kind")  # webhook | slack | teams
    name: String(200)
    url_encrypted: Text              # SecretBox
    url_hint: String(300)
    secret_encrypted: Text | None    # webhook only
    event_groups: ARRAY(String(32))  # EventGroup values
    all_environments: bool  server_default true
    enabled: bool           server_default true
    created_at, updated_at: DateTime(tz) server_default now()  (updated_at onupdate now())
    last_delivery_at: DateTime(tz) | None
    last_delivery_ok: bool | None
class IntegrationEnvironment(Base):  # "integration_environment"; PK (integration_id, environment_id), both FK ON DELETE CASCADE
class IntegrationOutbox(Base):       # "integration_outbox"
    id: BigInteger PK; audit_entry_id: BigInteger; project_id: UUID (no FK, like audit_entry); created_at
class IntegrationDelivery(Base):     # "integration_delivery"
    id = uuid_pk(); integration_id FK integration.id ON DELETE CASCADE, index
    audit_entry_id: BigInteger | None; event_id: String(64); event_type: String(100)
    payload: JSONB                   # the event (contract C); the body is rendered per kind when sent
    status: DeliveryStatus           pg_enum(DeliveryStatus, "integration_delivery_status")  # pending|delivered|failed
    attempts: int default 0; next_attempt_at: DateTime(tz) | None
    last_status_code: int | None; last_error: Text | None; response_body: Text | None  # first 2048 chars
    duration_ms: int | None; created_at (index); delivered_at | None
    Index("ix_integration_delivery_due", "status", "next_attempt_at")
```
`EventGroup` values are `flags`, `targeting`, `segments` and `change_requests`. The UI labels them Flags, Targeting and state, Segments, Change requests.

### B. Event groups (`events.py`)
```python
GROUP_ACTIONS: dict[EventGroup, tuple[str, ...]]   # exact actions, or prefixes ending in "."
FLAGS: flag.create flag.update flag.archive flag.restore flag.delete "flag.variation." flag.lifecycle.update
TARGETING: flag.config.update flag.toggle flag.kill flag.copy "flag.schedule." "flag.rollout."
SEGMENTS: segment.create segment.update segment.config.update segment.delete
CHANGE_REQUESTS: change_request.create .approve .reject .apply .fail .cancel   # not .comment
def group_of(action: str) -> EventGroup | None
```

### C. Event payload (`build_event`) and request
```json
{"id": "evt_<audit entry id>", "type": "<action>", "occurred_at": "<ISO 8601 Z>",
 "project": "shop", "environment": "production" | null,
 "resource": {"type": "flag", "key": "new-checkout"},
 "actor": {"kind": "user", "label": "mona@example.com"},
 "before": {...} | null, "after": {...} | null, "url": "<public url>" | null}
```
- `url`:
  - flag, not deleted: `{public}/projects/{p}/flags/{key}`, plus `?env={env}` when the event has an environment;
  - flag deleted: `{public}/projects/{p}/flags`;
  - segment: `{public}/projects/{p}/segments`;
  - change request: `{public}/projects/{p}/change-requests`;
  - null when `MEGOOFLAGS_PUBLIC_URL` is unset.
- Ping: `ping_event(project_key, integration_name)` gives `{"id": "evt_ping_<uuid hex>", "type": "ping", "resource": {"type": "integration", "key": name}, "environment": null, "before": null, "after": null, "actor": {"kind": "system", "label": "MegooFlags"}, …}`.
- Headers on every send:
  - `Content-Type: application/json`, `User-Agent: MegooFlags-Webhook/<__version__>`, `X-MegooFlags-Event: <type>`, `X-MegooFlags-Delivery: <delivery id>`;
  - webhooks only: `X-MegooFlags-Signature: t=<unix>,v1=<hex hmac_sha256(secret, f"{t}.".encode() + body)>`.

### D. Integration API schemas (`schemas/integrations.py`)
```python
class IntegrationCreate(ApiModel): kind: IntegrationKind; name: Name; url: str = Field(min_length=8, max_length=2000)
    event_groups: list[EventGroup] = Field(min_length=1); all_environments: bool = True
    environments: list[Key] = []; enabled: bool = True
class IntegrationUpdate(ApiModel): name: Name|None=None; url: str|None=None; event_groups: list[EventGroup]|None=None
    all_environments: bool|None=None; environments: list[Key]|None=None; enabled: bool|None=None
class IntegrationOut(ApiModel): id: UUID; kind; name; url_hint: str; event_groups: list[EventGroup]
    all_environments: bool; environments: list[str]; enabled: bool
    last_delivery_at: datetime|None; last_delivery_ok: bool|None; created_at; updated_at
class IntegrationCreated(IntegrationOut): secret: str | None = Field(description="Webhook signing secret. Shown only once.")
class IntegrationSecret(ApiModel): secret: str
class DeliveryOut(ApiModel): id: UUID; event_id: str; event_type: str; status: DeliveryStatus; attempts: int
    next_attempt_at; last_status_code: int|None; last_error: str|None; response_body: str|None
    duration_ms: int|None; created_at; delivered_at; request_body: JsonValue   # render(kind, payload)
```
- `url_hint(url)` is `f"{scheme}://{host}/…{path[-4:]}"`, or `f"{scheme}://{host}"` when the path is `/` or empty.
- Secret: `"mf_whsec_" + secrets.token_hex(16)`.
- Audit actions: `integration.create`, `.update`, `.delete`, `.rotate_secret`, `.test`, `.redeliver` (resource type `integration`, key = id). The snapshot is `IntegrationOut` (no URL, no secret).

### E. Outbound safety (`net.py`)
```python
class UnsafeUrl(ValueError)
def validate_url(url: str, kind: IntegrationKind, *, allow_private: bool) -> None
    # https only (http only if allow_private); host required; no userinfo; slack: host == "hooks.slack.com"
def is_allowed_ip(ip: IPv4Address | IPv6Address, *, allow_private: bool) -> bool
    # unwraps ip.ipv4_mapped / 6to4; public = ip.is_global and not ip.is_multicast
    # allow_private: everything except unspecified and multicast
Resolver = Callable[[str, int], Awaitable[list[str]]]   # default: loop.getaddrinfo(host, port, type=SOCK_STREAM)
class CheckedBackend(httpcore.AsyncNetworkBackend):  # wraps httpcore.AnyIOBackend
    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # resolve; every address must pass is_allowed_ip, else UnsafeUrl; connect to the first checked IP
class SafeTransport(httpx.AsyncHTTPTransport):  # trust_env=False; self._pool = httpcore.AsyncConnectionPool(ssl_context=httpx.create_ssl_context(), network_backend=CheckedBackend(...))
@dataclass class PostResult: status: int | None; body: str; duration_ms: int; error: str | None
async def post_json(url, body: bytes, headers, *, allow_private, timeout, transport: httpx.AsyncBaseTransport | None = None) -> PostResult
    # follow_redirects=False; reads at most 65536 bytes (aiter_bytes), keeps the first 2048 chars
```
TLS SNI and `Host` keep the original hostname, because httpcore passes `server_hostname` separately to `start_tls`.

### F. Delivery rules (`worker.py`)
- `RETRY_DELAYS = (30 s, 2 min, 10 min, 1 h, 6 h)`; `MAX_ATTEMPTS = 6`.
- `retryable(status, error)`: True for a network error, a timeout, 408, 429 or 5xx. Any 2xx means delivered. Anything else, 3xx included, fails at once.
- After a failed attempt number `n`: if it is retryable and `n < 6`, the delivery stays pending with `next_attempt_at = now + RETRY_DELAYS[n-1]`. Otherwise its status becomes `failed` and `next_attempt_at` becomes None.
- Each attempt updates `integration.last_delivery_at` and `last_delivery_ok`, and increments `INTEGRATION_DELIVERIES.labels(kind, outcome)`, where outcome is one of `delivered`, `retry` or `failed`.

### G. Go client (`cli/client`)
```go
type Client struct { BaseURL, Token, UserAgent string; HTTP *http.Client; MaxRetries int; Sleep func(time.Duration) }
func New(baseURL, token string, opts ...Option) *Client       // Options: WithHTTPClient, WithUserAgent, WithMaxRetries
type Error struct { Status int; Code, Message string; Details any }  // from {"code","message","details"}
func (e *Error) Error() string                                // "<code>: <message> (HTTP <status>)"
func IsCode(err error, code string) bool; func IsNotFound(err error) bool
func (c *Client) Do(ctx context.Context, method, path string, query url.Values, in, out any) error
// GETs retry on 429/5xx/network errors: up to MaxRetries (default 3), honours Retry-After, else 0.5s,1s,2s
func ListAll[T any](ctx context.Context, c *Client, path string, query url.Values) ([]T, error) // limit=500, follows next_cursor
```
Methods. Each takes `ctx` first; the types in `types.go` mirror the API's JSON names.

| Area | Methods |
|---|---|
| Projects | `ListProjects`, `GetProject(p)`, `CreateProject(ProjectCreate)`, `UpdateProject(p, ProjectUpdate)`, `DeleteProject(p)` |
| Environments | `ListEnvironments(p)`, `GetEnvironment(p,e)`, `CreateEnvironment(p, EnvironmentCreate)`, `UpdateEnvironment(p,e, EnvironmentUpdate)`, `DeleteEnvironment(p,e)` |
| Segments | `ListSegments(p)`, `GetSegment(p,s)`, `CreateSegment(p, SegmentCreate)`, `UpdateSegment(p,s, SegmentUpdate)` (PATCH), `PutSegmentConfig(p,s, SegmentConfig)` (PUT), `DeleteSegment(p,s)` |
| Flags | `ListFlags(p, ListFlagsOptions{Archived bool; Query string; Tags []string})`, `GetFlag(p,f)`, `CreateFlag(p, FlagCreate)`, `UpdateFlag(p,f, FlagUpdate)`, `SetLifecycle(p,f, permanent bool)`, `ArchiveFlag`, `RestoreFlag`, `DeleteFlag`, `CreateVariation(p,f, VariationIn)`, `UpdateVariation(p,f,v, VariationUpdate)`, `DeleteVariation(p,f,v)` |
| Configs | `ListFlagStates(p,f) []FlagEnvironmentState`, `GetFlagConfig(p,f,e) FlagConfig`, `PutFlagConfig(p,f,e, FlagConfigIn)`, `Toggle(p,f,e, enabled bool, version *int)`, `Kill(p,f,e)`, `CopyFlag(p,f, CopyIn) []FlagEnvironmentState` |
| Change requests | `CreateChangeRequest(p, ChangeRequestCreate) ChangeRequest` |
| Evaluation | `Evaluate(p,e, EvaluateIn) EvaluateOut`, `Ruleset(p,e, ifNoneMatch string) (body []byte, etag string, notModified bool, err error)` |
| Keys and identity | `ListSdkKeys`, `CreateSdkKey(p,e, SdkKeyCreate) SdkKeyCreated`, `RevokeSdkKey(p,e,id)`, `Me() Me` (GET `/api/auth/me`) |

The targeting types `Target`, `Rule`, `Clause`, `Serve`, `Rollout`, `Weight`, `Prerequisite`, `SegmentRule` and `Variation` carry both `json` and `yaml` tags with the API's snake_case names, so that `flagfiles` reuses them. Clause `values` is `[]any`. `Serve` is `{Variation *string; Rollout *Rollout}`, and each field has `omitempty`.

### H. Flags-as-code files (`internal/flagfiles`)
```yaml
# megooflags.yaml
apiVersion: megooflags/v1
project: {key: shop, name: Shop, description: ""}
environments:
  - {key: development, name: Development, description: "", approvals_required: 0}
# segments/beta.yaml   (file name = key)
name: Beta
description: ""
included: [u1]
excluded: []
rules: [{description: "", clauses: [{attribute: email, operator: ends_with, values: ["@example.com"], negate: false}]}]
# flags/new-checkout.yaml
name: New checkout
description: ""
type: boolean
tags: [checkout]
owner: mona@example.com      # omitted when null
variations: [{key: "true", name: "", description: "", value: true}, {key: "false", name: "", description: "", value: false}]
off_variation: "false"
permanent: false
archived: false
environments:
  production:
    enabled: true
    targets: [{variation: "true", values: [u1]}]
    rules: [{description: "", clauses: [...], variation: "true"}]   # or rollout: {bucket_by: key, weights: [{variation: "true", weight: 40000}, ...]}
    fallthrough: {variation: "false"}
    prerequisites: [{flag: other, variation: "true"}]
```
```go
type Dir struct { Project ProjectFile; Segments map[string]Segment; Flags map[string]Flag }
type ProjectFile struct { APIVersion string `yaml:"apiVersion"`; Project ProjectMeta `yaml:"project"`; Environments []Environment `yaml:"environments"` }
type Flag struct { Key string `yaml:"-"`; Name, Description, Type string; Tags []string; Owner *string `yaml:"owner,omitempty"`
  Variations []client.Variation; OffVariation string `yaml:"off_variation"`; Permanent, Archived bool; Environments map[string]FlagEnv }
type FlagEnv struct { Enabled bool; Targets []client.Target; Rules []client.Rule; Fallthrough client.Serve; Prerequisites []client.Prerequisite }
type Segment struct { Key string `yaml:"-"`; Name, Description string; Included, Excluded []string; Rules []client.SegmentRule }
func Load(dir string) (*Dir, error)            // yaml.v3 Decoder.KnownFields(true); errors "flags/x.yaml:12: field foo not found"
func Validate(d *Dir, serverSegments []string) []error
func Write(dir string, d *Dir) error           // writes megooflags.yaml, segments/*.yaml, flags/*.yaml; removes stale *.yaml there
```
YAML library: `go.yaml.in/yaml/v3`. Canonical writing does the following:
- struct field order is preserved, with an indent of 2;
- map keys under `environments` are sorted;
- tags, included and excluded are sorted, and so are the values of clauses that use the `in_segment` operator;
- every other list keeps its order, because order carries meaning;
- every file ends with a newline.

### I. Sync (`internal/sync`)
```go
type Op string     // "create" | "update" | "archive" | "restore" | "delete"
type Kind string   // "project" | "environment" | "segment" | "segment_config" | "flag" | "lifecycle" | "variation" | "config"
type Change struct { Op Op; Kind Kind; Key, Env, Variation, Summary string
  Project *flagfiles.ProjectMeta; Environment *flagfiles.Environment; Segment *flagfiles.Segment
  Flag *flagfiles.Flag; VariationValue *client.Variation; Config *flagfiles.FlagEnv }
type Plan struct { Changes []Change }   // already in apply order
type Options struct { Envs []string; Prune bool }
func Snapshot(ctx, c *client.Client, project string, envs []string) (*flagfiles.Dir, error)
func Diff(files, server *flagfiles.Dir, opts Options) Plan
type ApplyOptions struct { DryRun, ChangeRequests bool; Out io.Writer }
type Result struct { Applied []Change; NeedsApproval []Change; ChangeRequests []string; Failures []Failure }
type Failure struct { Change Change; Err error }
func Apply(ctx, c *client.Client, project string, plan Plan, opts ApplyOptions) Result
func (p Plan) Print(w io.Writer)   // "+ flag new-checkout", "~ config new-checkout [production]: rules", "- segment beta"
```
Apply order (from the design):
1. the project and its environments;
2. segment creates and segment details, then segment configs;
3. flag creates, details, lifecycle, archive and restore;
4. new and changed variations;
5. per-environment configs;
6. removed variations;
7. with `--prune`, flags missing from the files are archived and segments missing from the files are deleted.

### J. Importers (`internal/importers`)
```go
type ReportLine struct { Resource, Key, Env, Message string }
type Report struct { Tool string; Lines []ReportLine }
func (r *Report) Add(resource, key, env, format string, args ...any)
func (r Report) Empty() bool
func (r Report) Markdown() string   // title, the bucketing notice (always), then a table of lines sorted by resource,key,env
type Options struct { Project string; EnvMap map[string]string }
func ParseEnvMap(pairs []string) (map[string]string, error)         // "prod=production"
func NormalizeKey(raw string, used map[string]bool, r *Report, resource string) string
// lowercase; characters outside [a-z0-9._-] become "-"; leading non-alnum stripped; max 100; collisions get "-2", "-3"; every change gets a report line
type Source struct { URL, Token, File, Project string; HTTP *http.Client }
// each tool package: func Fetch(ctx, Source) (*Export, error); func Convert(*Export, importers.Options) (*flagfiles.Dir, importers.Report)
```
Bucketing notice, written verbatim: "Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently from <tool>, so users can land in a different bucket than before."

## Steps

### 1. API: ruleset endpoint for API tokens
Files: `api/src/megooflags_api/routers/evaluate.py`, a new `api/tests/test_ruleset_endpoint.py`, `api/openapi.json`, `web/lib/api/schema.d.ts`, `CHANGELOG.md`.

Write the failing tests first, using the helpers `create_project`, `create_flag`, `create_sdk_key`, `seed_user`, `seed_binding`, `sign_in`, `bearer` and `assert_error`:
- `test_body_and_etag_match_sdk_ruleset`: GET `/api/projects/demo/environments/production/ruleset` with the admin client returns 200, `application/json`, and the same bytes and `ETag` as `/sdk/v1/ruleset` with a server key of that environment.
- `test_if_none_match_304`.
- `test_viewer_can_read`: an org viewer gets 200.
- `test_hidden_environment_404`: a user bound only to `development` gets 404 `not_found` for `production`.
- `test_unknown_project_404`.
- `test_reflects_writes_at_once`: toggle a flag, and the ETag changes on the next GET.

Run `cd api && uv run pytest tests/test_ruleset_endpoint.py -q`. It fails with 404 (no route).

Implementation in `evaluate.py`:
```python
Read = Annotated[Authz, Depends(require(Permission.PROJECT_READ))]
@router.get("/projects/{project}/environments/{env}/ruleset", response_class=Response, responses={200: {"content": {"application/json": {}}, "description": "The ruleset (spec/ruleset.schema.json), as /sdk/v1/ruleset returns it."}, 304: {"description": "The ruleset matches `If-None-Match`."}})
async def get_ruleset(session: SessionDep, authz: Read, request: Request, project: str, env: str, if_none_match: Annotated[str | None, Header()] = None) -> Response:
    """The environment's ruleset for an SDK bootstrap file (`megooflags ruleset pull`)."""
    entry = await request.app.state.ruleset_cache.get_entry(session, EnvRef.of(authz.environment))  # max_age=0
    if etag_matches(if_none_match, entry.etag): return Response(status_code=304, headers={"ETag": entry.etag})
    return Response(entry.body, media_type="application/json", headers={"ETag": entry.etag, "Cache-Control": "no-cache"})
```
Run the tests again, then `uv run pytest tests/test_route_permissions.py tests/test_audit_coverage.py tests/test_openapi.py -q` (a GET needs no audit sample). Regenerate the spec and client (AGENTS.md section 8). CHANGELOG under Unreleased, Added: "`GET /api/projects/{project}/environments/{env}/ruleset` returns an environment's ruleset to API tokens with `project.read`, for SDK bootstrap files."

Commit: `feat(api): ruleset download for API tokens (MF-85)`.

### 2. API: webhook settings and outbound address check
Files:
- `settings.py`: add `webhook_allow_private: bool = False`, `webhook_timeout_seconds: float = Field(10, gt=0, le=60)` and `webhook_retention_days: int = Field(7, ge=1)`;
- `services/integrations/__init__.py` and `services/integrations/net.py` (contract E), with `IntegrationKind` added to `models/enums.py`;
- `api/pyproject.toml`: add `httpcore>=1.0` explicitly with `uv add httpcore`, and commit `uv.lock`;
- `deploy/docker-compose.yml`: add `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE: ${MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE:-false}` and the timeout and retention variables, styled like `MEGOOFLAGS_PERSIST_CONTEXT`;
- `deploy/.env.example`: a comment block for each of the three variables.

Write the failing tests first in `tests/test_integrations_net.py`, with no database:
- `validate_url` accepts `https://example.com/x`. It refuses `http://` without allow-private, userinfo, a missing host, a Slack URL that is not on `hooks.slack.com`, and `ftp://`.
- `is_allowed_ip` refuses 10.0.0.1, 172.16.0.1, 192.168.1.1, 127.0.0.1, ::1, 169.254.169.254, fe80::1, ::ffff:127.0.0.1, 0.0.0.0, 100.64.0.1 and 224.0.0.1. It allows 93.184.216.34 and 2606:2800:220:1::1. With allow-private it allows 127.0.0.1 and still refuses 0.0.0.0.
- `CheckedBackend` with a fake resolver returning `["93.184.216.34", "10.0.0.5"]` raises `UnsafeUrl`, because every address must be allowed.
- A resolver that answers public at validation and private at connect still fails at connect: `CheckedBackend.connect_tcp` re-resolves, so `post_json` returns `error` starting with "unsafe address".
- A redirect is not followed: with `httpx.MockTransport`, a 302 comes back as `PostResult(status=302)` after one request.
- A response body over 64 KB is cut, and only 2048 chars are kept.

Run `uv run pytest tests/test_integrations_net.py -q`. It fails with ImportError.

Implement `net.py` as in contract E. Pass `transport` to `httpx.AsyncClient(transport=…, follow_redirects=False, timeout=timeout, trust_env=False)`. `post_json` catches `httpx.TimeoutException` ("timeout"), `httpx.HTTPError` and `UnsafeUrl` (as `str(exc)`) into `PostResult.error`, and never raises.

Run the tests, then `uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy`. CHANGELOG: nothing yet; the settings are announced with integrations in step 6.

Commit: `feat(api): webhook settings and outbound address check (MF-85)`.

### 3. API: integration models and migration 0014
Files:
- `models/enums.py`: add `DeliveryStatus` and `EventGroup`;
- `models/integration.py` (contract A), and export the four classes from `models/__init__.py`;
- `alembic/versions/0014_integrations.py` (revision `"0014"`, down_revision `"0013"`, styled like 0013). It creates the enums `integration_kind` and `integration_delivery_status`, the four tables and their indexes. `downgrade()` drops the tables in reverse order, then runs `DROP TYPE` for both enums;
- `tests/test_migrations.py`: `test_single_head` expects `["0014"]`.

Tests first:
- update `test_single_head`, which fails with `["0013"]`;
- `tests/test_models_migration.py` already compares the metadata with the migrated schema, so run it after writing the models but before the migration, and see it fail.

Run `uv run pytest tests/test_migrations.py tests/test_models_migration.py -q`. Implement, run again, then `bash scripts/check_migrations.sh` (it upgrades, downgrades and upgrades again). Also add a `test_project_delete_cascades_integrations` in a new `tests/test_integrations_api.py`. It inserts an `Integration` and a delivery directly with the sessionmaker, deletes the project through the API, and asserts that both rows are gone. It also checks that deleting an environment removes its `integration_environment` rows.

Commit: `feat(api): integration tables (MF-85)`.

### 4. API: event groups, event payload and outbox
Files:
- `services/integrations/events.py` (contracts B and C);
- `services/integrations/outbox.py`: `async def enqueue(session, entry: AuditEntry) -> None` adds an `IntegrationOutbox(audit_entry_id=entry.id, project_id=entry.project_id)` when `entry.project_id is not None and group_of(entry.action) is not None`;
- `services/audit.py`: call `await outbox.enqueue(session, entry)` after the flush in both `record` and `record_system`. Import inside the function body if a cycle appears.

Tests first, in `tests/test_integrations_outbox.py`:
- `group_of` is parametrised over every action in contract B, plus `flag.variation.create`, `flag.schedule.run`, `flag.rollout.step` and None for `change_request.comment`, `integration.create`, `project.update` and `sdk_key.create`.
- `test_toggle_writes_one_outbox_row` uses the API.
- `test_rolled_back_change_writes_nothing`: with a session, call `audit.record_system(...)` for `flag.toggle`, then roll back; the outbox is empty.
- `test_org_actions_write_nothing`: create an API token; the outbox is empty.
- `test_scheduler_action_writes_row`: `record_system` for `flag.schedule.run` with a project id.
- `test_build_event_shape`: it builds an event from a real audit entry (actor, before, after redacted, environment key) with `public_url=None` (url None), and with `"https://flags.example.com"` (the exact URLs of contract C for flag, deleted flag, segment and change request).

Signature: `build_event(entry: AuditEntry, project_key: str, environment_key: str | None, public_url: str | None) -> dict[str, Any]`.

Run `uv run pytest tests/test_integrations_outbox.py tests/test_audit_record.py -q`: the outbox tests fail, the audit tests pass. Implement and run again. Then run the full `uv run pytest -q`, because every audited write now adds at most one insert. If a query-count test in `tests/test_scale.py` or elsewhere counts statements on a flag write, raise its bound by exactly one and add a comment saying it is the integration outbox.

Commit: `feat(api): integration outbox written with the audit entry (MF-85)`.

### 5. API: Slack and Teams message formatting
File: `services/integrations/format.py`. All functions are pure.
- `summarise(event) -> str`.
- `slack_body(event) -> dict`: `{"text": s, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": s}}]}`. When `url` is set, add a block `{"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{url}|Open in MegooFlags>"}]}`.
- `teams_body(event) -> dict`: `{"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": [{"type": "TextBlock", "text": s, "wrap": True}], "actions": [{"type": "Action.OpenUrl", "title": "Open in MegooFlags", "url": url}] if url else []}}]}`.
- `render(kind, event) -> dict`: the event itself for webhooks.

Escape Slack `&`, `<` and `>` in user-supplied text.

Sentences take the form `{actor} {verb} {resource phrase}{ in env}{ (project)}`. The verb table covers:
- flag.create, created; update, updated; archive, archived; restore, restored; delete, deleted; variation.create, update and delete, "added, changed or removed a variation of"; lifecycle.update, "changed the lifecycle of";
- config.update, "changed the targeting of"; toggle, "turned on" or "turned off" (from `after.enabled`); kill, "killed"; copy, "copied the targeting of"; schedule.create, "scheduled a change to"; schedule.run, "ran a scheduled change to"; schedule.cancel, "cancelled a scheduled change to"; schedule.fail, "failed a scheduled change to"; rollout.start, step, pause, resume, complete, abort, fail and cancel, "{started|advanced|paused|resumed|completed|aborted|failed|cancelled} the rollout of";
- segment create, update, config.update and delete;
- change_request create, approve, reject, apply, fail and cancel ("opened", "approved", "rejected", "applied", "could not apply" and "cancelled" a change request for);
- ping, "This is a test notification from MegooFlags for project {p}.".

An action missing from the table falls back to "{actor} did {type} on {resource type} {key}".

Example: "mona@example.com turned on flag new-checkout in production (shop)".

Tests first, in `tests/test_integrations_format.py`: parametrised over every action in contract B, plus `flag.toggle` with `enabled` false and ping. Assert the exact summary for each, the Slack JSON structure with and without url, the Teams card structure, and the escaping.

Run `uv run pytest tests/test_integrations_format.py -q`. It fails, then passes after you implement.

Commit: `feat(api): Slack and Teams message formatting (MF-85)`.

### 6. API: integrations CRUD and secret rotation
Files:
- `schemas/integrations.py` (contract D);
- `services/integrations/crud.py`;
- `routers/integrations.py`, with `Manage = Annotated[Authz, Depends(require(Permission.PROJECT_MANAGE))]`;
- `main.py`: include the router in the `/api` module loop;
- `tests/test_integrations_api.py`, `tests/test_audit_coverage.py`, the spec and client, and `CHANGELOG.md`.

Routes (prefix `/projects/{project}/integrations`):

| Route | Result |
|---|---|
| `GET` | `list[IntegrationOut]`, by name |
| `POST` | 201 `IntegrationCreated` |
| `GET /{id}` | `IntegrationOut` |
| `PATCH /{id}` | `IntegrationOut` |
| `DELETE /{id}` | 204 |
| `POST /{id}/rotate-secret` | `IntegrationSecret` |

Behaviour:
- `id` is a UUID; an unknown id, or one from another project, is 404 `not_found("integration", id)`.
- Validation:
  - the URL goes through `validate_url(url, kind, allow_private=settings.webhook_allow_private)`, and `UnsafeUrl` becomes 422 `validation_error(msg, "url")`;
  - host addresses are also checked on save, by resolving them with `loop.getaddrinfo` and running `is_allowed_ip` on each (the same message);
  - an unknown or hidden environment key is 422 at `environments`;
  - `all_environments=false` with an empty `environments` list is 422.
- Rotating anything other than a webhook is 422 `validation_error("only webhooks have a signing secret", "kind")`.
- The URL and secret are encrypted with `SecretBoxDep` and never appear in a response after creation, in an audit entry or in a log line.
- Every mutation calls `audit.record("integration.<x>", "integration", str(id), project_id=…, before=…, after=…)`.

Tests first:
- CRUD round trip;
- the secret starts with `mf_whsec_`, is 41 chars long and appears only in the create and rotate responses (GET and list have no `secret` key);
- `url_hint` masking;
- the URL is not in `audit_entries(app)` JSON;
- the Slack host check;
- refusal of a private address (`https://127.0.0.1/x`) and acceptance of it with `create_app(Settings(..., webhook_allow_private=True))`;
- environments validation;
- permissions: an org viewer gets 403 on POST and 403 on GET (`project.manage` is needed for both), and a project with no access gives 404;
- rotation changes the secret.

Add `AUDIT_SAMPLES` entries for POST, PATCH, DELETE and rotate-secret, with a `prepare` that creates a webhook. Use `webhook_allow_private` in the test settings, or the URL `https://example.com/hook` with a monkeypatched resolver.

Run `uv run pytest tests/test_integrations_api.py tests/test_audit_coverage.py tests/test_route_permissions.py -q`. It fails, then passes after you implement. Regenerate the spec and client. CHANGELOG, Added: "Webhooks, Slack and Microsoft Teams notifications per project (Integrations page, `project.manage`), with the settings `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE`, `MEGOOFLAGS_WEBHOOK_TIMEOUT_SECONDS` and `MEGOOFLAGS_WEBHOOK_RETENTION_DAYS`."

Commit: `feat(api): integrations API (MF-85)`.

### 7. API: fan-out job
Files:
- `services/integrations/fanout.py`: `async def run_integration_fanout(sessionmaker, now, *, public_url: str | None) -> JobResult`;
- `services/scheduler.py`: `default_jobs(settings: Settings | None = None)` adds `Job("integration_fanout", partial(run_integration_fanout, public_url=…))` with every tick;
- `Scheduler.__init__` gets the keyword `settings: Settings | None = None`, passed to `default_jobs` when `jobs is None`; `main.create_app` passes `settings=settings`;
- `tests/helpers.make_scheduler` passes `settings=app.state.settings`.

The job runs in one transaction:
1. Select up to 500 rows with `select(IntegrationOutbox).order_by(id).limit(500).with_for_update(skip_locked=True)`.
2. Load their audit entries with the project and environment keys (one query each, joined with `Project` and `Environment`).
3. Load the enabled integrations of those projects with their environment ids (one query).
4. For each row and each matching integration: the group must be in `event_groups`, and the integration must have `all_environments`, or the event has no environment, or the event's environment must be among the chosen ones. Insert an `IntegrationDelivery(status=pending, next_attempt_at=now, event_id=event["id"], event_type=action, payload=event, audit_entry_id=…)`.
5. Delete the claimed rows, commit, and return `JobResult(ok=<deliveries created>)`.

Rows whose project has no matching integration are simply deleted.

Tests first, in `tests/test_integrations_fanout.py`, driving `make_scheduler(app, FakeClock(...)).tick()` or calling the job directly:
- an integration with only `segments` receives a segment change and not a flag toggle;
- an integration limited to `development` gets nothing for a `production` toggle, and still gets a project-wide event without an environment (e.g. `flag.create`);
- a paused integration gets nothing;
- two integrations both get a delivery with the same `event_id`;
- the outbox is empty afterwards;
- a rollout step produces two deliveries (`flag.rollout.step` and `flag.config.update`) when the targeting group is on: start a rollout and tick until a step;
- the job's statement count is constant in the number of rows: seed 5 and then 50 outbox rows and compare `count_statements` lengths.

Run `uv run pytest tests/test_integrations_fanout.py tests/test_scheduler.py -q`. It fails, then passes after you implement.

Commit: `feat(api): fan out audit events to integrations (MF-85)`.

### 8. API: delivery worker
Files:
- `services/integrations/worker.py`;
- `metrics.py`: `INTEGRATION_DELIVERIES = Counter("megooflags_integration_deliveries_total", "Integration delivery attempts by kind and outcome.", ["kind", "outcome"])`;
- `main.py`: `app.state.delivery_worker = DeliveryWorker(app.state.sessionmaker, settings, app.state.secret_box, is_leader=lambda: app.state.scheduler.is_leader)`, and `background_jobs` appends `app.state.delivery_worker.run()` inside `if scheduler_enabled`.

```python
def sign(secret: str, timestamp: int, body: bytes) -> str  # "t=<ts>,v1=<hex>"
class DeliveryWorker:
    def __init__(self, sessionmaker, settings, secret_box, *, is_leader: Callable[[], bool], transport: httpx.AsyncBaseTransport | None = None, clock=now_utc, poll_seconds: float = 1.0, concurrency: int = 10)
    async def run(self) -> None        # loop: if is_leader(): await self.send_due(); sleep(poll_seconds); log and continue on exceptions
    async def send_due(self) -> int     # claim ≤10 pending with next_attempt_at <= now (FOR UPDATE SKIP LOCKED), set next_attempt_at = now + timeout + 30 s, commit; send concurrently; return count
    async def deliver(self, delivery_id: UUID) -> IntegrationDelivery  # one attempt now, applying contract F; used by send_due and by the test route
```
The body is `json.dumps(render(kind, payload), separators=(",", ":")).encode()`. The URL is decrypted per send. The headers follow contract C. The timeout comes from settings.

Tests first, in `tests/test_integrations_worker.py`, using `httpx.MockTransport` handlers that record requests, with `webhook_allow_private=True` because MockTransport skips the network backend:
- the signature verifies with `hmac` against the secret from create;
- the headers are present;
- a Slack integration gets the `slack_body`;
- 500 then 200: attempts 2, then delivered;
- 500 six times: failed after attempt 6, with the next attempts at +30 s, +2 min, +10 min, +1 h and +6 h (FakeClock);
- 404: failed after 1 attempt;
- 429 and 408 retry;
- a timeout (the handler raises `httpx.ReadTimeout`) retries;
- `last_delivery_ok` and `last_delivery_at` are updated;
- the metric counters increment;
- `send_due` with `is_leader=lambda: False` through `run()` for one loop sends nothing;
- at most 10 deliveries per call.

Run `uv run pytest tests/test_integrations_worker.py tests/test_metrics.py -q`. It fails, then passes after you implement.

Commit: `feat(api): deliver integration events with retries (MF-85)`.

### 9. API: test send, delivery log, redeliver and retention
Routes in `routers/integrations.py`:

| Route | Result |
|---|---|
| `POST /{id}/test` | 201 `DeliveryOut`. Creates a delivery with `ping_event(...)` and `audit_entry_id=None`, calls `request.app.state.delivery_worker.deliver(id)` at once, returns the delivery, audit `integration.test` |
| `GET /{id}/deliveries?cursor&limit` (limit 1 to 100, default 25) | `Page[DeliveryOut]`, newest first. Keyset on `(created_at, id)`; the cursor encodes `f"{created_at.isoformat()}|{id}"` with `encode_cursor` |
| `POST /{id}/deliveries/{delivery}/redeliver` | 201 `DeliveryOut`. Only for `status == failed`, else 409 `invalid_state("only a failed delivery can be sent again")`. Copies `event_id`, `event_type`, `payload` and `audit_entry_id` into a new pending delivery due now, audit `integration.redeliver` |

`DeliveryOut.request_body = render(integration.kind, payload)`.

Retention: `run_delivery_retention(sessionmaker, now, *, days)` deletes non-pending deliveries with `created_at < now - days`. Register it in `default_jobs` as `Job("integration_retention", partial(..., days=settings.webhook_retention_days), every=timedelta(hours=1))`.

Tests first, in `tests/test_integrations_api.py` and `tests/test_integrations_worker.py`:
- test send with a MockTransport set on `app.state.delivery_worker.transport` returns a delivered ping;
- the delivery list order and cursor;
- redeliver of a failed delivery keeps the `event_id` and is pending;
- redeliver of a delivered one gives 409;
- retention removes an 8-day-old delivered row and keeps a pending one;
- the delivery routes need `project.manage`;
- the new POST routes have `AUDIT_SAMPLES`.

Run `uv run pytest tests/test_integrations_api.py tests/test_integrations_worker.py tests/test_audit_coverage.py tests/test_route_permissions.py -q`. It fails, then passes after you implement. Then run the full API check from AGENTS.md section 9 (ruff, format, mypy, pytest, `scripts/check_migrations.sh`) and report the pytest counts. Regenerate the spec and client.

Commit: `feat(api): test sends, delivery log and redelivery (MF-85)`.

### 10. Web: integrations helpers
Files: `web/lib/integrations/groups.ts`, `status.ts` and `form.ts`, plus unit tests in `web/tests/unit/integrations-*.test.ts`.

Types come from `components["schemas"]` (`IntegrationOut`, `IntegrationCreate`, `IntegrationUpdate`, `DeliveryOut`, `EventGroup`, `IntegrationKind`).
- `groups.ts`:
  - `EVENT_GROUPS: {id: EventGroup; label: string; description: string}[]` in the order Flags, Targeting and state, Segments, Change requests, each with a one-line description of its actions;
  - `KIND_LABELS = {webhook: "Webhook", slack: "Slack", teams: "Microsoft Teams"}`;
  - `groupLabel(id)`.
- `status.ts`:
  - `integrationStatus(i): "active" | "failing" | "paused"`: paused when `!enabled`, failing when `last_delivery_ok === false`, else active;
  - `statusPill(s): {status: Status; label}`: active maps to on/"Active", failing to error/"Failing", paused to off/"Paused";
  - `deliveryLabel(d)`: "Delivered", "Retrying (attempt n of 6)" or "Failed";
  - `canSendAgain(d)` is `d.status === "failed"`.
- `form.ts`:
  - `type IntegrationDraft = {kind; name; url; eventGroups: EventGroup[]; allEnvironments: boolean; environments: string[]; enabled: boolean}`;
  - `emptyDraft(kind)` has all four groups and all environments;
  - `draftFrom(i: IntegrationOut)` has url "";
  - `validateDraft(d, editing: boolean): Partial<Record<keyof IntegrationDraft, string>>`:
    - the name is required;
    - the URL is required unless editing;
    - the URL must be `https://` (an `http://localhost` URL is allowed only for dev, so use a server error for that and do not check it here);
    - Slack needs the host `hooks.slack.com`;
    - at least one group is required;
    - environments must not be empty when not all are chosen;
  - `toCreate(d): IntegrationCreate`;
  - `toUpdate(d, original): IntegrationUpdate` contains only the changed fields and leaves out `url` when it is blank.

Tests first, with a table-driven case for each function. Run `cd web && pnpm test`. It fails (module not found), then passes after you implement. Then run `pnpm lint && pnpm format:check && pnpm typecheck`.

Commit: `feat(web): integrations helpers (MF-85)`.

### 11. Web: integrations list, form and one-time secret
Files:
- `components/integrations/integration-list.tsx` (client component);
- `integration-form.tsx`;
- `secret-reveal.tsx`;
- `app/(app)/projects/[project]/integrations/page.tsx`, which replaces `PlaceholderPage` with `<IntegrationList project={decodeURIComponent(project)} />` and keeps the `Suspense` wrapper as `flags/page.tsx` does.

`IntegrationList`:
- It uses `useProjectPermissions(project, null, null)` and `useApiData(project, () => must(api.GET("/api/projects/{project}/integrations", …)))`.
- `PageHeader` has the title "Integrations", `projectTrail`, and a "+ Add integration" button shown when `can(perms, "project.manage")`.
- Failure shows a destructive `Alert` with "Try again". Loading shows a skeleton. With no rows, `EmptyState` says "No integrations yet" with the add button.
- Otherwise it renders a `DataList` with the caption "Integrations" and these columns:
  - name and kind (`key`);
  - status `StatusPill` (`status`);
  - last delivery (`<time>`, `main`);
  - events, as group labels (`extra`);
  - environments, "All" or a list (`extra`);
  - actions (`main`): a `DropdownMenu` labelled "Actions for <name>" with Deliveries, Edit, Send test, Pause/Resume and Delete.

Deliveries, Send test, Pause and Delete are wired in step 12. In this step render only Edit; step 12 adds the rest.

`IntegrationForm` is a `ResponsiveDialog` titled "Add integration" or "Edit integration":
- a type picker (radio group Webhook, Slack, Microsoft Teams), disabled when editing;
- Name;
- URL: on edit the placeholder shows `url_hint` and the help text says "Leave empty to keep the current URL"; Slack's help says "Incoming webhook URL (hooks.slack.com)" and Teams's says "Workflow URL from Microsoft Teams";
- event group checkboxes with descriptions;
- "All environments" or chosen environments: checkboxes from `GET /api/projects/{project}/environments`;
- an Active `Switch`.

Saving calls POST or PATCH through `must`, shows a toast ("Integration added" or "Saved") and reloads. A 422 maps `details` or the path to the field error, otherwise `toast.error(failureText(...))`.

`SecretReveal` is a `ResponsiveDialog` with `alert`, the title "Signing secret", the secret in a monospace `<code>`, `<CopyButton text={secret} label="Copy secret" />`, the text "Copy it now. You won't see it again.", and a "Done" button. It opens after a webhook is created.

Checks: run `pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test`, then open the page against a local API, add a webhook and see the secret once.

Commit: `feat(web): integrations list and form (MF-85)`.

### 12. Web: delivery log, send test, send again, pause and delete
Files: `components/integrations/delivery-log.tsx`, and `integration-list.tsx` (the remaining row actions).

`DeliveryLog({project, integration, open, onOpenChange})` is a `ResponsiveDialog` titled "Deliveries: <name>" that loads `GET …/integrations/{id}/deliveries`, with "Load more" while `next_cursor` is set. Each delivery row shows:
- event type, status label (`deliveryLabel`) and `<time>` created;
- the HTTP status or error, and the duration;
- a "Details" toggle (`aria-expanded`) that reveals `JsonViewer` with `label="Request body"` for `request_body`, and the response body in `<pre>`;
- a "Send again" button when `canSendAgain` is true, which POSTs redeliver, toasts "Queued again" and reloads.

The empty state reads "No deliveries in the last 7 days."

Row actions:
- "Send test" POSTs `/test` and toasts "Test delivered" or "Test failed: <status or error>".
- "Pause" and "Resume" PATCH `enabled`.
- "Delete" uses `ConfirmDialog` with destructive "Delete integration", then DELETE, toast and reload.
- "Deliveries" opens `DeliveryLog`.

Checks: run `pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test`, then try it by hand against a local API run with `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true` and a receiver such as `python3 -m http.server`, whose 501 for POST shows a failing delivery.

Commit: `feat(web): integration delivery log and row actions (MF-85)`.

### 13. Web: Playwright integrations spec, matrix and CI setting
Files:
- `web/e2e/support/receiver.ts`: `startReceiver(status = 200): Promise<{url: string; requests: ReceivedRequest[]; setStatus(n: number): void; close(): Promise<void>}>`, built on `node:http` on port 0, recording method, headers and the raw body;
- `web/e2e/integrations.spec.ts`;
- `web/e2e/matrix.spec.ts`: add `{ name: "integrations", path: "/projects/shop/integrations", mask: true }`, with an integration seeded in the spec's `beforeAll` or in `global-setup.ts` for `shop` so that the matrix shows a row;
- the new snapshots from `pnpm e2e:docker --update-snapshots`;
- `.github/workflows/ci.yml` `web-e2e` env: `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE: "true"`;
- AGENTS.md section 9: add `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true` to the web e2e API command.

Spec cases:
1. Add a webhook to the receiver URL through the UI, read the secret (`getByRole("code")`), and close the dialog.
2. Toggle `new-checkout` in `development` through `adminApi()`.
3. Poll `receiver.requests` for 20 s or less for a `flag.toggle`, then verify `X-MegooFlags-Signature` with `crypto.createHmac("sha256", secret)` over `${t}.${body}`.
4. Open Deliveries and see "Delivered". Expand Details and see the request body.
5. `setStatus(404)` then Send test: a "Failed" row appears, and "Send again" queues another.
6. Edit the name; Pause shows "Paused"; Delete removes the row.
7. The Slack form refuses a URL that is not on `hooks.slack.com`, and the Teams form saves.
8. A Viewer (`VIEWER.storageState`) has no Integrations nav item, and the page shows a not-found or forbidden state.
9. At 344 px: cards, no horizontal scroll (`noHorizontalScroll`), and the dialog is full screen.

Clean up every integration you create in `afterAll`.

Run `cd web && pnpm build && pnpm e2e -- integrations matrix` against the API started as in AGENTS.md section 9 with `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true`. It fails before the snapshots exist. Then run `pnpm e2e:docker --update-snapshots`, and run `pnpm e2e` again until it passes. Then run `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest`.

Commit: `test(web): integrations end-to-end and visual matrix (MF-85)`.

### 14. CLI: module scaffold, config, version and CI job
Files:
- `cli/go.mod` (`module github.com/mmhy2003/MegooFlags/cli`, `go 1.26.0`), with `go get github.com/spf13/cobra go.yaml.in/yaml/v3 golang.org/x/term`;
- `cli/LICENSE`;
- `cli/internal/buildinfo/version.go` (`const Version = "0.1.0"`, with a comment that the tag `cli/v<Version>` must match);
- `cli/internal/config/config.go`;
- `cli/cmd/megooflags/main.go`, `root.go` and `exit.go`;
- `.gitignore`: add `cli/bin/`, `cli/dist/`, `terraform/bin/`, `terraform/dist/`, `.terraform/`, `*.tfstate*` and `.terraform.lock.hcl` under `terraform/internal/`;
- `.github/workflows/ci.yml`: a `cli` job modelled on `sdk-go`, with `working-directory: cli`, `go-version-file: cli/go.mod` and `cache-dependency-path: cli/go.sum`. Its steps are gofmt, `go vet ./...`, `go test -race -count=1 ./...` and `go build -o bin/megooflags ./cmd/megooflags`.

Config contract:
```go
type Values struct { URL, Token, Project, Output string }
type File struct { URL string `yaml:"url,omitempty"`; Token string `yaml:"token,omitempty"`; Project string `yaml:"project,omitempty"`; Output string `yaml:"output,omitempty"` }
func DefaultPath(getenv func(string) string) (string, error)   // $XDG_CONFIG_HOME/megooflags/config.yaml, else ~/.config/megooflags/config.yaml
func Load(path string) (File, error)                            // missing file: zero File, nil
func Save(path string, f File) error                            // dir 0700, file 0600, atomic rename
func Resolve(flags Values, getenv func(string) string, file File) Values  // per field: flag, then MEGOOFLAGS_URL / MEGOOFLAGS_API_TOKEN / MEGOOFLAGS_PROJECT / MEGOOFLAGS_OUTPUT, then file; Output defaults to "table"
```
The root contract is `func run(ctx context.Context, args []string, io IO) int` with `type IO struct { In io.Reader; Out, Err io.Writer; Getenv func(string) string; IsTerminal bool }`.
- Global flags are `--url`, `--project` (`-p`), `-o/--output table|json|yaml` and `--config`. There is no `--token` flag, by design.
- Exit codes: `exitError{code int; err error}`; `ExitOK=0`, `ExitError=1`, `ExitUsage=2` (cobra flag and argument errors), `ExitDrift=3`, `ExitNeedsApproval=4`.
- `version` prints `megooflags <Version>`.
- `completion bash|zsh|fish|powershell` uses cobra's generators.

Tests first: `internal/config/config_test.go` covers precedence, the XDG path, mode 0600 after `Save` (skipped on Windows) and a missing file; `cmd/megooflags/root_test.go` covers `version` output, an unknown command giving exit 2, and `completion bash` containing `megooflags`. Run `cd cli && go test ./...`. It fails, then passes after you implement. Then run `gofmt -l .` (it prints nothing) and `go vet ./...`.

Commit: `feat(cli): module scaffold and configuration (MF-85)`.

### 15. CLI: API client core
Files: `cli/client/client.go`, `errors.go`, `client_test.go`, `errors_test.go`, and `doc.go` (package docs: a stable typed client for the management API, used by the CLI and the Terraform provider).

Implement contract G's core: `New`, the options, `Do` and `ListAll`, and `Error`, `IsCode` and `IsNotFound`.
- `Do` sets `Authorization: Bearer <token>`, `Accept: application/json`, and `Content-Type` when there is a body. User-Agent defaults to `megooflags-go-client`.
- A 204 or a nil `out` skips decoding.
- A non-2xx body is decoded as `{code, message, details}`; when it isn't JSON, `Code` is `http_<status>`.
- A GET retries on network errors, 429 and 5xx up to `MaxRetries`, honouring `Retry-After` seconds, else 0.5 s, 1 s and 2 s through `c.Sleep`, which tests replace. Non-GETs never retry.
- The token never appears in an error string.

Tests first, against `httptest.NewServer`:
- the header and token;
- the decoded error;
- the retry counts (a GET is retried 3 times, a POST never);
- `Retry-After`;
- `ListAll` following `next_cursor` over 3 pages;
- context cancellation;
- the error text has no token.

Run `cd cli && go test ./client/...`. It fails, then passes after you implement.

Commit: `feat(cli): API client core (MF-85)`.

### 16. CLI: API client resources
Files: `cli/client/types.go`, `projects.go`, `environments.go`, `segments.go`, `flags.go`, `configs.go`, `evaluate.go`, `sdkkeys.go` and `resources_test.go`.

Implement every method in contract G with the exact API paths:
- `/api/projects…` and `/environments/{env}`;
- `/flags/{flag}/environments/{env}` (GET and PUT);
- `/toggle`, `/kill`, `/copy`, `/variations/{v}`, `/lifecycle` (PUT `{permanent}`), `/archive`, `/restore`;
- `/change-requests`;
- `/environments/{env}/evaluate`;
- `/environments/{env}/ruleset` (sends `If-None-Match`; 304 gives `notModified=true`);
- `/environments/{env}/sdk-keys` and DELETE `…/{id}`;
- `/api/auth/me`.

Escape path segments with `url.PathEscape`. The types match the API JSON exactly; check them against `api/openapi.json`. `CopyIn` encodes the source as JSON `from`.

Tests first: one table test per method, against an `httptest` mux. Each case asserts the method, path, query and request JSON, and decodes a fixture response. Also test `Ruleset` with 200 and with 304, and JSON round trip of `FlagConfig` using the `Serve` variation or rollout.

Run `cd cli && go test ./client/...`. It fails, then passes after you implement.

Commit: `feat(cli): typed client for the management API (MF-85)`.

### 17. CLI: output formats, login, logout and whoami
Files: `cli/cmd/megooflags/output.go`, `auth.go`, `output_test.go` and `auth_test.go`.
- `output.go`:
  - `render(w, format string, v any, table func(*tabwriter.Writer))`: json is indented with 2 spaces, yaml uses `go.yaml.in/yaml/v3`, and table uses `text/tabwriter` with upper-case headers;
  - `printAPIError(err, w)` prints `error: <code>: <message>`, and for `forbidden` it adds `(missing permission '<perm>')`, which is already in the message from the API;
  - `newClient(values)` errors with exit 1 "no API token: run `megooflags login` or set MEGOOFLAGS_API_TOKEN" and "no URL: …".
- `login --url URL`:
  - reads the token from `MEGOOFLAGS_API_TOKEN` if set;
  - otherwise, when `IsTerminal`, prompts "API token: " with `term.ReadPassword`;
  - otherwise reads one line from stdin;
  - verifies it with `Me`, saves `{url, token}` with `config.Save`, and prints "Logged in to <url> as <label>".
- `logout` removes the token from the file.
- `whoami` prints kind, label and URL. It never prints the token.

Tests first: `login` with stdin `"tok\n"` against `httptest` saves a 0600 file in a temp `XDG_CONFIG_HOME`; a bad token prints `error: unauthorized: …` and exits 1; `whoami -o json`; output never contains the token (grep stdout and stderr).

Run `go test ./cmd/...`. It fails, then passes after you implement.

Commit: `feat(cli): login, whoami and output formats (MF-85)`.

### 18. CLI: project, env and segment commands
Files: `cli/cmd/megooflags/project.go`, `env.go`, `segment.go` and `resources_cmd_test.go`.

| Command | Calls |
|---|---|
| `project list`, `get <key>` | `ListProjects`, `GetProject` |
| `project create <key> --name N [--description D] [--env key:Name …]` | `CreateProject` |
| `project update <key> [--name] [--description] [--stale-after-days]` | `UpdateProject` |
| `project delete <key> --yes` | `DeleteProject` (without `--yes`: exit 2 "refusing to delete without --yes") |
| `env list`, `get <key>` | `-p` is required (from flag, env or config) |
| `env create <key> --name N [--description] [--sort-order]` | `CreateEnvironment` |
| `env update <key> [--name] [--description] [--sort-order] [--approvals-required n]` | `UpdateEnvironment` |
| `env delete <key> --yes` | `DeleteEnvironment` |
| `segment list`, `get <key>` | `get` prints details and config |
| `segment create <key> --name N [--description]` | `CreateSegment` |
| `segment update <key> [--name] [--description] [-f config.yaml]` | `-f` takes a YAML or JSON `SegmentConfig` and calls `PutSegmentConfig` |
| `segment delete <key> --yes` | `DeleteSegment` |

Tables: projects show KEY, NAME, ENVIRONMENTS; environments KEY, NAME, APPROVALS; segments KEY, NAME, UPDATED.

Tests first: each command runs against an `httptest` server that records requests. Assert the request and the output in table, json and yaml; a missing `-p` gives exit 2; delete without `--yes` gives exit 2; a 404 gives exit 1 with `error: not_found:`.

Run `go test ./cmd/...`. It fails, then passes after you implement.

Commit: `feat(cli): project, environment and segment commands (MF-85)`.

### 19. CLI: flag commands
Files: `cli/cmd/megooflags/flag.go` and `flag_test.go`.

| Command | Behaviour |
|---|---|
| `flag list [--archived] [--tag t …] [-q text]` | Table KEY, TYPE, NAME, and a column per environment showing on or off from `environments` |
| `flag get <key> [--env e]` | Details and variations; with `--env`, also the config |
| `flag create <key> --name N --type boolean\|string\|number\|json [--description] [--tag …] [--owner] [--variation key=jsonvalue …] [--off-variation k]` | `CreateFlag` |
| `flag update <key> [--name] [--description] [--tag …] [--owner] [--clear-owner] [--off-variation]` | `UpdateFlag` |
| `flag archive <key>`, `flag restore <key>` | `ArchiveFlag`, `RestoreFlag` |
| `flag delete <key> --yes` | The API requires the flag to be archived; print the API error otherwise |
| `flag variation set <flag> <key> --value JSON [--name] [--description]` | Creates the variation, or updates it when it exists (GET the flag first) |
| `flag variation remove <flag> <key>` | `DeleteVariation` |
| `flag config get <flag> --env e` | Prints the config in yaml by default when `-o` is table |
| `flag config set <flag> --env e -f file` | The file holds `FlagEnv` (contract H) as YAML or JSON. GET the current version, then PUT `FlagConfigIn`. On `version_conflict`, GET again and retry once |
| `flag on`, `flag off <flag> --env e` | `Toggle` |
| `flag kill <flag> --env e` | `Kill` |

A write in an approval-required environment surfaces `error: approval_required: …` with exit 4, and the hint "open a change request in the UI, or use `megooflags apply --change-requests`".

Tests first: every command against `httptest`; `config set` with a 409 `version_conflict` followed by success makes exactly 2 PUTs; `on` with a 409 `approval_required` exits 4.

Run `go test ./cmd/...`. It fails, then passes after you implement.

Commit: `feat(cli): flag commands (MF-85)`.

### 20. CLI: promote, evaluate and ruleset pull
Files: `cli/cmd/megooflags/promote.go`, `evaluate.go`, `ruleset.go` and their tests.

`promote <flag…|--all> --from E1 --to E2[,E3] [--include-state] [--change-requests]`:
1. Load the project's environments; `approvals_required > 0` marks a target that needs approval.
2. For each flag (with `--all`, every live flag), the direct targets go in one `CopyFlag(from, to, include_enabled)`.
3. For targets that need approval:
   - with `--change-requests`: GET the source config and the target's current config, build `FlagConfigIn` (the source's targets, rules, fallthrough and prerequisites; `enabled` from the source when `--include-state`, else from the target; the target's `version`), and `CreateChangeRequest({environment, flag, kind: "config", title: "Promote <flag> from E1 to E2", payload})`. Print its id.
   - without it: print "<flag> → <env>: needs approval" and remember exit 4.
4. Print one line per flag and target. A flag that fails does not stop the others, but sets exit 1. Exit 4 wins only when nothing failed.

`evaluate [flag…] --env e [--context ctx.json] [-c key=value …]`:
- The context comes from the file, merged with `-c` pairs. A value is parsed as JSON when it is valid JSON, else used as a string, so `-c beta=true` gives a bool.
- The default `key` is "cli".
- It calls `Evaluate`. The table shows FLAG, VALUE (JSON), VARIANT, REASON and ERROR.

`ruleset pull --env e -o FILE`:
- `-o -` writes stdout.
- `Ruleset(p, e, "")` writes the body atomically, and prints "wrote <file> (<n> flags)", counting the `flags` map.
- The `-o` flag is local to this command. It shadows the global output flag here; document that.

Tests first:
- promote direct (one copy call);
- promote with production `approvals_required: 1` and no flag (exit 4, no copy to production);
- promote with `--change-requests` (one change request POST with the target version);
- `--all` paging;
- evaluate context merging and parsing;
- ruleset pull writes the exact bytes.

Run `go test ./cmd/...`. It fails, then passes after you implement.

Commit: `feat(cli): promote, evaluate and ruleset pull (MF-85)`.

### 21. CLI: flagfiles YAML format
Files: `cli/internal/flagfiles/types.go`, `load.go`, `validate.go`, `write.go`, `flagfiles_test.go`, and `testdata/valid/…` and `testdata/invalid/…`. This implements contract H.

`Load`:
- reads `megooflags.yaml`, which must have `apiVersion: megooflags/v1`;
- reads `segments/*.yaml` and `flags/*.yaml`; the file name without `.yaml` is the key and must match `^[a-z0-9][a-z0-9._-]{0,99}$`;
- errors name the file and line: decode with `KnownFields(true)` and wrap `yaml.TypeError` and the node line.

`Validate` returns every error, not the first. It checks:
- the environment keys used in flags exist in `megooflags.yaml`;
- variation keys referenced by targets, rules, fallthrough and `off_variation` exist, as do prerequisite flags and their variations;
- `in_segment` values exist in the files or in `serverSegments`;
- rollout weights sum to 100000;
- variations are unique;
- `type` is one of the four types;
- variation values match the type.

`Write` is canonical as contract H describes: create the dirs and delete `*.yaml` files in `flags/` and `segments/` that are no longer in the `Dir`.

Tests first:
- a round trip: `Load(testdata/valid)`, `Write(tmp)`, then the bytes are equal to `testdata/valid`;
- loading the written output again gives a deep-equal `Dir`;
- an unknown field reports `flags/bad.yaml:3`;
- a bad apiVersion;
- each validation rule gives its own error and the errors are collected;
- the writer removes stale files;
- the order of keys and sorted lists is stable.

Run `go test ./internal/flagfiles/...`. It fails, then passes after you implement.

Commit: `feat(cli): flags-as-code file format (MF-85)`.

### 22. CLI: snapshot and export
Files: `cli/internal/sync/snapshot.go`, `snapshot_test.go`; `cli/cmd/megooflags/sync.go` (the `export` command).

`Snapshot(ctx, c, project, envs)` builds a `flagfiles.Dir`:
- the project with its environments;
- every segment, using `GetSegment` for the config;
- every flag, live and archived (`ListFlags` both ways), using `GetFlag` for its details and variations, and `permanent = lifecycle == "permanent"`;
- for each environment in the project (or only those in `envs`), the config from `GetFlagConfig`, mapped to `FlagEnv` with `version` and `updated_at` dropped.

The rule `RuleOut` maps to `client.Rule` without ids. It leaves out versions, timestamps, lifecycle state, schedules, running rollouts, SDK keys and integrations. An environment hidden from the token is simply absent.

`megooflags export --dir DIR [--env e …]`: `Snapshot`, then `flagfiles.Write`, then print "exported <n> flags and <m> segments to DIR".

Tests first: a fake API (an `httptest` mux with the fixtures in `internal/sync/testdata/api/*.json`) gives a `Dir` deep-equal to the expected one; `--env` limits environments; archived flags carry `archived: true`; `export` writes the files (golden directory `internal/sync/testdata/export-golden`, updated with `go test ./internal/sync -update`).

Run `go test ./internal/sync/... ./cmd/...`. It fails, then passes after you implement.

Commit: `feat(cli): export the server state as flags-as-code files (MF-85)`.

### 23. CLI: plan and diff
Files: `cli/internal/sync/plan.go`, `plan_test.go`; and the `diff` command in `cmd/megooflags/sync.go`. This implements contract I's `Diff`, `Plan` and `Print`.

Values are compared by meaning: each component is encoded to JSON, decoded into `any` and compared with `reflect.DeepEqual`, so `1` and `1.0`, and YAML or JSON forms, are equal. Lists that the canonical writer sorts are sorted first.

Changes, in apply order:
1. The project's name or description changed: `update project`.
2. Environments in the files but not on the server: `create environment`. Changed ones: `update environment`. Server-only environments are ignored.
3. Segments: `create segment` (details), `update segment` (details) and `segment_config` (included, excluded, rules).
4. Flags: `create flag`; `update flag` (name, description, tags, owner, off_variation); `lifecycle` when `permanent` differs; `archive` or `restore` when `archived` differs.
   - A flag whose type differs becomes a `Change` with Op `update` that `Apply` will fail with "type cannot change; delete and recreate the flag".
5. `create variation` and `update variation`.
6. `config` for each managed environment, meaning one present in the flag's file and in the `--env` filter when set, whose `FlagEnv` differs. The `Summary` names the differing parts: enabled, targets, rules, fallthrough, prerequisites.
7. `delete variation` for variations missing from the file.
8. With `Prune`: `archive flag` for live server flags missing from the files, and `delete segment` for missing segments.

`megooflags diff --dir DIR [--env e …] [--prune]` loads and validates the files (errors give exit 1), takes a `Snapshot`, prints the plan, and exits 3 when there are changes, else prints "No differences." and exits 0.

Tests first: table tests over pairs of `Dir` values for each change kind and ordering; an environment missing from a flag's file is unmanaged; `1` and `1.0` give no change; prune only with the flag; `diff` exit codes 0 and 3 against the fake API.

Run `go test ./internal/sync/... ./cmd/...`. It fails, then passes after you implement.

Commit: `feat(cli): diff flags-as-code files against the server (MF-85)`.

### 24. CLI: apply
Files: `cli/internal/sync/apply.go`, `apply_test.go`; and the `apply` command in `cmd/megooflags/sync.go`.

`Apply` runs the plan in order.
- `create project` happens only when the project does not exist: `GetProject` 404 leads to `CreateProject` with the environments. Environment `approvals_required` is then set with `UpdateEnvironment`.
- Flag create sends the variations and off variation. Variation create and update use `VariationIn` and `VariationUpdate`.
- Config: GET the current version, then PUT `FlagConfigIn{version, …}`; on `version_conflict`, GET again and retry once.
  - On `approval_required`: with `ChangeRequests`, `CreateChangeRequest(kind "config", title "Apply flags-as-code: <flag> in <env>", payload FlagConfigIn)` and the id is recorded. Without it, the change goes to `NeedsApproval`.
- `DryRun` only prints the plan.
- Any other error is recorded in `Failures`, and Apply continues. A change that depends on a failed flag create (its variations or configs) is skipped and also recorded as a failure.

`megooflags apply --dir DIR [--env e …] [--prune] [--dry-run] [--change-requests]`:
1. Load, then `Validate` with the server's segment keys. Any error prints every error and exits 1 before the first write.
2. Run `Snapshot`, `Diff` and `Apply`.
3. Print each applied change, then "needs approval: …" lines, then "opened change request <id>" lines, then failures.
4. Exit 1 if there are failures, else 4 if anything needs approval, else 0.

Tests first, against a stateful fake API (`internal/sync/fakeapi_test.go`, an in-memory store behind `httptest` that implements the endpoints Apply uses, including the 409s):
- apply to an empty project creates everything, then `Diff` is empty;
- the order is checked: a prerequisite flag is created before the config that references it;
- `version_conflict` is retried once;
- approval with and without `--change-requests` (exit 4);
- `--prune` archives and deletes;
- a failed flag does not stop the others (exit 1);
- validation stops before any request (the fake counts writes, and the count must be 0);
- `--dry-run` makes no writes.

Run `go test ./internal/sync/... ./cmd/...`. It fails, then passes after you implement.

Commit: `feat(cli): apply flags-as-code files (MF-85)`.

### 25. CLI: importer report and LaunchDarkly importer
Files:
- `cli/internal/importers/report.go`, `report_test.go` (contract J);
- `cli/internal/importers/launchdarkly/fetch.go`, `convert.go`, `convert_test.go`, `testdata/export.json`, and `testdata/golden/` (the Dir files plus `import-report.md`).

The `Export` type is `{Environments []Env{Key, Name}; Flags map[string][]Flag; Segments map[string][]Segment}`, with flags and segments keyed by LaunchDarkly environment key and the fields used from the LD API.

`Fetch`:
- the URL defaults to `https://app.launchdarkly.com`;
- headers are `Authorization: <token>` and `LD-API-Version: 20240415`;
- calls: `GET /api/v2/projects/{p}/environments`, then per environment `GET /api/v2/flags/{p}?env={e}&summary=0&limit=100&offset=N` and `GET /api/v2/segments/{p}/{e}?limit=50&offset=N`, following `_links.next` until it is absent;
- with `Source.File` set, it reads the JSON `Export` instead.

`Convert`:
- Variation keys: a slug of the `name`, else `v<index>`, made unique.
- The type: boolean for `kind: boolean`; otherwise string, number or json from the values.
- Fields: `on` maps to enabled; `targets` and `contextTargets` with contextKind user (or empty) map to targets; `rules` map to rules (op names as in the research table).
- `segmentMatch` becomes `in_segment` with mapped keys.
- `rollout.variations` weights are copied unchanged, `bucketBy` defaults to `key`; `fallthrough`, `offVariation` and `prerequisites` map to variation keys.
- Segments: a segment equal in every environment (compared by meaning) becomes one segment. Otherwise each environment gets `<key>-<env>`, and that environment's flags refer to it.
- Reported and not translated:
  - targets, clauses and rules with a contextKind other than user (the rule is skipped);
  - `experimentAllocation` (experiments; the rollout is kept);
  - `unbounded` segments (big segments, skipped);
  - flags with `migrationSettings` or purpose migration (skipped);
  - every key that `NormalizeKey` changed.
- Environment keys go through `EnvMap`, else `NormalizeKey`.

Tests first:
- `report_test.go`: `NormalizeKey` collisions and suffixes, `ParseEnvMap` errors, and `Markdown` always containing the notice;
- `convert_test.go`: `Convert(testdata/export.json)` is written with `flagfiles.Write` and compared to `testdata/golden` (`-update` flag); the result passes `flagfiles.Validate`; `Fetch` against `httptest` follows pagination and sends the headers.

The fixture covers a boolean flag with rules, a multivariate string flag with a rollout and prerequisites, a segment equal in two environments, one segment that differs between them, a non-user context, a big segment and a migration flag.

Run `go test ./internal/importers/...`. It fails, then passes after you implement.

Commit: `feat(cli): LaunchDarkly importer (MF-85)`.

### 26. CLI: Unleash importer
Files: `cli/internal/importers/unleash/fetch.go`, `convert.go`, `convert_test.go`, `testdata/export.json` and `testdata/golden/`.

The `Export` type is `{Environments []string; PerEnv map[string]BatchExport; Segments []Segment}`. `BatchExport` holds the response of `POST /api/admin/features-batch/export`: `features`, `featureStrategies`, `featureEnvironments` and `dependencies`. `Segment` is `{id, name, description, constraints}` from `GET /api/admin/segments`.

`Fetch`:
- header `Authorization: <token>`;
- `GET /api/admin/environments` gives the enabled environments;
- per environment: `POST /api/admin/features-batch/export` with `{"environment": e, "project": Source.Project}`;
- then `GET /api/admin/segments`;
- `--file` reads the `Export` JSON.

`Convert`:
- A feature becomes a boolean flag with variations true and false, off false. When any strategy or environment has variants, it becomes a string flag instead: variations are the variant names (value = `payload.value` when the payload type is string, the name otherwise; a json payload makes a json flag), plus `disabled` (value "") as the off variation, with a report line.
- Per environment, `featureEnvironments.enabled` maps to enabled.
- Strategies are taken in `sortOrder`; disabled ones are reported and skipped. They map as follows:
  - `default` with no constraints and no segments makes the fallthrough serve true (or the variants rollout) and ends processing;
  - `flexibleRollout` becomes a rollout `{bucket_by: stickiness default|userId → key, else the name; weights true = rollout×1000, false = rest}`;
  - `userWithId` becomes targets (comma-split `userIds`) for true;
  - constraints become clauses (operator map below), and `segments` become an `in_segment` clause;
  - the fallthrough is false unless set.
- Constraint operator map:
  - IN → in;
  - NOT_IN → in, negated;
  - STR_ENDS_WITH → ends_with; STR_STARTS_WITH → starts_with; STR_CONTAINS → contains;
  - NUM_EQ → in with numbers; NUM_GT → gt; NUM_GTE → gte; NUM_LT → lt; NUM_LTE → lte;
  - DATE_AFTER → after; DATE_BEFORE → before;
  - SEMVER_EQ → semver_eq; SEMVER_GT → semver_gt; SEMVER_LT → semver_lt;
  - REGEX → matches;
  - `inverted` → negate;
  - `contextName` userId → `key`.
- Reported:
  - `caseInsensitive` (kept case-sensitive);
  - IN_CIDR and `remoteAddress` (IP ranges, skipped);
  - stickiness `random`;
  - SEMVER_GTE and SEMVER_LTE;
  - custom and other strategies (skipped);
  - dependencies with `enabled: false` or more than one variant.
- Dependencies become prerequisites `{flag: parent, variation: "true"}`, or the first listed variant.
- Each segment becomes a segment with one rule made of all its constraints.

Tests first: golden as in step 25, with every row of the design's Unleash table in the fixture; `Fetch` against `httptest`.

Run `go test ./internal/importers/unleash/...`. It fails, then passes after you implement.

Commit: `feat(cli): Unleash importer (MF-85)`.

### 27. CLI: Flagsmith importer
Files: `cli/internal/importers/flagsmith/fetch.go`, `convert.go`, `convert_test.go`, `testdata/export.json` and `testdata/golden/`.

The `Export` type is `{Environments map[string]Document}`, keyed by environment name. A `Document` is Flagsmith's environment document (`feature_states`, `identity_overrides`, `project.segments[]` with `rules` and `feature_states`). The condition field is `property_`.

`Fetch`:
- the URL defaults to `https://api.flagsmith.com`;
- header `Authorization: Api-Key <token>`;
- `GET /api/v1/environments/?project={Source.Project}` (handle both a plain array and `{results, next}`), then per environment `GET /api/v1/environments/{api_key}/document/`;
- `--file` reads the `Export` JSON.

`Convert`:
- A feature with no value and no multivariate options anywhere becomes a boolean flag: `enabled` maps to enabled, and the fallthrough and off variation are true and false.
- Otherwise the flag is typed by its control value (string, number or boolean values become the string, number and boolean types).
  - Variations are `default` (the control value) and `option-<n>` for each multivariate option; the off variation is `default`.
  - The fallthrough is a rollout over `percentage_allocation × 1000`, with the remainder going to `default`, or `default` alone.
- Segment overrides (`project.segments[].feature_states`), ordered by `feature_segment.priority` ascending, become rules with the clause `{operator: in_segment, values: [segkey]}`.
  - They serve the variation whose value equals the override value, adding `segment-<segkey>` when it is new.
  - For boolean flags, an override with `enabled: false` serves false.
- Segment rules:
  - the top-level rule ALL with nested rules is expanded to OR-of-AND form: ALL concatenates clauses, ANY splits into several MegooFlags rules, NONE negates each condition;
  - anything deeper than 2 levels, or an expansion of more than 20 rules, is reported "rules nested too deep" and skipped.
- Condition operator map:
  - EQUAL → in; NOT_EQUAL → in, negated;
  - GREATER_THAN → gt; GREATER_THAN_INCLUSIVE → gte; LESS_THAN → lt; LESS_THAN_INCLUSIVE → lte (numbers parsed);
  - CONTAINS → contains; NOT_CONTAINS → contains, negated;
  - REGEX → matches;
  - IN → in (comma-split);
  - property `identifier` → `key`.
- Reported: IS_SET and IS_NOT_SET, MODULO, PERCENTAGE_SPLIT, and `identity_overrides` (one line per identity).

Tests first: golden as in step 25, with each row of the design's Flagsmith table in the fixture; the nested expansion in a unit test; `Fetch` against `httptest`.

Run `go test ./internal/importers/flagsmith/...`. It fails, then passes after you implement.

Commit: `feat(cli): Flagsmith importer (MF-85)`.

### 28. CLI: import command
Files: `cli/cmd/megooflags/import.go` and `import_test.go`.

`megooflags import unleash|flagsmith|launchdarkly (--url U --token-env VAR | --file F) --project P --out DIR [--source-project X] [--env-map a=b …] [--strict] [--save raw.json]`:
- The token is read from the environment variable named by `--token-env`, which defaults to `UNLEASH_API_TOKEN`, `FLAGSMITH_API_KEY` or `LAUNCHDARKLY_API_TOKEN`. It is never an argument, as with the CLI's own token.
- `--url` defaults are the tools' cloud hosts; Unleash has no default and requires `--url` with the API.
- `--source-project` is the other tool's project: the Unleash project id (default `default`), the Flagsmith project id, or the LaunchDarkly project key (defaults to `--project`).
- `--save` writes the fetched `Export` as JSON, which is the saved-file format `--file` reads.
- The command:
  1. converts and sets the project key to `--project`;
  2. writes the directory with `flagfiles.Write`;
  3. writes `DIR/import-report.md`;
  4. prints "<n> flags, <m> segments; <k> items in import-report.md", and "review the files, then run `megooflags apply --dir DIR`";
  5. exits 1 with `--strict` when the report is not empty.
- It never calls the MegooFlags API.

Tests first: for each tool, `--file testdata/export.json` writes the golden files and the report (reusing the importer testdata through a relative path); `--strict` gives exit 1; `--url` with no token variable exits 1 with a message; `--save` round trip; `--env-map` is applied.

Run `go test ./cmd/...`. It fails, then passes after you implement.

Commit: `feat(cli): import command for Unleash, Flagsmith and LaunchDarkly (MF-85)`.

### 29. CLI: end-to-end tests and cli-e2e CI job
Files:
- `cli/e2e/go.mod` (`module github.com/mmhy2003/MegooFlags/cli/e2e`, requires `github.com/mmhy2003/MegooFlags/cli` and `github.com/mmhy2003/MegooFlags/sdks/go` with `replace` to `..` and `../../sdks/go`, plus `github.com/santhosh-tekuri/jsonschema/v6` for schema validation);
- `cli/e2e/e2e_test.go` (`//go:build e2e`);
- `.github/workflows/ci.yml`: a `cli-e2e` job modelled on `sdk-go-e2e` (`needs: images`, `uses: ./.github/actions/sdk-e2e-stack` with `project: megooflags-cli`, `setup-go` with `cli/go.mod`, `working-directory: cli/e2e`, `run: go test -tags e2e -count=1 -v ./...`, logs on failure, `down -v` always). Also add `go vet -tags e2e ./...` in `cli/e2e` to the `cli` job.

`TestMain` skips when `MEGOOFLAGS_E2E_URL` or `MEGOOFLAGS_API_TOKEN` is unset, builds the binary with `go build -o $TMP/megooflags ../cmd/megooflags`, and runs it with `MEGOOFLAGS_URL` and `MEGOOFLAGS_API_TOKEN` in its environment. Each test creates projects `clie2e-<hex>` and deletes them in `t.Cleanup`.

Tests:
1. `TestFlagLifecycle`: `project create` with development, staging and production; `env update production --approvals-required 1`; `flag create`, `variation set`, `config set`, `on`, `off` and `kill` in staging; `archive`, `restore`, `delete` after archive.
2. `TestPromote`: promote staging to development directly (then `flag config get` matches); promote to production exits 4; with `--change-requests`, a change request exists (`GET /api/projects/{p}/change-requests` through `client`).
3. `TestEvaluateAndRuleset`: `evaluate -c key=u1` gives the expected variant; `ruleset pull` validates against `../../spec/ruleset.schema.json`; `megooflags.NewProvider(megooflags.Config{SDKKey: <a created server key>, BootstrapFile: file, Offline: true})` evaluates the flag with the OpenFeature client to the same value.
4. `TestFlagsAsCode`: export project A, apply to a new project B (`megooflags.yaml` project key rewritten), `diff` for B exits 0, a change through `client.Toggle`, then `diff` exits 3.
5. `TestImporters`: for each tool, `import --file ../internal/importers/<tool>/testdata/export.json --project <new>` and `apply` exit 0 (or 4 never: the fixtures have no approvals), and `evaluate` of one known flag and context matches the value the test expects for that fixture.

Run it locally with the AGENTS.md section 9 stack commands, then `cd cli/e2e && go test -tags e2e -count=1 -v ./...`. Without the environment it skips; with the stack running it passes. Then run actionlint.

Commit: `test(cli): end-to-end tests against the compose stack (MF-85)`.

### 30. Terraform: provider scaffold, project resource and data source, CI job
Files:
- `terraform/go.mod` (`module github.com/mmhy2003/MegooFlags/terraform`, `go 1.26.0`). Run `go get github.com/hashicorp/terraform-plugin-framework github.com/hashicorp/terraform-plugin-testing` and `go get -tool github.com/hashicorp/terraform-plugin-docs/cmd/tfplugindocs`, require the cli module at `v0.0.0` with `replace github.com/mmhy2003/MegooFlags/cli => ../cli`;
- `terraform/main.go`;
- `terraform/LICENSE`;
- `internal/provider/version.go`, `provider.go`, `errors.go`, `project_resource.go`, `project_data_source.go`, `convert.go`, and `provider_test.go` and `project_test.go`;
- `.github/workflows/ci.yml`: a `terraform` job like `cli` (gofmt, `go vet ./...`, `go test -race -count=1 ./...`, `go build -o bin/terraform-provider-megooflags .`), with `cache-dependency-path` set to `terraform/go.sum` and `cli/go.sum`.

The provider:
- has `url` (optional) and `token` (optional, sensitive), falling back to `MEGOOFLAGS_URL` and `MEGOOFLAGS_API_TOKEN`; a missing value gives a diagnostic error;
- `Configure` builds `client.New(url, token, client.WithUserAgent("terraform-provider-megooflags/"+Version))` and shares it as `ResourceData` and `DataSourceData`.

`errors.go`, `diagFromError(err, action string) diag.Diagnostic`:
- for `approval_required`: "<action> needs approval in this environment. Set approvals_required to 0 for environments Terraform manages, or make this change through a change request outside Terraform.";
- other API errors: "<code>: <message>".

`megooflags_project` resource:
- attributes: `key` (required, RequiresReplace), `name` (required), `description` (optional, computed default ""), `stale_after_days` (optional, computed), and `environments` (required list nested: `key`, `name`, `description`, `approvals_required` defaulting to 0);
- Create: `CreateProject` with the environments, then `UpdateEnvironment` for non-zero approvals;
- Read: 404 removes it from state;
- Update: patch the project; environments are matched by key (create the new ones, patch the changed ones, delete the removed ones); `sort_order` = list index;
- Delete: `DeleteProject`;
- `ImportState` uses the key.

The `megooflags_project` data source reads by `key`.

Tests first: unit tests in `convert.go`/`project_test.go` for API to model mapping and environment diffing (create, update and delete sets); `provider_test.go` validates the schema with `provider.New("test")()` through the framework's `providerserver` and checks that configuration from the environment works against `httptest`.

Run `cd terraform && go test ./...`. It fails, then passes after you implement.

Commit: `feat(terraform): provider with the project resource (MF-85)`.

### 31. Terraform: flag resource and data source
Files: `terraform/internal/provider/flag_resource.go`, `flag_data_source.go` and `flag_test.go`.

`megooflags_flag` attributes:
- `project` and `key` (RequiresReplace);
- `type` (RequiresReplace; boolean, string, number or json);
- `name`;
- `description` (optional, computed "");
- `tags` (set of string);
- `owner` (optional);
- `variations`: an optional, computed list nested `{key, name, description, value_json}`, where `value_json` is a JSON string, compared semantically by a custom `jsontypes.Normalized`-style type from `github.com/hashicorp/terraform-plugin-framework-jsontypes`. Boolean flags default to the server's true and false;
- `off_variation` (optional, computed);
- `permanent` (optional, default false).

Create: `CreateFlag`, then `SetLifecycle` when permanent. Update: `UpdateFlag`; variations are diffed by key (create, update, delete, with deletes after the off variation update); lifecycle. Delete: `ArchiveFlag`, then `DeleteFlag`, because the API refuses to delete a live flag. Import ID: `project/key`. A 404 on read removes it from state.

The `megooflags_flag` data source reads `project` and `key` and returns the same attributes plus `archived`.

Tests first: unit tests for the variation diff, the JSON value round trip (`1` and `1.0`), and the import ID parsing ("a/b" valid, "a" invalid).

Run `go test ./...`. It fails, then passes after you implement.

Commit: `feat(terraform): flag resource and data source (MF-85)`.

### 32. Terraform: flag_environment resource
Files: `terraform/internal/provider/flag_environment_resource.go` and `flag_environment_test.go`.

`megooflags_flag_environment` attributes:
- `project`, `flag` and `environment` (all RequiresReplace);
- `enabled` (required);
- `targets`: a list nested `{variation, values list(string)}`;
- `rules`: a list nested `{description, clauses list nested {attribute (default "key"), operator, values_json, negate (default false)}, variation (optional), rollout (optional single nested {bucket_by (default "key"), weights list nested {variation, weight}})}`;
- `fallthrough`: single nested `{variation, rollout}`;
- `prerequisites`: a list nested `{flag, variation}`;
- `version`: computed.

`values_json` holds the clause values as a JSON array (`jsonencode([...])`), so numbers and strings keep their types.

Create and Update: GET the version, PUT `FlagConfigIn`, and retry once on `version_conflict`; `approval_required` uses `diagFromError`. Read: GET; a 404 removes it from state. Delete: GET, then PUT with `enabled=false`, empty targets, rules and prerequisites, and the current fallthrough. Import ID: `project/flag/environment`.

Validators:
- exactly one of `variation` and `rollout` in a rule and in the fallthrough;
- weights sum to 100000 (`ValidateConfig`).

Tests first: model to `FlagConfigIn` and back is lossless, the validators, and import ID parsing.

Run `go test ./...`. It fails, then passes after you implement.

Commit: `feat(terraform): flag_environment resource (MF-85)`.

### 33. Terraform: segment and sdk_key resources
Files: `terraform/internal/provider/segment_resource.go`, `sdk_key_resource.go`, `segment_test.go` and `sdk_key_test.go`.

`megooflags_segment`:
- attributes: `project` and `key` (RequiresReplace), `name`, `description`, `included` and `excluded` (sets of string), and `rules` (a list nested `{description, clauses}` with the same clause shape as step 32);
- Create: `CreateSegment`, then `PutSegmentConfig`;
- Update: `UpdateSegment`, then `PutSegmentConfig`;
- Delete: `DeleteSegment`. An `in_use` error says which flags use it;
- Import ID: `project/key`.

`megooflags_sdk_key`:
- attributes: `project`, `environment`, `name` and `kind` (server or client), all RequiresReplace; computed `id`, `prefix`, `created_at`, and `secret` (sensitive, computed, `UseStateForUnknown`);
- Create: `CreateSdkKey`, keeping `key` as `secret`;
- Read: `ListSdkKeys`, matching on id; a missing or revoked key removes it from state;
- Delete: `RevokeSdkKey`;
- Import ID: `project/environment/id`. After import, `secret` is null; the docs say so.

Tests first: mapping, a revoked key is treated as gone, import parsing.

Run `go test ./...`. It fails, then passes after you implement.

Commit: `feat(terraform): segment and SDK key resources (MF-85)`.

### 34. Terraform: acceptance tests, docs and terraform-e2e job
Files:
- `terraform/internal/provider/acc_test.go` (the shared `testAccProtoV6ProviderFactories` and `testAccPreCheck`, which skips unless `TF_ACC`, `MEGOOFLAGS_E2E_URL` and `MEGOOFLAGS_API_TOKEN` are set; it maps `MEGOOFLAGS_E2E_URL` to `MEGOOFLAGS_URL`);
- `project_acc_test.go`, `flag_acc_test.go`, `flag_environment_acc_test.go`, `segment_acc_test.go`, `sdk_key_acc_test.go` and `data_sources_acc_test.go`;
- `terraform/examples/provider/provider.tf`, `examples/resources/<name>/resource.tf` and `import.sh`, and `examples/data-sources/<name>/data-source.tf`;
- `terraform/templates/index.md.tmpl`, with the filesystem mirror install;
- the generated `terraform/docs/` from `go tool tfplugindocs generate --provider-name megooflags`;
- `.github/workflows/ci.yml`:
  - the `terraform` job adds the step "docs up to date": `go tool tfplugindocs generate --provider-name megooflags && git diff --exit-code -- docs`;
  - a new `terraform-e2e` job (`needs: images`; the `sdk-e2e-stack` action with `project: megooflags-terraform`; `hashicorp/setup-terraform@v3` with `terraform_wrapper: false`; `setup-go`; `TF_ACC=1 go test -count=1 -v -run TestAcc ./...` in `terraform`; logs and down as in `sdk-go-e2e`).

Acceptance cases, using `resource.Test` with random keys `tfacc-<hex>`:
- create, update, `ImportState` with `ImportStateVerify`, and destroy for each resource (sdk_key `ImportStateVerifyIgnore: ["secret"]`);
- `CheckDestroy` asserts a 404 through `client`, and for `flag_environment` that the flag is off with empty targeting;
- `TestAccFlagEnvironment_approvalRequired`: an environment with `approvals_required = 1` gives `ExpectError: regexp.MustCompile("needs approval")`;
- the data sources read what the resources made.

Run `cd terraform && go test ./...` (acceptance tests skip). With the stack running, run `TF_ACC=1 MEGOOFLAGS_E2E_URL=… MEGOOFLAGS_API_TOKEN=… go test -count=1 -run TestAcc ./...`; it fails until the cases are right, then passes. Then run `go tool tfplugindocs generate --provider-name megooflags`, check that `git status` shows only `terraform/docs`, and run actionlint.

Commit: `test(terraform): acceptance tests and generated docs (MF-85)`.

### 35. Release: build script and release jobs for cli and terraform tags
Files: `scripts/go_release.py`, `scripts/tests/test_go_release.py` and `.github/workflows/release.yml`.

`go_release.py --kind cli|terraform --version X.Y.Z[-rc.N] --out dist`:
- `TARGETS = [(linux, amd64), (linux, arm64), (darwin, amd64), (darwin, arm64), (windows, amd64), (windows, arm64)]`;
- it builds with `CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o <tmp>/<bin>`:
  - cli: in `cli/`, `./cmd/megooflags`, binary `megooflags[.exe]`, archives `megooflags_<v>_<os>_<arch>.tar.gz` (a zip for windows) containing the binary, `README.md` and `LICENSE`;
  - terraform: in `terraform/`, `.`, binary `terraform-provider-megooflags_v<v>[.exe]`, archive `terraform-provider-megooflags_<v>_<os>_<arch>.zip`;
- it writes `dist/SHA256SUMS` in `sha256sum` format, sorted by name.

The pure functions `archive_name(kind, version, os, arch)`, `binary_name(...)` and `sha256sums(paths)` are tested. `build` takes an injectable runner, and the tests use a fake runner that writes dummy binaries, then check the archive contents and the sums.

`release.yml`:
- Add the tag patterns `cli/v[0-9]+.[0-9]+.[0-9]+`, `cli/v[0-9]+.[0-9]+.[0-9]+-rc.[0-9]+`, and the same two for `terraform/v`.
- Add two jobs:
  - `cli-release`: `if: startsWith(github.ref_name, 'cli/v')`, `permissions: contents: write`. Steps:
    1. checkout with `fetch-depth: 0`;
    2. the main-branch check `case` block copied from `sdk-tag` (rc tags skip it);
    3. "Tag matches version": `v="${GITHUB_REF_NAME#cli/v}"; grep -q "const Version = \"${v%%-rc.*}\"" cli/internal/buildinfo/version.go`;
    4. `setup-go` with `cli/go.mod`;
    5. `go vet` and `go test -race -count=1 ./...` in `cli`;
    6. `python3 scripts/go_release.py --kind cli --version "$v" --out dist`;
    7. `gh release create "$GITHUB_REF_NAME" dist/* --title "MegooFlags CLI $v" --notes "See CHANGELOG.md and cli/README.md." --latest=false --prerelease` (always a pre-release while 0.x or rc), with `GH_TOKEN: ${{ github.token }}`.
  - `terraform-release`: the same, with `terraform/internal/provider/version.go`, `--kind terraform`, setup-go `terraform/go.mod`, and the title "MegooFlags Terraform provider $v".

Tests first: `uv run --frozen --project api pytest scripts/tests/test_go_release.py -q` fails, then passes after you implement. Then run `uv run --project api ruff check ../scripts`, the same with `ruff format --check`, `python3 scripts/go_release.py --kind cli --version 0.1.0 --out /tmp/mfdist` as a real local build (then delete /tmp/mfdist), and actionlint. Never push a tag.

Commit: `ci: release jobs for the CLI and the Terraform provider (MF-85)`.

### 36. Git sync example workflows
Files: `docs/examples/git-sync/megooflags-diff.yml`, `megooflags-apply.yml` and `megooflags-export.yml`.

Every workflow installs the CLI from the release archive: `gh release download cli/v0.1.0 --repo mmhy2003/MegooFlags --pattern 'megooflags_*_linux_amd64.tar.gz'` and untars it into `$RUNNER_TEMP/bin`, which is added to `GITHUB_PATH`. The version is a top-level `env: MEGOOFLAGS_CLI_VERSION`. Every workflow uses `MEGOOFLAGS_URL: ${{ vars.MEGOOFLAGS_URL }}`, `MEGOOFLAGS_API_TOKEN: ${{ secrets.MEGOOFLAGS_API_TOKEN }}` and `MEGOOFLAGS_PROJECT: ${{ vars.MEGOOFLAGS_PROJECT }}`, with the directory `flags/`.

- **diff**: `on: pull_request: paths: [flags/**]`; runs `megooflags diff --dir flags`. Exit 3 fails the check with the plan in the log, and the plan also goes to `$GITHUB_STEP_SUMMARY`.
- **apply**: `on: push: branches: [main], paths: [flags/**]`; `concurrency: megooflags-apply`; runs `megooflags apply --dir flags --change-requests`. Exit 4 is not a failure: the step summary lists the opened change requests.
- **export**: `on: schedule: cron "*/30 * * * *"` and `workflow_dispatch`; `permissions: contents: write, pull-requests: write`. Steps:
  1. check out main;
  2. `megooflags export --dir flags`;
  3. if `git status --porcelain flags` is empty, exit;
  4. otherwise force-push the branch `megooflags/export` with one commit "Sync flags from MegooFlags";
  5. `gh pr view megooflags/export` or `gh pr create --head megooflags/export --base main --title "Sync flag changes from MegooFlags" --body …`, which explains that an open change request can make this PR undo a Git change until it is approved.

Checks: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest docs/examples/git-sync/*.yml` passes, and so does plain actionlint of `.github/workflows`.

Commit: `docs: Git sync example workflows for flags as code (MF-85)`.

### 37. Docs, README, AGENTS.md and final checks
Files:
- `docs/cli.md`: install from release archives with checksum verification, login, configuration precedence, every command with an example, output formats, exit codes 0 to 4, and local development (`ruleset pull` with the Go SDK `BootstrapFile` and `Offline: true`, and `evaluate`);
- `docs/flags-as-code.md`: the layout of contract H, what is not in the files, `--env`, export, diff and apply, apply order, approvals and `--change-requests`, `--prune`, the Git sync flow with the three example workflows, why export runs on a schedule (a webhook cannot start a GitHub workflow without a relay; a small relay example in prose), and the open-change-request caveat;
- `docs/importers.md`: the commands per tool, the `--file` and `--save` formats, the translation tables and what gets reported, key lowercasing, the re-bucketing notice, and review-then-apply;
- `docs/integrations.md`: creating webhooks, Slack and Teams; event groups and their actions; the payload of contract C; headers; signature verification examples in Python, Node.js and Go (including a 5-minute timestamp tolerance); retries and the schedule; at-least-once delivery and deduping on `id`; the address rules and `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE`; retention; the metric;
- `cli/README.md` (short, linking to `docs/cli.md`), and `terraform/README.md` (filesystem mirror install: unzip into `~/.terraform.d/plugins/registry.terraform.io/mmhy2003/megooflags/<v>/<os>_<arch>/`, a `provider_installation` block with `filesystem_mirror` and `direct { exclude = ["registry.terraform.io/mmhy2003/megooflags"] }`, and a usage example);
- `README.md`: add the layout lines `cli/       megooflags CLI and Go API client` and `terraform/ Terraform provider`, plus a sentence under Quick start or Development linking to `docs/cli.md`;
- `AGENTS.md`:
  - section 1: rows for `cli/` and `terraform/`, plus Go modules for `cli/`, `cli/e2e/` and `terraform/` in the package manager list;
  - section 9: a "CLI and Terraform checks" block with the `cli`, `cli-e2e`, `terraform` and `terraform-e2e` commands from this plan's Final verification;
  - section 10: agents never push `cli/v*` or `terraform/v*` tags;
  - section 11 (or a new short section): the CLI and provider have their own versions (0.1.0) and the tags `cli/vX.Y.Z` and `terraform/vX.Y.Z`, published as GitHub pre-releases with `--latest=false`;
- `CHANGELOG.md` under Unreleased, Added:
  - "`megooflags` CLI 0.1.0 …";
  - "Flags as code: `export`, `apply`, `diff` …";
  - "Importers from Unleash, Flagsmith and LaunchDarkly …";
  - "Terraform provider 0.1.0 (GitHub release files) …".

Check: every command and flag named in the docs exists (`megooflags <cmd> --help`). Then run the whole Final verification below and report the pytest, Vitest and Go test counts; `git status` must show no build outputs.

Commit: `docs: CLI, flags as code, importers, integrations and Terraform (MF-85)`.

## Final verification
```bash
# version and scripts
python3 scripts/check_version.py
uv run --frozen --project api pytest scripts/tests -q
# API
(cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest)
bash scripts/check_migrations.sh
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json
pnpm --dir web gen:api
git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts
# web (API started with MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true, see AGENTS.md section 9)
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test)
(cd web && pnpm e2e)
# CLI and provider
(cd cli && gofmt -l . && go vet ./... && go test -race -count=1 ./... && go build -o bin/megooflags ./cmd/megooflags)
(cd cli/e2e && gofmt -l . && go vet -tags e2e ./...)
(cd terraform && gofmt -l . && go vet ./... && go test -race -count=1 ./... && go tool tfplugindocs generate --provider-name megooflags && git diff --exit-code -- docs)
# end to end against the compose stack (AGENTS.md section 9 stack commands)
(cd cli/e2e && go test -tags e2e -count=1 -v ./...)
(cd terraform && TF_ACC=1 MEGOOFLAGS_URL=$MEGOOFLAGS_E2E_URL go test -count=1 -v -run TestAcc ./...)
(cd sdks/go && go test -tags e2e -count=1 -v ./e2e)
# workflows and images
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest docs/examples/git-sync/*.yml
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh http && bash scripts/smoke.sh https
```
