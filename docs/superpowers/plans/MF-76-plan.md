# MF-76 Implementation Plan: Core data model and management REST API

**Goal:** Add the PostgreSQL data model for projects, environments, flags, variations, segments and normalized per-environment targeting, and expose all of it through an unauthenticated management REST API under `/api/*`. The spec is exported to `api/openapi.json`, and the web client is regenerated from it.

**Architecture:** SQLAlchemy 2 typed models (`models/`) and one hand-reviewed Alembic revision `0002`. A request-scoped `AsyncSession` (`db.get_session`). Pydantic v2 schemas (`schemas/`, `extra="forbid"`). HTTP-free domain logic (`services/`). Thin FastAPI routers (`routers/`), all mounted under `/api` with a shared `get_actor()` dependency. Errors use one JSON body (`errors.py`). Targeting edits replace the whole config inside one transaction, guarded by `flag_environment.version`, and the replace runs under a `SELECT … FOR UPDATE` row lock.

**Conventions (AGENTS.md):**
- Use uv only. Run every API command from `api/`.
- ruff + `mypy --strict` on `src`.
- Every schema change goes into the Alembic revision with a working `downgrade()`.
- After any route or schema change, regenerate **and commit** `api/openapi.json` and `web/lib/api/schema.d.ts`.
- Add a CHANGELOG `[Unreleased]` entry. No version bump.
- Commit messages: `type(scope): … (MF-76)`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**Checks every step must pass** (Postgres from AGENTS.md §9 must be running, with `MEGOOFLAGS_DATABASE_URL` exported):
```bash
cd api && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest
# only steps that change routes or schemas:
cd api && uv run python -m megooflags_api.export_openapi openapi.json
cd web && pnpm install --frozen-lockfile && pnpm gen:api
```
`tests/test_openapi.py::test_committed_spec_is_current` fails whenever `api/openapi.json` is stale, so every router step regenerates it.

**Implementation decisions the design leaves to the plan** (these don't change the design):
- **Cross-reference FKs.** The design's RESTRICT FKs (variation, segment and prerequisite-flag references) are declared without `ondelete`. That is Postgres `NO ACTION`: it refuses the delete just like `RESTRICT`, but it checks at the end of the statement. Cascading deletes of a whole flag, environment or project then succeed. A plain `RESTRICT` can fail mid-cascade when the referencing row is deleted by the same statement.
- **Default fallthrough.** A new `flag_environment` row's fallthrough is the flag's first variation (lowest `sort_order`, then `key`). For boolean flags that is `true`, because the variations are created in the order `true`, `false`. The same rule is used in SQL for the environment backfill.
- **Transactions.** `get_session` yields a session. Write endpoints call `await session.commit()` themselves before returning. On an exception the session closes and rolls back. Services `flush()` after writes, so unique and FK violations surface inside the request and get mapped to errors.
- **ORM style.** No `relationship()`. Services run explicit `select`/`insert`/`delete` statements, so the async code never lazy-loads. `TimestampMixin` sets `__mapper_args__ = {"eager_defaults": True}`.
- **Pagination.** Projects, flags and segments are keyset-paginated (`cursor`, `limit`). Environments and variations are short lists returned unpaged.
- **Kill and toggle.** These bump `version` only when `enabled` actually changes.
- **Extra error codes.** Besides the design's codes, 422 uses `validation_error` (bad body, bad values, weights, duplicates) and `prerequisite_cycle`.

## File map

| File | Action | Responsibility |
|---|---|---|
| `api/src/megooflags_api/db.py` | modify | `Base` with a naming convention; `create_sessionmaker()`; `get_session()` dependency |
| `api/src/megooflags_api/models/__init__.py` | create | Imports every model so `Base.metadata` is complete |
| `api/src/megooflags_api/models/base.py` | create | `uuid_pk()`, `TimestampMixin`, `pg_enum()` |
| `api/src/megooflags_api/models/enums.py` | create | `FlagType`, `Operator`, `SegmentTargetKind` (StrEnum) |
| `api/src/megooflags_api/models/project.py` | create | `Project`, `Environment` |
| `api/src/megooflags_api/models/flag.py` | create | `Flag`, `Variation` |
| `api/src/megooflags_api/models/targeting.py` | create | `FlagEnvironment`, `FlagTarget`, `FlagRule`, `RolloutWeight`, `Clause`, `ClauseSegment`, `FlagPrerequisite` |
| `api/src/megooflags_api/models/segment.py` | create | `Segment`, `SegmentTarget`, `SegmentRule` |
| `api/alembic/env.py` | modify | `import megooflags_api.models` so autogenerate sees the tables |
| `api/alembic/versions/0002_core_model.py` | create | All tables, enums and indexes, plus a full downgrade |
| `api/src/megooflags_api/errors.py` | create | `ErrorBody`, `ApiError`, error factories, `ERROR_RESPONSES`, `install_error_handlers()` |
| `api/src/megooflags_api/deps.py` | create | `Actor`, `get_actor()` (returns `None`) |
| `api/src/megooflags_api/schemas/__init__.py` | create | empty |
| `api/src/megooflags_api/schemas/common.py` | create | `ApiModel`, `Key`, `Page[T]`, `FlagEnvironmentState` |
| `api/src/megooflags_api/schemas/projects.py` | create | Project and environment schemas |
| `api/src/megooflags_api/schemas/flags.py` | create | Flag and variation schemas |
| `api/src/megooflags_api/schemas/targeting.py` | create | `ClauseIn/Out`, config in/out, toggle and copy bodies |
| `api/src/megooflags_api/schemas/segments.py` | create | Segment schemas |
| `api/src/megooflags_api/services/__init__.py` | create | empty |
| `api/src/megooflags_api/services/lookup.py` | create | Key → row lookups that raise 404 |
| `api/src/megooflags_api/services/pagination.py` | create | Cursor encoding and keyset paging |
| `api/src/megooflags_api/services/projects.py` | create | Project CRUD, default environments |
| `api/src/megooflags_api/services/environments.py` | create | Environment CRUD, `INSERT … SELECT` backfill |
| `api/src/megooflags_api/services/values.py` | create | Validates variation values against the flag type |
| `api/src/megooflags_api/services/flags.py` | create | Flag CRUD, list query, archive, restore, delete, per-environment row creation |
| `api/src/megooflags_api/services/variations.py` | create | Variation CRUD |
| `api/src/megooflags_api/services/usage.py` | create | In-use queries (flag, variation, segment) |
| `api/src/megooflags_api/services/clauses.py` | create | Operator value validation, clause row building and loading |
| `api/src/megooflags_api/services/segments.py` | create | Segment CRUD, segment config replace |
| `api/src/megooflags_api/services/prerequisites.py` | create | `has_cycle()`, `check_prerequisites()` |
| `api/src/megooflags_api/services/targeting.py` | create | `load_config`, `replace_config`, `toggle`, `kill`, `copy_config` |
| `api/src/megooflags_api/routers/projects.py`, `environments.py`, `flags.py`, `flag_environments.py`, `segments.py` | create | HTTP layer |
| `api/src/megooflags_api/main.py` | modify | Sessionmaker, error handlers, `/api` routers with `get_actor` |
| `api/tests/conftest.py` | modify | `migrated`, `app` and `api` fixtures |
| `api/tests/helpers.py` | create | `create_project`, `create_flag`, `assert_error`, `count_statements` |
| `api/tests/test_models_migration.py`, `test_errors.py`, `test_projects.py`, `test_environments.py`, `test_flag_values.py`, `test_flags.py`, `test_variations.py`, `test_clauses.py`, `test_segments.py`, `test_flag_config.py`, `test_prerequisites.py`, `test_flag_actions.py`, `test_copy.py`, `test_scale.py` | create | Tests |
| `api/tests/test_migrations.py` | modify | Head `0002`; `alembic check` |
| `api/tests/test_openapi.py` | modify | Asserts the `/api` paths and `ErrorBody` |
| `api/openapi.json`, `web/lib/api/schema.d.ts` | regenerate | Generated spec and client |
| `CHANGELOG.md` | modify | `[Unreleased]` entries, including the "unauthenticated" warning |

## Steps

### 1. Data model and Alembic revision 0002
- Write `api/tests/test_models_migration.py` first:
  - `test_metadata_has_all_tables` asserts that `set(Base.metadata.tables)` after `import megooflags_api.models` equals {project, environment, flag, variation, flag_environment, flag_target, flag_rule, rollout_weight, clause, clause_segment, flag_prerequisite, segment, segment_target, segment_rule}.
  - `test_enum_values` asserts the exact `FlagType` and `Operator` values.
- Change `test_single_head` in `api/tests/test_migrations.py` to expect `["0002"]`.
- Add `test_models_match_migrations`, skipped unless `MEGOOFLAGS_DATABASE_URL` is set. It runs `subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=API_DIR, check=True)`, then the same with `"check"`.
- Run `cd api && uv run pytest tests/test_models_migration.py tests/test_migrations.py -v`. Expected: `ModuleNotFoundError: megooflags_api.models`, and the head is still `0001`.
- Implement Appendix A: the naming convention in `db.py`, `models/*`, and `import megooflags_api.models  # noqa: F401` in `alembic/env.py`.
- Generate the revision with `cd api && uv run alembic upgrade head && uv run alembic revision --autogenerate --rev-id 0002 -m core_model`, then review it against the Appendix A checklist:
  - the `use_alter` FK
  - GIN index
  - CHECK constraints
  - `downgrade()` drops the tables in reverse order, then `DROP TYPE` for `clause_operator`, `segment_target_kind` and `flag_type`
- Run `bash scripts/check_migrations.sh`. Expected: `migrations OK (2 revisions)`.
- Run the full checks. Expected: pass, and `openapi.json` is unchanged.
- Commit: `feat(api): core data model and migration 0002 (MF-76)`.

### 2. Request sessions, error body, actor dependency and DB test fixtures
- Write `api/tests/test_errors.py` first:
  - `test_api_error_body`: build `create_app()`, add a test route that raises `not_found("flag", "x")`, and assert a 404 with `{"code": "not_found", "message": "flag 'x' not found", "details": None}`.
  - `test_request_validation_uses_error_body`: a test route with a `body: ApiModel` subclass gets an extra field and returns 422 `validation_error` whose `details` is a list of `{loc, msg, type}`.
  - `test_integrity_error_codes`: `integrity_error_status_code("23505") == (409, "key_conflict")`, `"23503" -> (409, "in_use")`, `"23514" -> (422, "validation_error")`.
  - `test_session_dependency` (DB): uses the new `app` fixture and checks that `app.state.sessionmaker` produces a session that can `SELECT 1`.
  - `test_get_actor_is_none`.
- Run `uv run pytest tests/test_errors.py -v`. Expected: an ImportError.
- Implement Appendix B: `db.py` (`create_sessionmaker`, `get_session`), `errors.py`, `deps.py` and `schemas/common.py` (`ApiModel`, `Key`). In `main.py`, set `app.state.sessionmaker = create_sessionmaker(app.state.engine)` and call `install_error_handlers(app)`.
- Implement the Appendix C fixtures and helpers (`conftest.py`, `helpers.py`).
- Run the tests and the full checks. `openapi.json` must not change, because there are no new routes yet.
- Commit: `feat(api): request sessions, error body and actor hook (MF-76)`.

### 3. Projects API with default environments
Read the full step detail in the steps list. It is repeated verbatim in each subtask.

### 4–17
See the `steps` array. Each entry's detail is the build instruction for that step, and the appendices below hold the shared code and contracts it refers to.

## Appendix A — models and migration

`db.py`:
```python
from sqlalchemy import MetaData
NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
```

`models/base.py`:
```python
def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())

class TimestampMixin:
    __mapper_args__ = {"eager_defaults": True}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

def pg_enum(cls: type[StrEnum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])
```

`models/enums.py`:
- `FlagType`: boolean, string, number, json.
- `Operator`: in, not_in, contains, starts_with, ends_with, matches, lt, lte, gt, gte, semver_eq, semver_lt, semver_gt, before, after, in_segment.
- `SegmentTargetKind`: included, excluded.

Columns. "cascade" means `ForeignKey(..., ondelete="CASCADE")`. "ref" means `ForeignKey(...)` with no ondelete (Postgres NO ACTION, see the decisions above). Text columns `description` are `Text`, `default=""`, `server_default=""`. Keys are `String(100)` and names `String(200)`.

| Model / table | Columns | Table args |
|---|---|---|
| `Project` / `project` (+Timestamp) | id, key, name, description | `UniqueConstraint("key")` |
| `Environment` / `environment` (+Timestamp) | id, project_id cascade→project, key, name, description, sort_order int server_default 0 | `UniqueConstraint("project_id","key")` |
| `Flag` / `flag` (+Timestamp) | id, project_id cascade, key, name, description, type `pg_enum(FlagType,"flag_type")`, tags `ARRAY(Text)` server_default `'{}'`, owner `String(200)` null, off_variation_id ref→variation.id **`use_alter=True`** null, archived_at timestamptz null | `UniqueConstraint("project_id","key")`; `Index("ix_flag_project_archived_key","project_id","archived_at","key")`; `Index("ix_flag_tags","tags",postgresql_using="gin")`; `Index("ix_flag_project_owner","project_id","owner")` |
| `Variation` / `variation` | id, flag_id cascade, key, name (default ""), description, value `JSONB` (Mapped[Any], NOT NULL; JSON null is allowed as a value), sort_order int | `UniqueConstraint("flag_id","key")` |
| `FlagEnvironment` / `flag_environment` (+Timestamp) | id, flag_id cascade, environment_id cascade, enabled bool server_default false, version int server_default 1, fallthrough_variation_id ref→variation null, fallthrough_bucket_by `String(200)` null | `UniqueConstraint("flag_id","environment_id")`; `Index(None,"environment_id")` |
| `FlagTarget` / `flag_target` | id, flag_environment_id cascade, variation_id ref, context_key `String(256)` | `UniqueConstraint("flag_environment_id","context_key")` |
| `FlagRule` / `flag_rule` | id, flag_environment_id cascade, position int, description, variation_id ref null, bucket_by `String(200)` null | `UniqueConstraint("flag_environment_id","position")` |
| `RolloutWeight` / `rollout_weight` | id, flag_rule_id cascade null, flag_environment_id cascade null, variation_id ref, weight int | `CheckConstraint("num_nonnulls(flag_rule_id, flag_environment_id) = 1", name="one_owner")`; `CheckConstraint("weight BETWEEN 0 AND 100000", name="weight_range")`; `UniqueConstraint("flag_rule_id","variation_id")`; `UniqueConstraint("flag_environment_id","variation_id")` |
| `Clause` / `clause` | id, flag_rule_id cascade null, segment_rule_id cascade null, position int, attribute `String(200)`, operator `pg_enum(Operator,"clause_operator")`, values `JSONB` server_default `'[]'`, negate bool server_default false | `CheckConstraint("num_nonnulls(flag_rule_id, segment_rule_id) = 1", name="one_owner")`; `UniqueConstraint("flag_rule_id","position")`; `UniqueConstraint("segment_rule_id","position")` |
| `ClauseSegment` / `clause_segment` | clause_id cascade (PK), segment_id ref→segment (PK) | `Index(None,"segment_id")` |
| `FlagPrerequisite` / `flag_prerequisite` | id, flag_environment_id cascade, prerequisite_flag_id ref→flag, variation_id ref, position int | `UniqueConstraint("flag_environment_id","prerequisite_flag_id")`; `Index(None,"prerequisite_flag_id")` |
| `Segment` / `segment` (+Timestamp) | id, project_id cascade, key, name, description | `UniqueConstraint("project_id","key")` |
| `SegmentTarget` / `segment_target` | id, segment_id cascade, context_key `String(256)`, kind `pg_enum(SegmentTargetKind,"segment_target_kind")` | `UniqueConstraint("segment_id","context_key")` |
| `SegmentRule` / `segment_rule` | id, segment_id cascade, position int, description | `UniqueConstraint("segment_id","position")` |

**Migration review checklist:**
- `revision = "0002"`, `down_revision = "0001"`, and the docstring is `"""Core data model: projects, environments, flags, segments, targeting."""`.
- The `flag.off_variation_id` FK is created with `op.create_foreign_key` after the `variation` table exists, and dropped first in `downgrade()`.
- The GIN index uses `postgresql_using="gin"`.
- Both CHECK constraints on `rollout_weight` and the one on `clause` are present.
- `server_default`s are present: `''`, `'{}'`, `'[]'::jsonb`, `false`, `1`, `0`, `now()`, `gen_random_uuid()`.
- `downgrade()` drops every table, then runs `op.execute("DROP TYPE clause_operator")`, `… segment_target_kind` and `… flag_type`.
- `uv run alembic check` reports "No new upgrade operations detected." If it reports a false diff, fix the model or the migration, never the test.

## Appendix B — sessions, errors, actor, common schemas

`db.py` additions:
```python
def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)

async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session (and transaction) per request. Write endpoints call `await session.commit()`;
    on an exception the session closes and the transaction rolls back."""
    async with request.app.state.sessionmaker() as session:
        yield session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
```

`errors.py`:
```python
class ErrorBody(BaseModel):
    code: str
    message: str
    details: JsonValue = None

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: JsonValue = None) -> None: ...

def not_found(entity: str, key: str) -> ApiError            # 404 not_found, "<entity> '<key>' not found"
def key_conflict(entity: str, key: str) -> ApiError         # 409 key_conflict, "<entity> '<key>' already exists"
def version_conflict(current: int) -> ApiError              # 409 version_conflict, details {"version": current}
def in_use(message: str, used_by: list[dict[str, str]]) -> ApiError  # 409 in_use, details {"used_by": used_by}
def not_archived(key: str) -> ApiError                      # 409 not_archived
def invalid_reference(message: str, path: str) -> ApiError  # 422 invalid_reference, details {"path": path}
def validation_error(message: str, path: str | None = None) -> ApiError  # 422, details {"path": path} or None
def prerequisite_cycle(message: str, path: str) -> ApiError # 422 prerequisite_cycle
def integrity_error_status_code(sqlstate: str | None) -> tuple[int, str]
    # "23505" -> (409,"key_conflict"); "23503" -> (409,"in_use"); "23514" -> (422,"validation_error"); else (500,"internal_error")
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {404: {"model": ErrorBody}, 409: {"model": ErrorBody}, 422: {"model": ErrorBody}}
def install_error_handlers(app: FastAPI) -> None
    # ApiError -> JSONResponse(status, ErrorBody)
    # RequestValidationError -> 422 validation_error "request validation failed",
    #   details=[{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
    # sqlalchemy.exc.IntegrityError -> integrity_error_status_code(getattr(exc.orig, "sqlstate", None))
```
Paths in `details.path` are dotted, with indexes: `rules[0].clauses[1].values[0]`, `fallthrough.rollout.weights[1].variation`.

`deps.py`:
```python
@dataclass(frozen=True)
class Actor:
    id: str

async def get_actor() -> Actor | None:
    """No authentication yet: the management API is open. The governance task plugs auth in here."""
    return None
```

`schemas/common.py`:
```python
KEY_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,99}$"
Key = Annotated[str, StringConstraints(pattern=KEY_PATTERN)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Description = Annotated[str, StringConstraints(max_length=10000)]
class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
class Page(ApiModel, Generic[T]):
    items: list[T]
    next_cursor: str | None
class FlagEnvironmentState(ApiModel):   # added in step 5
    environment: str
    enabled: bool
    version: int
    updated_at: datetime
```

Routers mount in `main.py`:
```python
for module in (projects, environments, flags, flag_environments, segments):  # grows step by step
    app.include_router(module.router, prefix="/api", dependencies=[Depends(get_actor)])
```
Each router is `APIRouter(tags=[...], responses=ERROR_RESPONSES)`. Status codes: create returns 201, delete returns 204, everything else 200.

## Appendix C — test fixtures and helpers

Add to `api/tests/conftest.py` and keep `client_for`:
```python
API_DIR = Path(__file__).resolve().parents[1]

@pytest.fixture(scope="session")
def migrated() -> None:
    if "MEGOOFLAGS_DATABASE_URL" not in os.environ:
        pytest.skip("MEGOOFLAGS_DATABASE_URL is not set")
    # Subprocess, not alembic.command: env.py calls fileConfig, which would reset test logging.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=API_DIR, check=True)

@pytest.fixture
async def app(migrated: None) -> AsyncIterator[FastAPI]:
    app = create_app(Settings())
    async with app.state.engine.begin() as conn:
        await conn.execute(text("TRUNCATE project CASCADE"))  # every table hangs off project
    try:
        yield app
    finally:
        await app.state.engine.dispose()

@pytest.fixture
async def api(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
```

`api/tests/helpers.py`:
```python
async def create_project(api: httpx.AsyncClient, key: str = "demo",
                         environments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"key": key, "name": key.title()}
    if environments is not None:
        body["environments"] = environments
    r = await api.post("/api/projects", json=body); assert r.status_code == 201, r.text; return r.json()

async def create_flag(api: httpx.AsyncClient, project: str = "demo", key: str = "new-ui",
                      type: str = "boolean", **extra: Any) -> dict[str, Any]:
    r = await api.post(f"/api/projects/{project}/flags",
                       json={"key": key, "name": key, "type": type, **extra})
    assert r.status_code == 201, r.text; return r.json()

def assert_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json(); assert body["code"] == code; return body

@contextmanager
def count_statements(engine: AsyncEngine) -> Iterator[list[str]]:
    statements: list[str] = []
    def listener(conn, cursor, statement, params, context, executemany) -> None:
        statements.append(statement)
    event.listen(engine.sync_engine, "before_cursor_execute", listener)
    try: yield statements
    finally: event.remove(engine.sync_engine, "before_cursor_execute", listener)
```
Tests import these with `from helpers import create_project, …`, the same way they already import `from conftest import ClientFactory`.

## Appendix D — schemas (contract)

`schemas/projects.py`:
- `EnvironmentCreate(key: Key, name: Name, description: Description = "", sort_order: int | None = None)`
- `EnvironmentUpdate(name: Name | None = None, description: Description | None = None, sort_order: int | None = None)`
- `EnvironmentOut(key, name, description, sort_order, created_at, updated_at)`
- `ProjectCreate(key: Key, name: Name, description = "", environments: list[EnvironmentCreate] | None = Field(None, min_length=1))`
- `ProjectUpdate(name: Name | None, description: Description | None)`
- `ProjectSummary(key, name, description, created_at, updated_at)`
- `ProjectOut(ProjectSummary + environments: list[EnvironmentOut])`
- Default environments: `development`/"Development" (sort 0), `staging`/"Staging" (1), `production`/"Production" (2).

`schemas/flags.py`:
- `Tag = Annotated[str, StringConstraints(min_length=1, max_length=64)]`
- `VariationIn(key: Key, name: str = Field("", max_length=200), description: Description = "", value: JsonValue)`
- `VariationUpdate(name: str | None, description: Description | None, value: JsonValue)`. Use `model_fields_set` to tell "value: null" apart from an absent value.
- `VariationOut(key, name, description, value: JsonValue)`
- `FlagCreate(key: Key, name: Name, description = "", type: FlagType, tags: list[Tag] = Field([], max_length=50), owner: str | None = Field(None, max_length=200), variations: list[VariationIn] | None = None, off_variation: Key | None = None)`
- `FlagUpdate(name, description, tags, owner, off_variation)`. Every field is optional, and `owner` may be explicitly `null`.
- `FlagSummary(key, name, description, type, tags, owner, archived: bool, archived_at: datetime | None, environments: list[FlagEnvironmentState], created_at, updated_at)`
- `FlagOut(FlagSummary + off_variation: str, variations: list[VariationOut])`
- Tags are deduplicated, keeping order.

`schemas/targeting.py`:
- `Scalar = StrictStr | StrictInt | StrictFloat | StrictBool`
- `ClauseIn(attribute: str = Field("key", min_length=1, max_length=200), operator: Operator, values: list[Scalar] = Field(min_length=1, max_length=1000), negate: bool = False)`. `ClauseOut` has the same fields. For `in_segment`, `values` are segment keys.
- `WeightIn(variation: Key, weight: int = Field(ge=0, le=100000))`
- `RolloutIn(bucket_by: str = Field("key", min_length=1, max_length=200), weights: list[WeightIn] = Field(min_length=1))`
- `Serve(variation: Key | None = None, rollout: RolloutIn | None = None)`. A model validator requires exactly one of the two.
- `RuleIn(Serve + description: Description = "", clauses: list[ClauseIn] = Field(min_length=1))`
- `TargetIn(variation: Key, values: list[ContextKey] = Field(min_length=1))`, with `ContextKey = Annotated[str, StringConstraints(min_length=1, max_length=256)]`
- `PrerequisiteIn(flag: Key, variation: Key)`
- `FlagConfigIn(version: int, enabled: bool, targets: list[TargetIn] = [], rules: list[RuleIn] = [], fallthrough: Serve, prerequisites: list[PrerequisiteIn] = [])`
- `FlagConfigOut(environment: str, enabled, version, targets, rules, fallthrough, prerequisites, updated_at)`. The output types reuse the input models. Targets are grouped per variation in variation `sort_order`, with values sorted.
- `ToggleIn(enabled: bool, version: int | None = None)`
- `CopyIn(source: Key = Field(alias="from"), to: list[Key] = Field(min_length=1), include_enabled: bool = False)`

`schemas/segments.py`:
- `SegmentCreate(key, name, description = "")`
- `SegmentUpdate(name?, description?)`
- `SegmentRuleIn(description = "", clauses: list[ClauseIn] = Field(min_length=1))`
- `SegmentConfigIn(included: list[ContextKey] = [], excluded: list[ContextKey] = [], rules: list[SegmentRuleIn] = [])`
- `SegmentSummary(key, name, description, created_at, updated_at)`
- `SegmentOut(SegmentSummary + included, excluded (sorted), rules)`

## Appendix E — pagination
```python
def encode_cursor(key: str) -> str      # urlsafe base64 of the key, without padding
def decode_cursor(cursor: str) -> str   # raises validation_error("invalid cursor", "cursor")
def page_slice(rows: list[T], limit: int, key_of: Callable[[T], str]) -> tuple[list[T], str | None]
    # the query fetches limit + 1 rows; if there are more than limit, cut and return encode_cursor(key_of(last kept))
```
Queries use `WHERE key > :after ORDER BY key LIMIT :limit + 1`. Query params: `cursor: str | None = None`, `limit: int = Query(50, ge=1, le=500)`.

## Appendix F — per-environment row SQL
New flag (in `services/flags.py`), after the variations are inserted:
```sql
INSERT INTO flag_environment (flag_id, environment_id, fallthrough_variation_id)
SELECT :flag_id, e.id, :first_variation_id FROM environment e WHERE e.project_id = :project_id
```
New environment (in `services/environments.py`), one statement whatever the number of flags:
```sql
INSERT INTO flag_environment (flag_id, environment_id, fallthrough_variation_id)
SELECT f.id, :env_id,
       (SELECT v.id FROM variation v WHERE v.flag_id = f.id ORDER BY v.sort_order, v.key LIMIT 1)
FROM flag f WHERE f.project_id = :project_id
```
Flag list environment summary, one query per page:
```sql
SELECT fe.flag_id, e.key, fe.enabled, fe.version, fe.updated_at
FROM flag_environment fe JOIN environment e ON e.id = fe.environment_id
WHERE fe.flag_id = ANY(:flag_ids) ORDER BY e.sort_order, e.key
```

## Appendix G — clause validation (`services/clauses.py`)
```python
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
                    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
                    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$")
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")

def check_clause(clause: ClauseIn, path: str, *, allow_segments: bool) -> None
```
Rules, where `path` is the clause path and each bad value reports `f"{path}.values[{i}]"`:
- `in`, `not_in`: any scalar.
- `contains`, `starts_with`, `ends_with`: `str`.
- `matches`: `str` that `re.compile` accepts (catch `re.error`).
- `lt`, `lte`, `gt`, `gte`: `int` or `float`, not `bool`, finite.
- `semver_*`: a `str` that matches `SEMVER`.
- `before`, `after`: a `str` that matches `RFC3339` and parses with `datetime.fromisoformat`.
- `in_segment`: `str` keys. With `allow_segments=False`, it raises `validation_error("segment rules cannot use in_segment", f"{path}.operator")`.

All failures raise `validation_error`.

Also in this module:
```python
async def insert_clauses(session, clauses: list[ClauseIn], *, flag_rule_id: UUID | None,
                         segment_rule_id: UUID | None, segment_ids: dict[str, UUID]) -> None
    # position = index; for in_segment store values=[] and one clause_segment row per key
async def load_clauses(session, *, flag_rule_ids: list[UUID] | None = None,
                       segment_rule_ids: list[UUID] | None = None) -> dict[UUID, list[ClauseOut]]
    # two queries (clauses, then clause_segment joined to segment for keys)
async def resolve_segments(session, project_id: UUID, clauses_by_path: list[tuple[str, ClauseIn]]) -> dict[str, UUID]
    # one query; an unknown key raises invalid_reference("unknown segment 'k'", f"{path}.values[i]")
```

## Appendix H — prerequisites (`services/prerequisites.py`)
```python
def has_cycle(edges: dict[UUID, set[UUID]], start: UUID) -> bool
    """True if `start` is reachable from any of its own prerequisites (iterative DFS)."""
async def check_prerequisites(session, flag: Flag, environment_id: UUID,
                              prereqs: list[PrerequisiteIn]) -> list[tuple[UUID, UUID]]
```
`check_prerequisites` returns `(prerequisite_flag_id, variation_id)` in order, after these checks:
- Duplicate flag keys → `validation_error` at `prerequisites[i].flag`.
- Self → `prerequisite_cycle` at `prerequisites[i].flag`.
- Unknown or other-project flag → `invalid_reference` at `prerequisites[i].flag`.
- Archived flag → `invalid_reference` "prerequisite flag 'k' is archived".
- Variation not on that flag → `invalid_reference` at `prerequisites[i].variation`.
- Cycle → `prerequisite_cycle` at `prerequisites`.

The edges come from one query: `SELECT fe.flag_id, fp.prerequisite_flag_id FROM flag_prerequisite fp JOIN flag_environment fe ON fe.id = fp.flag_environment_id WHERE fe.environment_id = :env`. The current flag's existing edges are replaced by the proposed ones.

## Appendix I — usage (`services/usage.py`)
```python
async def flag_dependents(session, flag_id: UUID) -> list[dict[str, str]]
    # [{"flag": key, "environment": env_key}] of flags whose prerequisites include flag_id
async def variation_usage(session, variation: Variation, flag: Flag) -> list[dict[str, str]]
    # entries {"usage": "off_variation"} and {"usage": "targets"|"rules"|"rollouts"|"fallthrough"|"prerequisites", "environment": env_key, "flag": key}
async def segment_dependents(session, segment_id: UUID) -> list[dict[str, str]]
    # [{"flag": key, "environment": env_key}] via clause_segment -> clause -> flag_rule -> flag_environment
```
Each function returns its results sorted and deduplicated.

## Appendix J — `services/targeting.py`
```python
async def load_config(session, flag: Flag, fe: FlagEnvironment, env_key: str) -> FlagConfigOut
async def replace_config(session, flag: Flag, fe: FlagEnvironment, body: FlagConfigIn,
                         *, check_version: bool = True, set_enabled: bool = True) -> None
async def set_enabled(session, fe: FlagEnvironment, enabled: bool) -> None  # bumps version only on change
async def copy_config(session, flag: Flag, source: Environment, targets: list[Environment],
                      include_enabled: bool) -> None
```
`load_config` runs a fixed number of queries (no N+1). `replace_config` must be called with `fe` locked (`get_flag_environment(..., lock=True)`), and it runs in this order:
1. Version check (409 `version_conflict` with the current version).
2. Resolve the variation keys of the flag into a key → id map. An unknown key raises `invalid_reference` with its path.
3. Validate:
   - targets: a context key in two lists → `validation_error` at `targets[i].values[j]`
   - rules: `check_clause(..., allow_segments=True)`
   - weights: duplicate variation → `validation_error` at `….weights[j].variation`; sum ≠ 100000 → `validation_error` at `….rollout.weights`
   - segments: `resolve_segments`
   - prerequisites: `check_prerequisites`
4. Delete the old children: `flag_target`, `flag_rule` (which cascades its clauses, clause segments and weights), fallthrough `rollout_weight` (by `flag_environment_id`), and `flag_prerequisite`.
5. Insert the new rows with Python-generated UUIDs, `position` = array index. The fallthrough sets `fallthrough_variation_id` or `fallthrough_bucket_by` plus the weight rows (the other field becomes NULL).
6. Bump the row: `version += 1`, `updated_at = now()`, and `enabled` when `set_enabled`.
7. `flush()`, then `refresh(fe)`.

`copy_config` loads the source with `load_config`, then for each target (locked in id order) calls `replace_config(..., check_version=False, set_enabled=include_enabled)`, passing a `FlagConfigIn` built from the source output with `version=target.version`.

## Final verification
```bash
docker run -d --name mf-pg -e POSTGRES_USER=megooflags -e POSTGRES_PASSWORD=megooflags -e POSTGRES_DB=megooflags -p 5432:5432 postgres:16
export MEGOOFLAGS_DATABASE_URL=postgresql+asyncpg://megooflags:megooflags@localhost:5432/megooflags
python3 scripts/check_version.py && uv run --frozen --project api pytest scripts/tests
(cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest -v)
bash scripts/check_migrations.sh
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build)
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json
pnpm --dir web gen:api
git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh
```
Expected:
- Every test passes, with no DB test skipped.
- The migrations print `migrations OK (2 revisions)`.
- There is no drift in the generated files.
- The smoke test passes. Caddy already routes `/api/*` to the API.
