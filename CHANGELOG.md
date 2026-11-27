# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows the versioning rules in [AGENTS.md](AGENTS.md).

## [Unreleased]

## [1.0.0] - 2026-11-27

### Changed
- Each `api` container runs several uvicorn worker processes: one per CPU, at most 8, or `MEGOOFLAGS_API_WORKERS`. Each worker adds memory; set it explicitly when the container has a CPU limit. `/metrics` reports the sum over all workers.
- The API trusts `X-Forwarded-*` headers only from private networks (configurable with `FORWARDED_ALLOW_IPS`), not from any address.
- API logs are JSON lines on stdout, with one access-log line per request. Set `MEGOOFLAGS_LOG_LEVEL` to change the level.
- The guides moved from `docs/` to the docs site at https://mmhy2003.github.io/MegooFlags/.
- A new link for someone who never joined is an invitation again, not a password reset.
- Remote evaluation is faster: at 2000 requests per second on one API container with 8 workers, OFREP answers with p99 20.4 ms and `/sdk/v1/evaluate` with p99 35.8 ms, both without errors (before: 266.1 ms, and 29.6 s with errors at only 1331 requests per second).
- Flag changes reach SDKs across API replicas faster: p95 1.58 s over three replicas (before: 5.31 s).

### Added
- `/api/*` requires authentication. Open `/setup` right after the first start to create the first admin (until then anyone who can reach the instance can), then create service tokens (`POST /api/tokens`) for scripts and CI.
- `MEGOOFLAGS_SECRET_KEY` is required (32+ characters, e.g. `openssl rand -hex 32`). The API refuses to start without it.
- The API requires Valkey. The bundled `docker-compose.yml` runs a `valkey` service; if you run the API elsewhere, set `MEGOOFLAGS_VALKEY_URL`, because the API refuses to start without it.
- `MEGOOFLAGS_SITE_ADDRESS` defaults to empty, which means plain HTTP on port 80 only. Caddy publishes port 443 only when it is set to a domain.
- docker-compose.control-plane.yml for the control plane of a multi-node deployment.
- docker-compose.worker.yml, Caddyfile.worker and .env.worker.example to run a worker node.
- `MEGOOFLAGS_ROLE` (`standalone`, `control-plane`, `worker`): workers serve `/sdk/*` and `/ofrep/*` from a signed snapshot on disk and keep serving while the control plane is down.
- Nodes API (`/api/nodes`) to add, drain, resume, rotate, revoke and remove worker nodes on a control plane.
- Nodes page for control planes: add workers, see health and snapshot age, drain, rotate, revoke and remove.
- Docs for multi-node deployments and a migration guide from a single node.
- Performance and sizing page with the load test report against each performance target, and sizing guidance from the measured numbers; see https://mmhy2003.github.io/MegooFlags/deploy/performance/.
- `megooflags` CLI 0.1.0 Beta: projects, environments, segments and flags (create, update, archive, restore, delete, variations, targeting, on, off, kill), `promote` between environments (with change requests where approvals are required), `evaluate`, and `ruleset pull` for SDK bootstrap files and offline local development. Table, JSON or YAML output and stable exit codes for scripts. Released with `cli/vX.Y.Z` tags as archives for Linux, macOS and Windows with `SHA256SUMS`; see https://mmhy2003.github.io/MegooFlags/use/cli/.
- Flags as code: `megooflags export`, `diff` and `apply` keep a project in a directory of YAML files, with drift detection (`diff` exits 3) and example GitHub Actions workflows for two-way Git sync in `docs/examples/git-sync/`. `apply` never bypasses approvals (exit 4, or `--change-requests`) and never deletes without `--prune`; see https://mmhy2003.github.io/MegooFlags/use/flags-as-code/.
- Importers from Unleash, Flagsmith and LaunchDarkly: `megooflags import <tool>` reads the tool's API or a saved file and writes flags-as-code files plus `import-report.md` listing everything not translated as it was; see https://mmhy2003.github.io/MegooFlags/migrate/overview/.
- Terraform provider 0.1.0 Beta with the resources `megooflags_project`, `megooflags_flag`, `megooflags_flag_environment`, `megooflags_segment` and `megooflags_sdk_key` and two data sources. It is not in the Terraform Registry: install it from the `terraform/vX.Y.Z` GitHub release files through a filesystem mirror, as `terraform/README.md` shows.
- Webhooks, Slack and Microsoft Teams notifications per project (Integrations page, `project.manage`), with the settings `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE`, `MEGOOFLAGS_WEBHOOK_TIMEOUT_SECONDS` and `MEGOOFLAGS_WEBHOOK_RETENTION_DAYS`. The payload, signature check and retries are in https://mmhy2003.github.io/MegooFlags/use/integrations/.
- Helm chart for Kubernetes (`oci://ghcr.io/mmhy2003/charts/megooflags`): a single node, a control plane, or worker nodes (one node token per replica), with Ingress or Gateway API routing and bundled or external PostgreSQL and Valkey; see https://mmhy2003.github.io/MegooFlags/deploy/kubernetes/.
- `GET /api/projects/{project}/environments/{env}/ruleset`: API tokens and users with `project.read` on the environment can download the same ruleset (and `ETag`) that `/sdk/v1/ruleset` serves, for SDK bootstrap files.
- Go server SDK (`github.com/mmhy2003/MegooFlags/sdks/go`, 0.1.0 Beta): an OpenFeature provider that evaluates flags locally, follows changes over SSE with a polling fallback, works offline from a bootstrap file or its last-known cache, and sends evaluation counts and exposures in the background. Released with `sdks/go/vX.Y.Z` tags.
- Node.js server SDK (npm `@megooflags/node`, 0.1.0 Beta, Node.js 22 or later): an OpenFeature provider for `@openfeature/server-sdk` that evaluates flags locally, follows changes over SSE with a polling fallback, works offline from a bootstrap file or its last-known cache, and sends evaluation counts and exposures in the background. Released with `sdk-node/vX.Y.Z` tags.
- Python server SDK (PyPI `megooflags`, 0.1.0 Beta, Python 3.11 or later): an OpenFeature provider for `openfeature-sdk` that evaluates flags locally, follows changes over SSE with a polling fallback, works offline from a bootstrap file or its last-known cache, and sends evaluation counts and exposures in the background, also in forking servers such as gunicorn. Released with `sdk-python/vX.Y.Z` tags.
- Java server SDK (Maven `io.github.mmhy2003:megooflags`, 0.1.0 Beta, Java 11 or later): an OpenFeature provider for `dev.openfeature:sdk` that evaluates flags locally, follows changes over SSE with a polling fallback, works offline from a bootstrap file or its last-known cache, and sends evaluation counts and exposures in the background. Released with `sdk-java/vX.Y.Z` tags.
- .NET server SDK (NuGet `MegooFlags`, 0.1.0 Beta, .NET 8 or later): an OpenFeature provider for the `OpenFeature` .NET SDK that evaluates flags locally, follows changes over SSE with a polling fallback, works offline from a bootstrap file or its last-known cache, and sends evaluation counts and exposures in the background. Released with `sdk-dotnet/vX.Y.Z` tags.
- Ruby server SDK (gem `megooflags`, 0.1.0 Beta, Ruby 3.4 or later): an OpenFeature provider for `openfeature-sdk` that evaluates flags locally, follows changes over SSE with a polling fallback, works offline from a bootstrap file or its last-known cache, and sends evaluation counts and exposures in the background, also in forking servers such as Puma, Unicorn and Passenger. Released with `sdk-ruby/vX.Y.Z` tags.
- PHP server SDK (Composer `megooflags/megooflags`, 0.1.0 Beta, PHP 8.3 or later): an OpenFeature provider for `open-feature/sdk` that evaluates flags locally from a ruleset cache shared by all requests, works offline from a bootstrap file or that cache, and sends evaluation counts and exposures in batches; an optional sync daemon (`megooflags-sync`) follows changes over SSE with a polling fallback, and without it requests refresh the cache after the response was sent. Released with `sdk-php/vX.Y.Z` tags.
- JavaScript client SDK (npm `@megooflags/js`, 0.1.0 Beta, evergreen browsers): an OpenFeature web provider for `@openfeature/web-sdk` that evaluates flags remotely with a client key, so no targeting rule reaches the browser, follows changes over SSE with a polling fallback, keeps the last values in `localStorage` for offline starts, accepts bootstrap values and sends exposures in batches. Released with `sdk-js/vX.Y.Z` tags.
- React client SDK (npm `@megooflags/react`, 0.1.0 Beta, React 18 or later): a `<MegooFlagsProvider>` component that registers the JavaScript provider for its subtree, plus the OpenFeature React hooks (`useBooleanFlagValue` and the others) re-exported from `@openfeature/react-sdk`. Released with `sdk-react/vX.Y.Z` tags.
- React Native client SDK (npm `@megooflags/react-native`, 0.1.0 Beta, React Native 0.76 or later): the same component and hooks for React Native, following changes over SSE with a polling fallback, keeping the last values in AsyncStorage when `@react-native-async-storage/async-storage` is installed (it is optional), and sending pending exposures when the app goes to the background. Released with `sdk-react-native/vX.Y.Z` tags.
- iOS client SDK (SwiftPM `MegooFlags` from `mmhy2003/megooflags-swift`, 0.1.0 Beta, iOS 15 or macOS 12 and Swift 6.1): an OpenFeature provider for the OpenFeature Swift SDK that evaluates flags remotely with a client key, follows changes over SSE with a polling fallback, keeps the last values on the device for offline starts, accepts bootstrap values and sends exposures in batches. Released with `sdk-ios/vX.Y.Z` tags; there is no CocoaPods pod.
- Android client SDK (Maven `io.github.mmhy2003:megooflags-android`, 0.1.0 Beta, Android API 21 or later): an OpenFeature provider for the OpenFeature Kotlin SDK that evaluates flags remotely with a client key, follows changes over SSE with a polling fallback, keeps the last values on the device for offline starts, accepts bootstrap values and sends exposures in batches. Released with `sdk-android/vX.Y.Z` tags.
- Flutter client SDK (pub.dev `megooflags`, 0.1.0 Beta, Flutter 3.38 or later): an OpenFeature provider for the OpenFeature Dart SDK that evaluates flags remotely with a client key, follows changes over SSE with a polling fallback, keeps the last values in `shared_preferences` for offline starts, accepts bootstrap values and sends exposures in batches. Released with `sdk-flutter/vX.Y.Z` tags.
- `GET /api/projects/{project}/flags` accepts `env` and `enabled` to list only flags that are on or off in an environment.
- `GET /api/audit` and `/api/audit/export` accept `resource_key` to show the history of one flag or other resource.
- `GET /api/projects/{project}/flags/{flag}/environments/{env}/evaluations?range=24h|7d|30d` returns a flag's evaluation counts per variation, hourly or daily.
- Web UI app shell: a sidebar with a project switcher and a menu that follows your permissions (it collapses to icons and becomes a drawer on phones), and a header with ⌘K/Ctrl+K search, a light/dark/system theme switcher and an account menu. It uses the cyberpunk neon palette, works from 280 px wide, and warns when MegooFlags is opened over plain HTTP.
- Web UI flags pages: a searchable, filterable flag list with bulk on/off, tag and archive actions; a New flag form; and a flag page with an environment strip (toggle, kill switch with confirmation), a targeting editor (prerequisites, individual targets, rules with segments, percentage rollouts) that saves after a reviewed diff or opens a change request where approval is needed, copy to other environments, Test this flag, change history with diffs, an evaluation chart, and settings for details, variations, archive, restore and delete. It works on phones from 280 px.
- Your theme and sidebar choices are saved on your account (`PATCH /api/account/preferences`) and returned by `/api/auth/me`, so they follow you to other devices.
- `GET /api/auth/me/permissions?project=<key>` lists what you may do org-wide, in a project and in each of its environments.
- Sign-in with a password, plus a second factor: TOTP, passkeys (WebAuthn) or single-use recovery codes. Admins can require MFA for everyone with `require_mfa` (`PUT /api/settings`). Web pages `/setup`, `/sign-in`, `/sign-in/mfa`, `/invite/<token>` and `/account/security`.
- Single sign-on through OIDC and SAML 2.0 (tested with Keycloak), with just-in-time provisioning and mapping of identity-provider groups to teams and roles. Just-in-time provisioning over OIDC needs `email_verified: true` in the ID token or userinfo; an identity provider that does not send it by default, such as Microsoft Entra ID, needs a claim mapping that adds it, or SAML. Deleting a provider also removes the team memberships and role bindings its group sync granted.
- Users, invitations, teams, custom roles and role bindings at org, project or environment scope, alongside the built-in Admin, Editor and Viewer roles.
- API tokens: service tokens (`/api/tokens`) and personal tokens (`/api/account/tokens`), scoped by project, environment and permission.
- Rotation with an overlap window for SDK keys and service tokens: the old secret keeps working for `overlap_seconds`.
- An audit log of every change and sign-in (who, what, when, client IP and a before/after diff), with filters and CSV or NDJSON export under `/api/audit`.
- Security headers (CSP, `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`, and HSTS over HTTPS) from Caddy and the web app. The security guide (https://mmhy2003.github.io/MegooFlags/deploy/security/) maps the OWASP ASVS L2 chapters to how they are met.
- `MEGOOFLAGS_PUBLIC_URL` is required for passkeys and SSO; without it their endpoints return 409 `public_url_required`.
- Prometheus metrics on `:8000/metrics` in every `api` container, reachable only inside the compose network. `.env.example` shows a scrape config.
- OpenTelemetry traces for API requests and database queries, enabled by setting `OTEL_EXPORTER_OTLP_ENDPOINT`.
- `MEGOOFLAGS_PUBLIC_URL` sets the external URL. When it is empty, the URL is derived from the request.
- Caddy load-balances across API replicas started with `docker compose up -d --scale api=N`.
- Management REST API under `/api`: projects, environments, flags (boolean, string, number, JSON) with variations, tags, owner, archive/restore/delete, reusable segments, and per-environment targeting (allow/deny lists, rules with operators, percentage rollouts with a bucketing attribute, prerequisite flags). The OpenAPI spec is published in `api/openapi.json`.
- Per-environment toggle, kill switch, and copying a flag's config between environments.
- `POST /api/projects/{project}/environments/{env}/evaluate` evaluates flags for a context with the reference evaluation engine. The evaluation spec, ruleset format and SDK conformance suite are published in `spec/`. Regular expressions in `matches` clauses must use the portable RE2/Python subset: lookaround, backreferences, atomic groups, possessive quantifiers and conditionals are refused.
- SDK keys: per-environment server and client keys, created, listed and revoked under `/api/projects/{project}/environments/{env}/sdk-keys`. The secret is shown only once.
- SDK delivery API under `/sdk/v1`: ruleset download for server keys with `ETag` and `If-None-Match`, remote evaluation that gives client keys values only (client keys are rate-limited), server-sent events streaming with a heartbeat and a polling fallback, and flag changes, the kill switch included, reaching every API replica within seconds.
- Remote evaluation meets its target of p99 under 50 ms at 2000 requests per second per container: OFREP p99 20.4 ms and `/sdk/v1/evaluate` p99 35.8 ms with 8 workers; see https://mmhy2003.github.io/MegooFlags/deploy/performance/.
- OpenFeature Remote Evaluation Protocol (OFREP) under `/ofrep/v1`, with server or client keys.
- Exposure events and evaluation counts through `POST /sdk/v1/events`. The evaluation context is stored only with `MEGOOFLAGS_PERSIST_CONTEXT=true`, and exposures are deleted after `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS` (default 30).
- Settings `MEGOOFLAGS_VALKEY_URL`, `MEGOOFLAGS_PERSIST_CONTEXT`, `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS`, `MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE`, `MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`, `MEGOOFLAGS_STREAM_RECHECK_SECONDS` and `MEGOOFLAGS_API_WORKERS`, documented in `deploy/.env.example`.
- `spec/delivery.md`, the delivery spec for SDK authors: keys, endpoints, status codes, ETags and polling, streaming, events, CORS and OFREP.
- Change requests: an environment can require 1 to 10 approvals (`approvals_required` on the environment). Config edits, toggles, copies into it, new scheduled changes and new progressive rollouts there then return 409 `approval_required` and go through a change request (`/api/projects/{project}/change-requests`), with comments, approve and reject, and the change applied by the last approval. Authors can't review their own request, and a request fails instead of applying if the flag changed in the meantime. The kill switch, rollout pause, resume and abort, and cancelling a scheduled change stay direct. Reviewing needs the new `change.approve` permission, added to the built-in Admin and Editor roles; **add it to custom roles** whose members should review.
- Scheduled changes: enable or disable a flag, or set its fallthrough rollout, at a set time up to a year ahead (`…/environments/{env}/schedules`). Changes missed while the API was down run as soon as it is back.
- Progressive rollouts: move the fallthrough from one variation to another in timed steps, with pause, resume and abort (`…/environments/{env}/rollout`). The kill switch pauses a running rollout, and editing the fallthrough by hand stops it.
- Flag lifecycle (`new`, `active`, `permanent`, `stale`, `archived`) and stale detection from SDK evaluation counts: a flag no environment has evaluated for the project's `stale_after_days` (default 30) becomes `stale`. Flags gain `lifecycle` and `last_evaluated_at`, the flag list a `lifecycle` filter, and `PUT …/flags/{flag}/lifecycle` marks a flag permanent.
- A background scheduler that runs this timed work on one API process at a time, chosen with a Postgres advisory lock, with the settings `MEGOOFLAGS_SCHEDULER_ENABLED` and `MEGOOFLAGS_SCHEDULER_INTERVAL_SECONDS` and the metrics `megooflags_scheduler_leader` and `megooflags_scheduler_jobs_total`. See https://mmhy2003.github.io/MegooFlags/use/release-workflows/.
- Update notices: about once a day the API asks the public GitHub Releases API for the newest MegooFlags release. Nothing about the instance is sent. Set `MEGOOFLAGS_UPDATE_CHECK=false` to turn it off.
- `GET /api/system/version`: the running version and, for org admins, a newer release when one exists.
- Reverse proxy samples for nginx, Caddy, Traefik and HAProxy in `docs/examples/proxies/`, tested in CI with an SDK stream held through each, and a guide to running behind a proxy, Cloudflare, an AWS load balancer or Coolify (https://mmhy2003.github.io/MegooFlags/deploy/reverse-proxy/).
- Change requests returned by the API say whether you may approve (`can_approve`) or cancel (`can_cancel`) them.
- `GET /api/tokens?project=<key>` lists the service tokens that can act in a project. The list is now newest first.
- `GET /api/permissions` lists every permission with the level (organization, project, environment) where it works.
- SSO providers returned by the API include the redirect URI (OIDC) or the ACS URL, entity ID and metadata URL (SAML) to enter at the identity provider.
- Users returned by the API show when their unused invitation link expires (`invitation_expires_at`).
- Change requests page: review a change with its diff, comment, approve, reject or cancel, also on a phone.
- Audit log page with filters, before/after details and CSV or NDJSON export, plus an organization audit log in Settings.
- API & SDK keys page: create, rotate (with an overlap) and revoke SDK keys, and manage the service tokens that can act in the project.
- API tokens page for personal tokens limited to a project or environment.
- Settings: members and invitations, teams, custom roles, service tokens, SSO providers with set-up URLs and group mappings, and the require two-factor setting.
- `GET /api/projects/{project}/segments/{segment}/usage` lists the flags and environments whose rules use a segment.
- Segment list items have `flag_count`: how many flags use the segment in the environments you can see.
- `PUT /api/projects/{project}/environment-order` reorders all environments of a project in one call.
- Segments page: create segments, edit included and excluded keys and rules, see which flags use a segment; deleting is blocked while flags use it.
- Environments page: create, edit, reorder and delete environments, and require approvals (protected environments).
- Project settings: name, description and stale-flag days; create a project from the project switcher and delete one with its key typed.
- Automation tab on the flag page: schedule turning a flag on or off or a new default rollout, and start, pause, resume or abort a progressive rollout. In an environment that needs approval these open change requests.

## [0.1.0]

Never released: no `v0.1.0` tag, image or GitHub release exists. Its items ship in 1.0.0.

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
