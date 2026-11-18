# Contributing to MegooFlags

Thank you for helping. Every kind of contribution is welcome:

- **Issues**: report a bug with the steps to reproduce it, the version you run (`VERSION`, or the
  version in the sidebar footer) and what you expected to happen.
- **Discussions**: ask questions and share ideas before you write code.
- **Pull requests**: fix a bug, improve the docs or add a feature. For a big change (a new public API,
  an SDK protocol change, a breaking change, a new deployment topology or a large UI flow) write an
  [RFC](rfcs/README.md) first, so the design is agreed before the work starts.

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md). Report security problems
privately, as described in [SECURITY.md](SECURITY.md), never in a public issue.

[AGENTS.md](AGENTS.md) holds the full rules. Coding agents and humans follow the same ones; this page
is the short version.

## Setup

Prerequisites:

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node 22 and pnpm (run `corepack enable`; the version is pinned in each `package.json`)
- Docker
- Go, for the Go SDK, the CLI, the Terraform provider and the load tests

Each directory has exactly one package manager and lockfile (AGENTS.md section 1). Run `uv` and `pnpm`
from the package directory and do not install tools globally. The commands to start the API and the
web app are in the [README](README.md#development).

## Branches and pull requests

- Start every branch from an up-to-date `origin/develop`:

  ```bash
  git fetch origin
  git switch -c <branch> origin/develop
  ```

- Open your pull request against `develop`: `gh pr create --base develop`. `main` holds released code
  and changes only through a release pull request.
- Pull requests are merged with a merge commit, never squashed or rebased.
- Write commit messages as `type(scope): summary`, for example `fix(api): reject empty flag keys` or
  `docs(site): explain the TLS modes`. Common types are `feat`, `fix`, `docs`, `test`, `refactor` and
  `chore`.

## Checks

Run the checks for the areas you touched before you open a pull request. They are the same commands that
CI runs; the full list, with the Postgres, Valkey and Keycloak setup, is in
[AGENTS.md section 9](AGENTS.md#9-local-checks-the-same-as-ciyml). From the repository root:

```bash
# api (needs Postgres and Valkey)
(cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts \
  && uv run mypy && uv run pytest)
# migrations
bash scripts/check_migrations.sh
# scripts
uv run --frozen --project api pytest scripts/tests
# web
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test)
# docs site
(cd site && pnpm install --frozen-lockfile && pnpm format:check && pnpm check && pnpm build && pnpm test)
```

Each SDK, the CLI and the Terraform provider have their own group in AGENTS.md section 9.

## Tests first

Write the test before the code: write a test, run it and see it fail for the reason you expect, write
the smallest change that makes it pass, then tidy up while it stays green. A bug fix comes with a test
that fails without the fix. Tests check behaviour, not which functions were called.

## CHANGELOG

Every user-visible change adds a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md), in the
right group: **Added**, **Changed**, **Fixed** or **Removed**. Write it for users: what changed and what
they need to do. Internal refactors and test-only changes need no entry (AGENTS.md section 6).

## Database migrations

Every schema change is an Alembic revision with a working `downgrade()`, and there is exactly one head.
Never edit a released migration; fix it with a new revision. Breaking schema changes use
**expand/contract** across two releases, so that old and new API replicas can run side by side during a
rolling upgrade: first add the new column or table and write to both, then, in the next release, read
only the new one and drop the old one. AGENTS.md section 7 has a worked example.

## API client

The web client is generated from the API's OpenAPI spec. After any change to API routes or models,
regenerate both files and commit them with the change (AGENTS.md section 8):

```bash
cd api && uv run python -m megooflags_api.export_openapi openapi.json
cd web && pnpm gen:api
```

CI regenerates both and fails on any drift.

## Releases

Maintainers cut releases. Versioning, the release pull request from `develop` into `main` and the tags
for the server, the SDKs, the CLI and the Terraform provider are described in AGENTS.md sections 3 to 5
and 10 to 12. Contributors never push tags.

## License

No CLA and no sign-off: by contributing you agree your contribution is licensed under Apache-2.0, like
the rest of the project.
