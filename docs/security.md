# Security

MegooFlags targets the [OWASP ASVS 4.0.3](https://owasp.org/www-project-application-security-verification-standard/)
Level 2 baseline. This page maps each ASVS chapter to how the requirement is met, or to why it
doesn't apply, and ends with the notes an operator needs. Paths are relative to the repository
root; `api/…` files are under `api/src/megooflags_api/`.

## ASVS 4.0.3 L2 map

| Chapter | How it is met |
|---|---|
| **V1 Architecture** | One API owns every security decision; the web app holds no secrets and only calls it. Authentication and authorization live in one package, `api/auth/`. The threat surface (management API, SDK API, sign-in and SSO) is described in the MF-79 design. Dependencies are pinned in `api/uv.lock` and `web/pnpm-lock.yaml` and installed with `--frozen`. |
| **V2 Authentication** | Passwords are hashed with argon2id (`api/auth/passwords.py`), must be 12–128 characters, are checked against a bundled breached and common password list (`api/auth/data/common_passwords.txt`), and have no composition rules. Sign-in errors are generic. Failed sign-ins are throttled per account (10) and per IP (100) over 15 minutes, and MFA failures per user (10) (`api/auth/throttle.py`). The second factor is TOTP (`api/auth/mfa.py`: 30 s steps, ±1 step, a used step is never accepted again) or a passkey through WebAuthn (`api/auth/webauthn.py`, with a sign-count check). Ten single-use, hashed recovery codes back them up. Admins can require MFA for everyone with `require_mfa` (`PUT /api/settings`). Changing the password or a factor needs a factor verified in the last 10 minutes. SSO through OIDC (`api/auth/sso/oidc.py`: PKCE, state and nonce, signed ID tokens) and SAML 2.0 (`api/auth/sso/saml.py`: signed assertions, audience and time checks, replay IDs kept in Valkey). |
| **V3 Session management** | Sessions are server-side (`api/auth/sessions.py`): the cookie holds a random token and the database only its SHA-256. Each sign-in issues a new session, and its token is rotated when the second factor is verified, when MFA enrolment completes and on a password change. It ends after 12 hours idle or 7 days in total (`MEGOOFLAGS_SESSION_IDLE_HOURS`, `MEGOOFLAGS_SESSION_ABSOLUTE_DAYS`), at sign-out, and for every other session of the user after a password change. Cookies are `HttpOnly` and `SameSite=Lax`. Over HTTPS (also behind a trusted proxy that sends `X-Forwarded-Proto: https`) they are `Secure` and named with the `__Host-` prefix. Cookie-authenticated unsafe requests must pass the CSRF Origin check (`api/auth/csrf.py`). Users can list and end their sessions under `/api/account/sessions`. |
| **V4 Access control** | Deny by default: every `/api` route declares `require(permission)` (`api/auth/require.py`), or is on a short public allow-list (setup, sign-in, SSO callbacks). `api/tests/test_route_permissions.py` fails if a route does neither. Permissions come from roles bound at org, project or environment scope (`api/auth/rbac.py`); hidden projects and environments return 404. Personal tokens are capped by their owner's permissions, `/account/*` refuses tokens, and the last org admin can't be removed (by an unbind, a team change, SSO group sync or deleting an SSO provider). Nobody grants more than they hold: a role binding, a new or rotated service token, a team membership and a permission added to a role need the caller to hold each permission granted, at that scope (`not_held` in `api/auth/rbac.py`). The RBAC matrix is tested in `api/tests/test_rbac.py` and `api/tests/test_rbac_routes.py`. |
| **V5 Validation, sanitization and encoding** | Every request body and query is validated by Pydantic schemas (`api/schemas/`), and errors use one format (`api/errors.py`). Database access goes through SQLAlchemy with bound parameters. Regular expressions in targeting clauses are restricted to a portable subset. React escapes output in the web app. Redirect targets after sign-in and SSO are validated relative paths, with no backslash or control character. A SAML metadata URL is fetched once, when the provider is saved: at most 1 MB, and refused unless it is IdP metadata, without echoing the body. CSV export prefixes cells starting with `=`, `+`, `-`, `@`, tab or CR with `'`. |
| **V6 Stored cryptography** | Passwords use argon2id. API tokens, SDK keys, session tokens, invitation links and recovery codes are random and stored only as SHA-256 hashes (`api/services/sdk_keys.py`, `api/services/api_tokens.py`). Secrets that must be read back (TOTP seeds, SSO client secrets, SAML private keys) are encrypted with Fernet, with the key derived from `MEGOOFLAGS_SECRET_KEY` by HKDF-SHA256 (`api/crypto.py`). Randomness comes from `secrets`. |
| **V7 Error handling and logging** | Every change writes an audit entry in the same transaction (`api/services/audit.py`): who, what, when, the client IP from trusted forwarded headers, and a before/after diff with secret fields replaced by `***`. Sign-in and other auth events are audited too. The audit log is filterable and exportable as CSV or NDJSON (`/api/audit`, `/api/audit/export`). The access log (`api/logging.py`) records only the method, the path and the status: never the `Authorization` header, cookies or `?key=` (`api/tests/test_logging.py`). Unhandled errors return a generic 500. |
| **V8 Data protection** | Secrets are shown once, at creation or rotation. Exposure events don't store the evaluation context unless `MEGOOFLAGS_PERSIST_CONTEXT=true`, and are deleted after `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS`. Client SDK keys only get flag values, never rulesets. |
| **V9 Communication** | TLS is terminated by Caddy (`deploy/Caddyfile`), automatically with Let's Encrypt when `MEGOOFLAGS_SITE_ADDRESS` is a domain, or by the operator's own proxy. HTTP redirects to HTTPS, and HSTS is sent over HTTPS. Forwarded headers are trusted only from configured proxies (`MEGOOFLAGS_TRUSTED_PROXIES`, `FORWARDED_ALLOW_IPS`). |
| **V10 Malicious code** | No dynamic code execution or plugins. Dependencies are locked and installed with `--frozen` in CI and the images. |
| **V11 Business logic** | First-run setup is atomic (an advisory lock plus a "no user exists" check), so exactly one admin is created. Concurrent writes follow a fixed lock order. Sign-in, MFA and client SDK requests are rate-limited across replicas through Valkey. |
| **V12 Files and resources** | Doesn't apply: there are no file uploads or downloads from user-supplied paths. The audit export is generated by the API. |
| **V13 API and web services** | The API is JSON-only and documented in `api/openapi.json`. Callers authenticate with a session cookie (plus the CSRF Origin check) or an `mf_api_` bearer token scoped by project, environment and permission. SDK endpoints use separate server and client keys (`spec/delivery.md`). |
| **V14 Configuration** | Caddy (`deploy/Caddyfile`) and Next.js (`web/next.config.ts`) send `Content-Security-Policy` (with `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin` and `X-Frame-Options: DENY`; Caddy removes the `Server` header and adds `Strict-Transport-Security` over HTTPS. `scripts/smoke.sh` checks them. `/metrics` isn't exposed through Caddy. The API refuses to start without `MEGOOFLAGS_SECRET_KEY`. |

## Operator notes

- **Secret key.** Set `MEGOOFLAGS_SECRET_KEY` to at least 32 characters, for example
  `openssl rand -hex 32`. The API refuses to start without it. Keep it stable and backed up:
  changing it makes the stored TOTP seeds, SSO client secrets and SAML keys unreadable.
- **Public URL.** Set `MEGOOFLAGS_PUBLIC_URL` (for example `https://flags.example.com`). Passkeys
  and SSO need a stable origin and return 409 `public_url_required` without it. It is also the
  origin the CSRF check accepts.
- **Finish setup right after upgrading.** Until the first admin exists, `/setup` is open to anyone
  who can reach the instance, and the API logs a warning at startup and on every `/readyz`. Open
  `/setup` as soon as the upgrade is deployed, then create service tokens (`POST /api/tokens`) for
  scripts and CI.
- **Rotating keys with overlap.** Rotate an SDK key with
  `POST /api/projects/{project}/environments/{env}/sdk-keys/{id}/rotate` and a service token with
  `POST /api/tokens/{id}/rotate`. Both return the new secret and keep the old one working for
  `overlap_seconds` (default 86400, at most 30 days). Deploy the new secret within that window; afterwards the old one is refused
  and open streams that use it are closed. Use `overlap_seconds=0` to revoke at once, for example
  after a leak.
