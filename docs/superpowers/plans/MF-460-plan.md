# MF-460 plan: release 1.0.0 (GA)

## Goal

Ship MegooFlags `1.0.0`: re-check the GA checklist against the code, relabel the SDKs, the CLI and the Terraform provider as 0.1.0 Beta, bump the server to 1.0.0 with first-release notes, re-measure the NFRs at 1.0.0, set the checklist verdict to **ready**, and open the draft release pull request `develop` → `main`.

## Architecture

This is a release task. It changes only docs, version metadata and committed perf evidence. No product code changes. The work runs on the task branch `megoo/MF-460-release-v1-0-0-ga-close-the-ga-checklist` (from `origin/develop`, which already holds MF-457/458/459) and goes into `develop` as one pull request with one commit per step. A draft `develop` → `main` pull request titled `Release 1.0.0` is opened at the end. **Agents never create or push tags, GitHub releases, images or charts.**

## Build-machine notes (apply to every step)

- System `python3` is 3.10. Run repository scripts with `uv run --frozen --project api python scripts/<name>.py` (this matters for `check_version.py`, `release_meta.py` and `perf_report.py`).
- Services: `megoocolab-runner services run api -- …` (Postgres, Valkey, Keycloak) and `megoocolab-runner services run db -- …` (Postgres, Valkey).
- Web e2e: run it in the pinned Playwright image (`pnpm e2e:docker`). It runs as root and leaves root-owned `web/test-results`, `web/.next` and `web/node_modules`, plus an untracked `/.pnpm-store`. Afterwards, fix ownership with `docker run --rm -v "$PWD:/w" mcr.microsoft.com/playwright:v1.65.0-noble chown -R $(id -u):$(id -g) /w/web` and delete `.pnpm-store`. The backgrounded API outlives the runner, so always add a `trap` that kills it.
- Site: the CPU has no AVX2, so Astro's native compiler binding and Pagefind die with SIGILL. Use the workaround in the memory note `build-machine-no-avx2`: the wasm compiler binding, Pagefind built with cargo, and pnpm through `npx -y pnpm@10.34.9`. Re-apply it after every `pnpm install`. `astro build` exits 0 even when Pagefind crashed, so confirm the output contains `[build] Complete!`.
- Never commit `perf/results/tiny.json`, `perf/results/only.json`, synced site pages, `dist`, caches or `.pnpm-store`.

## File map

| File | Change | Step |
|---|---|---|
| `docs/ga-checklist.md` | quick start time (section 2), then the new perf numbers, then the header, verdict and sections 2, 4 and 5 | 2, 6, 7 |
| `sdks/{go,node,python,java,dotnet,ruby,php,js,react,react-native,ios,android,flutter}/README.md` | `Status:` line Alpha → Beta | 3 |
| `sdks/README.md` | status table `0.1.0 (Alpha)` → `0.1.0 (Beta)` | 3 |
| `site/src/content/docs/sdks/overview.md` | table and the "Alpha means…" sentence | 3 |
| `sdks/flutter/CHANGELOG.md` | "First Alpha release." → "First Beta release." | 3 |
| `cli/README.md`, `terraform/README.md`, `site/src/content/docs/use/cli.md` | add the "0.1.0, Beta" status | 3 |
| `ROADMAP.md`, `SECURITY.md`, `deploy/.env.worker.example`, `deploy/helm/megooflags/README.md`, `site/src/content/docs/deploy/multi-node.md`, `site/src/content/docs/deploy/upgrades.md` | pre-1.0 wording reworded for 1.0 | 4 |
| `README.md`, `deploy/.env.example`, `deploy/.env.worker.example`, `deploy/helm/megooflags/README.md`, `site/src/content/docs/deploy/{single-node,multi-node,multi-node-migration,observability,upgrades}.md` | pinning and `/healthz` examples 0.1.0 → 1.0.0 | 4 |
| `VERSION`, `api/pyproject.toml`, `web/package.json`, `deploy/helm/megooflags/Chart.yaml`, `CHANGELOG.md`, `api/uv.lock`, `api/openapi.json`, `web/lib/api/schema.d.ts` | bump to 1.0.0 | 5 |
| `perf/results/1.0.0.json` (new), `perf/REPORT.md`, `CHANGELOG.md`, `docs/ga-checklist.md` | full run at 1.0.0 and the numbers it produces | 6 |

These stay as they are: SDK, CLI and Terraform version constants, manifests and lockfiles; `perf/results/0.1.0.json`; `release.yml`; the SDK, CLI and Terraform version examples (for example `site/src/content/docs/start/quick-start.mdx:223`, `use/cli.md` `v=0.1.0`, `terraform/README.md` `v=0.1.0`); `api/tests/test_cluster_compat.py`; checklist section 3.

## Reference A: local checks (AGENTS.md section 9, adapted to this machine)

Run from the repository root. Every block must pass.

```bash
# version and script tests
uv run --frozen --project api python scripts/check_version.py
uv run --frozen --project api pytest scripts/tests
# api lint and tests
megoocolab-runner services run api -- bash -c 'cd api && uv sync --frozen && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts && uv run mypy && uv run pytest'
# migrations
megoocolab-runner services run db -- bash scripts/check_migrations.sh
# web
(cd web && pnpm install --frozen-lockfile && pnpm lint && pnpm format:check && pnpm typecheck && pnpm build && pnpm test)
# web e2e in the pinned Playwright image (needs :8000 free)
megoocolab-runner services run db -- bash -c 'set -e; (cd api && uv run alembic upgrade head); (cd api && MEGOOFLAGS_SECRET_KEY=$(openssl rand -hex 32) MEGOOFLAGS_PUBLIC_URL=http://localhost:3000 MEGOOFLAGS_API_WORKERS=1 MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true MEGOOFLAGS_ROLE=control-plane uv run python -m megooflags_api.serve) & PID=$!; trap "pkill -P $PID; kill $PID" EXIT; cd web && pnpm e2e:docker --retries=0'
# then fix ownership as in the build-machine notes
# OpenAPI drift
uv run --frozen --project api python -m megooflags_api.export_openapi api/openapi.json
pnpm --dir web gen:api
git diff --exit-code -- api/openapi.json web/lib/api/schema.d.ts
# docs site (AVX2 workaround; look for "[build] Complete!")
(cd site && npx -y pnpm@10.34.9 install --frozen-lockfile && npx -y pnpm@10.34.9 format:check && npx -y pnpm@10.34.9 check && npx -y pnpm@10.34.9 build && npx -y pnpm@10.34.9 test)
# images and smoke
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/smoke.sh http && bash scripts/smoke.sh https
bash scripts/quickstart_test.sh        # prints "quick start: Ns"
bash scripts/proxy_samples_test.sh     # prints "proxies OK"
bash scripts/cluster_test.sh           # prints "cluster OK"
bash scripts/perf.sh tiny              # prints "perf OK (tiny)"; tiny.json is git-ignored
# helm (helm v3.19.0, kind v0.30.0, kubeconform v0.7.0, kubectl in ~/.local/bin)
MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py
bash scripts/helm_test.sh              # prints "helm OK"
git status                             # nothing untracked or modified that should not be
```

The web e2e must not change a visual baseline: the sidebar version label is masked in `web/e2e/matrix.spec.ts`. If a baseline does fail, regenerate it only with `pnpm e2e:docker --update-snapshots` in the pinned image. Commit only the PNGs whose change is the intended one; the memory note `matrix-update-snapshots-all-noise` describes how to tell them apart.

## Reference B: wording for Beta (step 3)

The meaning of Beta, used wherever a sentence is needed: **the features are complete and tested, and the API may still change before 1.0.0.**

- `sdks/{go,node,python,java,dotnet,ruby,php}/README.md`: `Status: 0.1.0, Alpha.` → `Status: 0.1.0, Beta.`
- `sdks/{js,react,react-native,ios,android,flutter}/README.md`: `Status: 0.1.0, Alpha: the API may still change.` → `Status: 0.1.0, Beta: the API may still change before 1.0.0.`, with the rest of the line (the requirements) unchanged.
- `sdks/README.md` rows 37–49: `0.1.0 (Alpha)` → `0.1.0 (Beta)`.
- `site/src/content/docs/sdks/overview.md`: in each row `0.1.0 (Alpha) |` → `0.1.0 (Beta) |`. The Status column becomes 12 characters wide: header `| Status       |`, separator `| ------------ |`. Line 28 becomes `Beta means the features are complete and tested, and the API may still change before 1.0.0. Server SDKs pass the shared conformance suite, client SDKs the` (the next line is unchanged).
- `sdks/flutter/CHANGELOG.md`: `- First Alpha release.` → `- First Beta release.`
- `cli/README.md`: a new paragraph after the first paragraph (before the code block): `Status: 0.1.0, Beta: the features are complete and tested, and the commands and flags may still change before 1.0.0. The CLI is versioned apart from the server.`
- `terraform/README.md`: a new paragraph after the first paragraph (before `## Install`): `Status: 0.1.0, Beta: the features are complete and tested, and resources and attributes may still change before 1.0.0. The provider is versioned apart from the server.`
- `site/src/content/docs/use/cli.md` line 11: `The CLI has its own version, 0.1.0 (Beta), independent of the server: the features are complete and tested, and the commands and flags may still change before 1.0.0.`

## Reference C: pre-1.0 docs reworded for 1.0 (step 4)

The worker compatibility rule follows `api/src/megooflags_api/services/cluster/compat.py` (see `api/tests/test_cluster_compat.py`): the major version must match, and the worker runs the same minor as the control plane or one minor older, never newer.

- `ROADMAP.md` line 3–4, the first paragraph: `MegooFlags reached GA with `v1.0.0`. This roadmap follows the release plan of the product requirements; later phases have no fixed dates.` The heading `## Done for GA (`v1.0.0`)` stays.
- `SECURITY.md`, the paragraph under "Supported versions": `Security fixes go into the latest release: upgrade to it to get a fix. The SDKs, the CLI and the Terraform provider follow the same rule, each for its own latest release.`
- `deploy/.env.worker.example` lines 5–6:
  ```
  # Image version. A worker must run the same major release as the control plane and the same minor
  # or one minor older: pin the control plane's exact release, e.g. MEGOOFLAGS_VERSION=1.0.0, and
  # upgrade the control plane first.
  ```
- `deploy/.env.example` line 4: `MEGOOFLAGS_VERSION=0.1.0` → `MEGOOFLAGS_VERSION=1.0.0`.
- `deploy/helm/megooflags/README.md` line 21: `` `0.1.0` `` → `` `1.0.0` ``. Lines 314–315: replace `Before 1.0, a minor upgrade makes the old workers Incompatible until they are upgraded, so upgrade them right away.` with `A worker may run one minor older than the control plane, so upgrading by one minor leaves the old workers in sync; a major upgrade, or a jump of more than one minor, makes them Incompatible until they are upgraded, so upgrade them right away.`
- `README.md` line 30: `` (for example `0.1.0`) `` → `` (for example `1.0.0`) ``.
- `site/src/content/docs/deploy/multi-node.md`:
  - lines 235–238, the two bullets become one: `- The major versions must match, and the worker may run the same minor or one minor older than the control plane, never newer (`1.2.x` and `1.3.x` work together; `1.1.x` and `1.3.x` do not, nor do `1.x` and `2.x`).`
  - lines 266–268: `A major upgrade (`1.x` to `2.x`), or a jump of more than one minor, makes the old workers Incompatible from the moment the control plane is upgraded until they are upgraded. They keep serving, but flag changes do not reach them in between, so upgrade them right away. Patch upgrades and an upgrade by one minor (`1.2` to `1.3`) have no such gap.`
  - line 75 `e.g. 0.1.0.` → `e.g. 1.0.0.`; line 77 `"version":"0.1.0"` → `"version":"1.0.0"`.
- `site/src/content/docs/deploy/upgrades.md`:
  - lines 19–20: `From 1.0.0 on, releases are full GitHub releases and only `X.Y.Z-rc.N` release candidates are pre-releases. A release that breaks compatibility comes only with a new major version.`
  - line 28: `MEGOOFLAGS_VERSION=1.0.0`.
  - lines 70–71: `… never an older one. Workers must run the same major version as the control plane and the same minor or one minor older.`
  - line 15 (`Published from 1.0 on only.`) stays.
- `site/src/content/docs/deploy/single-node.md`: line 39 `` `v0.1.0` `` → `` `v1.0.0` ``, line 53 `` `0.1.0` `` → `` `1.0.0` ``.
- `site/src/content/docs/deploy/multi-node-migration.md`: lines 20, 25, 38 and 108, `0.1.0` → `1.0.0`.
- `site/src/content/docs/deploy/observability.md` line 13: `"version": "0.1.0"` → `"version": "1.0.0"`.

## Reference D: CHANGELOG `[1.0.0]` (step 5)

Result structure:

```markdown
## [Unreleased]

## [1.0.0] - YYYY-MM-DD        <- the bump commit's date: `date +%F`

### Changed
- Each `api` container runs several uvicorn worker processes … (the existing lines from "Each `api` container" down to "Flag changes reach SDKs across API replicas faster", unchanged)

### Added
- `/api/*` requires authentication. Open `/setup` right after the first start to create the first admin (until then anyone who can reach the instance can), then create service tokens (`POST /api/tokens`) for scripts and CI.
- `MEGOOFLAGS_SECRET_KEY` is required (32+ characters, e.g. `openssl rand -hex 32`). The API refuses to start without it.
- The API requires Valkey. The bundled `docker-compose.yml` runs a `valkey` service; if you run the API elsewhere, set `MEGOOFLAGS_VALKEY_URL`, because the API refuses to start without it.
- `MEGOOFLAGS_SITE_ADDRESS` defaults to empty, which means plain HTTP on port 80 only. Caddy publishes port 443 only when it is set to a domain.
- docker-compose.control-plane.yml … (every existing Added line, in order, with only the edits below)

## [0.1.0]

Never released: no `v0.1.0` tag, image or GitHub release exists. Its items ship in 1.0.0.

### Added
… (unchanged, including the empty Changed/Fixed/Removed headings)
```

Edits inside the moved lines (all other lines stay word for word):

1. Remove the five lines from `- **Breaking / upgrade steps:**` through `- **Upgrade step:** \`MEGOOFLAGS_SITE_ADDRESS\` …` (current lines 10–14). They become the four Added lines above. The `:80` upgrade sentence is dropped because nobody has an older `.env`.
2. Line 54 (Web UI app shell): delete the sentence ` Segments and Environments are placeholder pages for now.`
3. Line 72 (`POST …/evaluate`): `with the new reference evaluation engine` → `with the reference evaluation engine`; `must now use the portable` → `must use the portable`.
4. The 13 SDK lines (38–50): `0.1.0 Alpha` → `0.1.0 Beta`.
5. CLI line: ``- `megooflags` CLI 0.1.0:`` → ``- `megooflags` CLI 0.1.0 Beta:``.
6. Terraform line: `- Terraform provider 0.1.0 with` → `- Terraform provider 0.1.0 Beta with`.

Step 6 updates the perf numbers in three lines: the two "faster" lines in Changed and the "meets its target" line in Added.

## Reference E: checklist text at "ready" (step 7)

- Header, lines 3–7: `… revision 2, in the Knowledge Base) and checked against the code at `1.0.0` on <re-run date, YYYY-MM-DD>. Statuses are …`. The rest is unchanged.
- Verdict, lines 9–10: `**Verdict: ready.** Every row in sections 1 and 2 is done or an accepted difference in section 4, and section 6 has no open blocker. The maintainer steps in section 5 remain: the maintainer merges and tags `v1.0.0`.`
- Section 2 intro: `(the `full` run of <date> on <CPUs> CPUs and <GB> GB, results in `perf/results/1.0.0.json`)`.
- Section 4, the SDK bullet becomes: `- **SDKs, the CLI and the Terraform provider are versioned apart and are Beta at 0.1.0** (the maintainer's decision of 2026-11-27), each with its own tags (`sdks/README.md`, `sdks/RELEASING.md`), which answers open question 6.`
- Section 5 is replaced by:
  1. Enable GitHub Pages with the source "GitHub Actions" (Settings → Pages) **before** merging the release pull request: that merge starts `.github/workflows/docs.yml` by itself. If Pages was not enabled in time, run the workflow by hand (Actions → Docs → Run workflow). The README's docs links return 404 until it has run.
  2. Enable private vulnerability reporting (Settings → Code security) and content reports (Settings → Moderation options → Reported content), which `SECURITY.md` and `CODE_OF_CONDUCT.md` rely on.
  3. Update the PRD section 5 matrix in the Knowledge Base with section 3 above.
  4. Merge the MF-460 pull request into `develop`, mark the `Release 1.0.0` pull request ready, and merge it with a merge commit.
  5. Push the tag `v1.0.0` on that merge commit on `main`. Agents never do this.
  6. After the first release, make the GHCR package `charts/megooflags` public, once.
  7. Release the SDKs, the CLI and the Terraform provider separately, when you choose, with their own `0.1.0` tags (`sdks/RELEASING.md`, AGENTS.md section 12).
- Section 6 stays `None.` plus `No competitor matrix cell is unverified.` The six `##` headings keep their text and order (`scripts/tests/test_ga_checklist.py`).

## Reference F: pull request bodies (step 9)

Write the bodies to files under `/tmp`, never into the repository.

**Task PR** (`/tmp/mf460-task-pr.md`), into `develop`, title `Release 1.0.0 (GA): version bump and GA checklist ready (MF-460)`:
- Summary: the server goes to 1.0.0 (VERSION, api, web, Helm chart, CHANGELOG `[1.0.0]` corrected for a first release); SDKs, CLI and Terraform stay at 0.1.0 and are relabelled Beta; the pre-1.0 docs are reworded; the full perf run at 1.0.0 is in `perf/results/1.0.0.json` and `perf/REPORT.md`; the GA checklist verdict is **ready**.
- The NFR table from checklist section 2.
- The local checks run (Reference A) and `release_meta.py --tag v1.0.0` output (`tags=1.0.0,1.0,1,latest`, `prerelease=false`).
- "Maintainer steps": the seven items of Reference E section 5, numbered.
- Last line: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

**Release PR** (`/tmp/mf460-release-pr.md`), `develop` → `main`, title `Release 1.0.0`:
- First line: `Do not merge before #<task PR number> is merged into develop.` Until then this PR shows 0.1.0.
- `GA checklist verdict: **ready** (docs/ga-checklist.md).`
- `Release notes: the CHANGELOG [1.0.0] section.`
- "Maintainer steps": the same seven items.
- Last line: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Steps

1. Re-check the GA checklist evidence and run the fast local checks
2. Run the Docker local checks and record the new quick start time
3. Relabel the SDKs, the CLI and the Terraform provider from Alpha to Beta
4. Reword the pre-1.0 docs and move the server version examples to 1.0.0
5. Bump the server to 1.0.0 with the first-release CHANGELOG
6. Run the full load test at 1.0.0 and update every quoted number
7. Set the GA checklist to ready
8. Final verification of the whole state
9. Push, open or update the task pull request, and open the draft release pull request

(Full detail in the `steps` array; each detail refers to References A–F above.)

## Final verification

```bash
uv run --frozen --project api python scripts/check_version.py                 # version OK: 1.0.0
uv run --frozen --project api python scripts/check_version.py --tag v1.0.0     # version OK: 1.0.0 (tag v1.0.0)
uv run --frozen --project api python scripts/release_meta.py --tag v1.0.0 --notes-out /tmp/mf460-notes.md
#   version=1.0.0 / image_version=1.0.0 / tags=1.0.0,1.0,1,latest / prerelease=false
#   /tmp/mf460-notes.md holds the corrected [1.0.0] section (no "Breaking", "placeholder", "Alpha")
uv run --frozen --project api pytest scripts/tests                              # includes test_ga_checklist, test_release_meta, test_perf_report
grep -n 'Verdict: ready' docs/ga-checklist.md
git grep -n 'Alpha' -- sdks/README.md 'sdks/*/README.md' sdks/flutter/CHANGELOG.md site/src/content/docs/sdks/overview.md CHANGELOG.md docs/ga-checklist.md   # prints nothing
git grep -n -i 'before 1\.0' -- ROADMAP.md SECURITY.md deploy site/src/content/docs/deploy   # prints nothing
# plus every block of Reference A, then CI green on both pull requests
```

Done when the draft `Release 1.0.0` pull request is open with the checklist verdict **ready**, and the task pull request into `develop` carries the maintainer reminders.

## Requirement coverage

| Design item | Step |
|---|---|
| 1. Re-run the checklist and the local checks; new quick start time | 1, 2 |
| 2. Alpha → Beta labels | 3 |
| 3. "Before 1.0" docs and the 1.0.0 pinning examples | 4 |
| 4. Bump in one commit, regenerated files, CHANGELOG `[1.0.0]` | 5 |
| 5. Full load test at 1.0.0 and the quoted numbers | 6 |
| 6. Checklist to ready | 7 |
| Testing: re-run the checks after step 6, `release_meta.py` | 8 |
| 7. Draft release PR and reminders in both PRs; edge cases (existing PR, no drafts) | 9 |
| Errors: failing row or check, failing perf gate → stop and ask | 1, 2, 6, 8 |
