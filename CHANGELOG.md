# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows the versioning rules in [AGENTS.md](AGENTS.md).

## [Unreleased]

## [0.1.0]

### Added
- Monorepo scaffold: FastAPI `api/` and Next.js `web/` with a shared `VERSION`.
- `/healthz` and `/readyz` endpoints.
- Typed web API client generated from the OpenAPI spec.
- Docker images `megooflags-api` and `megooflags-web`, reference Docker Compose bundle with Caddy.
- CI (lint, types, tests, migrations, build, images, smoke) and tag-driven release pipeline.

### Changed

### Fixed

### Removed
