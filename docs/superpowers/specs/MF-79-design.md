# MF-79: Governance and security (auth, RBAC, SSO, MFA, API tokens, key rotation, audit log)

## Goal
Today `/api/*` is open to anyone. After this change:
- Every management endpoint needs a signed-in user or an API token.
- Every endpoint checks permissions at org, project or environment level.
- Every change writes an audit entry.
- People can sign in with a password plus MFA (TOTP or passkeys), or through SSO (OIDC or SAML 2.0) with JIT provisioning and group mapping.
- SDK keys can be rotated with an overlap window.

The web app gets only the minimal pages needed to sign in (mockup v2). Every other screen is MF-82.

## Decisions
- **One design for everything** (your answer). The build plan will be long and ordered so each step leaves `main` green.
- **API plus minimal sign-in pages** (your answer). The pages are `/setup`, `/sign-in`, `/sign-in/mfa`, `/invite/<token>` and `/account/security`. Users, teams, roles, tokens, SSO providers and the audit log are API-only until MF-82. Rejected: API-only, and a full admin UI.
- **The first visitor becomes admin** (your answer). While no user exists, `/setup` (and `POST /api/setup`) is open.
  - It is atomic: a transaction-scoped advisory lock plus a "no user exists" check. If two people race, exactly one wins and the other gets 409.
  - While no user exists, the API logs a WARNING at startup and on every `/readyz`, and the CHANGELOG tells operators to finish setup right after upgrading.
  - Rejected: a setup token, admin credentials from env vars, and a CLI command.
- **RBAC = permission sets bound at a scope** (your answer).
  - A fixed permission catalogue. Roles are named sets of permissions. Built-in Admin, Editor and Viewer roles are seeded and read-only. Custom roles can hold any subset.
  - A binding gives a user, team or service token a role at org, project or environment scope, and it applies to that scope and everything below it.
  - Allow-only: effective rights are the union of all bindings.
  - Rejected: a fixed role ladder, and allow/deny policy documents.
- **SSO providers live in the database and are managed through the API** (your answer). Several providers can be active. Client secrets and SAML private keys are encrypted with `MEGOOFLAGS_SECRET_KEY`. Every change is audited. Rejected: an env/file config, and both.
- **Both kinds of API token** (your answer).
  - **Service tokens** get their own role bindings and survive staff changes.
  - **Personal tokens** get a permission subset and a scope. They are always capped by the owner's current rights and stop working when the owner is disabled.
- **MFA: TOTP and passkeys (WebAuthn)** (your answer), plus single-use recovery codes. A passkey also works for passwordless sign-in.
  - I added an org setting, `require_mfa` (default off). When it is on, local-account users must enrol a factor before they get a full session.
  - SSO users rely on their IdP's MFA.
  - *Assumption, flagged for review:* the org setting `require_mfa` comes from the TOTP option you saw earlier. Drop it if you don't want it.
- **Single organisation per instance** (PRD Q5, as the task says). "Org scope" means the whole instance. There is no org table, only a singleton `org_settings` row.
- **Server-side sessions, not JWTs.** A session can be revoked at once ("sign out other sessions", disabling a user). Sessions are stored hashed in Postgres, like SDK keys.
- **`flag.owner` stays free text.** Moving it to a user reference is out of scope.

## Architecture
New modules, following the existing `routers/` → `services/` → `models/` split:

| Module | Purpose |
|---|---|
| `auth/principal.py` | `Principal` (kind `user`/`service_token`/`personal_token`, id, label, auth method). Replaces `deps.Actor`. `get_principal` replaces `deps.get_actor` (the single hook MF-76 left), reading the session cookie or `Authorization: Bearer mf_api_…`. |
| `auth/rbac.py` | Permission catalogue, built-in roles, `Authorizer`. The authorizer loads the principal's bindings (user + team bindings, or token bindings) once per request and answers `can(permission, project_id, env_id)`. It also filters list endpoints (only visible projects and environments). |
| `auth/require.py` | `require("flag.write")` dependency. It resolves `{project}` and `{env}` from the path and raises 403 otherwise. Every `/api` route declares exactly one. |
| `auth/sessions.py`, `auth/passwords.py`, `auth/csrf.py` | Session create/rotate/expire, cookies, argon2id hashing, common-password check, Origin check. |
| `auth/mfa.py`, `auth/webauthn.py` | TOTP (pyotp), recovery codes, WebAuthn registration and assertion (py_webauthn). |
| `auth/sso/oidc.py`, `auth/sso/saml.py`, `auth/sso/provisioning.py` | OIDC with Authlib (code flow + PKCE, state, nonce, discovery). SAML with python3-saml (SP-initiated, signed assertions required). JIT provisioning and group sync. |
| `services/audit.py` | `record(session, principal, request_meta, action, resource, before, after)`, called in the same transaction as the change. Also query, filters and streaming export. |
| `services/api_tokens.py`, `services/users.py`, `teams.py`, `roles.py`, `bindings.py`, `sso_providers.py`, `invitations.py` | CRUD plus their rules. |
| `crypto.py` | Fernet encryption of stored secrets, keyed by `MEGOOFLAGS_SECRET_KEY` (a new required setting; startup fails without it). |
| `routers/auth.py`, `users.py`, `teams.py`, `roles.py`, `api_tokens.py`, `sso.py`, `audit.py`, `setup.py`, `account.py` | Endpoints (below). |

**Changes to existing code:**
- **`main.py`:** routers depend on `get_principal`. The public auth routes (`/api/setup`, `/api/auth/*` sign-in, SSO callbacks, providers list) are mounted without it, from an explicit allow-list.
- **Write services** (projects, environments, flags, flag config, toggles, copy, archive/delete, variations, segments, SDK keys) call `audit.record` with snapshots taken from their existing output schemas.
- **`sdk_keys`:**
  - New `expires_at` and `rotated_from_id` columns, plus a rotate endpoint.
  - `KeyResolver` treats an expired key like a revoked one.
  - The stream re-check loop closes streams whose key has expired.
- **Client IP and scheme** already come from trusted forwarded headers (uvicorn `proxy_headers` + `FORWARDED_ALLOW_IPS`, Caddy `trusted_proxies`, DEP-3). Audit entries, the throttles and the `Secure` cookie decision use `request.client.host` and `request.url.scheme` as they are.
- **Access log:** never logs `Authorization`, cookies or `?key=`.
- **Web:**
  - A Next.js middleware calls `/api/auth/me` and redirects to `/sign-in` (or to `/setup` while no user exists).
  - The five pages are built with the existing shadcn components.
  - A small QR library renders the TOTP enrolment QR code.
- **`deploy/`:**
  - `.env.example` gains `MEGOOFLAGS_SECRET_KEY` and a note that `MEGOOFLAGS_PUBLIC_URL` is required for passkeys and SSO.
  - The API image installs `xmlsec1` libraries for SAML.
  - `compose.ci.yml` adds a Keycloak service that imports a test realm.

## Data and interfaces
**New tables** (one Alembic revision per group, each with a working downgrade):
- **`user`:** `id`, `email` (unique, case-insensitive), `name`, `password_hash` (nullable for SSO-only users), `status` (active/disabled), `created_at`, `last_login_at`.
- **`team`** (`key`, `name`) and **`team_member`** (`team_id`, `user_id`, `source` manual or sso:`<provider>`).
- **`role`:** `key`, `name`, `description`, `builtin`, `permissions text[]` (checked against the catalogue).
- **`role_binding`:**
  - Subject: `subject_kind` user/team/service_token and `subject_id`.
  - Scope: `scope` org/project/environment, `project_id` and `environment_id` (FKs, cascade), with a check constraint matching the scope.
  - Also `role_id` and `source` (manual or sso).
- **`session`:** `token_hash`, `user_id`, `state` (pending_mfa, enrol_mfa or active), `auth_method`, `created_at`, `last_seen_at`, `idle_expires_at`, `absolute_expires_at`, `ip`, `user_agent`.
- **`api_token`:**
  - Identity: `kind` service/personal, `name`, `prefix`, `secret_hash`.
  - Ownership: `owner_user_id` (personal tokens) and `created_by`.
  - Personal tokens only: `permissions text[]`, `project_id`, `environment_id`.
  - Lifetime: `expires_at`, `revoked_at`, `last_used_at` (throttled writes), `rotated_from_id`.
- **`totp_credential`:** secret encrypted, `confirmed_at`, `last_used_step` (stops a code from being replayed).
- **`webauthn_credential`:** `credential_id`, `public_key`, `sign_count`, `transports`, `name`, `last_used_at`.
- **`recovery_code`:** `code_hash`, `used_at`.
- **`invitation`:** `token_hash`, `user_id`, `purpose` (invite or reset), `expires_at` (7 days), `used_at`.
- **`sso_provider`:**
  - Basics: `key`, `name`, `protocol` (oidc or saml), `enabled`.
  - `config jsonb`. OIDC: issuer, client_id, scopes, groups claim. SAML: IdP metadata XML or URL, entity id, attribute names for email, name and groups.
  - `secret_encrypted`.
  - Provisioning: `jit_enabled`, `link_by_verified_email`, `default_role_id`.
- **`sso_group_mapping`:** provider, IdP group name, and either a team or a role at a scope.
- **`user_identity`:** `provider_id`, `subject`, `user_id`; unique on provider + subject.
- **`org_settings`:** a singleton row with `require_mfa`.
- **`audit_entry`:**
  - `id` bigserial, `occurred_at`.
  - Actor: `actor_kind`, `actor_id`, `actor_label`, `auth_method`.
  - Request: `ip`, `user_agent`, `request_id`.
  - What changed: `action` (for example `flag.config.update`, `auth.sign_in.failed`), `resource_type`, `resource_key`, `project_id`, `environment_id`.
  - `before jsonb`, `after jsonb`. Indexed by time, action, actor and project. No update or delete path exists.
- **`sdk_key`:** adds `expires_at` and `rotated_from_id`.

**Permission catalogue (first version):**
- Org: `org.manage` (settings and SSO providers), `member.manage` (users, teams, bindings, invitations), `role.manage`, `token.manage` (service tokens), `project.create`, `audit.read`.
- Project: `project.read`, `project.manage`, `environment.manage`, `flag.create`, `flag.update` (metadata and variations), `flag.archive` (archive, restore, delete), `segment.write`.
- Environment: `flag.config.write` (targeting PUT and copy into the environment), `flag.toggle` (toggle and kill switch), `evaluate`, `sdk_key.read`, `sdk_key.manage`.

Built-in roles: **Admin** has everything. **Editor** has everything except the org-level permissions apart from `project.create`. **Viewer** has `project.read`, `evaluate`, `sdk_key.read` and `audit.read`. A binding of `audit.read` at project scope shows only that project's entries.

**Endpoints (all under `/api`):**
- **Setup:** `GET /setup/status` and `POST /setup`.
- **Auth:** `POST /auth/sign-in` (email and password), `POST /auth/mfa/totp`, `POST /auth/mfa/recovery`, `POST /auth/webauthn/options` and `/verify` (sign-in or second factor), `POST /auth/sign-out`, `GET /auth/me`, `GET /auth/providers` (public list for the sign-in page).
- **SSO flows:** `GET /auth/sso/{provider}/start` and `/callback` (OIDC); `GET …/saml/metadata` and `POST …/saml/acs` (SAML).
- **Account (`/account`):**
  - Password: `PUT /account/password` (current password required).
  - TOTP: `POST /account/totp` (enrol, returns `otpauth://`), `/account/totp/confirm`, `DELETE /account/totp`.
  - Passkeys: `/account/passkeys` (list, register, rename, delete).
  - Recovery codes: `POST /account/recovery-codes` (regenerate, shown once).
  - Sessions: `GET /account/sessions` and `DELETE /account/sessions/{id}` (or "others").
  - Personal tokens: `/account/tokens`.
  - Removing the last MFA factor, or regenerating recovery codes, needs a factor verified in the last 10 minutes.
- **Users, teams and roles:**
  - `/users`: create returns a one-time invite link; also update, disable and enable, `POST /users/{id}/reset-link`, and `DELETE /users/{id}/mfa` (admin reset).
  - `/invitations/{token}`: GET to check, POST to set the password.
  - `/teams` and `/teams/{key}/members`, `/roles`.
  - `/bindings`, filterable by subject or scope.
- **Tokens and SSO admin:** `/tokens` (service tokens, with bindings), `/tokens/{id}/rotate`, `/sso/providers` and their `/mappings`, `/settings`.
- **SDK keys:** `POST /projects/{project}/environments/{env}/sdk-keys/{id}/rotate` with `{overlap_seconds}`. Default 86400, max 30 days, 0 means revoke at once. It returns the new key once; the old key works until `expires_at`. `POST /tokens/{id}/rotate` works the same way.
- **Audit:** `GET /audit` with filters `actor`, `action`, `resource_type`, `project`, `env`, `from` and `to`, cursor-paginated like the other lists. `GET /audit/export?format=csv|ndjson` streams with the same filters. Both need `audit.read` and are scoped by the caller's bindings.

**Formats:**
- API tokens are `mf_api_<32 base62>` and stored as SHA-256, matching the SDK key scheme.
- The session cookie is `__Host-mf_session` (HttpOnly, Secure, SameSite=Lax, Path=/), with a 256-bit random value. Over plain HTTP (local dev) it falls back to `mf_session` without `Secure`, decided from the forwarded proto.
- Default timeouts: idle 12 hours, absolute 7 days, pending MFA 5 minutes. All three are settings.

## Error handling and edge cases
- **401 vs 403:** no or invalid credentials return 401. A valid principal that lacks the permission returns 403. Unknown resources are still 404, but only after the permission check at the parent scope, so a hidden project returns 404 instead of revealing that it exists.
- **Sign-in protection:**
  - Sign-in returns one generic error for every failure.
  - Failures are throttled in Valkey per account and per IP, returning 429 with `Retry-After`. Failures are audited (`auth.sign_in.failed`).
  - Password verification runs even for unknown emails, so timing doesn't reveal which accounts exist.
- **Sessions:** the session id rotates on sign-in, after MFA and after a password change. A password change ends all other sessions, and disabling a user ends all of theirs.
- **CSRF:** cookie-authenticated unsafe requests must send an `Origin` (or `Sec-Fetch-Site: same-origin`) that matches `MEGOOFLAGS_PUBLIC_URL` or the request host; otherwise 403. Bearer-token requests are exempt.
- **SSO account linking:**
  - SSO never links to an existing local account unless the provider has `link_by_verified_email` set and the IdP asserts a verified email.
  - Otherwise an email clash returns a clear error and an `auth.sso.denied` audit entry.
  - A disabled user can't sign in through SSO.
- **SSO checks:**
  - OIDC: state, nonce, issuer, audience and expiry.
  - SAML: signatures, audience, destination, InResponseTo and NotOnOrAfter. Assertion IDs are kept in Valkey until they expire, so a replayed assertion is refused.
  - SAML login state is kept in Valkey keyed by RelayState, not in a cookie, because the ACS request is a cross-site POST.
- **Group sync:** runs on every SSO sign-in. It adds and removes only the `source=sso:<provider>` memberships and bindings; manual ones are never touched. If nothing maps, the user can sign in but sees nothing, unless the provider has a `default_role`.
- **Admin safety:** the last active user who holds Admin at org scope can't be disabled, demoted or unbound (409). Built-in roles can't be edited or deleted. A custom role that is still in use can't be deleted (409).
- **Personal tokens:** effective rights are the token's permissions intersected with the owner's rights at request time. Raising them above the owner's current rights at creation is refused (422).
- **Key overlap:** the expiry of a rotated key is checked on every resolve, with at most the 10-second resolver cache delay. Streams that use an expired key close on the next re-check, at most 30 seconds later.
- **Audit content:**
  - Secrets, hashes, TOTP secrets and SSO client secrets never reach `before`/`after`; they are redacted as `"***"`.
  - Each audit write is part of the change's transaction, so a change without its audit entry can't be committed.
  - Reads aren't audited. Auth events are.
- **Missing `MEGOOFLAGS_PUBLIC_URL`:** passkeys and SSO are unavailable (their endpoints return 409 `public_url_required`) because they need a stable origin and RP ID.
- **Setup race:** exactly one `POST /setup` succeeds; the other returns 409 `already_set_up`.

## OWASP ASVS L2 baseline
This task covers:
- **Passwords:** argon2id, 12–128 characters, checked against a bundled common/breached password list, no composition rules.
- **Sign-in and MFA:** generic errors, throttling, TOTP and passkeys with recovery codes.
- **Sessions:** server-side sessions with rotation and idle/absolute timeouts, cookie flags, and the CSRF Origin check.
- **Authorization:** least privilege by default, with a test that every route declares a permission.
- **Stored secrets:** hashed or encrypted.
- **Logging and audit:** no secrets in logs, and an audit trail of security events.
- **Security headers:** Caddy and Next.js send CSP, `frame-ancestors 'none'`, `X-Content-Type-Options`, `Referrer-Policy` and HSTS when served over HTTPS.

A `docs/security.md` maps each ASVS L2 chapter to how it is met, or to why it doesn't apply.

## Testing
Tests are written first, as the repo does.
- **Route table tests:**
  - Every `/api` route either declares `require(...)` or is in the public allow-list.
  - Every mutating route appears in a table of sample requests, and running each one with an Admin token adds at least one audit entry with the right actor, IP and action. The test fails if a new mutating route is missing from the table.
- **RBAC matrix:** inheritance org → project → environment, team bindings, custom roles, environment-only bindings (for example `flag.toggle` on production only), filtered list endpoints, 404 for hidden projects, personal-token capping, service tokens, and the last-admin guard.
- **Auth:**
  - Setup, including a concurrent race test.
  - Sign-in and throttling.
  - Cookie flags with and without `X-Forwarded-Proto: https` from a trusted proxy.
  - Session expiry and rotation, and CSRF Origin rejection.
  - Invitations and password reset.
- **MFA:** TOTP (including the replay window), recovery codes, the `require_mfa` enrolment gate, and WebAuthn with a software authenticator fixture (registration, assertion, sign-count check).
- **SSO with Keycloak:** a real Keycloak in CI (`compose.ci.yml`, realm JSON with an OIDC client, a SAML client, users and groups). The tests drive the login form with httpx. They skip when `MEGOOFLAGS_KEYCLOAK_URL` is unset, the same pattern as Valkey. They cover:
  - OIDC and SAML end to end.
  - JIT provisioning.
  - Group → team and group → role mapping, including removal on the next sign-in.
  - Email-collision refusal.
- **SSO negative cases** (unit tests with locally signed tokens and assertions): a bad signature, wrong audience, replayed assertion, state or nonce mismatch, and an expired assertion.
- **Rotation:** SDK keys and service tokens. The old key works during the overlap and gets 401 afterwards; streams close; `overlap_seconds=0`.
- **Audit:** filters, cursor pagination, CSV and NDJSON export, scoping to project bindings, and secret redaction.
- **Other:** migration up and down (`check_migrations.sh`), the regenerated OpenAPI spec and TS client, and the web lint, typecheck and build. The deploy smoke test signs in through `/setup` and then uses a service token.

## Upgrade notes (CHANGELOG, breaking, minor bump)
- `/api/*` now requires authentication. Right after upgrading, open `/setup` to create the first admin, then create service tokens for scripts and CI.
- Set `MEGOOFLAGS_SECRET_KEY`; the API refuses to start without it.
- Remove the old "management API has no authentication" warning.

## Out of scope
- Change requests and approvals (G-4).
- SCIM provisioning, and email sending (invitations and resets are links an admin shares).
- An "SSO only" mode that disables password sign-in.
- Multiple organisations, IP allow-lists, and audit retention or pruning.
- Moving `flag.owner` to a user reference.
- Admin screens for users, teams, roles, tokens, SDK keys, SSO and the audit log (MF-82).
- IdP-initiated SAML and SAML single logout.
