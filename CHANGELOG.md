# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows the versioning rules in [AGENTS.md](AGENTS.md).

## [Unreleased]

### Changed
- **Upgrade step:** the API now requires Valkey. The bundled `docker-compose.yml` runs a `valkey` service; if you run the API elsewhere, set `MEGOOFLAGS_VALKEY_URL`, because the API refuses to start without it.
- **Security:** the management API has no authentication in this release. Do not expose `/api/*` to untrusted networks until authentication ships.
- **Upgrade step:** `MEGOOFLAGS_SITE_ADDRESS` now defaults to empty, which means plain HTTP on port 80 only. Caddy publishes port 443 only when it is set to a domain. If your `.env` has `MEGOOFLAGS_SITE_ADDRESS=:80`, change it to `MEGOOFLAGS_SITE_ADDRESS=`, otherwise port 443 is still published on the host.
- Each `api` container runs several uvicorn worker processes: one per CPU, at most 8, or `MEGOOFLAGS_API_WORKERS`. Each worker adds memory; set it explicitly when the container has a CPU limit. `/metrics` reports the sum over all workers.
- The API trusts `X-Forwarded-*` headers only from private networks (configurable with `FORWARDED_ALLOW_IPS`), not from any address.
- API logs are JSON lines on stdout, with one access-log line per request. Set `MEGOOFLAGS_LOG_LEVEL` to change the level.

### Added
- Prometheus metrics on `:8000/metrics` in every `api` container, reachable only inside the compose network. `.env.example` shows a scrape config.
- OpenTelemetry traces for API requests and database queries, enabled by setting `OTEL_EXPORTER_OTLP_ENDPOINT`.
- `MEGOOFLAGS_PUBLIC_URL` sets the external URL. When it is empty, the URL is derived from the request.
- Caddy load-balances across API replicas started with `docker compose up -d --scale api=N`.
- Management REST API under `/api`: projects, environments, flags (boolean, string, number, JSON) with variations, tags, owner, archive/restore/delete, reusable segments, and per-environment targeting (allow/deny lists, rules with operators, percentage rollouts with a bucketing attribute, prerequisite flags). The OpenAPI spec is published in `api/openapi.json`.
- Per-environment toggle, kill switch, and copying a flag's config between environments.
- `POST /api/projects/{project}/environments/{env}/evaluate` evaluates flags for a context with the new reference evaluation engine. The evaluation spec, ruleset format and SDK conformance suite are published in `spec/`. Regular expressions in `matches` clauses must now use the portable RE2/Python subset: lookaround, backreferences, atomic groups, possessive quantifiers and conditionals are refused.
- SDK keys: per-environment server and client keys, created, listed and revoked under `/api/projects/{project}/environments/{env}/sdk-keys`. The secret is shown only once.
- SDK delivery API under `/sdk/v1`: ruleset download for server keys with `ETag` and `If-None-Match`, remote evaluation that gives client keys values only (client keys are rate-limited), server-sent events streaming with a heartbeat and a polling fallback, and flag changes, the kill switch included, reaching every API replica within seconds.
- OpenFeature Remote Evaluation Protocol (OFREP) under `/ofrep/v1`, with server or client keys.
- Exposure events and evaluation counts through `POST /sdk/v1/events`. The evaluation context is stored only with `MEGOOFLAGS_PERSIST_CONTEXT=true`, and exposures are deleted after `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS` (default 30).
- Settings `MEGOOFLAGS_VALKEY_URL`, `MEGOOFLAGS_PERSIST_CONTEXT`, `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS`, `MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE`, `MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`, `MEGOOFLAGS_STREAM_RECHECK_SECONDS` and `MEGOOFLAGS_API_WORKERS`, documented in `deploy/.env.example`.
- `spec/delivery.md`, the delivery spec for SDK authors: keys, endpoints, status codes, ETags and polling, streaming, events, CORS and OFREP.

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
