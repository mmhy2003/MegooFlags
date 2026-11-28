# MF-78 implementation plan: SDK delivery API

**Goal:** an SDK authenticates with an environment-scoped SDK key and can download the ruleset (server keys), evaluate remotely (client keys get values only), stream changes over SSE, use OFREP, and send exposure events and evaluation counts. A kill switch reaches every stream on every API replica in under 5 s at p95.

**Architecture:** Valkey is a required dependency. One client per replica (`app.state.valkey`) handles readiness, client-key rate limits and change publishing. A dedicated pub/sub connection (`ChangeBus.run`) receives `megooflags:changes` messages. Those messages invalidate the `RulesetCache` and the `KeyResolver`, and wake the `StreamHub`. The hub reloads the ruleset and pushes it to the SSE subscribers of each environment. A periodic re-check, plus a re-check after every Valkey resubscribe, covers lost messages. `/sdk/v1/*`, `/ofrep/v1/*` and the key management routes are new routers. The hot path makes no DB query when the caches are warm: keys are cached for 10 s, and the ruleset is trusted for 1 s or until a Valkey invalidation arrives. Events go to Postgres (`exposure_event`, `evaluation_count`), with a retention job.

## Conventions (every step)
- Write the test first, run it and see it fail, then implement and see it pass. Keep `uv run ruff check . ../scripts`, `uv run ruff format --check . ../scripts`, `uv run mypy` and the full `uv run pytest` green, all run from `api/`.
- Services: Postgres and Valkey, as in AGENTS.md §9 (step 2 adds the Valkey line):
  `docker run -d --name mf-pg -e POSTGRES_USER=megooflags -e POSTGRES_PASSWORD=megooflags -e POSTGRES_DB=megooflags -p 5432:5432 postgres:16`
  `docker run -d --name mf-valkey -p 6379:6379 valkey/valkey:8`
  `export MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags MEGOOFLAGS_VALKEY_URL=redis://localhost:6379/0`
- Tests that need Valkey take the `valkey_up` fixture from step 1, which skips when `MEGOOFLAGS_VALKEY_URL` is unset. DB tests use the existing `app` and `api` fixtures.
- After any route or schema change, regenerate the spec and client. Run `uv run python -m megooflags_api.export_openapi openapi.json` from `api/`, then `pnpm --dir web install --frozen-lockfile` (once) and `pnpm --dir web gen:api` from the repo root. Commit `api/openapi.json` and `web/lib/api/schema.d.ts` with the step.
- Commit each step on `megoo/MF-78-sdk-delivery-api-ruleset-download-sse-st` with a conventional message ending in `(MF-78)`, a blank line, and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.megoocolab/`. Only the last step adds the CHANGELOG entry.
- Error bodies use the existing `ErrorBody` / `ApiError` (`errors.py`). New error codes: `unauthorized` (401), `forbidden` (403), `rate_limited` (429), `payload_too_large` (413).

## Details this plan fixes (the design leaves them open)
1. `MEGOOFLAGS_VALKEY_URL` defaults to `redis://localhost:6379/0`, like `database_url`. `create_app()` must still build without Valkey, because `export_openapi` and the CI web job call it. "Required" is enforced at startup: the lifespan PINGs Valkey and raises if it can't reach it, so the API refuses to start.
2. Change message payload on `megooflags:changes` is JSON `{"kind":"project","id":"<uuid>"}` or `{"kind":"key","id":"<sdk key uuid>"}`. `publish` also dispatches the message locally before sending it, so the writing replica never depends on the Valkey round trip.
3. `evaluation_count.variant` is NOT NULL (it is part of the primary key). A null variant is stored as `""`. Duplicate count rows in one batch are summed in Python before the upsert, because Postgres `ON CONFLICT` can't touch the same row twice in one statement.
4. The ETag is `"` + the first 32 hex characters of the SHA-256 of the serialized ruleset bytes + `"`. It changes only when the content changes, so a flag rename that leaves the ruleset unchanged pushes nothing. The evaluate and OFREP-bulk ETags are `derived_etag(ruleset_etag, key_kind, canonical_json(context), canonical_json(flags))`.
5. Remote evaluation counts include every result except `FLAG_NOT_FOUND`, from `/sdk/v1/evaluate` and both OFREP routes. A 304 response is not counted, because nothing was evaluated.
6. A stream re-validates its key on every heartbeat through `KeyResolver` (10 s TTL). A revoked key therefore closes within about TTL + heartbeat even without Valkey, and at once with Valkey.
7. The access log already logs `scope["path"]` without the query string, so `?key=` never reaches it (a test proves this). The OpenTelemetry FastAPI instrumentation would record the full URL, so `/sdk/v1/stream` is added to `tracing.EXCLUDED_URLS`.
8. `uvicorn` gets `--timeout-graceful-shutdown 5` in the Dockerfile, so open SSE streams don't block a container stop. SDKs reconnect.
9. SDK auth dependencies open their own short session (`request.app.state.sessionmaker()`) instead of `SessionDep`, so a long-lived stream never holds a DB connection.
10. Background loops issue no DB statements while idle: no subscribers, nothing to flush. Existing statement-count tests therefore stay exact.

## File map
| File | Responsibility |
|---|---|
| `api/pyproject.toml`, `api/uv.lock` | Add the `valkey>=6.1` runtime dependency |
| `api/src/megooflags_api/settings.py` | New settings (Appendix A) |
| `api/src/megooflags_api/valkey.py` (new) | `create_valkey`, `redacted_url`, `check_valkey`, `valkey_ready` |
| `api/src/megooflags_api/main.py` | Lifespan: Valkey check, background jobs, shutdown. App state wiring, new routers, CORS middleware |
| `api/src/megooflags_api/routers/health.py` | `/readyz` checks Postgres and Valkey in parallel |
| `api/src/megooflags_api/errors.py` | `ApiError.headers`, plus `unauthorized`, `forbidden`, `rate_limited`, `payload_too_large` and `SDK_ERROR_RESPONSES` |
| `api/src/megooflags_api/metrics.py` | Counters `megooflags_rate_limit_errors`, `megooflags_change_publish_errors` |
| `api/src/megooflags_api/models/enums.py`, `models/sdk.py` (new), `models/__init__.py` | `SdkKeyKind`, `SdkKey`, `ExposureEvent`, `EvaluationCount` |
| `api/alembic/versions/0003_sdk_delivery.py` (new) | The three tables, the enum `sdk_key_kind`, and a downgrade |
| `api/src/megooflags_api/schemas/sdk_keys.py` (new) | `SdkKeyCreate`, `SdkKeyOut`, `SdkKeyCreated` |
| `api/src/megooflags_api/services/sdk_keys.py` (new) | Key generation, hashing, create/list/revoke, `SdkPrincipal`, `KeyResolver` |
| `api/src/megooflags_api/routers/sdk_keys.py` (new) | Management key endpoints |
| `api/src/megooflags_api/services/rate_limit.py` (new) | `RateLimiter` (Valkey fixed window, fails open) |
| `api/src/megooflags_api/sdk_auth.py` (new) | `require_server_key`, `require_any_key`, `require_ofrep_key`, `require_stream_key` and the `Annotated` deps |
| `api/src/megooflags_api/services/ruleset.py` | `EnvRef`. `build_ruleset` takes an `EnvRef` |
| `api/src/megooflags_api/services/ruleset_cache.py` | `CachedRuleset`, `get_entry(max_age)`, `invalidate(project_id)` |
| `api/src/megooflags_api/routers/evaluate.py` | Passes `EnvRef.of(env_row)` |
| `api/src/megooflags_api/etags.py` (new) | `canonical_json`, `derived_etag`, `etag_matches` |
| `api/src/megooflags_api/cors.py` (new) | `SdkCorsMiddleware` |
| `api/src/megooflags_api/schemas/sdk.py` (new) | `SdkEvaluationResult`, `SdkEvaluateOut` |
| `api/src/megooflags_api/routers/sdk.py` (new) | `/sdk/v1/ruleset`, `/evaluate`, `/stream`, `/events` |
| `api/src/megooflags_api/services/changes.py` (new) | `ChangeBus`, `get_change_bus`, `ChangesDep` |
| Write routers `projects`, `environments`, `flags`, `flag_environments`, `segments`, `sdk_keys` | Publish after commit |
| `api/src/megooflags_api/services/streams.py` (new) | `Revoked`, `Subscriber`, `StreamHub` |
| `api/src/megooflags_api/tracing.py` | Excludes `/sdk/v1/stream` |
| `api/src/megooflags_api/schemas/ofrep.py`, `routers/ofrep.py` (new) | OFREP |
| `api/src/megooflags_api/schemas/events.py`, `services/events.py` (new) | Events, counts, `EvaluationCounter`, retention |
| `api/Dockerfile` | `--timeout-graceful-shutdown 5` |
| `deploy/docker-compose.yml`, `deploy/.env.example`, `.github/workflows/ci.yml`, `AGENTS.md`, `scripts/smoke.sh` | Valkey and the propagation test |
| `scripts/latency.py`, `scripts/propagation_test.py`, `scripts/bench_sdk_evaluate.py` (new), `scripts/tests/test_propagation.py` (new) | Propagation and benchmark tools |
| `spec/delivery.md` (new), `spec/README.md`, `CHANGELOG.md` | Docs |
| Tests (new) | `api/tests/test_valkey.py`, `test_sdk_keys.py`, `test_sdk_auth.py`, `test_sdk.py`, `test_changes.py`, `test_stream.py`, `test_fanout.py`, `test_ofrep.py`, `test_events.py`. Additions to `conftest.py`, `helpers.py`, `test_readyz.py`, `test_ruleset_cache.py`, `test_migrations.py`, `test_models_migration.py`, `test_openapi.py`, `test_logging.py`, `test_evaluate.py` |

## Steps

### 1. Valkey client, settings, startup check and readiness
See the step detail below (identical to the `steps` entry) and Appendix A.

### 2. Valkey in compose, CI and AGENTS.md
### 3. SDK tables and migration 0003
### 4. SDK key service and management endpoints
### 5. Key resolution, SDK auth dependencies and client rate limit
### 6. Ruleset cache: max_age, invalidate, serialized body and ETag
### 7. /sdk/v1/ruleset, /sdk/v1/evaluate and CORS
### 8. ChangeBus and publishing from every write route
### 9. StreamHub and /sdk/v1/stream
### 10. Cross-replica fan-out, periodic re-check and resubscribe re-check
### 11. Propagation test script in the compose smoke job
### 12. OFREP endpoints
### 13. Exposure events, evaluation counts and retention
### 14. Delivery spec, benchmark script, CHANGELOG and final checks

(The full text of each step is in the `steps` array, one to one. The appendices below hold the contracts they refer to.)

## Appendix A: settings and valkey.py
```python
# settings.py, new fields on Settings
valkey_url: str = "redis://localhost:6379/0"
persist_context: bool = False
exposure_retention_days: int = Field(30, ge=1)
client_rate_limit_per_minute: int = Field(600, ge=1)
sse_heartbeat_seconds: float = Field(15, gt=0)
stream_recheck_seconds: float = Field(30, gt=0)
```
```python
# valkey.py
VALKEY_TIMEOUT = 2.0
logger = logging.getLogger(__name__)

def create_valkey(url: str) -> Valkey:          # no I/O: from_url connects lazily
    return Valkey.from_url(url, socket_connect_timeout=VALKEY_TIMEOUT,
                           socket_timeout=VALKEY_TIMEOUT, health_check_interval=15)

def redacted_url(url: str) -> str:               # "redis://host:port/db", never user or password
async def check_valkey(client: Valkey, url: str) -> None:
    # PING under asyncio.timeout(VALKEY_TIMEOUT + 1). On any exception:
    # logger.error("cannot reach Valkey at %s: set MEGOOFLAGS_VALKEY_URL to a running Valkey", redacted_url(url))
    # raise RuntimeError(f"cannot reach Valkey at {redacted_url(url)}") from exc
async def valkey_ready(client: Valkey) -> bool:  # PING with timeout; False on any exception
```
```python
# main.py
def background_jobs(app: FastAPI) -> list[Coroutine[Any, Any, None]]: ...   # [] in step 1; later steps append
async def shutdown_jobs(app: FastAPI) -> None: ...                           # no-op in step 1; step 13 flushes counts

@asynccontextmanager
async def lifespan(app):
    await check_valkey(app.state.valkey, app.state.settings.valkey_url)
    tasks = [asyncio.create_task(job) for job in background_jobs(app)]
    try:
        yield
    finally:
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await shutdown_jobs(app)
        await app.state.valkey.aclose()
        if app.state.tracer_provider is not None: app.state.tracer_provider.shutdown()
        await app.state.engine.dispose()
```
`create_app` sets `app.state.valkey = create_valkey(settings.valkey_url)`.

## Appendix B: models/sdk.py
```python
class SdkKeyKind(StrEnum):            # models/enums.py
    SERVER = "server"
    CLIENT = "client"

class SdkKey(Base):
    __tablename__ = "sdk_key"
    __table_args__ = (UniqueConstraint("secret_hash"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environment.id", ondelete="CASCADE"), index=True)
    kind: Mapped[SdkKeyKind] = mapped_column(pg_enum(SdkKeyKind, "sdk_key_kind"))
    name: Mapped[str] = mapped_column(String(200))
    prefix: Mapped[str] = mapped_column(String(14))
    secret_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __mapper_args__ = {"eager_defaults": True}

class ExposureEvent(Base):
    __tablename__ = "exposure_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)     # BIGSERIAL
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environment.id", ondelete="CASCADE"), index=True)
    flag_key: Mapped[str] = mapped_column(Text)
    variant: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

class EvaluationCount(Base):
    __tablename__ = "evaluation_count"
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environment.id", ondelete="CASCADE"), primary_key=True)
    flag_key: Mapped[str] = mapped_column(Text, primary_key=True)
    variant: Mapped[str] = mapped_column(Text, primary_key=True)       # "" for null
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(BigInteger)
```
`flag_key` is plain text with no FK, so history survives flag deletion. `TRUNCATE project CASCADE` in the `app` fixture reaches all three tables through `environment`.

## Appendix C: keys, principal, resolver, auth, rate limit
```python
# services/sdk_keys.py
KEY_PREFIXES = {SdkKeyKind.SERVER: "mf_server_", SdkKeyKind.CLIENT: "mf_client_"}
BASE62 = string.digits + string.ascii_letters
KEY_LENGTH = 42      # 10-char prefix + 32 random
DISPLAY_PREFIX = 14

def generate_key(kind: SdkKeyKind) -> str        # prefix + 32 x secrets.choice(BASE62)
def hash_key(key: str) -> str                    # sha256 hexdigest
def key_kind(key: str) -> SdkKeyKind | None      # by prefix and exact length 42, else None
async def create_key(session, environment: Environment, body: SdkKeyCreate) -> tuple[SdkKey, str]
async def list_keys(session, environment: Environment) -> Sequence[SdkKey]     # ORDER BY created_at, id
async def revoke_key(session, environment: Environment, key_id: uuid.UUID) -> SdkKey
    # not_found("sdk key", str(key_id)) when absent or in another environment. Revoking twice keeps the first revoked_at.

@dataclass(frozen=True, slots=True)
class SdkPrincipal:
    key_id: uuid.UUID
    kind: SdkKeyKind
    secret_hash: str
    environment: EnvRef        # services/ruleset.py
    revoked: bool

class KeyResolver:
    def __init__(self, ttl: float = 10.0, clock: Callable[[], float] = time.monotonic) -> None
    async def resolve(self, session: AsyncSession, key: str) -> SdkPrincipal | None
        # key_kind(key) is None -> None without a DB query; else resolve_hash(session, hash_key(key))
    async def resolve_hash(self, session: AsyncSession, secret_hash: str) -> SdkPrincipal | None
        # cache hit younger than ttl -> 0 statements. Miss: ONE query joining sdk_key to environment.
        # Found rows (active or revoked) are cached; unknown hashes are not cached.
    def forget(self, key_id: uuid.UUID) -> None   # drop cached entries for that key id
```
```python
# services/rate_limit.py
class RateLimiter:
    def __init__(self, client: Valkey, limit_per_minute: int, clock: Callable[[], float] = time.time) -> None
    async def hit(self, key_id: uuid.UUID, client_ip: str) -> int | None
        # window = int(now // 60); k = f"megooflags:rl:{key_id}:{client_ip}:{window}"
        # pipeline(transaction=True): INCR k; EXPIRE k 120. If count > limit -> return max(1, 60 - int(now % 60)) (Retry-After).
        # On any Valkey/OS/timeout error: logger.warning(...), metrics.RATE_LIMIT_ERRORS.inc(), return None (fail open).
```
```python
# sdk_auth.py. Each dependency opens `async with request.app.state.sessionmaker() as session` for the lookup.
def bearer_token(request: Request) -> str | None          # "Authorization: Bearer <key>", scheme case-insensitive
async def authenticate(request: Request, key: str | None) -> SdkPrincipal
    # None/unknown -> unauthorized("missing or unknown SDK key") (401, header WWW-Authenticate: Bearer)
    # revoked -> forbidden("the SDK key is revoked")
async def limit_client(request: Request, principal: SdkPrincipal) -> None
    # client keys only: retry = await app.state.rate_limiter.hit(principal.key_id, request.client.host or "unknown")
    # retry is not None -> rate_limited(retry) (429, header Retry-After)
async def require_server_key(request) -> SdkPrincipal     # bearer only; a client key -> forbidden("client keys cannot download the ruleset")
async def require_any_key(request) -> SdkPrincipal        # bearer only; + limit_client
async def require_ofrep_key(request, x_api_key: str | None = Header(None, alias="X-API-Key")) -> SdkPrincipal  # bearer, else X-API-Key; + limit_client
async def require_stream_key(request, key: str | None = Query(None, description="Client keys only: browsers' EventSource cannot set headers.")) -> SdkPrincipal
    # bearer first; else ?key=. A query key whose key_kind is SERVER -> unauthorized("server keys must not be sent in the URL"). + limit_client
ServerKeyDep, AnyKeyDep, OfrepKeyDep, StreamKeyDep = Annotated[SdkPrincipal, Depends(...)]
```
`errors.py`: `ApiError.__init__(..., headers: dict[str, str] | None = None)`, and `_api_error` passes `headers=exc.headers`. `SDK_ERROR_RESPONSES = {401, 403, 422, 429: {"model": ErrorBody}}`.

## Appendix D: RulesetCache
```python
# services/ruleset.py
@dataclass(frozen=True, slots=True)
class EnvRef:
    id: uuid.UUID
    project_id: uuid.UUID
    key: str
    @classmethod
    def of(cls, environment: Environment) -> "EnvRef": ...
async def build_ruleset(session, environment: EnvRef) -> dict[str, Any]   # body unchanged; it uses .id, .project_id and .key
```
```python
# services/ruleset_cache.py
SDK_MAX_AGE = 1.0

@dataclass(frozen=True, slots=True)
class CachedRuleset:
    fingerprint: str
    ruleset: Ruleset
    body: bytes        # json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode()
    etag: str          # '"' + sha256(body).hexdigest()[:32] + '"'

@dataclass(slots=True)
class _Entry:
    cached: CachedRuleset
    project_id: uuid.UUID
    checked_at: float

class RulesetCache:
    def __init__(self, max_environments: int = 256, clock: Callable[[], float] = time.monotonic) -> None
    async def get_entry(self, session, environment: EnvRef, max_age: float = 0.0) -> CachedRuleset
        # 1. entry exists and max_age > 0 and clock() - entry.checked_at < max_age -> move_to_end, return (0 statements)
        # 2. started = clock(); fingerprint query (1 statement); a match -> entry.checked_at = started, return
        # 3. per-env lock, re-check, build, parse, serialize, store _Entry(checked_at=started), LRU evict (as today)
    async def get(self, session, environment: EnvRef, max_age: float = 0.0) -> Ruleset   # (await get_entry(...)).ruleset
    def invalidate(self, project_id: uuid.UUID) -> None    # checked_at = -inf for every entry of that project
```

## Appendix E: ChangeBus
```python
CHANNEL = "megooflags:changes"

class ChangeBus:
    def __init__(self, client: Valkey, url: str) -> None
    on_project: list[Callable[[uuid.UUID], None]]
    on_key_revoked: list[Callable[[uuid.UUID], None]]
    on_resubscribe: list[Callable[[], Awaitable[None]]]
    async def publish(self, project_id: uuid.UUID) -> None          # dispatch locally, then _send
    async def publish_key_revoked(self, key_id: uuid.UUID) -> None
    async def _send(self, message: dict[str, str]) -> None
        # asyncio.timeout(2): client.publish(CHANNEL, json.dumps(message)). Any exception ->
        # logger.warning("change publish failed; the periodic re-check delivers it", exc_info=True), CHANGE_PUBLISH_ERRORS.inc()
    def dispatch(self, raw: str | bytes) -> None   # parse; unknown or malformed -> log at WARNING and ignore
    async def run(self) -> None
        # forever: sub = Valkey.from_url(url, socket_connect_timeout=2, socket_timeout=None, health_check_interval=15)
        # async with sub.pubsub() as ps: await ps.subscribe(CHANNEL); await each on_resubscribe;
        #   async for m in ps.listen(): if m["type"] == "message": dispatch(m["data"])
        # except (ValkeyError, OSError, TimeoutError): log; sleep(backoff 0.5 -> x2 -> max 5 s); reset backoff after a successful subscribe.
        # finally: await sub.aclose()

def get_change_bus(request: Request) -> ChangeBus: return request.app.state.change_bus
ChangesDep = Annotated[ChangeBus, Depends(get_change_bus)]
```
Wiring in `create_app`: `bus.on_project.append(cache.invalidate)` and `bus.on_key_revoked.append(resolver.forget)`. Step 9 adds `hub.notify_project` and `hub.notify_key_revoked`. Step 10 adds `on_resubscribe.append(hub.recheck_all)`.

## Appendix F: StreamHub and the stream endpoint
```python
@dataclass(frozen=True, slots=True)
class Revoked: pass
StreamItem = CachedRuleset | Revoked

class Subscriber:
    def __init__(self, principal: SdkPrincipal) -> None:
        self.principal = principal
        self.queue: asyncio.Queue[StreamItem] = asyncio.Queue(maxsize=1)
    def offer(self, item: StreamItem) -> None   # if full: get_nowait() (drop older); put_nowait(item)

class StreamHub:
    def __init__(self, sessionmaker, cache: RulesetCache, debounce: float = 0.1) -> None
    def subscribe(self, principal) -> Subscriber
    def unsubscribe(self, sub) -> None          # drops _last_etag[env] when the env's last subscriber leaves
    def notify_project(self, project_id) -> None
        # add the subscribed env ids of that project to a pending set; when no flush task is running,
        # create one that sleeps `debounce`, then refresh(pending) (bursts merge)
    def notify_key_revoked(self, key_id) -> None   # offer Revoked() to every subscriber with that key id
    async def recheck_all(self) -> None            # refresh(all subscribed env ids); no-op without subscribers
    async def refresh(self, env_ids) -> None
        # per env, in its own session: exists = scalar(select(Environment.id).where(Environment.id == env_id))
        # None -> offer Revoked() to its subscribers. Else entry = await cache.get_entry(session, env_ref, max_age=0);
        # if entry.etag != _last_etag.get(env_id): store it, then offer(entry) to its subscribers
        # exceptions are logged, never raised
    async def run_recheck_loop(self, interval: float) -> None   # while True: sleep(interval); await recheck_all()
    @property
    def subscriber_count(self) -> int
```
Endpoint generator (`routers/sdk.py`):
```
sub = hub.subscribe(principal)
try:
  yield b"retry: 3000\n\n"
  entry = get_entry(short session, principal.environment, max_age=SDK_MAX_AGE); last = request Last-Event-ID header
  if entry.etag != last: yield frame(entry); last = entry.etag
  loop:
    try: item = await asyncio.wait_for(sub.queue.get(), settings.sse_heartbeat_seconds)
    except TimeoutError:
        p = await resolver.resolve_hash(short session, principal.secret_hash)
        if p is None or p.revoked: yield REVOKED_FRAME; return
        yield b": ping\n\n"; continue
    if isinstance(item, Revoked): yield REVOKED_FRAME; return
    if item.etag != last: yield frame(item); last = item.etag
finally: hub.unsubscribe(sub)
```
Frames: server `b"id: <etag>\nevent: put\ndata: " + entry.body + b"\n\n"`. Client `id: <etag>\nevent: changed\ndata: {"etag": "<etag>"}` (built with `json.dumps`). `REVOKED_FRAME = b"event: revoked\ndata: {}\n\n"`. Response: `StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})`.

## Appendix G: events
```python
# schemas/events.py
MAX_BATCH = 1000
class ExposureIn(ApiModel):
    flag_key: str = Field(min_length=1, max_length=200)
    variant: str | None = Field(None, max_length=200)
    reason: str = Field(min_length=1, max_length=50)
    timestamp: AwareDatetime
    context: dict[str, JsonValue] | None = None
class CountIn(ApiModel):
    flag_key: str = Field(min_length=1, max_length=200)
    variant: str | None = Field(None, max_length=200)
    hour: AwareDatetime                       # truncated to the hour on insert
    count: int = Field(ge=1, le=1_000_000_000)
class EventsIn(ApiModel):
    exposures: list[ExposureIn] = Field(default_factory=list, max_length=MAX_BATCH)
    counts: list[CountIn] = Field(default_factory=list, max_length=MAX_BATCH)
```
```python
# services/events.py
MAX_BODY_BYTES = 1_000_000
RETENTION_LOCK_ID = 7_808_001          # pg_try_advisory_xact_lock key
CountKey = tuple[uuid.UUID, str, str, datetime]   # env, flag_key, variant or "", hour

def truncate_hour(value: datetime) -> datetime
async def limit_body_size(request: Request) -> None      # len(await request.body()) > MAX_BODY_BYTES -> payload_too_large()
async def record_exposures(session, environment_id, items: Sequence[ExposureIn], *, persist_context: bool, now: datetime) -> None
    # one INSERT with all rows; occurred_at = min(item.timestamp, now); context only when persist_context; nothing when items is empty
async def add_counts(session, counts: Mapping[CountKey, int]) -> None
    # pg insert(...).on_conflict_do_update(index_elements=[4 pk cols], set_={"count": EvaluationCount.count + excluded.count}); nothing when empty
class EvaluationCounter:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None
    def record(self, environment_id: uuid.UUID, results: Iterable[tuple[str, str | None]]) -> None
    async def flush(self, sessionmaker) -> None     # swap the pending dict; empty -> return without a session; on failure log and merge back
    async def run(self, sessionmaker, interval: float = 10.0) -> None
async def delete_expired(session, retention_days: int) -> int
    # SELECT pg_try_advisory_xact_lock(:id); False -> 0. Else DELETE FROM exposure_event WHERE received_at < now() - make_interval(days => :d); return rowcount
async def run_retention(sessionmaker, retention_days: int, interval: float = 3600.0) -> None   # run now, then every interval; errors logged
```

## Appendix H: write routes that must publish (22)
| Method | Path (under `/api`) | Project id from |
|---|---|---|
| POST | `/projects` | the created `Project.id` |
| PATCH, DELETE | `/projects/{project}` | `row.id` (read before delete) |
| POST | `/projects/{project}/environments` | `project_row.id` |
| PATCH, DELETE | `/projects/{project}/environments/{env}` | `row.project_id` |
| POST | `/projects/{project}/flags` | `row.project_id` |
| PATCH, DELETE | `/projects/{project}/flags/{flag}` | `row.project_id` |
| POST | `.../flags/{flag}/archive`, `.../restore`, `.../copy`, `.../variations` | flag `project_id` |
| PATCH, DELETE | `.../flags/{flag}/variations/{variation}` | `flag_row.project_id` |
| PUT | `.../flags/{flag}/environments/{env}` | `project_row.id` |
| POST | `.../flags/{flag}/environments/{env}/toggle`, `.../kill` | `project_row.id` |
| POST | `/projects/{project}/segments` | `row.project_id` |
| PUT, PATCH, DELETE | `/projects/{project}/segments/{segment}` | `row.project_id` |

Excluded from the coverage check: `POST .../environments/{env}/evaluate` (read-only), `POST .../sdk-keys` (a new key changes nothing that streams see), and `DELETE .../sdk-keys/{id}` (it publishes a key message instead, tested separately). Each route adds `bus: ChangesDep` and calls `await bus.publish(<id>)` on the line right after `await session.commit()`.

## Appendix I: test helpers (`api/tests/helpers.py`)
```python
@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[str]:
    """Serve `app` with uvicorn on a free port (lifespan on). Yields the base URL."""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="on", log_config=None,
                            timeout_graceful_shutdown=1)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    # wait (max 10 s) until server.started; if task.done() first, await it to surface the error
    port = server.servers[0].sockets[0].getsockname()[1]
    try: yield f"http://127.0.0.1:{port}"
    finally: server.should_exit = True; await task

@dataclass
class SseEvent:
    event: str | None; data: str; id: str | None; comment: bool

async def next_events(lines: AsyncIterator[str], count: int, timeout: float) -> list[SseEvent]
    # parse SSE lines (comment lines ": ..." become SseEvent(comment=True)); fail the test on timeout

async def create_sdk_key(api, kind: str, project="demo", env="production", name="k") -> dict[str, Any]
```
httpx's `ASGITransport` buffers the whole response, so stream tests use `running()` and a real `httpx.AsyncClient(base_url=url)` with `client.stream("GET", ...)` and `response.aiter_lines()`.

## Appendix J: propagation script (`scripts/propagation_test.py`, stdlib only)
- Args: `--host api --port 8000 --replicas 3 --iterations 20 --timeout 10`.
- `replica_ips(host, port)` uses `socket.getaddrinfo` (unique IPv4s) and asserts the count equals `--replicas`.
- Setup through the first IP: create project `prop-<8 hex>` with environments `[{"key":"production","name":"Production"}]`, create a boolean flag `kill-me`, toggle production on, and create a server key.
- One thread per replica opens `GET http://<ip>:<port>/sdk/v1/stream` with `Authorization: Bearer`. `parse_sse(lines)` yields events; for each `put` it pushes `(time.monotonic(), ruleset["flags"]["kill-me"]["enabled"])` to that replica's queue.
- Each iteration: toggle on and wait until every replica reports `True`. Then `t0 = monotonic()`, `POST .../kill`, wait for `False` on each replica and record `t - t0`. A wait longer than `--timeout` fails.
- Print count, p50, p95 and max (`latency.percentile`). Exit 1 if p95 ≥ 5 s. Always delete the project at the end.
- `scripts/latency.py`: `percentile(samples: Sequence[float], q: float) -> float` (nearest-rank), shared with the benchmark.

## Final verification
From the repo root, with Postgres and Valkey running and both env vars exported:
```bash
python3 scripts/check_version.py && uv run --frozen --project api pytest scripts/tests
(cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest)
bash scripts/check_migrations.sh
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build)
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api && git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh http && bash scripts/smoke.sh https
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```
`smoke.sh http` runs 3 API replicas plus Valkey and the propagation test (p95 < 5 s over 20 kill-switch flips). The manual benchmark (`scripts/bench_sdk_evaluate.py`) records p99 at 2k RPS in the PR description.
