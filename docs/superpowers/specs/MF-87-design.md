# MF-87 — Launch readiness: docs site, community files, update notices, load tests, GA checklist

## Goal

A new user finds a published docs site in the brand palette, gets a flag evaluated in under five minutes, and can deploy, upgrade and migrate from it. The repository has the usual community files, admins see when a newer release exists, and a maintainer has a load test report and a GA checklist to decide on `v1.0.0`.

## Decisions

| Decision | Chosen | Why, and what was rejected |
|---|---|---|
| Scope | All five pieces in one design (your answer) | I suggested splitting; you chose one design. The plan orders the pieces so each is reviewable on its own. |
| Docs generator | Astro Starlight (your answer) | Least code to own. Rejected: Next.js + Fumadocs (a second Next app), Docusaurus (heavier). |
| Docs hosting | GitHub Pages at `https://mmhy2003.github.io/MegooFlags/` (your answer) | No server or DNS. A custom domain can be added later. |
| Telemetry | **Removed** (your answer: "I don't want to collect any data") | No setting, no instance ID, no pings, no collector. The docs state that MegooFlags collects no usage data. |
| Update notices | On by default; `MEGOOFLAGS_UPDATE_CHECK=false` turns the request off (your answer) | Same behaviour as MegooColab. |
| Notice placement | Pill in the sidebar footer, next to the running version (your answer, mockup v2) | Rejected: a dismissible banner above the page. |
| Load test environment | The build machine, in containers limited to the NFR footprints (your answer) | Repeatable with one command. Rejected: dedicated VMs, a manual Actions workflow. |
| Code of conduct contact | GitHub only, no email (your answer) | "Report content" on the item, or a private security advisory. |
| Version bump | Not in this task (your answer) | The task ends at the GA checklist. The 1.0.0 bump is the normal release pull request afterwards. |
| Docs versions | One version, built from `main` | `main` holds released code, so the site always describes the latest release. Versioned docs are not needed before 1.0. |
| Where guides live | `site/src/content/docs/` is the one home for guides | Starlight only loads pages from there. The eight guides in `docs/` move in; `docs/` keeps `examples/` and `superpowers/`. Files that must stay elsewhere are copied in at build time (see "Synced pages"). |
| Latest-release source | GitHub Releases API, `releases/latest` | Needs no infrastructure. It returns only full releases, and CLI, Terraform and SDK releases are published with `--latest=false`, so it is always the server. |
| Load tool | One small Go program, `perf/` | 100k open streams and a fixed 2k RPS are beyond the Python scripts (one process sends a few hundred requests per second). It replaces `scripts/bench_sdk_evaluate.py`. |

## Architecture

### 1. Docs site (`site/`, new pnpm project)

- **Astro Starlight**, static output, `site: https://mmhy2003.github.io`, `base: /MegooFlags`. Search is Starlight's built-in Pagefind.
- **Theme:** `site/src/styles/brand.css` maps the app's tokens (the hex values in `web/app/globals.css`, light and dark) onto Starlight's `--sl-color-*` variables. Fonts are Geist and Geist Mono, as in the app. A test compares the hex values in `brand.css` with `globals.css`, so the two can't drift (UI-7).
- **Logo:** light and dark SVG variants of the app's diamond mark with the wordmark, in `site/src/assets/`. The README uses the same files.
- **Sidebar and pages** (as in mockup v2):

| Section | Pages | Source |
|---|---|---|
| Start here | Introduction, Quick start, Concepts | New |
| Deploy | Single node, TLS modes, Behind a reverse proxy, Multi-node, Move to multi-node, Upgrades and version pinning, Configuration (every environment variable), Observability, Security, Performance and sizing | New, except Multi-node, Move to multi-node and Security (moved from `docs/`) |
| Use | Release workflows, Flags as code, Integrations, CLI, Terraform | Moved from `docs/`; Terraform synced |
| SDKs | Overview, then one page per SDK (13) | Overview new; SDK pages synced from `sdks/<lang>/README.md` |
| Reference | REST API, Evaluation spec, Delivery protocol | Generated from `api/openapi.json`; synced from `spec/` |
| Migrate | Overview and importers, From Unleash, From Flagsmith, From LaunchDarkly | Overview moved from `docs/importers.md`; three new guides |
| Project | Roadmap, Contributing, RFCs, Code of conduct, Security policy, Privacy, Release notes | Synced from the root files; Privacy new |

- **Synced pages:** `site/scripts/sync.mjs` runs before every build. From a manifest it copies files that must live elsewhere (SDK READMEs, `CHANGELOG.md`, `spec/*.md`, `terraform/README.md`, the community files, `perf/REPORT.md`) into the content directory, adds front matter and rewrites relative links to site or GitHub URLs. The copies are git-ignored, so each text has one source.
- **REST API reference:** generated from `api/openapi.json` with the `starlight-openapi` plugin. The plan checks the plugin against the Starlight version and the 3.1 spec first; if it can't handle the spec, the fallback is one static reference page rendered from the same file.
- **Quick start:** download the release bundle, set the secret key, `docker compose up -d`, create the admin at `/setup`, create a flag and a server key, evaluate with curl through OFREP, then the same in SDK tabs. Every command is checked against the code.
- **Behind a reverse proxy (DEP-5):** sample configs for nginx, Caddy, Traefik and HAProxy in front of the single-node bundle in `docs/examples/proxies/`, plus notes for Cloudflare, AWS ALB and Coolify. Each covers the SSE rules: no response buffering, idle timeout above the 15 s heartbeat, forwarded headers, `MEGOOFLAGS_TRUSTED_PROXIES` and `MEGOOFLAGS_PUBLIC_URL`.
- **Migration guides:** per tool, a concept map, the `megooflags import` steps, what is reported instead of translated (from `docs/importers.md`), and how to switch SDKs through OpenFeature.
- **Publishing:** new workflow `.github/workflows/docs.yml` builds the site and deploys it to GitHub Pages on every push to `main`, and on manual dispatch. A new `site` job in `ci.yml` runs the format check, `astro check`, the build, an internal link check and the palette test on pull requests.
- **References to moved files** in `README.md`, `AGENTS.md`, `CHANGELOG.md` (unreleased section), `cli/README.md` and `deploy/.env.example` are updated. `docs/README.md` points to the site.

### 2. Community files

| File | Content |
|---|---|
| `README.md` | Logo (light and dark), badges in the brand colours (CI, release, license, docs, OpenFeature), a short quick start that links to the site, links to the files below. Development notes stay. |
| `CONTRIBUTING.md` | How to set up, the checks to run, branch and pull request rules, CHANGELOG and migration rules, taken from `AGENTS.md`. No CLA and no sign-off; contributions are Apache-2.0. |
| `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1. Reports go through GitHub's "Report content" or a private security advisory. |
| `SECURITY.md` | Supported versions and GitHub private vulnerability reporting. |
| `ROADMAP.md` | From the PRD release plan: done for GA, Phase 2, and how to propose items. |
| `rfcs/README.md`, `rfcs/0000-template.md` | RFC process: when one is needed, a pull request into `rfcs/`, discussion, a maintainer decision, numbering. |

### 3. Update notices

- **Check (API):** new `services/updates.py`. A scheduler job on the leader (standalone and control plane; workers have no scheduler) asks `https://api.github.com/repos/mmhy2003/MegooFlags/releases/latest` at most once every 24 hours, and again after 1 hour if the last attempt failed. It sends no data about the instance: only a `User-Agent` with the MegooFlags version. It accepts only a tag of the form `vX.Y.Z` and stores the version; the release URL is built from the tag, never taken from the response.
- **Storage:** a single-row table `update_status` (`latest_version`, `checked_at`, `attempted_at`), so all replicas agree and a restart does not call GitHub again. The existing scheduler throttle is in memory only, which is why the times are stored.
- **Endpoint:** `GET /api/system/version`, for any signed-in user (new `routers/system.py`).
- **Web:** `loadShell()` loads it on the server; a new `components/shell/version-footer.tsx` in the sidebar footer shows `v1.0.0` to everyone and the pill "Update: 1.1.0" to org admins. The pill is a link to the release notes that opens in a new tab, with an icon and a label (UI-4) and a 44 px hit area. With the sidebar collapsed it is an icon button with the tooltip "Update available: 1.1.0".
- **Off switch:** with `MEGOOFLAGS_UPDATE_CHECK=false` the job is not registered, no request is made, and the endpoint returns no update even if one was stored earlier.

### 4. Load tests

- **`perf/`** (new Go module): `mfload` with three commands. `seed` creates a project of a given size. `evaluate` sends requests at a fixed rate, open loop, measuring from each request's due time as the old script did. `streams` holds N SSE streams and times each change from the write to its arrival on every stream. Each writes a JSON summary.
- **`scripts/perf.sh`**: one command that starts the stacks from the same compose files as the smoke and cluster tests, with overrides in `scripts/perf/` that set CPU and memory limits, runs the scenarios, and writes `perf/results/<version>.json`. The compose helpers it shares with `cluster_test.sh` move to `scripts/cluster/lib.sh`.
- **`scripts/perf_report.py`** renders the results into `perf/REPORT.md`: one row per NFR with target, measured value and verdict, plus the hardware, limits and versions of the run. The site shows it under "Performance and sizing".

| Scenario | What runs | Gate (from PRD section 7) |
|---|---|---|
| Local evaluation | Each server SDK's existing p99 guard on 10k flags, in its language's Docker image | p99 under 1 ms |
| Remote evaluation | One `api` container behind Caddy, 10k-flag project, random contexts from 1M users, 2k RPS for 60 s after warm-up: single-flag OFREP, and `/sdk/v1/evaluate` on a 100-flag environment | p99 under 50 ms, no errors |
| Propagation | 20 kill-switch flips while streams are open, on 3 API replicas and on the multi-node cluster | p95 under 5 s |
| 10k flags | The gates above measured on a 10k-flag project. Ruleset size and download time, flag list latency and SDK start time are reported without a gate | As above |
| 100k streams | Find how many streams one worker holds inside 1 vCPU / 512 MB, start enough workers, hold 100k streams for 10 minutes with a flip every 30 s | No dropped streams, propagation p95 under 5 s, memory inside the limit |
| Single-node footprint | Full bundle with limits that sum to 2 vCPU / 2 GB, a small-team load (100 flags, 20 streams, 50 RPS, one write per second) for 10 minutes | No container killed or restarted; the remote and propagation gates hold |
| Worker footprint | Limits that sum to 1 vCPU / 512 MB, measured in the 100k scenario | Same as that scenario |

The PRD does not say which request the 2k RPS target means or what load a "small team" is, so the two definitions above are mine and are approved with this design. Evaluating a whole 10k-flag environment in one request is reported, not gated.

### 5. GA checklist (`docs/ga-checklist.md`)

1. Every P0 requirement of PRD section 6 and 8, and each item of the GA row in section 11, with status and evidence (file, test or CI job), verified against the code.
2. The NFR verdicts, linked to `perf/REPORT.md`, and the measured time to first flag.
3. The PRD section 5 matrix re-checked cell by cell against each competitor's current public docs, with a source link and date, and the cells that changed.
4. Differences from the PRD: telemetry removed, so the "monthly active instances" metric can't be measured; the bundle also runs Valkey.
5. Maintainer steps: enable GitHub Pages, private vulnerability reporting and content reports; decide the SDK, CLI and Terraform versions; update the PRD matrix in the Knowledge Base; bump to 1.0.0, open the release pull request, tag.
6. Open blockers, if any.

## Data and interfaces

- **Setting:** `MEGOOFLAGS_UPDATE_CHECK` (boolean, default `true`), added to `settings.py`, `deploy/.env.example`, `docker-compose.yml` and `docker-compose.control-plane.yml`.
- **Migration `0016`:** table `update_status`, one row, with a working downgrade.
- **Endpoint:** `GET /api/system/version` returns `{"version": "1.0.0", "update": {"version": "1.1.0", "url": "https://github.com/mmhy2003/MegooFlags/releases/tag/v1.1.0"}}`. `update` is `null` unless the caller holds `org.manage`, the check is on, and the stored version is newer than the running one.
- **Contract files:** `api/openapi.json`, `web/lib/api/schema.d.ts` and `web/lib/api/check.ts` regenerated in the same commit; CHANGELOG entries under `[Unreleased]`.
- **`AGENTS.md`:** `site/` (pnpm) and `perf/` (Go modules) in the layout and package-manager lists, and their checks in section 9.

## Error handling and edge cases

- **Update check:** a timeout, a network error, a non-200 answer, a body over 1 MB or an unexpected tag is logged, counted as a failed job and retried after an hour. The last known version stays. A 404 means no full release exists yet (all `0.Y.Z` releases are pre-releases) and counts as "no update".
- **Release candidates:** an instance on `1.1.0-rc.1` is older than `1.1.0`. Pre-release tags from GitHub are never offered.
- **After an upgrade** the stored version is no longer newer, so the pill disappears without waiting for the next check.
- **`/api/system/version` fails:** the shell shows no version line; nothing else changes.
- **Docs build:** a broken internal link, a manifest entry whose source file is missing, or a palette mismatch fails CI.
- **Docs before the first deploy:** the site goes live on the first push to `main` after a maintainer enables Pages, so on the release pull request. Until then README links to it return 404; the checklist lists this.
- **Load tests on a small host:** the harness checks cores and memory first. A scenario that does not fit runs at the largest size that does, and the report marks it "reduced" with the size used, never as passed. A full-size run then stays open in the checklist.
- **A missed target:** settings and defaults (worker count, limits, proxy settings) may be tuned in this task and are documented. If a target still fails, the build stops and asks you rather than starting a rewrite; the gap goes into the checklist as a blocker.
- **Competitor docs unreachable during the build:** the cell is marked "not verified", which is a blocker.

## Testing

- **API (pytest, TDD):** the version comparison; the check with `httpx.MockTransport` for success, 404, errors, bad tags and an oversized body; the 24 h and 1 h timing with `FakeClock`; job registration for each value of the setting; the endpoint for an admin, a viewer and the check turned off; the route-permission test; migration up and down. Tests never reach the network.
- **Web:** Vitest for the footer's text helpers. Playwright: the version shows for an admin and a viewer; the pill's states are added to the UI kit page, which the visual and axe matrix already covers at all six widths in both themes. The version text is masked in snapshots, and baselines are refreshed with `pnpm e2e:docker --update-snapshots`.
- **Site:** unit tests for the sync script's link rewriting; the `site` CI job (build, link check, palette test).
- **Quick start:** `scripts/quickstart_test.sh` runs the documented commands against locally built images and fails if the time from `docker compose up` to the first evaluated flag is five minutes or more. It runs in CI.
- **Proxy samples:** `scripts/proxy_samples_test.sh` puts each of the four proxies in front of the stack and checks that the UI answers and that a stream delivers a flag change promptly and survives longer than one heartbeat. It runs in CI.
- **Load tool:** Go tests for the rate scheduler, percentiles and the SSE reader against `httptest` servers; pytest for the report renderer. A `perf-smoke` CI job runs `scripts/perf.sh` in a tiny profile with no latency gates, so the suite keeps working. The full run happens once on the build machine and its report is committed.
- **Workflows:** `actionlint` on the changed and new workflow files.

## Out of scope

- Telemetry of any kind, and the collector.
- The 1.0.0 version bump, the release pull request, tags and publishing; SDK, CLI and Terraform version changes.
- Versioned docs, translations, a custom domain, a blog or comparison page, in-app links to the docs.
- A Settings page: with telemetry gone, nothing in this task needs one.
- Performance rewrites beyond tuning settings; availability targets (covered by the MF-86 failure suite, which the checklist cites).
- Editing the PRD in the Knowledge Base, and repository settings (Pages, reporting): maintainer steps listed in the checklist.
