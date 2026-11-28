# MF-79 plan: auth, RBAC, MFA, SSO, API tokens, key rotation, audit log

**Goal:** every `/api` endpoint needs a signed-in user or an API token and checks a permission at org, project or environment scope. Every change writes an audit entry. People sign in with a password plus TOTP or a passkey, or through OIDC or SAML SSO. SDK keys and service tokens rotate with an overlap window.

**Architecture:**
- **Request auth:** a new `megooflags_api/auth/` package. `principal.py` turns a session cookie or an `mf_api_` bearer token into a `Principal`. `rbac.py` holds the permission catalogue and an `Authorizer` built once per request from role bindings. `require.py` provides `require(permission)`, which every `/api` route declares; it resolves `{project}`/`{env}` from the path.
- **Storage:** sessions, tokens, MFA credentials, SSO providers and the audit log are Postgres tables (migrations 0004–0011). Throttles, WebAuthn challenges, SSO state and SAML replay IDs live in Valkey under `megooflags:auth:*`.
- **Web:** five sign-in pages, plus a Next 16 `proxy.ts` that redirects to them.

## Conventions (every step)
- **Branch and commits:** branch `megoo/MF-79-governance-and-security-auth-rbac-sso-oi`. Commit each step as `<type>(<scope>): <summary> (MF-79)`, then a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `.megoocolab/`. `CHANGELOG.md` is edited only in step 27.
- **TDD:** write the test, see it fail, implement, see it pass. Keep green from `api/`: `uv run ruff check . ../scripts`, `uv run ruff format --check . ../scripts`, `uv run mypy`, and the full `uv run pytest`. Services are Postgres and Valkey, as in AGENTS.md §9.
- **Dependencies:** `cd api && uv add <pkg>` (or `--dev`). Commit `pyproject.toml` and `uv.lock` together.
- **Route or schema change:** run `uv run python -m megooflags_api.export_openapi openapi.json` (in `api/`), then `pnpm --dir web gen:api`. Commit both generated files with the step.
- **Migrations:**
  - Create with `uv run alembic revision --autogenerate -m <slug>`, then rename to `NNNN_<slug>.py`.
  - Set `revision="NNNN"` and `down_revision` to the previous revision.
  - `downgrade()` also drops the enum types.
  - Run `bash scripts/check_migrations.sh`; `test_models_match_migrations` must stay green.
  - Add every new table to `tests/test_models_migration.py`.
- **Errors:** use `ApiError` and add a factory in `errors.py` for each new code (Appendix K).
- **Audit:** write routes call `auditor.record(...)` (Appendix F) before `session.commit()`. From step 14 on, every new mutating route also gets a row in `AUDIT_SAMPLES`.

## Details this plan fixes (the design leaves them open)
1. **Secret key at startup:**
   - `MEGOOFLAGS_SECRET_KEY` (at least 32 characters) is checked at startup, in `serve.main` and the lifespan, not in `create_app()`. That way `export_openapi`, the CI web job and the tests still work.
   - The Fernet key is derived with HKDF-SHA256 (`info=b"megooflags/secrets/v1"`).
   - `conftest.py` sets a test key before it imports the app.
2. **Permission level:** each permission has a *level*, the most specific scope it can be granted at. A binding grants a permission only when the binding's scope is that level or broader. So an environment-scope Editor gets `flag.toggle` there, but never `environment.manage`. `audit.read` is level *project*, which is what makes the design's project-scoped audit work.
3. **Visibility from environment bindings:** an environment-scope binding also grants `project.read` on its project. Such a principal sees only the environments it is bound to. Hidden projects and environments return 404.
4. **No creator binding:** creating a project doesn't add a binding for the creator.
5. **Routes that check inside the handler:**
   - `copy` declares `require(project.read)`, then checks `flag.config.write` on each target environment.
   - `POST /bindings` declares `authenticated()`, then checks `member.manage` (user and team subjects) or `token.manage` (service tokens).
   - `/audit*` declares `require(audit.read, anywhere=True)` and scopes the rows itself.
6. **Session states:**
   - `pending_mfa` sessions are accepted only by `/auth/mfa/*`, `/auth/webauthn/*`, `/auth/me` and `/auth/sign-out`.
   - `enrol_mfa` sessions are accepted only by routes with `allow_enrolment=True` (TOTP and passkey enrolment), plus `/auth/me` and `/auth/sign-out`.
   - `/account/*` is `session_only`, so tokens get 403 and can't mint other tokens.
7. **`session.mfa_verified_at`:** a new column that backs the "factor verified in the last 10 minutes" rule.
8. **Expired credentials:** an expired SDK key acts exactly like a revoked one (403, and the stream gets `revoked`), following "KeyResolver treats an expired key like a revoked one". An expired, revoked or unknown API token returns 401.
9. **Audit entries outlive projects:** `audit_entry.project_id` and `environment_id` have no foreign keys, so entries survive project deletion. Responses LEFT JOIN the keys, which become `null` once the project is gone.
10. **SSO details:**
    - The default role is always bound at org scope, with `source=sso:<key>`.
    - A SAML email counts as verified, because the assertion is signed.
    - OIDC state is kept in Valkey and bound to the browser by the `mf_sso` cookie (SameSite=Lax, Path=/api/auth/sso). SAML state is keyed by RelayState only.
    - Callbacks redirect 303 to a validated relative `next`, or to `/sign-in?error=<code>`.
11. **Libraries:**
    - `argon2-cffi` (defaults).
    - `pyotp` (30 s, `valid_window=1`).
    - `webauthn` (py_webauthn 2.x).
    - `authlib` for PKCE, plus `joserfc` (Authlib's JOSE library) for ID tokens. `authlib.jose` emits deprecation warnings, and the definition of done allows no warnings.
    - `python3-saml`, with `lxml` and `xmlsec` built from source (`[tool.uv] no-binary-package`) to avoid the libxml2 mismatch.
12. **Throttles:** Valkey keys with a 15-minute window. Sign-in allows 10 failures per account and 100 per IP; MFA allows 10 failures per user. They fail open with a logged warning, like `RateLimiter`.
13. **Throttled timestamp writes:** API token `last_used_at` is written at most every 5 minutes, and session `last_seen_at` and idle expiry at most every 60 s. Test tokens are seeded with `last_used_at=now()`, so the statement-count tests stay exact.
14. **Next 16 proxy:** Next 16 renamed `middleware.ts` to `proxy.ts`. It calls the API at `MEGOOFLAGS_API_URL` (default `http://api:8000`, set in compose).

## File map
- **API, new:**
  - `crypto.py`.
  - `auth/{__init__,rbac,principal,require,passwords,sessions,csrf,throttle,mfa,webauthn}.py`, `auth/data/common_passwords.txt`.
  - `auth/sso/{__init__,state,oidc,saml,provisioning}.py`.
  - `models/{identity,sso,audit}.py`.
  - `services/{users,teams,roles,bindings,invitations,api_tokens,org_settings,sso_providers,audit}.py`.
  - `schemas/{auth,identity,tokens,audit,sso}.py`.
  - `routers/{setup,auth,account,users,teams,roles,api_tokens,sso,audit}.py`.
  - `alembic/versions/0004_identity`, `0005_sessions`, `0006_api_tokens`, `0007_audit`, `0008_invitations`, `0009_key_rotation`, `0010_mfa`, `0011_sso`.
- **API, modified:**
  - `settings.py`, `main.py`, `serve.py`, `errors.py`, `logging.py`, `tracing.py`, `routers/health.py`.
  - All management routers.
  - `services/{sdk_keys,streams,projects,environments}.py`, `sdk_auth.py`, `routers/sdk.py`.
  - `models/{enums,sdk,__init__}.py`, `schemas/{common,sdk_keys}.py`.
  - `deps.py` is deleted.
- **Tests:** `conftest.py`, `helpers.py`, `soft_webauthn.py`, `sso_fixtures.py`, and one `test_<area>.py` per step.
- **Ops:** `api/Dockerfile`, `.github/workflows/ci.yml`, `deploy/{docker-compose.yml,compose.ci.yml,.env.example,Caddyfile}`, `deploy/keycloak/megooflags-test-realm.json`, `scripts/{smoke.sh,propagation_test.py}`.
- **Web:** `web/proxy.ts`, `web/lib/auth.ts`, `web/app/{setup,sign-in,sign-in/mfa,invite/[token],account/security}/page.tsx`, `web/components/ui/*`, `web/next.config.ts`.
- **Docs:** `docs/security.md`, `CHANGELOG.md`, `AGENTS.md`.

## Steps
1. Secret key setting, SecretBox and startup check
2. Permission catalogue, identity tables and migration 0004
3. Password hashing, policy and the users service
4. Server-side sessions (migration 0005), cookies and CSRF check
5. API tokens table (migration 0006) and token service
6. Principal, Authorizer and require()
7. Audit log table (migration 0007) and audit.record
8. Setup, sign-in, sign-out, /auth/me, throttling and the no-user warning
9. Roles and bindings endpoints with the last-admin guard
10. Users and invitations (migration 0008)
11. Teams and team members
12. Service tokens
13. Enforce authentication and permissions on every management route
14. Audit every existing write route, plus the audit coverage test
15. Audit log query and export
16. SDK key rotation with overlap (migration 0009) and service token rotation
17. Account: password change and sessions
18. Personal API tokens
19. TOTP, recovery codes, MFA sign-in and require_mfa (migration 0010)
20. Passkeys (WebAuthn)
21. SSO providers and group mappings (migration 0011)
22. OIDC sign-in, JIT provisioning and group sync
23. SAML 2.0 sign-in, with xmlsec in CI and the image
24. Keycloak in CI and end-to-end SSO tests
25. Web: session-aware proxy, setup and sign-in pages
26. Web: MFA, invitation and account security pages
27. Security headers, log redaction, docs/security.md, CHANGELOG and AGENTS.md

The full detail of each step is in the steps array; the appendices below hold the shared contracts.

## Appendix A: settings and crypto
```python
# Settings
secret_key: SecretStr | None = None   # "" -> None; when set, length >= 32
session_idle_hours: float = Field(12, gt=0)
session_absolute_days: float = Field(7, gt=0)
pending_mfa_minutes: float = Field(5, gt=0)

# crypto.py
class SecretBox:
    def __init__(self, secret: str): ...          # Fernet(b64(HKDF-SHA256(len 32, info=b"megooflags/secrets/v1")))
    def encrypt(self, s: str) -> str: ...
    def decrypt(self, t: str) -> str: ...         # ValueError on a bad token
def require_secret_key(settings) -> SecretBox: ...  # RuntimeError mentioning MEGOOFLAGS_SECRET_KEY
SecretBoxDep  # reads app.state.secret_box, which is None without a key
```

## Appendix B: tables
**Enums** (`pg_enum`):
- `UserStatus` (active, disabled)
- `SubjectKind` (user, team, service_token)
- `ScopeKind` (org, project, environment)
- `SessionState` (pending_mfa, enrol_mfa, active)
- `ApiTokenKind` (service, personal)
- `InvitationPurpose` (invite, reset)
- `SsoProtocol` (oidc, saml)

**0004:**
- **`user`:**
  - `id`; `email` String(320), with a unique index `uq_user_email_lower` on `lower(email)`.
  - `name`; `password_hash` NULL; `status`; `created_at`; `last_login_at` NULL.
- **`team`:** `id`; `key`, unique; `name`; `created_at`.
- **`team_member`:**
  - `team_id` and `user_id`, both FK CASCADE, together the primary key.
  - `source`, default `'manual'`. Index on `user_id`.
- **`role`:** `id`; `key`, unique; `name`; `description`; `builtin`; `permissions` ARRAY(Text); `created_at`; `updated_at`.
- **`role_binding`:**
  - `id`; `subject_kind`; `subject_id`; `role_id` FK RESTRICT; `scope`.
  - `project_id` and `environment_id`, both FK CASCADE NULL.
  - `source`, default 'manual'; `created_at`.
  - CHECK `scope_matches`: org has neither id, project has only `project_id`, environment has both.
  - UNIQUE (`subject_kind`, `subject_id`, `role_id`, `scope`, `project_id`, `environment_id`) with `postgresql_nulls_not_distinct=True`.
  - Index on (`subject_kind`, `subject_id`).
- **`org_settings`:** `id` SmallInteger PK with CHECK `id = 1`; `require_mfa`; `updated_at`.
- **Seed rows:** the built-in roles, with literal lists in the migration, and `org_settings` row 1.

**0005:**
- **`session`:**
  - `id`; `token_hash`, unique; `user_id` FK CASCADE; `state`; `auth_method`.
  - `created_at`, `last_seen_at`, `idle_expires_at`, `absolute_expires_at`, `mfa_verified_at` NULL.
  - `ip`; `user_agent`(512).

**0006:**
- **`api_token`:**
  - `id`; `kind`; `name`; `prefix`(14); `secret_hash`, unique.
  - `owner_user_id` FK CASCADE NULL; `created_by` NULL.
  - `permissions` ARRAY NULL; `project_id` and `environment_id`, FK CASCADE NULL.
  - `created_at`, `expires_at`, `revoked_at`, `last_used_at`, `rotated_from_id`.
  - CHECK `(kind='personal') = (owner_user_id IS NOT NULL)`.

**0007:**
- **`audit_entry`:**
  - `id` bigserial; `occurred_at`.
  - `actor_kind`, `actor_id` NULL, `actor_label`, `auth_method` NULL.
  - `ip`, `user_agent`, `request_id`.
  - `action`, `resource_type`, `resource_key`.
  - `project_id` and `environment_id`, with no FKs.
  - `before` and `after` JSONB.
  - Indexes on `occurred_at`, `action`, `actor_id` and `project_id`.

**0008:**
- **`invitation`:** `id`; `token_hash`, unique; `user_id` FK CASCADE; `purpose`; `created_at`; `expires_at`; `used_at`.

**0009:**
- **`sdk_key`** gains `expires_at` and `rotated_from_id`.

**0010:**
- **`totp_credential`:** `user_id` as PK and FK; `secret_encrypted`; `created_at`; `confirmed_at`; `last_used_step`.
- **`recovery_code`:** `id`; `user_id`; `code_hash`; `used_at`.
- **`webauthn_credential`:** `id`; `user_id`; `credential_id` bytes, unique; `public_key` bytes; `sign_count`; `transports` ARRAY; `name`; `created_at`; `last_used_at`.

**0011:**
- **`sso_provider`:**
  - `id`; `key`, unique; `name`; `protocol`; `enabled`.
  - `config` JSONB; `secret_encrypted` NULL.
  - `jit_enabled`, default true; `link_by_verified_email`, default false.
  - `default_role_id` FK RESTRICT NULL; timestamps.
- **`sso_group_mapping`:**
  - `id`; `provider_id` FK CASCADE; `idp_group`.
  - `team_id` NULL, or `role_id` + `scope` + `project_id` + `environment_id`.
  - CHECK: exactly one target, a team or a role with its scope.
- **`user_identity`:** `id`; `provider_id`; `subject`; `user_id`; `created_at`; UNIQUE (`provider_id`, `subject`).

## Appendix C: rbac.py
`Permission` is a StrEnum with these values: `org.manage`, `member.manage`, `role.manage`, `token.manage`, `project.create`, `audit.read`, `project.read`, `project.manage`, `environment.manage`, `flag.create`, `flag.update`, `flag.archive`, `segment.write`, `flag.config.write`, `flag.toggle`, `evaluate`, `sdk_key.read`, `sdk_key.manage`.

`PERMISSION_LEVEL` sets each permission's level:

| Level | Permissions |
|---|---|
| org | `org.manage`, `member.manage`, `role.manage`, `token.manage`, `project.create` |
| project | `audit.read`, `project.read`, `project.manage`, `environment.manage`, `flag.create`, `flag.update`, `flag.archive`, `segment.write` |
| environment | `flag.config.write`, `flag.toggle`, `evaluate`, `sdk_key.read`, `sdk_key.manage` |

`BUILTIN_ROLES` defines the three built-in roles:
- `admin` holds every permission.
- `editor` holds everything except `org.manage`, `member.manage`, `role.manage`, `token.manage` and `audit.read`.
- `viewer` holds `project.read`, `evaluate`, `sdk_key.read` and `audit.read`.

`RANK` orders the scopes: org 0, project 1, environment 2.
```python
@dataclass(frozen=True) class Grant: scope; project_id; environment_id; permissions: frozenset[Permission]
class Authorizer:
    can(perm, project_id=None, environment_id=None) -> bool
      # some g: perm in g.permissions and RANK[g.scope] <= RANK[LEVEL[perm]] and
      # (org, or project and g.project_id == project_id, or env and g.environment_id == environment_id),
      # or (detail 3) perm is project.read and some env grant has g.project_id == project_id
    can_anywhere(perm) -> bool
    can_see_environment(project_id, env_id) -> bool   # strict project.read (an org or project grant), or a grant on that env
    visible_project_ids() -> set | None               # None means all
    visible_environment_ids(project_id) -> set | None
    projects_with(perm) -> set | None                 # None means org-wide
class CappedAuthorizer(Authorizer)   # personal token: perm in token.permissions and inside the token scope and owner.can(...)
async def load_authorizer(session, principal) -> Authorizer
    # one SELECT: role_binding JOIN role, LEFT JOIN environment for project_id;
    # user: (user, uid) OR (team, team_id IN user's teams); service token: (service_token, id)
```

## Appendix D: principal and require
```python
class PrincipalKind(StrEnum): USER, SERVICE_TOKEN, PERSONAL_TOKEN
@dataclass(frozen=True) class Principal:
    kind; id; label; auth_method
    user_id = None; session_id = None; session_state = None
    token_permissions = None; token_project_id = None; token_environment_id = None
async def resolve_principal(request, session, *, allow_enrolment=False, allow_pending=False) -> Principal
#  Bearer: mf_api_ resolve_token, else 401. Personal tokens need an active owner. Label: name, or "email (name)". auth_method "token".
#  Cookie: lookup_session, else 401; a disabled user is 401; unsafe methods go through csrf.check_origin;
#  pending is 401 mfa_required and enrol is 403 mfa_enrolment_required, unless allowed; then sessions.touch.
#  Sets request.state.principal.
get_principal / PrincipalDep        # the defaults
@dataclass class Authz: principal; authorizer; project: Project|None; environment: Environment|None
    def check(perm, project_id=None, environment_id=None)   # raises 403 forbidden
class Require: __init__(permission|None, *, anywhere=False, allow_enrolment=False, session_only=False); __call__(request, session: SessionDep) -> Authz
#  session_only rejects a non-user with 403; the authorizer is cached on request.state;
#  path {project}: get_project (404), hidden -> 404; {env}: get_environment (404), not can_see_environment -> 404;
#  then the permission check (None means no check; anywhere means can_anywhere) -> 403
require(perm, *, anywhere=False); authenticated(*, allow_enrolment=False, session_only=False)
PUBLIC_ROUTES: frozenset[tuple[method, path]]   # entries are added by the step that creates each public route
```
- **Routers with `Depends(get_principal)`:** `main.py` mounts the existing routers this way, along with `users`, `teams`, `roles`, `api_tokens`, `audit` and `sso.admin_router`.
- **Routers without it:** `setup`, `auth`, `account`, `users.public_router` and `sso.flow_router`. Every route in them is public or declares its own `Require`.

## Appendix E: sessions
```python
COOKIE_SECURE="__Host-mf_session"; COOKIE_PLAIN="mf_session"
read_cookie(request); set_cookie(response, request, token, max_age); clear_cookie(response, request)
#  https: __Host-, Secure, HttpOnly, SameSite=Lax, Path=/; http: mf_session without Secure
create_session(session, request, user, *, state, auth_method, settings, mfa_verified=False) -> (row, token)
#  token_urlsafe(32), stored as sha256; pending: both expiries = now + pending_mfa_minutes
lookup_session(session, token) -> (row, user) | None    # deletes and returns None once expired
touch(session, row, settings)                           # at most every 60 s
rotate(session, request, row, *, state, settings, mfa_verified=False) -> (row, token)   # deletes the old row
end_sessions(session, user_id, *, except_id=None) -> int
```
`csrf.check_origin(request)`:
- Safe methods pass.
- Otherwise `Origin` must equal the origin of `public_url`, or `scheme://Host`.
- A missing `Origin` with `Sec-Fetch-Site: same-origin` also passes.
- Anything else gets 403 `csrf_failed`.
- It applies to cookie requests only.

## Appendix F: audit
```python
RequestMeta(ip, user_agent, request_id); request_meta(request)   # request.client.host, UA[:512], X-Request-ID[:128]
SECRET_FIELDS = {password, current_password, new_password, password_hash, secret, secret_hash, secret_encrypted, token,
  token_hash, client_secret, sp_private_key, totp_secret, code, code_hash, recovery_codes, credential, public_key}
redact(value)          # recursive; those keys become "***"
snapshot(model_or_mapping)
record(session, principal|None, meta, action, resource_type, resource_key, *, project_id=None,
       environment_id=None, before=None, after=None, actor_label=None)   # add + flush, never commits; no principal means "anonymous"
Auditor.record(action, resource_type, resource_key, **kw)   # principal from request.state; AuditDep
```
Actions, grouped by resource:

| Resource | Actions |
|---|---|
| project | `project.create`, `project.update`, `project.delete` |
| environment | `environment.create`, `environment.update`, `environment.delete` |
| flag | `flag.create`, `flag.update`, `flag.archive`, `flag.restore`, `flag.delete`, `flag.config.update`, `flag.toggle`, `flag.kill`, `flag.copy` (one entry per target) |
| variation | `flag.variation.create`, `flag.variation.update`, `flag.variation.delete` |
| segment | `segment.create`, `segment.update`, `segment.config.update`, `segment.delete` |
| SDK key | `sdk_key.create`, `sdk_key.revoke`, `sdk_key.rotate` |
| user | `user.create`, `user.update`, `user.disable`, `user.enable`, `user.reset_link`, `user.mfa_reset`, `invitation.accept` |
| team | `team.create`, `team.update`, `team.delete`, `team.member.add`, `team.member.remove` |
| role and binding | `role.create`, `role.update`, `role.delete`, `binding.create`, `binding.delete` |
| tokens | `token.create`, `token.revoke`, `token.rotate`, `account.token.create`, `account.token.revoke` |
| settings | `settings.update` |
| SSO admin | `sso_provider.create`, `sso_provider.update`, `sso_provider.delete`, `sso_mapping.create`, `sso_mapping.delete`, `sso.sync` |
| auth | `auth.setup`, `auth.sign_in`, `auth.sign_in.failed`, `auth.sign_out`, `auth.mfa.failed`, `auth.sso.sign_in`, `auth.sso.denied` |
| account | `account.password.change`, `account.totp.enrol`, `account.totp.confirm`, `account.totp.remove`, `account.passkey.register`, `account.passkey.rename`, `account.passkey.delete`, `account.recovery_codes.regenerate`, `account.session.revoke` |

## Appendix G: permissions per route (all under /api)
**Existing routes**

| Route | Permission |
|---|---|
| `GET /projects` | `authenticated()`, filtered |
| `POST /projects` | `project.create` |
| `GET /projects/{project}` | `project.read` |
| `PATCH`/`DELETE /projects/{project}` | `project.manage` |
| `GET /projects/{project}/environments` | `project.read` (filtered) |
| `POST /projects/{project}/environments` | `environment.manage` |
| `GET .../environments/{env}` | `project.read` |
| `PATCH`/`DELETE .../environments/{env}` | `environment.manage` |
| `POST .../evaluate` | `evaluate` |
| `GET .../sdk-keys` | `sdk_key.read` |
| `POST .../sdk-keys`, `DELETE .../sdk-keys/{id}`, `POST .../rotate` | `sdk_key.manage` |
| Flag `GET` routes | `project.read` |
| `POST .../flags` | `flag.create` |
| Flag `PATCH`; variation `POST`/`PATCH`/`DELETE` | `flag.update` |
| archive, restore, flag `DELETE` | `flag.archive` |
| `GET .../flags/{flag}/environments` (filtered) and `GET .../environments/{env}` | `project.read` |
| `PUT .../environments/{env}` | `flag.config.write` |
| toggle, kill | `flag.toggle` |
| copy | `project.read`, plus per-target checks |
| Segment reads | `project.read` |
| Segment writes | `segment.write` |

**New routes**

| Route | Permission |
|---|---|
| `GET /setup/status`, `POST /setup` | public |
| `POST /auth/sign-in`, `/auth/sign-out`, `GET /auth/me` | public |
| `POST /auth/mfa/totp`, `/auth/mfa/recovery` | public |
| `POST /auth/webauthn/options`, `/auth/webauthn/verify` | public |
| `GET /auth/providers` | public |
| `GET /auth/sso/{provider}/start`, `GET /auth/sso/{provider}/callback`, `GET /auth/sso/{provider}/saml/metadata`, `POST /auth/sso/{provider}/saml/acs` | public |
| `GET`/`POST /invitations/{token}` | public |
| `GET /roles`, `GET /roles/{key}` | `authenticated()` |
| Role writes | `role.manage` |
| `GET /bindings` | `authenticated()`; without `member.manage` or `token.manage`, only the caller's own bindings |
| `POST /bindings`, `DELETE /bindings/{id}` | `authenticated()`, plus the handler check (detail 5) |
| `/users*` | `member.manage` |
| Team reads | `authenticated()` |
| Team writes | `member.manage` |
| `/tokens*` | `token.manage` |
| `GET /settings` | `authenticated()` |
| `PUT /settings` | `org.manage` |
| `/sso/providers*` | `org.manage` |
| `/audit`, `/audit/export` | `require(audit.read, anywhere=True)` |
| `/account/password`, `/account/sessions*`, `/account/tokens*`, `/account/recovery-codes` | `authenticated(session_only=True)` |
| `/account/totp*`, `/account/passkeys*` | `authenticated(allow_enrolment=True, session_only=True)` |

## Appendix H: test helpers (`tests/helpers.py`)
```python
RESET_SQL = ['TRUNCATE project, "user", team, role_binding CASCADE', "DELETE FROM role WHERE NOT builtin",
             "UPDATE org_settings SET require_mfa = false"]   # steps 5, 7 and 21 add api_token, audit_entry and sso_provider
async def reset_database(app)        # RESET_SQL; from step 8 also deletes Valkey megooflags:auth:* (errors ignored)
PASSWORD = "correct horse battery staple"; ORIGIN = {"Origin": "http://test"}
async def seed_user(app, email="admin@example.com", *, password=PASSWORD, role="admin", scope="org", project_id=None, environment_id=None, name="Test User") -> UUID
async def seed_binding(app, subject_kind, subject_id, role, scope="org", project_id=None, environment_id=None) -> UUID
async def seed_service_token(app, role="admin", *, scope="org", project_id=None, environment_id=None, name="tests") -> str  # last_used_at=now()
async def sign_in(client, email="admin@example.com", password=PASSWORD) -> httpx.Response
def bearer(token) -> dict; async def audit_entries(app, action=None) -> list[dict]
```
`conftest.py`:
- Sets the secret key environment variable (step 1).
- The `app` fixture calls `reset_database` (step 2).
- Adds an `anon` client fixture with `ORIGIN` (step 8).
- The `api` fixture sends the bearer header from `seed_service_token(app)` (step 13).
- Finalizers close `app.state.sso_http` when it exists (step 21).

## Appendix I: `tests/soft_webauthn.py`
```python
import base64, hashlib, json, os, struct
from dataclasses import dataclass, field
from typing import Any
import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

def b64url(b: bytes) -> str: return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def unb64url(s: str) -> bytes: return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

@dataclass
class SoftAuthenticator:
    origin: str
    rp_id: str
    key: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))
    credential_id: bytes = field(default_factory=lambda: os.urandom(32))
    sign_count: int = 0
    user_handle: bytes | None = None

    def _auth_data(self, flags: int, attested: bytes = b"") -> bytes:
        return hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.sign_count) + attested

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}).encode()

    def create(self, options: dict[str, Any]) -> dict[str, Any]:
        self.user_handle = unb64url(options["user"]["id"])
        n = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
        attested = bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": self._auth_data(0x45, attested)})
        cd = self._client_data("webauthn.create", options["challenge"])
        return {"id": b64url(self.credential_id), "rawId": b64url(self.credential_id), "type": "public-key",
                "response": {"clientDataJSON": b64url(cd), "attestationObject": b64url(att), "transports": ["internal"]},
                "clientExtensionResults": {}}

    def get(self, options: dict[str, Any], *, bump: bool = True) -> dict[str, Any]:
        if bump:
            self.sign_count += 1
        ad = self._auth_data(0x05)
        cd = self._client_data("webauthn.get", options["challenge"])
        sig = self.key.sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64url(self.credential_id), "rawId": b64url(self.credential_id), "type": "public-key",
                "response": {"clientDataJSON": b64url(cd), "authenticatorData": b64url(ad), "signature": b64url(sig),
                             "userHandle": b64url(self.user_handle) if self.user_handle else None},
                "clientExtensionResults": {}}
```

## Appendix J: Keycloak
**Realm file:** `deploy/keycloak/megooflags-test-realm.json`.

**Realm:** `megooflags-test`, with `sslRequired: "none"`.

**Groups:** `flag-admins` and `viewers`.

**Users:** every user has `emailVerified: true` and the password `Keycloak-test-1` (`temporary: false`).

| User | Email | Groups |
|---|---|---|
| `alice` | alice@example.com | `/flag-admins` |
| `bob` | bob@example.com | `/viewers` |
| `carol` | carol@example.com | none |

**OIDC client `megooflags-oidc`:**
- Confidential, with secret `megooflags-oidc-secret`.
- Standard flow only.
- `redirectUris: ["http://megooflags.test/*"]`.
- `pkce.code.challenge.method: S256`.
- Mapper: `oidc-group-membership-mapper`, with `claim.name: groups`, `full.path: false`, and the claim added to the ID token, the access token and userinfo.

**SAML client `http://megooflags.test/api/auth/sso/kc-saml/saml/metadata`:**
- ACS POST URL: `http://megooflags.test/api/auth/sso/kc-saml/saml/acs`.
- Assertions and the server are signed; the client is not.
- The name ID format is forced to `email`.
- Mappers: `saml-user-property-mapper` (`email` → attribute `email`) and `saml-group-membership-mapper` (attribute `groups`, `full.path: false`).

**Compose service** (in `compose.ci.yml`, `profiles: [keycloak]`):
- Image `quay.io/keycloak/keycloak:26.0`, command `start-dev --import-realm`.
- Environment: `KC_BOOTSTRAP_ADMIN_USERNAME`/`KC_BOOTSTRAP_ADMIN_PASSWORD` set to `admin`, and `KC_HEALTH_ENABLED: "true"`.
- The realm file is mounted read-only at `/opt/keycloak/data/import/`.
- Port `8180:8080`.
- Healthcheck: `exec 3<>/dev/tcp/127.0.0.1/9000 && printf 'GET /health/ready HTTP/1.0\r\n\r\n' >&3 && grep -q UP <&3`, with 60 retries.

## Appendix K: new error codes

| Status | Codes |
|---|---|
| 401 | `invalid_credentials` ("invalid email or password"), `mfa_required`, `invalid_code` |
| 403 | `mfa_enrolment_required`, `csrf_failed`, `reauth_required` |
| 404 | `invalid_invitation` |
| 409 | `already_set_up`, `last_admin`, `builtin_role`, `public_url_required`, `no_password`, `totp_exists`, `key_inactive`, `mfa_required_by_org` |
| 422 | `weak_password`, `exceeds_owner_rights` |
| 503 | `unavailable` |

Step 13 adds 401 and 403 to `ERROR_RESPONSES`.

## Final verification
Run these with Postgres, Valkey and Keycloak up. Start Keycloak with `docker compose -f deploy/docker-compose.yml -f deploy/compose.ci.yml --profile keycloak up -d --wait keycloak`, then `export MEGOOFLAGS_KEYCLOAK_URL=http://localhost:8180`.
```bash
python3 scripts/check_version.py && uv run --frozen --project api pytest scripts/tests
(cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest -v)
bash scripts/check_migrations.sh
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build)
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json && pnpm --dir web gen:api && git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh http && bash scripts/smoke.sh https
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
```
These must hold:
- `test_route_permissions`, `test_audit_coverage`, `test_rbac`, `test_rbac_routes` and `test_sso_keycloak` all pass, and none of them is skipped.
- The smoke test signs in through `/api/setup` and runs the propagation test with a service token.
- An unauthenticated `/api/projects` returns 401.
