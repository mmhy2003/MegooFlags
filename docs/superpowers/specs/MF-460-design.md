# MF-460 design: release 1.0.0 (GA)

## Goal

MegooFlags gets its first public release, `1.0.0`. This task re-runs the GA checklist and sets its verdict to **ready**, bumps the server to 1.0.0 with release notes that are correct for a first release, measures the performance targets again at 1.0.0, and opens the draft release pull request `develop` → `main`. The maintainer merges and tags.

## What I found

- `develop` already holds MF-457, MF-458 and MF-459. The checklist has every row **done**, section 6 says "None", and the verdict is "blockers closed". Nothing is missing; what is left is the re-check, the decisions below and the bump.
- `v0.1.0` was never released: the repository has no tags and `main` holds only the initial README commit. So 1.0.0 is the first release, and `develop` → `main` brings the whole product.
- No SDK, CLI or Terraform tag exists either.

## Decisions

| Question | Your answer | What it means |
|---|---|---|
| GA versions of the SDKs, CLI and Terraform provider | All stay at **0.1.0**, relabelled **Beta** | No version constant, manifest or lockfile of an SDK, the CLI or the provider changes. Only the labels change. `release.yml` keeps marking CLI and Terraform releases as pre-releases, which is right for 0.x. Rejected: 1.0.0 for the P0 SDKs (my suggestion), and keeping Alpha. |
| CHANGELOG `[1.0.0]` | **Move it and correct it for a first release** | Rejected: moving it word for word, and a rewrite as a short summary. |
| Load test evidence | **Run the full profile again at 1.0.0** | Rejected: keeping the 0.1.0 run, and you running it yourself. |
| Release pull request | **The agent opens it now, as a draft** | Rejected: you opening it after the merge, and a follow-up task. |

Three smaller choices are mine. Say so on review if you want them differently:

- **Competitor matrix (section 3) is not checked again.** It was checked on 2026-11-18 with no unverified cell. The checklist keeps that date.
- **`SECURITY.md`** loses its "Before 1.0.0" sentence and keeps the rule it already states: security fixes go into the latest release. No new support policy is invented.
- **`perf/results/0.1.0.json` stays** in the repository as the baseline before the release. Nothing reads it once `1.0.0.json` exists.

## Work, in order

All of it is on the task branch, in one pull request into `develop`. One commit per step.

1. **Re-run the checklist.** For every row in sections 1 and 2, confirm that the evidence (file, test, CI job) still exists and still proves the row, and run the local checks from AGENTS.md section 9: version, API lint and tests, migrations, web lint, typecheck, build and unit tests, web e2e in the pinned Playwright image, OpenAPI drift, the docs site, images and smoke, quick start, proxy samples, cluster, `perf.sh tiny` and Helm. Write the new quick start time into section 2. A row that no longer holds becomes an open blocker, and the work stops with a question to you (see "Errors").
2. **Alpha → Beta.** Change the label, not the version, in: the 13 `sdks/<lang>/README.md` status lines, the status table in `sdks/README.md`, the table and the "Alpha means…" sentence in `site/src/content/docs/sdks/overview.md`, and `sdks/flutter/CHANGELOG.md`. Add "0.1.0, Beta" to `cli/README.md`, `terraform/README.md` and `site/src/content/docs/use/cli.md`, which have no label today. Beta is described as: the features are complete and tested, and the API may still change before 1.0.0.
3. **Docs that speak about "before 1.0".** Reword them for a server at 1.0.0: `ROADMAP.md` (first paragraph), `SECURITY.md`, `deploy/.env.worker.example`, `deploy/helm/megooflags/README.md`, and `deploy/multi-node.md` and `deploy/upgrades.md` on the site (the worker rule is now: same major, same or one minor older). Change the pinning examples `MEGOOFLAGS_VERSION=0.1.0`, `v0.1.0` and the `/healthz` samples to 1.0.0 in `README.md`, `deploy/.env.example`, `deploy/.env.worker.example`, the Helm README and the site's deploy pages. Examples of SDK, CLI and Terraform versions stay 0.1.0.
4. **Bump to 1.0.0 in one commit** (AGENTS.md section 5): `VERSION`, `api/pyproject.toml`, `web/package.json`, `deploy/helm/megooflags/Chart.yaml` (`version` and `appVersion`), `CHANGELOG.md`, and the regenerated `api/uv.lock`, `api/openapi.json` and `web/lib/api/schema.d.ts`. `scripts/check_version.py` must print `version OK: 1.0.0`.
   The CHANGELOG change in that commit:
   - `[Unreleased]` moves into `## [1.0.0] - <date>`, with an empty `[Unreleased]` above it.
   - The "Breaking / upgrade steps" become plain requirements under **Added** or **Changed** (set `MEGOOFLAGS_SECRET_KEY`, the API needs Valkey, `MEGOOFLAGS_SITE_ADDRESS` defaults to plain HTTP, `/api/*` needs authentication and `/setup` creates the first admin).
   - Lines that are no longer true are fixed or removed, for example "Segments and Environments are placeholder pages for now", "the new reference evaluation engine" and "must now use".
   - The SDK, CLI and Terraform lines say "0.1.0 Beta".
   - `## [0.1.0]` stays, with one line saying it was never released.
   - Every other line stays as it is.
5. **Full load test at 1.0.0.** Run `bash scripts/perf.sh full` on this build machine (about an hour, nothing else running). Commit `perf/results/1.0.0.json` and the rendered `perf/REPORT.md`. Then update every place that quotes the numbers with the new ones: checklist sections 1 (F-6, S-1, HA-4), 2 and 4, the two "faster" lines and the "meets its target" line in the CHANGELOG `[1.0.0]`, and any site page that repeats them.
6. **Checklist to "ready".** In `docs/ga-checklist.md`: the header says it was checked against the code at 1.0.0 on the re-run date; the verdict becomes **ready**; section 2 names `perf/results/1.0.0.json`; the section 4 entry about SDKs becomes "SDKs, the CLI and the Terraform provider are versioned apart and are Beta at 0.1.0 (the maintainer's decision of 2026-11-27)"; section 5 keeps only the steps that are still yours (below); section 6 stays "None". The six headings and their order do not change, because `scripts/tests/test_ga_checklist.py` checks them.
7. **Open the draft release pull request**, after the task branch is pushed: `gh pr create --draft --base main --head develop --title "Release 1.0.0"`. Its body has the checklist verdict, a link to this task's pull request with "do not merge before that one is merged", and the reminders below. The same reminders go into the task's pull request.

## Reminders for the maintainer (in both pull requests and in section 5)

1. Enable GitHub Pages with the source "GitHub Actions". Do it **before** merging the release pull request: that merge starts `docs.yml` by itself. If Pages was not enabled in time, run the workflow by hand (Actions → Docs → Run workflow). The README's docs links return 404 until it has run.
2. Enable private vulnerability reporting and content reports.
3. Update the PRD section 5 matrix in the Knowledge Base with checklist section 3.
4. Merge the task's pull request into `develop`, mark the release pull request ready, and merge it with a merge commit.
5. Push the tag `v1.0.0` on that merge commit on `main`. Agents never do this.
6. After the first release, make the GHCR package `charts/megooflags` public, once.
7. The SDKs, the CLI and the provider are released separately, when you choose, with their own `0.1.0` tags (`sdks/RELEASING.md`, AGENTS.md section 12).

## Errors and edge cases

- **A checklist row fails the re-check, or a local check fails.** The agent does not fix product code in this task. It records the row as an open blocker, leaves the verdict as it is, and asks you whether to fix it here or in a new task.
- **A performance gate fails in the full run.** The agent stops and asks you, as you chose. It does not run again on its own, does not change a target, and does not set the verdict to ready.
- **Visual baselines.** The version label in the sidebar is masked in `matrix.spec.ts`, so the bump should not change a baseline. If one does change, it is regenerated only in the pinned Playwright image, and only the intended files are committed.
- **Drafts not available** for this repository: the release pull request is opened as a normal one whose body starts with "Do not merge before #N".
- **A `develop` → `main` pull request already exists:** the agent updates its title and body instead of opening a second one.
- **The date in `## [1.0.0] - <date>`** is the day of the bump commit. If you tag on a later day and want that day there, it is a one-line change in `develop` before you merge the release pull request.
- The agent never creates or pushes a tag, a GitHub release, an image or a chart.

## Testing

- The local checks of step 1 run again after step 6, so the final state is checked, not only the starting one: `check_version.py`, `scripts/tests` (they include the checklist, release metadata and perf report tests), API, migrations, web, e2e, OpenAPI drift, site, smoke, quick start, proxies, cluster and Helm.
- `python3 scripts/release_meta.py --tag v1.0.0 --notes-out <file>` must print `tags=1.0.0,1.0,1,latest` and `prerelease=false`, and the notes file must hold the corrected `[1.0.0]` section. This is the same call `release.yml` makes on the tag.
- The gates in `scripts/perf_report.py` decide the verdicts of the full run.
- CI on both pull requests must be green. No SDK, CLI or Terraform code changes, so their groups run in CI only.

## Out of scope

- Any SDK, CLI or Terraform version bump or release, and their registry setup.
- Fixing product defects that the re-check finds (they become blockers, see above).
- Checking the competitor matrix again, and editing the PRD in the Knowledge Base.
- Pushing tags, creating releases, enabling repository settings.
