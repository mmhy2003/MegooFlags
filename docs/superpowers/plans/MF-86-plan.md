# MF-86 implementation plan: multi-node deployment (control plane and workers)

**Goal:** one image set runs as `standalone`, `control-plane` or `worker`. Workers serve `/sdk/*` and `/ofrep/*` from signed per-environment snapshots held in memory and on disk, keep serving while the control plane or its database is down, buffer events, and are managed on a Nodes page. The task is done when the failure-scenario suite (`scripts/cluster_test.sh`) passes in CI.

**Architecture:** the SDK and OFREP routers keep one code path and read rulesets, keys, events and rate limits through small interfaces in `services/delivery.py` (C6). On `standalone` and `control-plane`, Postgres and Valkey adapters back them as today. On `worker`, a snapshot store backs them (`services/worker/`). The control plane (`services/cluster/`) keeps a node registry, builds versioned Ed25519-signed snapshots, and streams them to workers over SSE (`POST /api/cluster/v1/stream`). Workers send heartbeats and event batches with HTTPS POST. The web app adds an org-level Nodes page.

## Conventions for every step
- Branch `megoo/MF-86-multi-node-deployment-control-plane-work`. The pull request goes into `develop`. Each step is **one commit**, `<type>(<scope>): <summary> (MF-86)`, with scopes `api`, `web`, `deploy`, `scripts`, `ci` or `docs`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.megoocolab/`, caches, `web/test-results`, `web/playwright-report`, e2e auth state or `.pnpm-store/`.
- TDD: write the named test, run it and watch it fail (an import error counts for a new module), implement, run it again and watch it pass. Then run the step's checks.
- The API checks are the AGENTS.md section 9 commands: from `api/`, `uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest`. They need Postgres and Valkey; set `MEGOOFLAGS_DATABASE_URL`, `MEGOOFLAGS_VALKEY_URL` and `MEGOOFLAGS_SECRET_KEY` as AGENTS.md shows. Report the pytest pass and skip counts in the commit body.
- Any change to routes, schemas or enums: regenerate the spec and client **in the same commit** (`uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json` and `pnpm --dir web gen:api`), then check `git diff --exit-code` after a second run.
- Any user-visible change adds a line under `## [Unreleased]` in `CHANGELOG.md` (Added or Changed). The step says which line.
- Strict mypy, `from __future__` is not used in this repo, line length 100, and the docstring style follows the surrounding modules (one module docstring, short function docstrings).
- `standalone` behaviour must not change. The existing SDK, OFREP, stream, fan-out, events and audit tests pass **unmodified** (except where a step explicitly names a test to extend).

## File map

| Path | New/changed | Responsibility |
|---|---|---|
| `api/src/megooflags_api/settings.py` | changed | Role and worker settings (C1) |
| `api/src/megooflags_api/services/cluster/__init__.py` | new | Package marker |
| `api/src/megooflags_api/services/cluster/config.py` | new | `worker_config_errors` (C1) |
| `api/src/megooflags_api/services/cluster/compat.py` | new | Version rule (C2) |
| `api/src/megooflags_api/services/cluster/signing.py` | new | Ed25519 key from the secret key (C3) |
| `api/src/megooflags_api/services/cluster/snapshot_format.py` | new | Snapshot header, bytes, digest and verification, shared by both sides (C4) |
| `api/src/megooflags_api/models/cluster.py`, `models/__init__.py`, `models/enums.py` | new/changed | `Node`, `NodeToken`, `EnvironmentSnapshot`, `NodeEventBatch`, `NodeStatus`, `InstanceRole` (C5) |
| `api/alembic/versions/0015_cluster.py` | new | Tables and `snapshot_version_seq` |
| `api/src/megooflags_api/services/delivery.py` | new | Delivery interfaces and the DB adapters (C6) |
| `routers/sdk.py`, `routers/ofrep.py`, `sdk_auth.py`, `services/streams.py`, `services/ruleset_cache.py`, `main.py` | changed | Use only C6; `StreamHub` takes an optional `source` and a `Closed` item |
| `api/src/megooflags_api/services/cluster/nodes.py` | new | Node registry, tokens and status (C7) |
| `api/src/megooflags_api/schemas/nodes.py`, `schemas/cluster.py` | new | Management and worker-facing bodies (C8, C9) |
| `api/src/megooflags_api/routers/nodes.py` | new | `/api/nodes` management endpoints |
| `api/src/megooflags_api/services/changes.py` | changed | `node` message kind |
| `api/src/megooflags_api/errors.py` | changed | `not_control_plane`, `node_revoked`, `incompatible_version`, `not_ready` |
| `api/src/megooflags_api/services/cluster/snapshots.py` | new | Snapshot build and version assignment (C4, C5) |
| `api/src/megooflags_api/services/cluster/ingest.py` | new | Event batches stored once, plus batch retention |
| `api/src/megooflags_api/services/cluster/node_hub.py` | new | Open worker streams and snapshot fan-out (C9) |
| `api/src/megooflags_api/routers/cluster.py` | new | `/api/cluster/v1/stream`, `/heartbeat`, `/events` |
| `routers/sdk_keys.py` | changed | Create, rotate and revoke also publish the project change |
| `routers/health.py`, `routers/auth.py`, `schemas/auth.py` | changed | `role` in `/healthz` and `/api/auth/me`; worker readiness |
| `api/src/megooflags_api/services/worker/{__init__,store,sources,buffer,state,client,app}.py` | new | The worker (C10 to C12) |
| `api/src/megooflags_api/metrics.py` | changed | Worker and node metrics, `BEFORE_RENDER` hooks (C13) |
| `api/src/megooflags_api/serve.py` | changed | Worker start checks and one process |
| `api/src/megooflags_api/services/scheduler.py` | changed | `node_event_batches` retention job |
| `api/tests/test_cluster_*.py`, `api/tests/test_worker_*.py` | new | Unit and service tests |
| `api/tests/cluster/{cluster_harness.py,conftest.py,test_*.py}` | new | In-process cluster tests (C14) |
| `web/lib/nodes/{status,format}.ts`, `web/lib/nav.ts`, `web/lib/shell-data.ts` | new/changed | Pure helpers and the nav rule |
| `web/hooks/use-visible-interval.ts` | new | Refresh while the page is visible |
| `web/app/(app)/nodes/page.tsx`, `web/components/nodes/*` | new | Nodes page (W1) |
| `web/components/shell/nav.tsx`, `app-shell.tsx` | changed | Nodes icon and role |
| `web/tests/unit/nodes-*.test.ts`, `web/tests/unit/nav.test.ts` | new/changed | Vitest |
| `web/e2e/nodes.spec.ts`, `web/e2e/matrix.spec.ts`, `web/e2e/global-setup.ts`, baselines | new/changed | Playwright and the visual and axe matrix |
| `api/Dockerfile` | changed | `/var/lib/megooflags` owned by uid 10001 |
| `deploy/docker-compose.yml`, `deploy/docker-compose.control-plane.yml` | changed/new | `MEGOOFLAGS_ROLE` |
| `deploy/docker-compose.worker.yml`, `deploy/Caddyfile.worker`, `deploy/.env.worker.example`, `deploy/.env.example` | new/changed | Worker bundle |
| `scripts/tests/test_compose_files.py` | new | Drift and worker bundle checks |
| `docs/examples/multi-node/{README.md,nginx.conf,haproxy.cfg,Caddyfile,traefik.yml}` | new | Load balancer samples |
| `scripts/cluster_failures.py`, `scripts/tests/test_cluster_failures.py`, `scripts/cluster_test.sh`, `scripts/cluster/*.yml` | new | Failure-scenario suite |
| `.github/workflows/ci.yml`, `.github/workflows/release.yml` | changed | `cluster` job, `MEGOOFLAGS_ROLE` for web-e2e, release files |
| `docs/multi-node.md`, `docs/multi-node-migration.md`, `README.md`, `AGENTS.md`, `CHANGELOG.md` | new/changed | Docs |

---

## Contracts

### C1. Settings (`settings.py`) and worker config
Add to `Settings`:
```python
InstanceRoleName = Literal["standalone", "control-plane", "worker"]

role: InstanceRoleName = "standalone"
control_plane_url: AnyHttpUrl | None = None
node_token: SecretStr | None = None
control_plane_insecure: bool = False
control_plane_ca_file: str = ""
data_dir: Path = Path("/var/lib/megooflags")
worker_buffer_max_mb: int = Field(64, ge=1)
worker_drain_grace_seconds: float = Field(30, ge=0)
```
Add `control_plane_url` and `node_token` to the `_empty_is_none` validator.

`services/cluster/config.py`:
```python
NODE_TOKEN_PREFIX = "mf_node_"

def worker_config_errors(settings: Settings) -> list[str]:
    """What stops a worker from starting; empty when it can start."""
```
It returns these messages, in this order, for the cases that apply:
- `"MEGOOFLAGS_CONTROL_PLANE_URL is not set: set it to the control plane's URL, e.g. https://flags.example.com"`
- `"MEGOOFLAGS_NODE_TOKEN is not set: create the worker on the Nodes page and copy its token"`
- `"MEGOOFLAGS_NODE_TOKEN is not a node token (it starts with mf_node_)"`
- `"MEGOOFLAGS_CONTROL_PLANE_URL must use https://; set MEGOOFLAGS_CONTROL_PLANE_INSECURE=true to allow http on a private network"` (http without insecure)
- `"MEGOOFLAGS_CONTROL_PLANE_CA_FILE {path} does not exist"`

A worker needs no `MEGOOFLAGS_SECRET_KEY`, no database and no Valkey.

### C2. Version rule (`services/cluster/compat.py`)
```python
def minor_of(version: str) -> tuple[int, int]:
    """(X, Y) of "X.Y.Z" or "X.Y.Z-rc.N"; ValueError otherwise."""

def incompatibility(worker: str, control_plane: str) -> str | None:
    """None when the worker may connect, else why not (both versions in the text)."""
```
The rules:
- Unparsable version: refused.
- Different major: refused.
- Major 0: the minors must be equal.
- Major 1 and later: the worker's minor is the control plane's minor or one less, never newer.

The message has the form `"worker {w} cannot join control plane {cp}: {reason}; upgrade the control plane first, then the workers"`.

### C3. Signing (`services/cluster/signing.py`)
```python
HKDF_INFO = b"megooflags/snapshots/v1"

class SnapshotSigner:
    def __init__(self, secret: str) -> None: ...   # HKDF-SHA256, 32 bytes -> Ed25519PrivateKey.from_private_bytes
    @property
    def public_key(self) -> str: ...               # standard base64 of the 32 raw public bytes
    def sign(self, data: bytes) -> str: ...        # standard base64 signature

def verify(public_key: str, data: bytes, signature: str) -> bool:
    """False for a bad key, bad base64 or a wrong signature; never raises."""
```
`main.create_app` sets `app.state.snapshot_signer = SnapshotSigner(secret)` when the secret key is set, else `None` (like `secret_box`).

### C4. Snapshot format (`services/cluster/snapshot_format.py`)
```python
FORMAT = 1

@dataclass(frozen=True, slots=True)
class SnapshotKey:
    id: uuid.UUID
    kind: SdkKeyKind
    hash: str
    revoked_at: datetime | None
    expires_at: datetime | None

@dataclass(frozen=True, slots=True)
class SnapshotHeader:
    format: int
    environment_id: uuid.UUID
    environment_key: str
    project_id: uuid.UUID
    project_key: str
    version: int
    published_at: datetime
    keys: tuple[SnapshotKey, ...]        # sorted by id

    def env_ref(self) -> EnvRef: ...
    def to_json(self) -> bytes: ...      # compact JSON, sort_keys=True, datetimes as ISO 8601 UTC
    @classmethod
    def from_json(cls, data: bytes) -> "SnapshotHeader": ...   # SnapshotError when malformed

class SnapshotError(ValueError): ...

def content_digest(header: SnapshotHeader, ruleset: bytes) -> str:
    """SHA-256 hex of the header without version and published_at, b"\n", and the ruleset."""

@dataclass(frozen=True, slots=True)
class SignedSnapshot:
    header: SnapshotHeader
    ruleset: bytes            # exactly the bytes /sdk/v1/ruleset serves
    signature: str

    def payload(self) -> bytes: ...      # header.to_json() + b"\n" + ruleset  (what is signed)
    def to_bytes(self) -> bytes: ...     # signature + b"\n" + payload(); SnapshotError if header or ruleset contains b"\n"
    @classmethod
    def from_bytes(cls, data: bytes) -> "SignedSnapshot": ...   # SnapshotError for fewer than 3 lines, an unknown format, a bad header
    def verify(self, public_key: str) -> bool: ...
```
The same bytes are used on the wire and on disk. An SSE `put` event is `event: put`, `id: <version>`, and three `data:` lines (signature, header JSON, ruleset), so the joined data is `to_bytes()`. The compact JSON from `json.dumps` never holds a raw newline.

### C5. Tables (`models/cluster.py`, migration `0015_cluster`)
```python
SNAPSHOT_VERSION_SEQ = Sequence("snapshot_version_seq", metadata=Base.metadata)

class Node(Base):            # "node"
    id: uuid_pk(); name: String(100) unique; created_at (server now)
    revoked_at, drain_requested_at, last_heartbeat_at, synced_at: DateTime(tz) | None
    version: String(50) | None; snapshot_version: BigInteger | None
    sdk_streams: Integer server_default 0; buffered_events: BigInteger server_default 0
    address: String(100) | None; refused_version: String(50) | None; refused_at: DateTime(tz) | None

class NodeToken(Base):       # "node_token"
    id: uuid_pk(); node_id FK node.id ondelete CASCADE, index
    prefix: String(16); secret_hash: String(64) unique
    created_at (server now); expires_at, revoked_at: DateTime(tz) | None

class EnvironmentSnapshot(Base):   # "environment_snapshot"
    environment_id: FK environment.id ondelete CASCADE, primary key
    version: BigInteger; digest: String(64); published_at: DateTime(tz)

class NodeEventBatch(Base):  # "node_event_batch"
    batch_id: Uuid primary key; node_id FK node.id ondelete CASCADE
    received_at: DateTime(tz) server now, index
```
`models/enums.py` gains:
```python
class InstanceRole(StrEnum):
    STANDALONE = "standalone"
    CONTROL_PLANE = "control-plane"
    WORKER = "worker"

class NodeStatus(StrEnum):
    PENDING = "pending"
    HEALTHY = "healthy"
    STALE = "stale"
    DRAINING = "draining"
    INCOMPATIBLE = "incompatible"
    REVOKED = "revoked"
```
The status is computed, never stored, so there is no Postgres enum. The downgrade drops the four tables and the sequence.

**Version assignment** (`services/cluster/snapshots.py`):
```python
async def environment_refs(session, project_id: uuid.UUID | None = None) -> list[tuple[EnvRef, str]]:
    """Every environment (of one project) with its project key, ordered by id."""

async def build_snapshot(session, environment: EnvRef, project_key: str,
                         cache: RulesetCache, signer: SnapshotSigner) -> SignedSnapshot | None:
    """None when the environment no longer exists. Commits."""
```
The order matters:
1. `INSERT ... ON CONFLICT DO NOTHING` a row `(environment_id, 0, '', now())`, then `SELECT ... FOR UPDATE` it. Return `None` on a foreign key error, because the environment is gone.
2. With the lock held, `entry = await cache.get_entry(session, environment, max_age=0)`, and read the environment's `SdkKey` rows (id, kind, secret_hash, revoked_at, expires_at).
3. Compute `content_digest` with a version 0 header. If it equals the row's digest, keep the row's version and `published_at`. Otherwise take `version = nextval('snapshot_version_seq')`, set `published_at = now()` and update the row.
4. Commit, then sign `payload()`.

Because the content is read under the row lock, a higher version never carries older content.

`async def current_snapshot_version(session) -> int` returns the maximum `environment_snapshot.version`, or 0.

### C6. Delivery interfaces (`services/delivery.py`)
```python
class RulesetSource(Protocol):
    async def get_entry(self, environment: EnvRef) -> CachedRuleset: ...   # SDK read (may be SDK_MAX_AGE old)

class KeySource(Protocol):
    async def resolve(self, key: str) -> SdkPrincipal | None: ...
    async def resolve_hash(self, secret_hash: str) -> SdkPrincipal | None: ...

class EventSink(Protocol):
    def record_counts(self, environment_id: uuid.UUID, results: Iterable[tuple[str, str | None]]) -> None: ...
    async def store(self, environment_id: uuid.UUID, body: EventsIn, now: datetime) -> None: ...

class ClientLimiter(Protocol):
    async def hit(self, key_id: uuid.UUID, client_ip: str) -> int | None: ...

class StreamSource(Protocol):
    async def current(self, environment: EnvRef) -> CachedRuleset | None: ...   # fresh; None = environment gone
    async def inactive_keys(self, key_ids: set[uuid.UUID]) -> set[uuid.UUID]: ...

@dataclass
class Delivery:
    rulesets: RulesetSource
    keys: KeySource
    events: EventSink
    limiter: ClientLimiter
    unavailable: Callable[[], str | None] = lambda: None   # worker: "not_ready" or "revoked"

def get_delivery(request: Request) -> Delivery: ...

def db_delivery(app: FastAPI) -> Delivery: ...   # the DB/Valkey adapters below
```
The DB adapters (`DbRulesetSource`, `DbKeySource`, `DbEventSink`, `DbLimiter`) hold the **app**. They read `app.state.ruleset_cache`, `app.state.key_resolver`, `app.state.evaluation_counter`, `app.state.rate_limiter` and `app.state.sessionmaker` at call time, because existing tests replace those attributes (for example `app.state.key_resolver = KeyResolver(ttl=0)` in `test_stream.py`).
- `DbEventSink.store` holds the body of today's `send_events`: `record_exposures` with `persist_context`, then `add_counts`, then commit, all in its own session.
- `DbStreamSource(sessionmaker, cache)` holds today's `_refresh_one` and `_revoke_inactive` queries.

`StreamHub.__init__(self, sessionmaker, cache, debounce=0.1, *, source: StreamSource | None = None)` keeps its signature (`test_burst_merges` and `test_fanout.py` construct it positionally). A `None` source becomes `DbStreamSource(sessionmaker, cache)`. `StreamHub` also gains:
- `Closed`: a final item that ends a stream **without** a frame;
- `close_all()`: offers `Closed()` to every subscriber;
- `revoke_all()`: offers `Revoked()` to every subscriber.

`Subscriber.offer` treats `Closed` as final, like `Revoked`.

`services/ruleset_cache.py` gains `def from_body(body: bytes) -> CachedRuleset`: it parses the JSON, sets `fingerprint = etag`, and uses the same ETag formula as `_prepare`, so worker ETags equal the control plane's.

### C7. Node registry (`services/cluster/nodes.py`)
```python
NODE_TOKEN_LENGTH = 40          # "mf_node_" + 32 base62
TOKEN_DISPLAY_PREFIX = 12
HEARTBEAT_FRESH = timedelta(seconds=30)

def generate_node_token() -> str
def hash_token(token: str) -> str                     # sha256 hex, as sdk_keys.hash_key

def node_status(node: Node, *, active_tokens: int, now: datetime) -> NodeStatus
def is_syncing(node: Node, status: NodeStatus, current_version: int) -> bool   # healthy and snapshot_version < current

async def create_node(session, name: str) -> tuple[Node, str]             # 409 key_conflict("node", name)
async def list_nodes(session) -> list[tuple[Node, int, str | None]]        # node, active tokens, newest active prefix; by name
async def get_node(session, node_id, *, for_update=False) -> Node         # 404 not_found("node", id)
async def rotate_token(session, node, overlap: timedelta) -> str          # 409 invalid_state when revoked
async def revoke_node(session, node) -> None                              # node.revoked_at and every token's revoked_at (keep the first)
async def drain_node(session, node) -> None / resume_node(...)            # 409 invalid_state when revoked
async def delete_node(session, node) -> None                              # 409 invalid_state("revoke the node before removing it") unless revoked
async def authenticate_token(session, token: str) -> NodeAuth | None      # NodeAuth(node, token_id, active: bool)
```
`node_status` checks in this order and returns the first that applies:
1. `revoked_at` is set, or `active_tokens == 0`: Revoked.
2. `refused_at` is set and (`last_heartbeat_at` is None or `refused_at > last_heartbeat_at`): Incompatible.
3. `drain_requested_at` is set: Draining.
4. `last_heartbeat_at` is None: Pending.
5. `now - last_heartbeat_at > HEARTBEAT_FRESH`: Stale.
6. Otherwise: Healthy.

A token is active when it is neither revoked nor expired (`sdk_keys.is_inactive`).

`rotate_token` mirrors `sdk_keys.rotate_key`. Each active token gets `expires_at = min(existing, now + overlap)`, and an overlap of 0 revokes the tokens at once. Then it adds a new token.

### C8. Management API (`routers/nodes.py`, `schemas/nodes.py`)
```python
class NodeOut(ApiModel):
    id: uuid.UUID; name: str; status: NodeStatus; syncing: bool
    created_at: datetime; last_heartbeat_at: datetime | None; synced_at: datetime | None
    version: str | None; snapshot_version: int | None
    sdk_streams: int; buffered_events: int; address: str | None
    refused_version: str | None; token_prefix: str | None

class NodeList(ApiModel):
    version: str                # the control plane's __version__
    snapshot_version: int       # current_snapshot_version
    nodes: list[NodeOut]

class NodeCreate(ApiModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]

class NodeCreated(NodeOut):
    token: str
```
The routes use `ManageOrg = Annotated[Authz, Depends(require(Permission.ORG_MANAGE))]` plus the dependency `require_control_plane(request)`. It raises `not_control_plane()` unless `request.app.state.settings.role == "control-plane"`, and runs after RBAC.

| Route | Answer | Audit action |
|---|---|---|
| `GET /api/nodes` | `NodeList` | none |
| `POST /api/nodes` `NodeCreate` | 201 `NodeCreated` | `node.create` |
| `POST /api/nodes/{id}/rotate` `RotateIn` (from `schemas/common.py`) | 201 `NodeCreated` | `node.rotate` |
| `POST /api/nodes/{id}/revoke` | `NodeOut` | `node.revoke` |
| `POST /api/nodes/{id}/drain` | `NodeOut` | `node.drain` |
| `POST /api/nodes/{id}/resume` | `NodeOut` | `node.resume` |
| `DELETE /api/nodes/{id}` | 204 | `node.delete` |

Every audit entry uses `resource_type="node"`, `resource_key=str(id)` and `snapshot(NodeOut)`, and never includes the token. After the commit, every action except create and list calls `bus.publish_node(id)`.

The new errors in `errors.py`:
```python
def not_control_plane() -> ApiError:   # 409 "not_control_plane", "this instance runs with MEGOOFLAGS_ROLE=standalone; set MEGOOFLAGS_ROLE=control-plane to manage worker nodes"
def node_revoked() -> ApiError:        # 403 "node_revoked", "this node's token is revoked or expired"
def incompatible_version(worker: str, control_plane: str, reason: str) -> ApiError:  # 409, details {"worker": w, "control_plane": cp}
def not_ready(code: str, message: str) -> ApiError:  # 503 code ("not_ready" | "revoked")
```
`ChangeBus` gains `publish_node(node_id)` (message kind `"node"`) and `on_node: list[Callable[[uuid.UUID], None]]`. The request dispatches it like the other kinds.

### C9. Worker-facing protocol (`routers/cluster.py`, `schemas/cluster.py`, `services/cluster/node_hub.py`)
Every request carries `Authorization: Bearer <node token>` and `X-MegooFlags-Version: <worker version>`. The dependency `require_node(request) -> NodeCaller(node_id, token_id)` checks:
1. The role is not `control-plane`: 409 `not_control_plane`, with the message `"the control plane runs with MEGOOFLAGS_ROLE=standalone; set MEGOOFLAGS_ROLE=control-plane on it"`.
2. Missing or unknown token: 401 `unauthorized`.
3. The node is revoked or the token inactive: 403 `node_revoked`.
4. `incompatibility(header or "unknown", __version__)` is set: store `refused_version` and `refused_at` on the node, commit, and answer 409 `incompatible_version`.

The three routes are in `PUBLIC_ROUTES` and are included without `get_principal`.

```python
class StreamIn(ApiModel):
    versions: dict[uuid.UUID, int] = {}
    public_key: str | None = None          # the key the worker verifies with; a mismatch makes versions count as empty

class HeartbeatIn(ApiModel):
    version: str
    snapshot_version: int = Field(ge=0)
    snapshot_age_seconds: float | None = Field(None, ge=0)
    sdk_streams: int = Field(ge=0)
    buffered_events: int = Field(ge=0)
    ready: bool

class HeartbeatOut(ApiModel):
    state: Literal["active", "draining"]

class ClusterEventsIn(ApiModel):
    batch_id: uuid.UUID
    environment_id: uuid.UUID
    exposures: list[ExposureIn] = Field(default_factory=list, max_length=MAX_BATCH)
    counts: list[CountIn] = Field(default_factory=list, max_length=MAX_BATCH)
```

**Stream** (`POST /api/cluster/v1/stream`, `text/event-stream`) sends, in order:
1. `retry: 3000`.
2. `event: hello`, with data `{"version", "public_key", "persist_context", "state": "active"|"draining", "ping_seconds": settings.sse_heartbeat_seconds}`.
3. `put` for every environment whose version differs from `versions`, or for every environment when the key differs.
4. `event: remove`, data `{"environment_id"}`, for each id in `versions` that no longer exists.
5. `event: synced`, data `{"snapshot_version": n}`.
6. Then `put`, `remove`, `event: state` `{"state"}`, `event: revoked` `{}` (after which the stream closes), and `: ping` every `sse_heartbeat_seconds`.

The node row gets `address = request.client.host`, and its `refused_*` fields are cleared on a successful connect.

**NodeHub:**
```python
class NodeStream:                         # one connected worker
    node_id: uuid.UUID; token_id: uuid.UUID
    def offer_put(self, snap: SignedSnapshot) -> None      # replaces a pending item for that environment
    def offer_remove(self, environment_id) -> None          # same
    def offer_state(self, state: str) -> None
    def offer_revoked(self) -> None                         # final
    async def next(self, timeout: float) -> list[StreamEvent] | None   # None on timeout

class NodeHub:
    def __init__(self, sessionmaker, cache: RulesetCache, signer: Callable[[], SnapshotSigner], debounce=0.1)
    stream_count: int
    def subscribe(self, node_id, token_id) -> NodeStream / unsubscribe(stream)
    async def snapshots(self) -> dict[uuid.UUID, SignedSnapshot]      # all environments, built on demand
    def notify_project(self, project_id) -> None                       # debounced rebuild of that project's environments
    def notify_node(self, node_id) -> None                             # reload drain/revoke/token state, offer state or revoked
    async def recheck_all(self) -> None                                # rebuild everything and re-check node states and tokens
    async def run_recheck_loop(self, interval: float) -> None
```
- Rebuilds run one at a time under one lock. They keep the latest `SignedSnapshot` per environment, so it is signed once for every worker. They offer a `put` only when the version is higher than the last one offered, and a `remove` for a known environment of the project that has disappeared.
- Errors (the database down, for example) are logged, never raised, and the streams stay open.
- In `main.create_app`, `app.state.node_hub` is wired to `change_bus.on_project`, `on_node` and `on_resubscribe`. Its recheck loop runs in `background_jobs` only when the role is `control-plane`.
- `metrics.NODE_STREAMS` follows `stream_count`.

**Heartbeat** updates the node: `last_heartbeat_at = now()`, `version`, `snapshot_version`, `synced_at = now() - snapshot_age_seconds` (when given), `sdk_streams`, `buffered_events` and `address`, and clears `refused_*`. It answers `HeartbeatOut`.

**Events** (`services/cluster/ingest.py`):
```python
async def ingest_batch(session, node_id, body: ClusterEventsIn, *, persist_context: bool, now: datetime) -> bool
```
- Run `INSERT INTO node_event_batch ... ON CONFLICT DO NOTHING RETURNING batch_id`. If nothing is returned, the batch was seen before: answer 202 and do nothing else.
- If the environment no longer exists, keep the batch id and drop the data.
- Otherwise run `record_exposures` and `add_counts` (counts merged per key as in `send_events`) in the **same transaction**, then commit and answer 202.

`async def delete_old_batches(sessionmaker, now) -> JobResult` deletes rows older than 7 days. It is the scheduler `Job("node_event_batches", ..., every=timedelta(hours=1))`.

### C10. Worker store (`services/worker/store.py`)
On-disk layout under `settings.data_dir`:
```
snapshots/<environment_id>.snap   SignedSnapshot.to_bytes()
cluster.json                      {"public_key": str|null, "synced": bool, "revoked": bool,
                                   "persist_context": bool, "last_contact_at": ISO|null, "control_plane_version": str|null}
buffer/                           EventBuffer files (C11)
```
Every write is atomic: write `<name>.tmp` in the same directory, `fsync`, then `os.replace`.

```python
class SnapshotStore:
    def __init__(self, data_dir: Path, max_parsed: int = 32) -> None
    def load(self) -> None          # read cluster.json; verify every .snap with public_key; delete (and log) bad files and set valid=False
    valid: bool                     # False after a discarded file, until mark_synced()
    meta: ClusterMeta               # dataclass of cluster.json fields
    def save_meta(self) -> None
    def mark_synced(self) -> None   # synced=True, valid=True, save
    def versions(self) -> dict[uuid.UUID, int]
    def max_version(self) -> int
    def apply(self, snap: SignedSnapshot) -> bool   # False (keep the old one) for a bad signature, a lower or equal version, or an unknown format
    def remove(self, environment_id) -> EnvRef | None
    def principal(self, secret_hash: str) -> SdkPrincipal | None    # from the in-memory key index of all environments
    def entry(self, environment_id) -> CachedRuleset | None         # LRU of max_parsed parsed rulesets; reads the file on a miss
```
Only headers (the key index) stay in memory for every environment. Rulesets are parsed with `ruleset_cache.from_body`, which runs in `asyncio.to_thread` from the async sources.

### C11. Event buffer (`services/worker/buffer.py`)
```python
class EventBuffer:
    seal_seconds: float = 10.0; forward_seconds: float = 2.0
    def __init__(self, directory: Path, max_bytes: int, memory_items: int = 10_000,
                 clock: Callable[[], datetime] = now_utc) -> None
    def load(self) -> None                                   # pick up files left by an earlier run; drop files older than 7 days
    def add_counts(self, environment_id, results: Iterable[tuple[str, str | None]], hour: datetime | None = None) -> None
    def add_count_items(self, environment_id, counts: Sequence[CountIn]) -> None
    def add_exposures(self, environment_id, items: Sequence[ExposureIn], *, persist_context: bool) -> None   # context=None unless persist_context
    def seal(self) -> None          # memory -> batch files: counts-<ns>-<uuid>.json and exposures-<ns>-<uuid>.json, at most 1000 items each
    pending_events: int             # exposures plus count entries in memory and in files
    dropped_total: int
    async def run_sealer(self) -> None
    async def run_forwarder(self, send: Callable[[ClusterEventsIn], Awaitable[SendResult]]) -> None
```
- Each file holds one `ClusterEventsIn` JSON with its `batch_id`, fixed when the file is written.
- When the memory queue passes `memory_items` exposures, it seals at once.
- When the files pass `max_bytes`, the oldest **exposure** files are deleted and their items added to `dropped_total`. Count files are never dropped for size.
- The forwarder sends the oldest file first:
  - `accepted` (202): delete the file;
  - `rejected` (other 4xx): log and delete it;
  - `retry` (network error, 5xx, 401, 403 or 409): stop this round.
- `SendResult = Literal["accepted", "rejected", "retry"]`.
- The worker's `/sdk/v1/events` and evaluations feed it through `BufferEventSink` in `sources.py`.

### C12. Worker state, client and app
`services/worker/state.py`:
```python
class WorkerState:
    def __init__(self, store: SnapshotStore) -> None
    connected: bool = False; draining: bool = False; incompatible: str | None = None
    def touch(self) -> None                          # store.meta.last_contact_at = now (saved at most every 10 s)
    def unavailable_reason(self) -> str | None       # "revoked" if meta.revoked; "not_ready" if not (meta.synced and store.valid); else None
    def readiness(self) -> str | None                # unavailable_reason() or ("draining" if draining) or None
    def snapshot_age(self) -> float | None           # seconds since last_contact_at; None if never
```
`services/worker/client.py`:
```python
class ControlPlaneClient:
    heartbeat_seconds = 10.0; backoff_min = 0.5; backoff_max = 30.0; incompatible_retry_seconds = 60.0
    def __init__(self, settings, store, state, hub: StreamHub, buffer: EventBuffer,
                 transport: httpx.AsyncBaseTransport | None = None) -> None
    async def run(self) -> None              # the sync loop
    async def heartbeat_loop(self) -> None
    async def send_events(self, batch: ClusterEventsIn) -> SendResult
    async def aclose(self) -> None
```
- The `httpx.AsyncClient` uses `base_url=control_plane_url`; `verify` is an `ssl` context with the CA file when one is set, else `True`. It sends the token and version headers and uses `Timeout(10, read=3 * ping_seconds)`. The read timeout is 45 s until `hello` arrives.
- **The loop:**
  1. Open the stream with `StreamIn(versions=store.versions(), public_key=store.meta.public_key)`.
  2. On `hello`: update `public_key`, `persist_context` and `control_plane_version`; set `draining` from `state`; set `connected`.
  3. On `put`: `SignedSnapshot.from_bytes`, verify, `store.apply`; when it changed, `hub.notify_project(header.project_id)`.
  4. On `remove`: `store.remove`, then `hub.notify_project` (the source returns None, so those SDK streams get `revoked`).
  5. On `synced`: `store.mark_synced()`.
  6. On `state`: set draining or active.
  7. On any event or ping: `state.touch()`.
- **Errors:**
  - 409 `incompatible_version`: log the message, set `state.incompatible`, wait `incompatible_retry_seconds`.
  - 403 `node_revoked` or a `revoked` event: set `meta.revoked=True`, save, `hub.revoke_all()`, stop the loop.
  - 409 `not_control_plane`: log, wait 60 s.
  - Network errors and 5xx: back off from 0.5 s to 30 s (doubling), with `connected=False`.
- **Drain:** when draining starts, a task waits `worker_drain_grace_seconds`, then calls `hub.close_all()` if the worker is still draining.
- On a successful start the worker clears `meta.revoked`: a restart with a valid token recovers.

`services/worker/app.py`:
```python
def create_worker_app(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI
```
- State: `settings`, `store`, `worker_state`, `event_buffer` (directory `data_dir/buffer`, `max_bytes = worker_buffer_max_mb * 1024 * 1024`), `stream_hub = StreamHub(None, None, source=StoreStreamSource(store))`, `delivery = Delivery(StoreRulesetSource, StoreKeySource, BufferEventSink, MemoryRateLimiter, unavailable=state.unavailable_reason)` and `worker_client`.
- No `engine`, `sessionmaker`, `valkey`, `change_bus` or `/api/*` router.
- Middleware: `EventsBodyLimitMiddleware`, `AccessLogMiddleware`, `MetricsMiddleware` and `SdkCorsMiddleware`.
- Routers: `health`, `metrics`, `sdk` and `ofrep`.
- Lifespan:
  1. `worker_config_errors`: raise `RuntimeError` with the joined messages.
  2. `store.load()`, `buffer.load()`.
  3. Start the tasks `client.run()`, `client.heartbeat_loop()`, `buffer.run_sealer()`, `buffer.run_forwarder(client.send_events)` and `stream_hub.run_recheck_loop(settings.stream_recheck_seconds)`.
  4. On exit: cancel the tasks, `buffer.seal()`, `store.save_meta()`, `client.aclose()`.

`main.create_app(settings=None, *, cluster_transport=None)` returns `create_worker_app(settings, transport=cluster_transport)` when `settings.role == "worker"`.

- `/healthz` returns `HealthResponse(status, version, role)`.
- `/readyz` on a worker answers 200 `{"status": "ready"}`, or 503 `{"status": "not_ready" | "draining" | "revoked"}` from `state.readiness()`. It never touches the database.
- `sdk_auth.authenticate` first calls `delivery.unavailable()`. A reason raises `not_ready(reason, message)` with the messages "this worker has no valid snapshot yet" and "this worker's node token was revoked".
- 503 joins `SDK_ERROR_RESPONSES`.

### C13. Metrics (`metrics.py`)
```python
WORKER_SNAPSHOT_AGE = Gauge("megooflags_worker_snapshot_age_seconds", "Seconds since the worker last heard from the control plane while in sync.")
WORKER_SNAPSHOT_VERSION = Gauge("megooflags_worker_snapshot_version", "Highest snapshot version the worker has applied.")
WORKER_CONTROL_PLANE_CONNECTED = Gauge("megooflags_worker_control_plane_connected", "1 while the worker's stream to the control plane is open.")
WORKER_BUFFERED_EVENTS = Gauge("megooflags_worker_buffered_events", "Exposures and counts waiting to be forwarded.")
WORKER_EVENTS_DROPPED = Counter("megooflags_worker_events_dropped", "Exposures dropped because the buffer was full or older than 7 days.")
NODE_STREAMS = Gauge("megooflags_node_streams", "Open worker streams on this control-plane process.", multiprocess_mode="livesum")
BEFORE_RENDER: list[Callable[[], None]] = []   # render() calls each first
```
The worker app appends a callback that sets the four worker gauges from `state`, `store` and `buffer`. Dropped events are counted with `.inc(n)` when they happen.

### C14. In-process cluster harness (`api/tests/cluster/`)
`httpx.ASGITransport` buffers whole responses (see the `running()` docstring in `api/tests/helpers.py`), so SSE cannot go through it. The harness therefore keeps everything in one process and one event loop, but serves each app with `helpers.running()` (uvicorn on a free port). This keeps the design's intent: an in-process cluster with a link that can be cut.

`cluster_harness.py`:
```python
class CuttableTransport(httpx.AsyncBaseTransport):
    """Wraps AsyncHTTPTransport. cut(): open responses raise httpx.ReadError on the next read and
    new requests raise httpx.ConnectError. restore() lets requests through again."""
    def cut(self) -> None; def restore(self) -> None

@dataclass
class Worker:
    name: str; node_id: str; token: str; data_dir: Path; link: CuttableTransport
    app: FastAPI; url: str                      # the running worker
    sdk: httpx.AsyncClient                      # base_url=url
    async def restart(self) -> None             # stop, create_app again on the same data_dir and link, start
    async def stop(self) -> None

class Cluster:
    cp_app: FastAPI; cp_url: str; admin: httpx.AsyncClient     # admin = org admin service token, base_url=cp_url
    async def add_worker(self, name: str, *, token: str | None = None, version: str | None = None) -> Worker
    async def wait_ready(self, worker: Worker, within: float = 5.0) -> None
    async def wait_status(self, node_id: str, status: str, within: float = 10.0) -> dict[str, Any]
```
- The control plane runs `Settings(role="control-plane", sse_heartbeat_seconds=1, stream_recheck_seconds=1)` after `reset_database`.
- Each worker runs `Settings(role="worker", control_plane_url=cp_url, node_token=token, control_plane_insecure=True, data_dir=tmp_path / name, worker_drain_grace_seconds=0.5)`.
- Before the worker starts, the harness sets `app.state.worker_client.heartbeat_seconds = 0.2`, `backoff_max = 0.5`, `incompatible_retry_seconds = 0.5`, and `app.state.event_buffer.seal_seconds = forward_seconds = 0.2`.
- `version=` monkeypatches the version header the client sends (the attribute `worker_client.version`, default `__version__`).
- The `conftest.py` fixture `cluster` (it needs `migrated` and `valkey_up`) yields a `Cluster` and closes everything.
- The demo data comes from `test_sdk.setup_demo`.

---

## W1. Nodes page (web)
- **Route:** `web/app/(app)/nodes/page.tsx`, in the same shape as `integrations/page.tsx`. It renders `<NodeList />` from `web/components/nodes/node-list.tsx`.
- **Header:** `PageHeader` titled "Nodes", with the primary button "Add worker" and the description "Workers serve SDK and OFREP traffic from a signed copy of your flags."
- **Summary cards** (`components/nodes/node-summary.tsx`, `Card`):
  - "Workers": the count;
  - "Healthy": the healthy count;
  - "Need attention": Stale plus Incompatible plus Revoked;
  - "Snapshot": `v{snapshot_version}`, with "Control plane {version}" under it.
- **List:** `DataList` with these columns:
  - `name` (key): the name and the token prefix;
  - `status` (status): `StatusPill` from `lib/nodes/status.ts`;
  - `snapshot` (main): from `snapshotText`;
  - `heartbeat` (main): a relative time inside `<time>`;
  - `version` (main);
  - `streams` (extra): SDK streams;
  - `buffered` (extra);
  - `address` (extra);
  - `actions`: a `DropdownMenu` with "Drain" or "Resume", "Rotate token", "Revoke" and "Remove". Each item is enabled per `nodeActions`.
- **Empty list:** `EmptyState` with "No workers yet" and an "Add worker" button.
- **Failures:**
  - 409 `not_control_plane`: an `Alert` reading "Nodes are available on a control plane. Set MEGOOFLAGS_ROLE=control-plane on this instance to add workers."
  - 403: the existing permission notice pattern.
  - Any other error: `failureText`.
- **Dialogs:**
  - `add-node-dialog.tsx`: a `ResponsiveDialog` with a Name field (pattern hint), then a step with a token `<code>`, the `.env` lines from `envLines(origin, token)` in a `<pre>`, a `CopyButton` ("Copy .env lines"), and the warning "Copy it now. You won't see it again."
  - `rotate-node-dialog.tsx`: an overlap select (0 = "Revoke now", 3600, 86400 as the default, 604800), then the same token step.
  - `ConfirmDialog` for drain, resume, revoke (destructive) and remove (destructive), with the texts given in step 25.
- **Refresh:** `useVisibleInterval(reload, 10_000)` re-runs `useApiData` every 10 s while `document.visibilityState === "visible"`.

---

## Steps

### 1. Settings: role and worker settings
**Files:** `api/src/megooflags_api/settings.py`, `api/src/megooflags_api/services/cluster/__init__.py`, `api/src/megooflags_api/services/cluster/config.py`, `api/tests/test_settings_cluster.py`.

1. Write the tests in `test_settings_cluster.py`:
   - `test_role_defaults_to_standalone`;
   - `test_role_rejects_unknown` (a `ValidationError` for `role="edge"`);
   - `test_empty_control_plane_url_and_token_are_none`;
   - `test_worker_defaults` (`data_dir == Path("/var/lib/megooflags")`, buffer 64, grace 30);
   - `test_worker_config_errors_lists_missing_settings` (url and token missing, both messages in order);
   - `test_worker_config_errors_refuses_http_unless_insecure`;
   - `test_worker_config_errors_rejects_non_node_token`;
   - `test_worker_config_errors_missing_ca_file` (with `tmp_path`);
   - `test_worker_config_ok` (https, `mf_node_` token: `[]`).
2. Run `cd api && uv run pytest tests/test_settings_cluster.py -q`. It fails on the import.
3. Implement C1 exactly, then run the test again; it passes. Run the API checks.
4. No CHANGELOG yet (nothing uses it). Commit `feat(api): role and worker settings (MF-86)`.

### 2. Version compatibility rule
**Files:** `api/src/megooflags_api/services/cluster/compat.py`, `api/tests/test_cluster_compat.py`.

1. Write a parametrized `test_incompatibility_table` over (worker, control plane, ok):
   - `("0.3.1","0.3.9",True)`, `("0.3.0","0.4.0",False)`, `("0.4.0","0.3.0",False)`;
   - `("1.2.0","1.2.5",True)`, `("1.1.9","1.2.0",True)`, `("1.0.0","1.2.0",False)`, `("1.3.0","1.2.0",False)`;
   - `("2.0.0","1.9.0",False)`, `("1.9.0","2.0.0",False)`;
   - `("0.3.0-rc.1","0.3.0",True)`, `("unknown","0.1.0",False)`, `("0.1","0.1.0",False)`.
2. Also write `test_message_names_both_versions` (both strings appear, plus "upgrade the control plane first") and `test_minor_of` (`"1.4.2" -> (1, 4)`, `"1.4.2-rc.3" -> (1, 4)`, ValueError for `"x"`).
3. Run `uv run pytest tests/test_cluster_compat.py -q`. It fails on the import.
4. Implement C2 as one pure module, with no I/O. Run the test again; it passes. Run the checks.
5. Commit `feat(api): version rule for worker nodes (MF-86)`.

### 3. Snapshot signing and the snapshot format
**Files:** `services/cluster/signing.py`, `services/cluster/snapshot_format.py`, `api/tests/test_cluster_signing.py`, `api/tests/test_cluster_snapshot_format.py`.

1. Write the signing tests:
   - `test_same_secret_same_key` (two signers agree, which covers several replicas);
   - `test_other_secret_other_key`;
   - `test_sign_and_verify`;
   - `test_tampered_data_fails`;
   - `test_verify_never_raises` (garbage key, signature and base64 return False).
2. Write the format tests:
   - `test_round_trip_bytes` (`from_bytes(to_bytes())` equals the original);
   - `test_header_json_is_canonical` (sorted keys, no spaces, UTC ISO dates);
   - `test_digest_ignores_version_and_published_at`;
   - `test_digest_changes_with_keys` (a revoked key changes it);
   - `test_digest_changes_with_ruleset`;
   - `test_newline_refused` (a ruleset with `b"\n"` raises SnapshotError);
   - `test_unknown_format_refused` (format 2);
   - `test_truncated_refused`;
   - `test_verify_with_public_key` (a flipped byte in the ruleset gives False);
   - `test_env_ref`.
3. Run `uv run pytest tests/test_cluster_signing.py tests/test_cluster_snapshot_format.py -q`. It fails on the import.
4. Implement C3 and C4, using `cryptography.hazmat.primitives.asymmetric.ed25519` (already a dependency) and HKDF as in `crypto.py`. Set `app.state.snapshot_signer` in `main.create_app` (None without a secret key).
5. Run the tests again; they pass. Run the checks.
6. Commit `feat(api): signed snapshot format (MF-86)`.

### 4. Cluster tables and migration 0015
**Files:** `models/cluster.py`, `models/__init__.py`, `models/enums.py`, `api/alembic/versions/0015_cluster.py`, `api/tests/test_cluster_tables.py`.

1. Write the DB tests (they need the `app` fixture):
   - `test_node_name_unique` (IntegrityError);
   - `test_node_token_cascades_on_node_delete`;
   - `test_environment_snapshot_cascades_on_environment_delete` (create a project and environment through `helpers.create_project`);
   - `test_snapshot_version_sequence_increases` (`SELECT nextval('snapshot_version_seq')` twice, second greater);
   - `test_event_batch_primary_key` (a duplicate batch id raises).
2. Run `uv run pytest tests/test_cluster_tables.py -q`. It fails on the import.
3. Implement C5's models and enums. Write `0015_cluster.py` with `revision = "0015"` and `down_revision = "0014"`, in the 0014 style, with `op.execute(sa.schema.CreateSequence(...))` and a `downgrade()` that drops the tables in reverse order and then the sequence.
4. Run the tests again; they pass. Then run `bash scripts/check_migrations.sh` and `uv run pytest tests/test_models_migration.py tests/test_migrations.py -q` (metadata against migration).
5. Commit `feat(api): tables for nodes and snapshots (MF-86)`.

### 5. Delivery interfaces: refactor the SDK path (no behaviour change)
**Files:** `services/delivery.py` (new), `routers/sdk.py`, `routers/ofrep.py`, `sdk_auth.py`, `services/streams.py`, `services/ruleset_cache.py`, `main.py`, `api/tests/test_delivery.py`.

1. Write `test_delivery.py`. Its tests build a **fake** `Delivery` with in-memory classes and no database: a ruleset source serving `from_body` of the `test_sdk.RULESET` JSON, a key source with one server and one client principal, a recording sink, and a limiter that always allows.
   - Create `app = create_app(Settings())`, replace `app.state.delivery`, and call through `httpx.ASGITransport`.
   - `test_ruleset_served_from_source` (body and ETag);
   - `test_evaluate_uses_source_and_records_counts`;
   - `test_ofrep_uses_source`;
   - `test_events_go_to_sink`;
   - `test_unknown_key_401` and `test_client_key_rate_limited` (a limiter returning 7 gives 429 with `Retry-After: 7`);
   - `test_from_body_etag_matches_prepare` (`from_body(cached.body).etag == cached.etag` for a `RulesetCache` entry built by `_prepare`);
   - `test_stream_hub_with_source` (a hub built with `source=` refreshes through `current()` and offers `Revoked` for None);
   - `test_close_all_ends_without_frame` (`Closed` is final; `_events` returns without `REVOKED_FRAME`).
2. Run `uv run pytest tests/test_delivery.py -q`. It fails.
3. Implement C6:
   - Move today's route logic into the adapters.
   - Routers and `sdk_auth` reach state only through `get_delivery(request)`, plus `stream_hub` and `settings`.
   - In `_events`, the heartbeat re-validation uses `delivery.keys.resolve_hash`, and a `Closed` item returns.
   - `main.create_app` sets `app.state.delivery = db_delivery(app)` after the services exist.
4. Run the new tests, then **unchanged** `tests/test_sdk.py tests/test_ofrep.py tests/test_stream.py tests/test_fanout.py tests/test_events.py tests/test_sdk_auth.py tests/test_key_rotation.py tests/test_evaluate.py tests/test_scale.py`, then the full checks. OpenAPI must not change (`git diff --exit-code api/openapi.json` after export).
5. Commit `refactor(api): SDK routes read through delivery interfaces (MF-86)`.

### 6. Node registry service
**Files:** `services/cluster/nodes.py`, `api/tests/test_cluster_nodes.py`.

1. Write the pure tests for `node_status` (build `Node` objects in memory):
   - `test_status_pending`;
   - `test_status_healthy`;
   - `test_status_stale_after_30_s` (exactly 30 s is healthy, 31 s stale);
   - `test_status_draining_wins_over_healthy`;
   - `test_status_incompatible_until_next_heartbeat`;
   - `test_status_revoked_wins` (and `active_tokens=0` is Revoked);
   - `test_syncing` (healthy with an older snapshot version).
2. Write the DB tests (`app` fixture):
   - `test_create_returns_token_once_and_stores_hash` (the token matches `^mf_node_[0-9A-Za-z]{32}$`, the row holds the sha256, and the token's prefix is the first 12 characters);
   - `test_duplicate_name_409`;
   - `test_authenticate_token` (unknown gives None; revoked or expired gives `active=False`);
   - `test_rotate_overlap_keeps_old_until_expiry` (with the DB clock: the old token has `expires_at`, both authenticate as active);
   - `test_rotate_zero_revokes_old`;
   - `test_revoke_revokes_all_tokens`;
   - `test_drain_resume`;
   - `test_operations_on_revoked_409`;
   - `test_delete_requires_revoked`;
   - `test_list_counts_active_tokens`.
3. Run `uv run pytest tests/test_cluster_nodes.py -q`. It fails.
4. Implement C7, reusing `sdk_keys.BASE62` and `sdk_keys.is_inactive`. Take timestamps with `select(func.now())` as `sdk_keys` does. Run the tests again; they pass. Run the checks.
5. Commit `feat(api): node registry with rotating tokens (MF-86)`.

### 7. Nodes management API
**Files:** `schemas/nodes.py`, `routers/nodes.py`, `errors.py`, `services/changes.py`, `main.py`, `api/tests/test_nodes_api.py`, `api/tests/test_audit_coverage.py`, `api/tests/test_changes.py`, `api/openapi.json`, `web/lib/api/schema.d.ts`, `CHANGELOG.md`.

1. Write `test_nodes_api.py`. Its fixture `cp_api` sets `app.state.settings = app.state.settings.model_copy(update={"role": "control-plane"})` on the `app` fixture and uses the `api` client.
   - `test_create_list_rotate_revoke_drain_resume_delete` (the full flow and the shapes from C8; `token` only in create and rotate);
   - `test_list_has_versions` (`version == __version__`, `snapshot_version == 0`);
   - `test_standalone_409_not_control_plane` (the plain `api` fixture);
   - `test_requires_org_manage` (a viewer user via `seed_user` and `seed_binding` gets 403; anonymous gets 401);
   - `test_delete_unrevoked_409`;
   - `test_audit_entries_never_hold_the_token` (`helpers.audit_entries`: six actions, no `mf_node_` in the JSON);
   - `test_node_actions_publish_node_message` (a recording `on_node` callback).
2. In `test_changes.py`, add `test_dispatch_node_message`.
3. In `test_audit_coverage.py`, add samples for the six mutating node routes. Their `prepare` switches the role as above and creates a node through the API (revoke it first for DELETE).
4. Run `uv run pytest tests/test_nodes_api.py tests/test_changes.py tests/test_audit_coverage.py tests/test_route_permissions.py -q`. It fails.
5. Implement C8, and include `nodes` in the `/api` router loop in `main.py`.
6. Regenerate the OpenAPI spec and the client. Add to CHANGELOG under Added: "Nodes API (`/api/nodes`) to add, drain, resume, rotate, revoke and remove worker nodes on a control plane."
7. Run the checks. Commit `feat(api): nodes management endpoints (MF-86)`.

### 8. Snapshot builder with version assignment
**Files:** `services/cluster/snapshots.py`, `api/tests/test_cluster_snapshots.py`.

1. Write the tests (`app` fixture, `setup_demo` from `test_sdk`):
   - `test_build_signs_exact_ruleset_bytes` (`snap.ruleset == (await cache.get_entry(..., max_age=0)).body`, and it verifies with the signer's key);
   - `test_unchanged_content_keeps_version`;
   - `test_flag_change_bumps_version`;
   - `test_key_revoke_and_create_change_header_and_version`;
   - `test_versions_are_cluster_wide` (two environments get different, increasing numbers);
   - `test_concurrent_builds_never_go_backwards`: start two builds in separate sessions with a flag change committed between them (use an `asyncio.Event` to pause the first after the lock via a wrapped cache). Assert that the snapshot with the higher version carries the newer ruleset, and that the stored digest matches the newest content.
   - `test_deleted_environment_returns_none`;
   - `test_current_snapshot_version`.
2. Run `uv run pytest tests/test_cluster_snapshots.py -q`. It fails.
3. Implement the C5 version assignment and `environment_refs`. Run the tests again; they pass. Run the checks.
4. Commit `feat(api): versioned environment snapshots (MF-86)`.

### 9. SDK key changes publish the project change
**Files:** `routers/sdk_keys.py`, `api/tests/test_sdk_keys.py`.

1. Add `test_key_create_rotate_revoke_publish_project` to `test_sdk_keys.py`, with a recording `app.state.change_bus.on_project` callback: create, rotate and revoke each dispatch the project id.
2. Run `uv run pytest tests/test_sdk_keys.py -q`. It fails.
3. In each of the three routes, after the commit, call `await bus.publish(environment.project_id)`. Keep the existing `publish_key_revoked` calls, and add `bus: ChangesDep` to `create_sdk_key`.
4. Run `tests/test_sdk_keys.py tests/test_key_rotation.py tests/test_stream.py`, then the checks.
5. Commit `feat(api): SDK key changes reach the change bus (MF-86)`.

### 10. Worker-facing endpoints: auth, heartbeat, events
**Files:** `schemas/cluster.py`, `routers/cluster.py`, `services/cluster/ingest.py`, `auth/require.py` (PUBLIC_ROUTES), `services/scheduler.py`, `main.py`, `api/tests/test_cluster_endpoints.py`, `api/tests/test_audit_coverage.py` (NOT_AUDITED), `api/tests/test_scheduler.py`, OpenAPI and client.

1. Write `test_cluster_endpoints.py` (the control-plane role as in step 7; create a node through `/api/nodes`; `H = {"Authorization": f"Bearer {token}", "X-MegooFlags-Version": __version__}`):
   - `test_standalone_answers_409_not_control_plane`;
   - `test_unknown_token_401`;
   - `test_revoked_403_node_revoked`;
   - `test_expired_token_403` (rotate with overlap 1, then set `expires_at` in the past by SQL);
   - `test_incompatible_409_records_refusal` (header `"9.0.0"`: 409 `incompatible_version`, both versions in the message, and `GET /api/nodes` shows `incompatible` with `refused_version`);
   - `test_missing_version_header_409`;
   - `test_heartbeat_updates_node_and_returns_state` (status healthy, `synced_at` about now minus age; after drain the state is `draining`);
   - `test_events_stored_once` (the same batch id twice: counts added once, both 202);
   - `test_events_for_deleted_environment_accepted`;
   - `test_events_context_dropped_unless_persist`.
2. Extend `test_scheduler.py` with `test_batch_retention_deletes_after_7_days`.
3. Add the three routes to `NOT_AUDITED` (worker traffic, not admin actions), with a comment.
4. Run `uv run pytest tests/test_cluster_endpoints.py tests/test_scheduler.py tests/test_route_permissions.py tests/test_audit_coverage.py -q`. It fails.
5. Implement `require_node`, heartbeat and events from C9, `ingest.py`, and the scheduler job. Write the stream route as a stub answering 501 for now. Its test arrives in step 11, so leave the stub out of the router until then and add only the heartbeat and events routes and their PUBLIC_ROUTES entries.
6. Regenerate OpenAPI and the client. Run the checks.
7. Commit `feat(api): worker heartbeat and event ingest (MF-86)`.

### 11. NodeHub and the snapshot stream
**Files:** `services/cluster/node_hub.py`, `routers/cluster.py`, `auth/require.py`, `metrics.py` (`NODE_STREAMS`), `main.py`, `api/tests/test_cluster_stream.py`, `api/tests/test_audit_coverage.py`, OpenAPI and client.

1. Write `test_cluster_stream.py`. Serve the control-plane app with `helpers.running` (`Settings(role="control-plane", sse_heartbeat_seconds=1, stream_recheck_seconds=1)`) and read events with `helpers.next_events`.
   - `test_first_connect_hello_puts_synced` (`hello` fields; one `put` per demo environment whose three data lines parse with `SignedSnapshot.from_bytes` and verify with the hello key; then `synced` with the max version);
   - `test_versions_already_current_get_only_synced`;
   - `test_key_mismatch_resends_everything`;
   - `test_removed_environment_sends_remove` (send a random environment id in `versions`);
   - `test_flag_change_sends_put_under_1_s`;
   - `test_environment_delete_sends_remove`;
   - `test_drain_and_resume_send_state`;
   - `test_revoke_sends_revoked_and_closes`;
   - `test_ping_every_heartbeat`;
   - `test_lost_change_message_caught_by_recheck` (change a flag by SQL without publishing: a `put` within 3 s);
   - `test_node_streams_metric`.
   Also add unit tests (no server) for `NodeStream`: `test_node_stream_keeps_newest_per_environment` and `test_revoked_is_final`.
2. Run `uv run pytest tests/test_cluster_stream.py -q`. It fails.
3. Implement NodeHub and the stream route from C9. Wire them in `main.py` (callbacks; the recheck loop in `background_jobs` only on control-plane), and add the route to PUBLIC_ROUTES and NOT_AUDITED.
4. Regenerate OpenAPI and the client. Run the checks.
5. Commit `feat(api): snapshot stream to worker nodes (MF-86)`.

### 12. Worker snapshot store
**Files:** `services/worker/__init__.py`, `services/worker/store.py`, `api/tests/test_worker_store.py`.

1. Write the tests (no DB; build signed snapshots with `SnapshotSigner("s"*32)` and the `test_sdk.RULESET` bytes; `tmp_path`):
   - `test_apply_writes_atomically_and_reloads` (a new store on the same directory serves the same `entry().etag` and `principal()`);
   - `test_apply_rejects_bad_signature_and_keeps_old`;
   - `test_apply_rejects_older_or_equal_version`;
   - `test_corrupt_file_discarded_on_load` (truncate a `.snap`: the file is deleted, `valid` is False, and other environments still serve);
   - `test_tampered_file_discarded`;
   - `test_remove_deletes_file_and_keys`;
   - `test_principal_reports_revoked_and_expiry`;
   - `test_lru_reads_from_disk` (`max_parsed=1`, two environments, both served);
   - `test_meta_round_trip`;
   - `test_no_tmp_files_left`.
2. Run `uv run pytest tests/test_worker_store.py -q`. It fails.
3. Implement C10. Run the tests again; they pass. Run the checks.
4. Commit `feat(api): worker snapshot store on disk (MF-86)`.

### 13. Worker delivery sources and in-memory limiter
**Files:** `services/worker/sources.py`, `api/tests/test_worker_sources.py`.

1. Write the tests:
   - `test_ruleset_source_serves_store_entry`;
   - `test_unknown_environment_raises` (the source raises `RuntimeError`; routes never reach this because keys only come from the index);
   - `test_key_source_resolve_and_resolve_hash` (a wrong prefix or length gives None, as `KeyResolver`);
   - `test_stream_source_current_none_after_remove`;
   - `test_stream_source_inactive_keys` (revoked, expired and missing);
   - `test_memory_limiter_window` (with a fake clock: the limit+1th hit returns the seconds to the next minute, and the next window resets);
   - `test_memory_limiter_prunes_old_windows`.
2. Run `uv run pytest tests/test_worker_sources.py -q`. It fails.
3. Implement:
   - `StoreRulesetSource`, `StoreKeySource`, `StoreStreamSource` (`entry()` through `asyncio.to_thread`);
   - `MemoryRateLimiter(limit_per_minute, clock=time.time)`, with the same window and Retry-After maths as `RateLimiter`;
   - `BufferEventSink`, written against the C11 interface but implemented in step 14. Leave it out here and add it there.
4. Run the tests again; they pass. Run the checks.
5. Commit `feat(api): worker delivery sources (MF-86)`.

### 14. Worker event buffer
**Files:** `services/worker/buffer.py`, `services/worker/sources.py` (`BufferEventSink`), `api/tests/test_worker_buffer.py`.

1. Write the tests (`tmp_path`, fake clock):
   - `test_counts_summed_and_sealed`;
   - `test_exposures_spill_at_memory_limit`;
   - `test_context_removed_unless_persist`;
   - `test_drop_oldest_exposures_over_max_bytes` (counts kept, `dropped_total` grows);
   - `test_files_older_than_7_days_dropped_on_load`;
   - `test_restart_keeps_batches` (seal, a new buffer on the same directory, the same batch ids pending);
   - `test_forwarder_oldest_first_and_deletes_on_accept`;
   - `test_forwarder_keeps_on_retry_and_resends_same_batch_id` (the ambiguous timeout case);
   - `test_forwarder_deletes_rejected`;
   - `test_batches_at_most_1000_items`;
   - `test_sink_records_counts_and_events` (`BufferEventSink.store` for an `EventsIn`).
2. Run `uv run pytest tests/test_worker_buffer.py -q`. It fails.
3. Implement C11 and `BufferEventSink(buffer, persist_context: Callable[[], bool])`. Run the tests again; they pass. Run the checks.
4. Commit `feat(api): bounded event buffer on workers (MF-86)`.

### 15. Worker state and control-plane client
**Files:** `services/worker/state.py`, `services/worker/client.py`, `api/tests/test_worker_client.py`.

1. Write the tests with `httpx.MockTransport`. The handler returns a `Response` whose `stream` is an async byte iterator emitting SSE frames built from signed test snapshots.
   - `test_state_readiness_matrix` (not synced gives not_ready; synced and valid gives None; draining gives draining; revoked wins);
   - `test_sync_applies_puts_and_marks_synced` (the request body holds the store's versions and public key; headers carry the token and `X-MegooFlags-Version`);
   - `test_bad_signature_put_ignored`;
   - `test_remove_notifies_hub` (a fake hub records `notify_project`);
   - `test_state_draining_then_close_all_after_grace`;
   - `test_revoked_event_marks_revoked_and_stops`;
   - `test_403_node_revoked_stops`;
   - `test_409_incompatible_retries_after_delay`;
   - `test_reconnect_with_backoff_after_network_error` (a first call raises `httpx.ConnectError`);
   - `test_heartbeat_body`;
   - `test_send_events_results` (202 accepted, 422 rejected, 503 or ConnectError retry);
   - `test_tls_settings` (with insecure off, a CA file builds a context with that CA; `verify` is an `ssl.SSLContext`).
2. Run `uv run pytest tests/test_worker_client.py -q`. It fails.
3. Implement C12's state and client. Run the tests again; they pass. Run the checks.
4. Commit `feat(api): worker sync loop and heartbeat (MF-86)`.

### 16. Worker application, readiness, 503 gate, metrics and serve
**Files:** `services/worker/app.py`, `main.py`, `routers/health.py`, `sdk_auth.py`, `errors.py`, `metrics.py`, `serve.py`, `api/tests/test_worker_app.py`, `api/tests/test_serve.py`, `api/tests/test_health.py`, OpenAPI and client, `CHANGELOG.md`.

1. Write `test_worker_app.py` (no DB or Valkey: a worker app on `tmp_path` with a `MockTransport` that refuses connections):
   - `test_worker_has_no_api_routes` (`/api/projects` is 404; `app.state` has no `engine`);
   - `test_healthz_reports_role`;
   - `test_not_ready_without_snapshot_and_sdk_503` (`/readyz` 503 `not_ready`; `/sdk/v1/ruleset` with a key 503 `not_ready`);
   - `test_ready_from_disk_without_control_plane` (pre-write signed snapshots and a synced `cluster.json`, then start: 200 and the ruleset served);
   - `test_revoked_on_disk_serves_503_revoked`;
   - `test_draining_readyz_503_but_serves`;
   - `test_metrics_exposes_worker_gauges`;
   - `test_lifespan_refuses_bad_config`.
2. In `test_serve.py`, add:
   - `test_worker_starts_one_process_without_secret_or_valkey`;
   - `test_worker_ignores_api_workers_with_log` (caplog);
   - `test_worker_refuses_missing_settings`.
3. In `test_health.py`, assert `role == "standalone"`.
4. Run `uv run pytest tests/test_worker_app.py tests/test_serve.py tests/test_health.py -q`. It fails.
5. Implement C12's app and `main.create_app` branch, C13, the `serve.py` worker path, the 503 gate and the worker `/readyz`.
6. Regenerate OpenAPI and the client (the `role` and 503 changes). Add to CHANGELOG under Added: "`MEGOOFLAGS_ROLE` (`standalone`, `control-plane`, `worker`): workers serve `/sdk/*` and `/ofrep/*` from a signed snapshot on disk and keep serving while the control plane is down."
7. Run the checks. Commit `feat(api): worker role application (MF-86)`.

### 17. Instance role in /api/auth/me
**Files:** `schemas/auth.py` (`MeOut.role: InstanceRole`), `routers/auth.py`, `api/tests/test_me_permissions.py` (or the `/me` test file that exists), OpenAPI and client.

1. Add the test `test_me_reports_instance_role`: `standalone` by default, and `control-plane` after the settings `model_copy` switch.
2. Run it; it fails. Implement, using `InstanceRole(request.app.state.settings.role)`.
3. Regenerate OpenAPI and the client. Run the checks.
4. Commit `feat(api): instance role in /api/auth/me (MF-86)`.

### 18. Cluster harness: sync, readiness, propagation and equal answers
**Files:** `api/tests/cluster/cluster_harness.py`, `api/tests/cluster/conftest.py`, `api/tests/cluster/test_sync.py`.

1. Build C14. Then write the tests in `test_sync.py`:
   - `test_first_sync_makes_worker_ready` (not ready before the node exists; ready within 5 s; `GET /api/nodes` shows healthy with the current snapshot version);
   - `test_change_reaches_worker_sdk_stream_under_5_s` (open `/sdk/v1/stream` on the worker with a server key, toggle the flag on the control plane, receive `put` within 5 s);
   - `test_answers_equal_control_plane` (ruleset body and ETag, `/sdk/v1/evaluate` body and ETag for server and client keys, OFREP single and bulk bodies and ETags, for the same context, identical between `cp_url` and `worker.url`);
   - `test_revoked_sdk_key_refused_on_worker`;
   - `test_new_sdk_key_works_on_worker`.
2. Run `uv run pytest tests/cluster/test_sync.py -q`. Fix what fails in the worker or control-plane code (each fix with a focused unit test in the matching `test_worker_*` or `test_cluster_*` file).
3. Run the checks. Commit `test(api): in-process cluster tests for sync (MF-86)`.

### 19. Cluster tests: outages, restart and buffered events
**Files:** `api/tests/cluster/test_outages.py`, plus fixes where needed.

1. Write the tests:
   - `test_link_down_keeps_serving` (cut, then ruleset, evaluate and an SDK stream still work; the `megooflags_worker_control_plane_connected` gauge is 0; `/readyz` stays 200);
   - `test_restart_from_disk_with_link_down` (cut, `restart()`, ready at once with the same ETag);
   - `test_resync_on_reconnect_sends_only_changes` (cut, change one flag, restore: the worker's version is the new max, and the control plane sent one `put`; check with a counter on `NodeHub.offer` or the worker's applied count);
   - `test_buffered_events_stored_exactly_once` (cut, send `/sdk/v1/events` with counts 3, evaluate twice, restore: after the forwarder runs, the evaluation counts in the database are exactly 3 plus 2 evaluations; repeat a forwarder pass and they do not change);
   - `test_ambiguous_forward_stored_once` (make the link drop the response of the first events POST after the control plane handled it; the same batch is resent and counted once);
   - `test_stale_status_when_heartbeats_stop` (cut, set `last_heartbeat_at` back 31 s by SQL, the status is stale).
2. Run `uv run pytest tests/cluster/test_outages.py -q`. Fix what fails, with focused unit tests. Run the checks.
3. Commit `test(api): cluster outage tests (MF-86)`.

### 20. Cluster tests: revoke, rotate, drain, versions, keys and environments
**Files:** `api/tests/cluster/test_lifecycle.py`, plus fixes where needed.

1. Write the tests:
   - `test_revoke_stops_serving_and_survives_restart` (503 `revoked`, `/readyz` `revoked`, open SDK streams get `revoked`; after `restart()` still revoked);
   - `test_rotate_overlap_then_new_token` (rotate with overlap: the old worker keeps working; a worker restarted with the new token is healthy; after the overlap ends, which you force by SQL plus `recheck_all`, the old-token worker is revoked);
   - `test_drain_then_resume` (`/readyz` 503 `draining` while ruleset requests succeed; after the 0.5 s grace, open SDK streams end without `revoked`; resume makes it ready);
   - `test_incompatible_version_refused_and_shown` (`add_worker(version="9.0.0")`: Incompatible on the page, the worker not ready; a worker with snapshots on disk keeps serving them);
   - `test_environment_delete_removes_on_worker` (keys answer 401 and streams get `revoked`);
   - `test_changed_secret_key_refetches_everything` (restart the control plane app with another secret: the worker gets every snapshot again and stays consistent);
   - `test_worker_against_standalone_logs_not_control_plane`.
2. Run `uv run pytest tests/cluster/test_lifecycle.py -q`. Fix what fails, with focused unit tests. Run the whole API suite and the checks.
3. Commit `test(api): cluster lifecycle tests (MF-86)`.

### 21. Web: node helpers and the nav rule
**Files:** `web/lib/nodes/status.ts`, `web/lib/nodes/format.ts`, `web/lib/nav.ts`, `web/lib/shell-data.ts` (nothing new to load: the role comes from `me`), `web/components/shell/nav.tsx`, `web/components/shell/app-shell.tsx`, `web/components/shell/command-palette.tsx` (if it maps keys), `web/tests/unit/nodes-status.test.ts`, `web/tests/unit/nodes-format.test.ts`, `web/tests/unit/nav.test.ts`.

1. Write the Vitest tests first:
   - `nodes-status.test.ts`:
     - `nodePill` maps healthy to `{status:"on",label:"Healthy"}`, stale to `stale`/"Stale", pending to `pending`/"Pending", draining to `off`/"Draining", incompatible to `error`/"Incompatible", revoked to `killed`/"Revoked";
     - `nodeActions(node)` returns `{drain, resume, rotate, revoke, remove}` booleans: drain when not draining and not revoked; resume when draining; rotate and revoke when not revoked; remove only when revoked;
     - `summary(list)` gives `{workers, healthy, attention}`.
   - `nodes-format.test.ts`:
     - `relativeTime(iso, now)` gives "just now" under 10 s, then "12 s ago", "3 min ago", "2 h ago", "4 d ago", and "Never" for null;
     - `snapshotText(node, current)` gives "v42 · current", "v40 · syncing" (healthy and syncing), "v40 · 3 min old" (stale, from `synced_at`), "—" (pending);
     - `envLines(origin, token)` gives the three lines `MEGOOFLAGS_ROLE=worker`, `MEGOOFLAGS_CONTROL_PLANE_URL=<origin>`, `MEGOOFLAGS_NODE_TOKEN=<token>`.
   - `nav.test.ts`: change `navItems(project, perms)` to `navItems(project, perms, role)`. Rename the first test to "lists all items in order for an admin on a standalone instance". Add "shows nodes above settings on a control plane with org.manage", "hides nodes without org.manage" and "hides nodes on standalone and worker". Update the other calls with `"standalone"`.
2. Run `cd web && pnpm exec vitest run tests/unit/nodes-status.test.ts tests/unit/nodes-format.test.ts tests/unit/nav.test.ts`. It fails.
3. Implement:
   - `NavKey` gains `"nodes"`, href `/nodes`;
   - `NAV_ICONS.nodes = Server` (lucide);
   - `AppShell` passes `shell.me?.role ?? null`;
   - replace the "Nodes stays hidden" comment.
4. Run `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test`.
5. Commit `feat(web): node helpers and Nodes nav item (MF-86)`.

### 22. Web: Nodes page with list and summary cards
**Files:** `web/app/(app)/nodes/page.tsx`, `web/components/nodes/node-list.tsx`, `web/components/nodes/node-summary.tsx`, `web/hooks/use-visible-interval.ts`, `web/tests/unit/use-visible-interval.test.ts`.

1. Write the Vitest `use-visible-interval.test.ts` with fake timers and a stubbed `document.visibilityState`: it calls back every interval while visible, not while hidden, and once at once on becoming visible. This file needs the same test environment as the other unit tests; if `vitest.config.mts` runs `node`, keep the hook's logic in a pure `createVisibleInterval(callback, ms, doc)` exported from the hook file and test that.
2. Run it; it fails. Implement the hook.
3. Build W1's page, list, summary and failure states (the menu items are wired in step 23; render them disabled here).
   - Load with `api.GET("/api/nodes")` and `must`, following `integration-list.tsx` for `useApiData`, `failureText` and `Skeleton`.
   - Put times in `<time dateTime>`.
4. Run `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build`.
5. Commit `feat(web): Nodes page (MF-86)`.

### 23. Web: add, drain, resume, rotate, revoke and remove
**Files:** `web/components/nodes/add-node-dialog.tsx`, `web/components/nodes/rotate-node-dialog.tsx`, `web/components/nodes/node-token.tsx` (shared token step), `web/components/nodes/node-list.tsx`.

1. Implement W1's dialogs:
   - Add: the Name field validated against the C8 pattern, with the inline error "Use letters, digits, dots, dashes or underscores". On 409 `key_conflict` show "A node with this name already exists".
   - Rotate: overlap select, then the token step with the new token and `.env` lines.
   - The ConfirmDialog texts:
     - Drain: "Drain {name}?" / "It stops taking new traffic: /readyz turns unready and after the grace time its SDK streams close so SDKs reconnect elsewhere." / "Drain".
     - Resume: "Resume {name}?" / "It becomes ready and takes traffic again." / "Resume".
     - Revoke: "Revoke {name}?" / "It stops serving at once and answers SDK requests with 503. This can't be undone; add a new worker to replace it." / "Revoke" (destructive).
     - Remove: "Remove {name}?" / "It disappears from this list." / "Remove" (destructive).
   - Each action calls the C8 endpoint, shows a toast ("{name} is draining", "Token rotated", "{name} revoked", "{name} removed"), and reloads the list. Errors go through `showError` as in `integration-list.tsx`.
2. Run `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test && pnpm build`.
3. Commit `feat(web): node actions and the add worker dialog (MF-86)`.

### 24. Web e2e: Nodes spec, matrix and the control-plane role in CI
**Files:** `web/e2e/nodes.spec.ts`, `web/e2e/global-setup.ts`, `web/e2e/matrix.spec.ts`, new `web/e2e/matrix.spec.ts-snapshots/nodes-*.png`, `.github/workflows/ci.yml` (web-e2e env `MEGOOFLAGS_ROLE: control-plane`), `AGENTS.md` (section 9 e2e command gains `MEGOOFLAGS_ROLE=control-plane`).

1. In `global-setup.ts`, delete every node except `matrix-worker` and make sure a node `matrix-worker` exists (create it if missing). It never connects, so it stays Pending and stable.
2. Write `nodes.spec.ts`:
   - "adds a worker and shows its token once": the dialog shows `mf_node_` and the three `.env` lines; after Done, reopening the page shows the row Pending and no token anywhere;
   - "drains and resumes": the status becomes Draining, then Pending again;
   - "rotates the token": the new token is shown once;
   - "revokes and removes": Revoked, then Remove enabled, then the row is gone;
   - "a viewer has no Nodes link": with `VIEWER` storage state.
   Use unique names (`e2e-${Date.now()}`) and remove them at the end.
3. Add `{ name: "nodes", path: "/nodes", mask: true }` to `PAGES`.
4. Run the API as AGENTS.md section 9 with `MEGOOFLAGS_ROLE=control-plane`, then `cd web && pnpm build && pnpm e2e nodes.spec.ts`. Create the baselines **only** with `pnpm e2e:docker matrix.spec.ts -g nodes --update-snapshots`, and check that the other baselines are unchanged (`git status web/e2e`).
5. Run the full `pnpm e2e`. Commit `test(web): Nodes e2e and visual matrix (MF-86)`.

### 25. Compose: role setting and the control-plane file
**Files:** `deploy/docker-compose.yml`, `deploy/docker-compose.control-plane.yml`, `deploy/.env.example`, `api/Dockerfile`, `scripts/tests/test_compose_files.py`, `CHANGELOG.md`.

1. Write `scripts/tests/test_compose_files.py` (it runs under `uv run --frozen --project api pytest scripts/tests`):
   - `test_control_plane_file_matches_single_node`: the text of `docker-compose.control-plane.yml` equals `docker-compose.yml` with `${MEGOOFLAGS_ROLE:-standalone}` replaced by `${MEGOOFLAGS_ROLE:-control-plane}`, and nothing else differs;
   - `test_role_passed_to_api`: the line `MEGOOFLAGS_ROLE: ${MEGOOFLAGS_ROLE:-standalone}` is present;
   - `test_dockerfile_creates_data_dir`: the Dockerfile has `mkdir -p /var/lib/megooflags` and `chown 10001`.
2. Run `uv run --frozen --project api pytest scripts/tests/test_compose_files.py`. It fails.
3. Add `MEGOOFLAGS_ROLE` to the `api` environment, and create the control-plane file. In the Dockerfile, before `USER 10001`, add `RUN mkdir -p /var/lib/megooflags && chown 10001:10001 /var/lib/megooflags`. Document `MEGOOFLAGS_ROLE` in `.env.example`.
4. Add to CHANGELOG under Added: "`docker-compose.control-plane.yml` for the control plane of a multi-node deployment."
5. Run the test again; it passes. Then run `docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh http` (standalone unchanged).
6. Commit `feat(deploy): MEGOOFLAGS_ROLE and the control-plane compose file (MF-86)`.

### 26. Compose: the worker bundle and release files
**Files:** `deploy/docker-compose.worker.yml`, `deploy/Caddyfile.worker`, `deploy/.env.worker.example`, `.github/workflows/release.yml`, `scripts/tests/test_compose_files.py`, `CHANGELOG.md`.

1. Extend the tests:
   - `test_worker_bundle_settings`: the worker compose sets `MEGOOFLAGS_ROLE: worker` and requires `MEGOOFLAGS_CONTROL_PLANE_URL` and `MEGOOFLAGS_NODE_TOKEN` (`:?`). It passes `MEGOOFLAGS_CONTROL_PLANE_INSECURE`, `MEGOOFLAGS_CONTROL_PLANE_CA_FILE`, `MEGOOFLAGS_WORKER_BUFFER_MAX_MB`, `MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS`, `MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE`, `MEGOOFLAGS_SSE_HEARTBEAT_SECONDS` and `MEGOOFLAGS_LOG_LEVEL`. It mounts `worker_data:/var/lib/megooflags` and `./certs:/etc/megooflags/certs:ro`, and has no `db`, `valkey`, `web` or `migrate` service.
   - `test_worker_caddyfile_routes`: it proxies only `/sdk/* /ofrep/* /healthz /readyz` to `api:8000` with `flush_interval -1`, answers 404 otherwise, and uses `{$MEGOOFLAGS_SITE_ADDRESS::80}`.
   - `test_env_worker_example_lists_settings`: every setting from C1's worker rows appears.
   - `test_release_attaches_cluster_files`: `release.yml` names the four new files.
2. Run the tests; they fail.
3. Write the files:
   - The worker compose mirrors the `caddy` and `api` services of `docker-compose.yml`, with the same ports and TLS logic, and `name: megooflags-worker`.
   - The Caddyfile keeps the same global and header blocks without the CSP (no HTML is served).
   - Add the files to the `gh release` asset list in `release.yml`.
4. Add to CHANGELOG under Added: "`docker-compose.worker.yml`, `Caddyfile.worker` and `.env.worker.example` to run a worker node."
5. Check `docker compose -f deploy/docker-compose.worker.yml config` with dummy env values. Run `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest`.
6. Commit `feat(deploy): worker compose bundle (MF-86)`.

### 27. Load balancer samples
**Files:** `docs/examples/multi-node/README.md`, `nginx.conf`, `haproxy.cfg`, `Caddyfile`, `traefik.yml`, `scripts/tests/test_compose_files.py`.

1. Write the test `test_lb_samples_cover_settings`: each sample names the upstreams `worker-1`, `worker-2` and `worker-3` on port 80, uses `/readyz` as its health check, turns buffering off for SSE (nginx `proxy_buffering off`; HAProxy `timeout tunnel`; Caddy `flush_interval -1`; Traefik `flushInterval`), has a read or idle timeout above 60 s, and routes only `/sdk/` and `/ofrep/`.
2. Run it; it fails.
3. Write the samples:
   - nginx terminates TLS with `/etc/nginx/tls/fullchain.pem` and `privkey.pem`, and keeps the HTTP/1.1 upstream keepalive.
   - The README explains each file, the cloud load balancers (AWS ALB idle timeout, GCP backend service timeout, Azure Application Gateway request timeout, and the `/readyz` health check), and that worker hostnames are placeholders.
4. Run the test again; it passes. Check the nginx sample with `docker run --rm -v "$PWD/docs/examples/multi-node/nginx.conf:/etc/nginx/nginx.conf:ro" --add-host worker-1:127.0.0.1 --add-host worker-2:127.0.0.1 --add-host worker-3:127.0.0.1 nginx:1.27 nginx -t`, mounting dummy certs made with openssl in a temporary directory.
5. Commit `docs: load balancer samples for workers (MF-86)`.

### 28. Failure-scenario runner (`cluster_failures.py`)
**Files:** `scripts/cluster_failures.py`, `scripts/tests/test_cluster_failures.py`.

1. Read `scripts/propagation_test.py` and `scripts/latency.py`. The new script is **stdlib only** and imports `parse_sse`, `percentile` and the flag-toggle helpers from them.
2. Write the unit tests for its pure parts:
   - `test_status_wait_logic` (`all_status(nodes, names, "healthy")`);
   - `test_eval_total` (sums the hourly buckets from the evaluations endpoint JSON);
   - `test_p95_gate`;
   - `test_arguments`.
3. Run `uv run --frozen --project api pytest scripts/tests/test_cluster_failures.py`. It fails.
4. Implement the CLI:
   ```
   cluster_failures.py --lb URL --cp URL --worker URL --worker URL --worker URL --ca FILE --token TOKEN --ctl "bash scripts/cluster_test.sh ctl"
   ```
   Actions run through `subprocess.run(shlex.split(ctl) + [action, *args])`: `stop-worker N`, `start-worker N`, `restart-worker N`, `stop-control-plane`, `start-control-plane`, `stop-db`, `start-db`, `start-fresh-worker`, `stop-fresh-worker` (fresh worker URL `--fresh URL`), and `mem-worker N` (prints MiB).
5. The phases, each printing `PASS <row>` or exiting 1 with the reason:
   0. **Setup:** through `--cp`, create project `cluster` (flags `kill-me` and `outage-counter`, production environment), a server key and a client key; create nodes `worker-1..3`. The tokens are passed by the shell script, which creates them before the workers start; see step 29. Wait until all three nodes are healthy and current.
   1. **One worker down:** `stop-worker 2`. 50 ruleset, evaluate and OFREP requests through `--lb` all answer 200. A stream through the LB that ends is reopened through the LB and receives `put` within 5 s. After 35 s, `GET /api/nodes` shows worker-2 stale. Then `start-worker 2` and wait for healthy.
   2. **Control plane lost:** `stop-control-plane`. On every worker and the LB, the ruleset answers 200 with the pre-outage ETag; evaluate, OFREP and a stream with `put` work. `restart-worker 1`: `/readyz` 200 within 30 s. `start-fresh-worker`: `/readyz` 503 and the SDK answers 503 (then `stop-fresh-worker`). Send `/sdk/v1/events` with an `outage-counter` count of 5 to each worker (202).
   3. **Control plane restored:** `start-control-plane`. All nodes become healthy and current within 60 s. Run 20 kill-switch flips with streams open on every worker: p95 under 5 s. Within 60 s the `outage-counter` evaluation total equals 15; it is still 15 after another 15 s.
   4. **Database down:** `stop-db`. The workers serve through the LB, and the CP `/readyz` answers 503. `start-db`, wait for the CP to be ready; a flip reaches a worker stream within 10 s.
   5. **Footprint:** `mem-worker N` is under 256 for each N.
6. Run the tests again; they pass.
7. Commit `test(scripts): multi-node failure-scenario runner (MF-86)`.

### 29. Failure-suite stack (`cluster_test.sh`) and the CI job
**Files:** `scripts/cluster_test.sh`, `scripts/cluster/cp.override.yml`, `scripts/cluster/worker.override.yml`, `scripts/cluster/lb.yml`, `.github/workflows/ci.yml`, `AGENTS.md`.

1. Write `cluster_test.sh`, a bash script with `set -euo pipefail` in the style of `smoke.sh`, with a cleanup trap that prints the logs on failure.
   - It creates the docker network `megooflags-cluster`.
   - **Control plane:** `docker compose -p mf-cp -f deploy/docker-compose.control-plane.yml -f deploy/compose.ci.yml -f scripts/cluster/cp.override.yml up -d --wait`. The override sets `MEGOOFLAGS_SITE_ADDRESS=cp.megooflags.internal` (Caddy's internal CA), attaches caddy to the network with that alias, and publishes 8443.
   - Copy Caddy's `root.crt` to a temporary `certs/ca.crt`, as `smoke.sh` does.
   - Run first-run setup and create a token, as `smoke.sh` does. Create three nodes with `POST /api/nodes` and capture their tokens.
   - **Workers:** for each N, `docker compose -p mf-wN -f deploy/docker-compose.worker.yml -f scripts/cluster/worker.override.yml up -d --wait`, with env `MEGOOFLAGS_CONTROL_PLANE_URL=https://cp.megooflags.internal`, the token, `MEGOOFLAGS_CONTROL_PLANE_CA_FILE=/etc/megooflags/certs/ca.crt`, the certs directory, plain-HTTP Caddy, network alias `worker-N`, and host port `1808N`. The images come from `compose.ci.yml`-style `megooflags-api:ci`, set in the override.
   - **LB:** `scripts/cluster/lb.yml` runs `nginx:1.27` with `docs/examples/multi-node/nginx.conf` and a self-signed cert made with openssl, published on 9443.
   - `ctl <action>` implements the step 28 actions with `docker compose -p ... stop|start|restart` and `docker stats --no-stream`. The fresh worker uses project `mf-w4` with a fourth node and a new volume.
   - It then runs `python3 scripts/cluster_failures.py ...` and prints `cluster OK`.
2. Add the `cluster` job to `ci.yml`. It needs `images`, builds and loads both images the way the `smoke` job does, and runs `bash scripts/cluster_test.sh` with a timeout of 30 minutes. On failure it uploads the logs.
3. Add the command to the AGENTS.md section 9 "images and smoke" block, and add the job to the CI list there.
4. Run it locally after `docker build` (it must print `cluster OK`). Run actionlint.
5. Commit `ci: multi-node failure-scenario suite (MF-86)`.

### 30. Documentation
**Files:** `docs/multi-node.md`, `docs/multi-node-migration.md`, `README.md`, `AGENTS.md`, `CHANGELOG.md`, `deploy/.env.example`.

1. Write `docs/multi-node.md` with these sections:
   - roles and what each runs;
   - the settings table (C1, with defaults);
   - one control plane plus three workers, step by step (the Nodes page, Add worker, `.env.worker.example`, `docker compose -f docker-compose.worker.yml up -d`, a load balancer from `docs/examples/multi-node/`, and pointing the SDK hostname at it);
   - TLS (`https://` required, `MEGOOFLAGS_CONTROL_PLANE_INSECURE`, `MEGOOFLAGS_CONTROL_PLANE_CA_FILE`);
   - failure behaviour: the PRD 8.5 table and the design's edge cases (revoke stops serving, drain, a key created during an outage, client rate limits per worker, so N workers allow N times the limit);
   - rolling upgrades (the C2 rule, control plane first, then drain, upgrade and resume each worker);
   - metrics (C13) and a Prometheus alert example: `megooflags_worker_snapshot_age_seconds > 300 for 5m`;
   - workers as the edge relay (S-8);
   - the 1 vCPU / 512 MB footprint and that `MEGOOFLAGS_API_WORKERS` is ignored on workers.
2. Write `docs/multi-node-migration.md`, the design's four steps plus rollback, without downtime, with the exact commands.
3. Update the other files:
   - README: a short "Multi-node" section linking both documents.
   - AGENTS.md section 1: the `deploy/` row lists the new files. Section 10: the release attaches the four new files.
   - CHANGELOG under Added: "Nodes page for control planes: add workers, see health and snapshot age, drain, rotate, revoke and remove." and "Docs for multi-node deployments and a migration guide from a single node."
2. Check every setting name against `settings.py`, and every path you cite.
3. Commit `docs: multi-node deployment and migration guide (MF-86)`.

## Final verification
From the repository root, with Postgres, Valkey and the secret key exported (AGENTS.md section 9):
```bash
python3 scripts/check_version.py && uv run --frozen --project api pytest scripts/tests
(cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest)
(cd api && uv run pytest tests/cluster -v)
bash scripts/check_migrations.sh
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test)
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api && git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts
(cd api && MEGOOFLAGS_ROLE=control-plane MEGOOFLAGS_PUBLIC_URL=http://localhost:3000 MEGOOFLAGS_API_WORKERS=1 MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true uv run python -m megooflags_api.serve) &
(cd web && pnpm e2e)
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web
bash scripts/smoke.sh http && bash scripts/smoke.sh https     # standalone unchanged, propagation test green
bash scripts/cluster_test.sh                                   # the "done when": prints cluster OK
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```

## Design coverage
| Requirement | Steps |
|---|---|
| Roles from the same images (HA-1), compose files | 1, 16, 25, 26 |
| Per-node tokens, revoke, rotate, TLS (HA-2) | 1, 6, 7, 10, 15, 20 |
| Signed versioned snapshots streamed to workers | 3, 4, 8, 11 |
| Serve from memory and disk while the CP is down, after restart (HA-3) | 12, 13, 16, 19 |
| Under 5 s propagation, resync on reconnect (HA-4) | 11, 15, 18, 19, 28 |
| Event buffering (HA-5) | 10, 14, 19 |
| Nodes page, snapshot age metric (HA-6) | 7, 16, 17, 21 to 24 |
| Not ready without a valid snapshot (HA-7) | 15, 16, 18 |
| Version rule, rolling upgrades (HA-8) | 2, 10, 20, 30 |
| Docs, LB samples, migration (HA-9) | 27, 30 |
| One application, two back ends; equal answers and ETags | 5, 13, 18 |
| Failure-scenario suite in CI ("done when") | 28, 29 |
