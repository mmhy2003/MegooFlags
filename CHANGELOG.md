# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows the versioning rules in [AGENTS.md](AGENTS.md).

## [Unreleased]

### Changed
- **Breaking / upgrade steps:**
  - `/api/*` now requires authentication. Open `/setup` right after upgrading to create the first admin (until then anyone who can reach the instance can), then create service tokens (`POST /api/tokens`) for scripts and CI.
  - Set `MEGOOFLAGS_SECRET_KEY` (32+ characters, e.g. `openssl rand -hex 32`). The API refuses to start without it.
- **Upgrade step:** the API now requires Valkey. The bundled `docker-compose.yml` runs a `valkey` service; if you run the API elsewhere, set `MEGOOFLAGS_VALKEY_URL`, because the API refuses to start without it.
- **Upgrade step:** `MEGOOFLAGS_SITE_ADDRESS` now defaults to empty, which means plain HTTP on port 80 only. Caddy publishes port 443 only when it is set to a domain. If your `.env` has `MEGOOFLAGS_SITE_ADDRESS=:80`, change it to `MEGOOFLAGS_SITE_ADDRESS=`, otherwise port 443 is still published on the host.
- Each `api` container runs several uvicorn worker processes: one per CPU, at most 8, or `MEGOOFLAGS_API_WORKERS`. Each worker adds memory; set it explicitly when the container has a CPU limit. `/metrics` reports the sum over all workers.
- The API trusts `X-Forwarded-*` headers only from private networks (configurable with `FORWARDED_ALLOW_IPS`), not from any address.
- API logs are JSON lines on stdout, with one access-log line per request. Set `MEGOOFLAGS_LOG_LEVEL` to change the level.

### Added
- `GET /api/projects/{project}/flags` accepts `env` and `enabled` to list only flags that are on or off in an environment.
- Web UI app shell: a sidebar with a project switcher and a menu that follows your permissions (it collapses to icons and becomes a drawer on phones), and a header with ⌘K/Ctrl+K search, a light/dark/system theme switcher and an account menu. It uses the cyberpunk neon palette, works from 280 px wide, and warns when MegooFlags is opened over plain HTTP. Flags, Segments, Environments, Change requests, Audit log, API & SDK keys, Integrations and Settings are placeholder pages for now.
- Your theme and sidebar choices are saved on your account (`PATCH /api/account/preferences`) and returned by `/api/auth/me`, so they follow you to other devices.
- `GET /api/auth/me/permissions?project=<key>` lists what you may do org-wide, in a project and in each of its environments.
- Sign-in with a password, plus a second factor: TOTP, passkeys (WebAuthn) or single-use recovery codes. Admins can require MFA for everyone with `require_mfa` (`PUT /api/settings`). Web pages `/setup`, `/sign-in`, `/sign-in/mfa`, `/invite/<token>` and `/account/security`.
- Single sign-on through OIDC and SAML 2.0 (tested with Keycloak), with just-in-time provisioning and mapping of identity-provider groups to teams and roles. Just-in-time provisioning over OIDC needs `email_verified: true` in the ID token or userinfo; an identity provider that does not send it by default, such as Microsoft Entra ID, needs a claim mapping that adds it, or SAML. Deleting a provider also removes the team memberships and role bindings its group sync granted.
- Users, invitations, teams, custom roles and role bindings at org, project or environment scope, alongside the built-in Admin, Editor and Viewer roles.
- API tokens: service tokens (`/api/tokens`) and personal tokens (`/api/account/tokens`), scoped by project, environment and permission.
- Rotation with an overlap window for SDK keys and service tokens: the old secret keeps working for `overlap_seconds`.
- An audit log of every change and sign-in (who, what, when, client IP and a before/after diff), with filters and CSV or NDJSON export under `/api/audit`.
- Security headers (CSP, `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, and HSTS over HTTPS) from Caddy and the web app. `docs/security.md` maps the OWASP ASVS L2 chapters to how they are met.
- `MEGOOFLAGS_PUBLIC_URL` is required for passkeys and SSO; without it their endpoints return 409 `public_url_required`.
- Prometheus metrics on `:8000/metrics` in every `api` container, reachable only inside the compose network. `.env.example` shows a scrape config.
- OpenTelemetry traces for API requests and database queries, enabled by setting `OTEL_EXPORTER_OTLP_ENDPOINT`.
- `MEGOOFLAGS_PUBLIC_URL` sets the external URL. When it is empty, the URL is derived from the request.
- Caddy load-balances across API replicas started with `docker compose up -d --scale api=N`.
- Management REST API under `/api`: projects, environments, flags (boolean, string, number, JSON) with variations, tags, owner, archive/restore/delete, reusable segments, and per-environment targeting (allow/deny lists, rules with operators, percentage rollouts with a bucketing attribute, prerequisite flags). The OpenAPI spec is published in `api/openapi.json`.
- Per-environment toggle, kill switch, and copying a flag's config between environments.
- `POST /api/projects/{project}/environments/{env}/evaluate` evaluates flags for a context with the new reference evaluation engine. The evaluation spec, ruleset format and SDK conformance suite are published in `spec/`. Regular expressions in `matches` clauses must now use the portable RE2/Python subset: lookaround, backreferences, atomic groups, possessive quantifiers and conditionals are refused.
- SDK keys: per-environment server and client keys, created, listed and revoked under `/api/projects/{project}/environments/{env}/sdk-keys`. The secret is shown only once.
- SDK delivery API under `/sdk/v1`: ruleset download for server keys with `ETag` and `If-None-Match`, remote evaluation that gives client keys values only (client keys are rate-limited), server-sent events streaming with a heartbeat and a polling fallback, and flag changes, the kill switch included, reaching every API replica within seconds.
- **Known gap:** remote evaluation does not yet meet its p99 target of 50 ms at 2k requests per second per container. A local run with 8 workers and the load generator on the same host measured p50 11 ms and p99 266–505 ms, whether the project had 10 or 10,000 flags. The remaining tail will be profiled in a follow-up task.
- OpenFeature Remote Evaluation Protocol (OFREP) under `/ofrep/v1`, with server or client keys.
- Exposure events and evaluation counts through `POST /sdk/v1/events`. The evaluation context is stored only with `MEGOOFLAGS_PERSIST_CONTEXT=true`, and exposures are deleted after `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS` (default 30).
- Settings `MEGOOFLAGS_VALKEY_URL`, `MEGOOFLAGS_PERSIST_CONTEXT`, `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS`, `MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE`, `MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`, `MEGOOFLAGS_STREAM_RECHECK_SECONDS` and `MEGOOFLAGS_API_WORKERS`, documented in `deploy/.env.example`.
- `spec/delivery.md`, the delivery spec for SDK authors: keys, endpoints, status codes, ETags and polling, streaming, events, CORS and OFREP.
- Change requests: an environment can require 1 to 10 approvals (`approvals_required` on the environment). Config edits, toggles, copies into it, new scheduled changes and new progressive rollouts there then return 409 `approval_required` and go through a change request (`/api/projects/{project}/change-requests`), with comments, approve and reject, and the change applied by the last approval. Authors can't review their own request, and a request fails instead of applying if the flag changed in the meantime. The kill switch, rollout pause, resume and abort, and cancelling a scheduled change stay direct. Reviewing needs the new `change.approve` permission, added to the built-in Admin and Editor roles; **add it to custom roles** whose members should review.
- Scheduled changes: enable or disable a flag, or set its fallthrough rollout, at a set time up to a year ahead (`…/environments/{env}/schedules`). Changes missed while the API was down run as soon as it is back.
- Progressive rollouts: move the fallthrough from one variation to another in timed steps, with pause, resume and abort (`…/environments/{env}/rollout`). The kill switch pauses a running rollout, and editing the fallthrough by hand stops it.
- Flag lifecycle (`new`, `active`, `permanent`, `stale`, `archived`) and stale detection from SDK evaluation counts: a flag no environment has evaluated for the project's `stale_after_days` (default 30) becomes `stale`. Flags gain `lifecycle` and `last_evaluated_at`, the flag list a `lifecycle` filter, and `PUT …/flags/{flag}/lifecycle` marks a flag permanent.
- A background scheduler that runs this timed work on one API process at a time, chosen with a Postgres advisory lock, with the settings `MEGOOFLAGS_SCHEDULER_ENABLED` and `MEGOOFLAGS_SCHEDULER_INTERVAL_SECONDS` and the metrics `megooflags_scheduler_leader` and `megooflags_scheduler_jobs_total`. See `docs/release-workflows.md`.

## [0.1.0]

### Added
- Monorepo scaffold: FastAPI `api/` and Next.js `web/` with a shared `VERSION`.
- `/healthz` and `/readyz` endpoints.
- Typed web API client generated from the OpenAPI spec.
- Docker images `megooflags-api` and `megooflags-web`, reference Docker Compose bundle with Caddy.
- CI (lint, types, tests, migrations, build, images, smoke) and tag-driven release pipeline.
- AGENTS.md and CLAUDE.md with versioning, migration and release rules for coding agents.
- Reserved sdks/ and spec/ layout for in-repo SDKs.

### Changed

### Fixed

### Removed
