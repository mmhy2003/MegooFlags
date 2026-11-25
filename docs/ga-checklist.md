# GA checklist for 1.0.0

This checklist decides whether `v1.0.0` can be tagged. It is built from the product requirements
("PRD: MegooFlags — Open Source Feature Flag Platform", revision 2, in the Knowledge Base) and checked
against the code at `0.1.0` on 2026-11-18. Statuses are **done**, **partial** or **missing**. Evidence is a
file, a test or a `ci.yml` job; the API tests run in the `api-test` job and the web end-to-end tests in
the `web` job.

**Verdict: not ready.** Section 6 lists the blockers.

## 1. P0 requirements

### Flag management (PRD 6.1)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| F-1 | Create, edit, archive and delete flags with key, name, description, tags and owner | done | `api/src/megooflags_api/models/flag.py`, `api/tests/test_flags.py`, `web/e2e/flag-create.spec.ts`, `web/e2e/flags-list.spec.ts` |
| F-2 | Boolean, string, number and JSON flags | done | `api/src/megooflags_api/models/enums.py`, `api/tests/test_flag_values.py`, `web/e2e/flag-create.spec.ts` |
| F-3 | Several variations per flag with a default (off) variation | done | `api/tests/test_variations.py`, `api/tests/test_evaluation_engine.py` |
| F-4 | Per-environment state with independent rules | done | `api/tests/test_flag_config.py`, `web/e2e/flag-detail.spec.ts` |
| F-5 | Projects group flags, environments and members | done | `api/tests/test_projects.py`, `api/tests/test_environments.py`, `web/e2e/shell.spec.ts` (project switcher) |
| F-6 | Kill switch per environment, propagated to SDKs in under 5 s | partial | `api/tests/test_flag_actions.py`, `web/e2e/flag-detail.spec.ts`; propagation p95 is 5.31 s across 3 API replicas in [`perf/REPORT.md`](../perf/REPORT.md) (0.62 s through workers) |

### Targeting and rollouts (PRD 6.2)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| T-1 | Evaluation context with arbitrary attributes | done | `spec/evaluation.md`, `api/tests/test_evaluation_engine.py` |
| T-2 | Individual targeting by key | done | `api/tests/test_evaluation_engine.py`, `api/tests/test_copy.py` |
| T-3 | Operators: equals, in, contains, starts/ends with, regex, numeric, semver, date before/after | done | `api/src/megooflags_api/models/enums.py` (equals is `in` with one value), `api/tests/test_evaluation_operators.py`, `api/tests/test_evaluation_parse.py` |
| T-4 | Reusable segments shared across flags | done | `api/tests/test_segments.py`, `web/e2e/segments.spec.ts` |
| T-5 | Sticky, deterministic percentage rollouts with a configurable bucketing attribute | done | `api/tests/test_evaluation_bucketing.py`, `spec/conformance/bucketing.json` |

### SDKs and evaluation (PRD 6.3)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| S-1 | Server SDKs with local evaluation and streaming: Go, Node.js, Python, Java (P0) | done | `sdks/go`, `sdks/node`, `sdks/python`, `sdks/java`; jobs `sdk-go`, `sdk-node`, `sdk-python`, `sdk-java`; p99 under 1 ms in [`perf/REPORT.md`](../perf/REPORT.md) |
| S-2 | Client SDKs with remote evaluation: JavaScript and React (P0) | done | `sdks/js`, `sdks/react`; jobs `sdk-js`, `sdk-react` |
| S-3 | Every SDK is an OpenFeature provider | done | `sdks/README.md` (status table), `sdks/go/provider.go` |
| S-4 | OFREP-compatible evaluation endpoint | done | `api/src/megooflags_api/routers/ofrep.py`, `api/tests/test_ofrep.py` |
| S-5 | Updates over server-sent events with a polling fallback | done | `spec/delivery.md`, `api/tests/test_stream.py`, `sdks/go/internal/delivery/poller.go`, `sdks/node/src/internal/delivery/poller.ts`, `sdks/python/src/megooflags/_delivery/poller.py`, `sdks/js/src/internal/poller.ts` |
| S-6 | Offline mode and bootstrap from a file or the last known config | done | `sdks/go/README.md` ("Offline and bootstrap"), `sdks/node/README.md`, `sdks/python/README.md`, `sdks/java/README.md`, `sdks/js/README.md` |
| S-7 | Evaluation spec and conformance suite shared by all SDKs | done | `spec/conformance/README.md`, `spec/client/README.md`, `api/tests/test_conformance.py`, `sdks/go/conformance_test.go`, `sdks/node/test/conformance.test.ts`, `sdks/python/tests/test_conformance.py`, `sdks/java/src/test/java/io/github/mmhy2003/megooflags/ConformanceTest.java`, `sdks/js/test/shared-resolution.test.ts` |

### Governance and security (PRD 6.4)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| G-1 | Users, teams and roles with RBAC at org, project and environment level; custom roles | done | `api/tests/test_rbac.py`, `api/tests/test_roles_bindings.py`, `api/tests/test_teams.py`, `api/tests/test_route_permissions.py`, `web/e2e/settings.spec.ts` (members, teams, roles) |
| G-2 | Audit log of every change with a before/after diff, exportable | done | `api/tests/test_audit_coverage.py`, `api/tests/test_audit_query.py` (NDJSON and CSV export), `web/e2e/audit.spec.ts` |
| G-3 | SSO with OIDC and SAML 2.0, just-in-time provisioning and group mapping | done | `api/tests/test_sso_oidc.py`, `api/tests/test_sso_saml.py`, `api/tests/test_sso_keycloak.py`, `deploy/keycloak`, `web/e2e/settings.spec.ts` (SSO tab) |
| G-5 | API tokens scoped by project, environment and permission; separate server and client SDK keys | done | `api/tests/test_service_tokens.py`, `api/tests/test_personal_tokens.py`, `api/tests/test_sdk_keys.py`, `api/tests/test_sdk_auth.py`, `web/e2e/keys.spec.ts`, `web/e2e/account-tokens.spec.ts` |

### Developer experience (PRD 6.5)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| D-1 | REST API covering every UI action, with an OpenAPI spec | done | `api/openapi.json`, `api/tests/test_openapi.py`, `web/lib/api/schema.d.ts` (the web app calls only the API) |

### Web UI (PRD 6.7 and 6.8)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| UI-1 | Light, dark and system themes with no flash, saved per user | done | `web/e2e/preferences.spec.ts` |
| UI-2 | All colours come from tokens in `globals.css` | done | `web/app/globals.css`, `web/e2e/palette.spec.ts` |
| UI-3 | WCAG 2.1 AA in both themes, checked with axe in CI | done | `web/e2e/matrix.spec.ts` |
| UI-4 | Status pairs colour with an icon and a label | done | `web/e2e/kit.spec.ts` ("status pills pair an icon with their label") |
| UI-8 | App shell with a left sidebar and a top header, built with the shadcn/ui `Sidebar` | done | `web/components/ui/sidebar.tsx`, `web/e2e/shell.spec.ts` |
| UI-9 | Sidebar collapses to icons, saved per user; a drawer on small screens | done | `web/e2e/preferences.spec.ts`, `web/e2e/shell.spec.ts` |
| UI-10 | Header with search and ⌘K/Ctrl+K, theme switcher and account menu; fixed on scroll | done | `web/e2e/palette.spec.ts`, `web/e2e/preferences.spec.ts` |
| UI-12 | Responsive from 280 px with no horizontal page scrolling | done | `web/e2e/matrix.spec.ts` (280, 344, 390, 880, 1024 and 1440 px) |
| UI-13 | Folding, unfolding or rotating keeps input, scroll and open dialogs | done | `web/e2e/kit.spec.ts`, `web/e2e/flags-mobile.spec.ts`, `web/e2e/flag-create.spec.ts` |
| UI-14 | Tables (flags, segments, audit log, nodes) become cards on narrow screens | done | flags, audit log and nodes: `web/e2e/flags-mobile.spec.ts`, `web/e2e/audit.spec.ts` ("cards under 768 px"), `web/e2e/nodes.spec.ts`; segments: `web/e2e/segments.spec.ts` ("cards under 768 px") |
| UI-15 | On 344 px: toggle a flag, kill switch, rollout, approve or reject a change request, search | done | toggle, kill switch, rollout and search: `web/e2e/flags-mobile.spec.ts`; approve and reject a change request: `web/e2e/change-requests.spec.ts` ("approve and reject at 344 px") |
| UI-16 | 44 × 44 px touch targets, no hover-only controls, 16 px inputs | done | `web/components/ui/input.tsx`, `web/e2e/kit.spec.ts`, `web/e2e/flags-mobile.spec.ts` |
| UI-19 | Playwright visual and accessibility checks at six widths in both themes | done | `web/e2e/matrix.spec.ts`, `web/e2e/matrix.spec.ts-snapshots` |

### TLS modes and topologies (PRD 8.3 and 8.5)

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| DEP-1 | TLS mode chosen with environment variables only | done | `deploy/Caddyfile`, `scripts/smoke.sh` (`smoke` job, `http` and `https`) |
| DEP-2 | `MEGOOFLAGS_PUBLIC_URL` sets the external URL | done | `api/src/megooflags_api/urls.py`, `api/tests/test_urls.py` |
| DEP-3 | Forwarded headers trusted only from `MEGOOFLAGS_TRUSTED_PROXIES` | done | `deploy/Caddyfile`, `api/tests/test_urls.py`, `scripts/proxy_samples_test.sh` (`proxies` job) |
| HA-1 | Single node and multi-node from the same images, chosen with `MEGOOFLAGS_ROLE` | done | `deploy/docker-compose.control-plane.yml`, `deploy/docker-compose.worker.yml`, `scripts/tests/test_compose_files.py`, `scripts/cluster_test.sh` (`cluster` job) |
| HA-2 | Per-node tokens, revocable and rotatable, over TLS | done | `api/tests/test_cluster_nodes.py`, `api/tests/test_nodes_api.py`, `api/tests/test_cluster_signing.py` |
| HA-3 | Workers serve from memory, persist to disk and keep serving without the control plane | done | `api/tests/test_worker_store.py`, `api/tests/test_worker_app.py` (`test_ready_from_disk_without_control_plane`), `api/tests/cluster/test_outages.py` |
| HA-4 | Changes reach every worker within 5 s; workers that missed updates resync | done | `api/tests/test_worker_client.py`, `api/tests/cluster/test_sync.py`; propagation p95 0.62 s to workers in [`perf/REPORT.md`](../perf/REPORT.md) |
| HA-7 | Worker health and readiness; not ready until it has a snapshot | done | `api/tests/test_worker_app.py` (`test_not_ready_without_snapshot_and_sdk_503`), `api/tests/test_readyz.py` |

### The GA row of the release plan (PRD 11)

| Item | Status | Evidence |
|---|---|---|
| All P0 items | partial | the partial rows above |
| Change requests | done | API: `api/tests/test_change_requests.py`, `api/tests/test_approvals_gate.py`; the web flag page opens requests (`web/e2e/flag-detail.spec.ts`) and the Change requests page reviews them (`web/e2e/change-requests.spec.ts`) |
| Scheduled changes | done | `api/src/megooflags_api/routers/schedules.py`, `api/tests/test_schedules.py`, `web/e2e/flag-automation.spec.ts`; F-10 scheduled changes and T-6 progressive rollouts: `web/e2e/flag-automation.spec.ts` (also at 344 px with touch) |
| CLI | done | `cli/README.md`, `cli/e2e/e2e_test.go` (`cli` job) |
| Terraform provider | done | `terraform/main.go`, `terraform/examples` (`terraform` job) |
| Multi-node deployment replacing the relay | done | HA rows above, `scripts/cluster_test.sh`, `scripts/cluster_failures.py` (`cluster` job) |
| Mobile SDKs | done | `sdks/ios`, `sdks/android`, `sdks/flutter`, `sdks/react-native`; jobs `sdk-ios`, `sdk-android`, `sdk-flutter`, `sdk-react-native` |
| Importers from Unleash, Flagsmith and LaunchDarkly | done | `cli/internal/importers/unleash`, `cli/internal/importers/flagsmith`, `cli/internal/importers/launchdarkly` |

## 2. NFR verdicts

From [`perf/REPORT.md`](../perf/REPORT.md) (the `full` run of 2026-11-17 on 16 CPUs and 32 GB, results in
`perf/results/0.1.0.json`):

| NFR | Target | Measured | Verdict |
|---|---|---|---|
| Local evaluation, Go, Node.js, Python, Java, .NET, Ruby, PHP | p99 < 1 ms | every SDK's guard passed (highest p99: Node.js, 1 ms) | pass |
| Remote evaluation, OFREP | p99 < 50 ms at 2000 rps per API container | p99 266.1 ms, 0 errors at 2000 rps, 8 Uvicorn workers | fail |
| Remote evaluation, `/sdk/v1/evaluate` | p99 < 50 ms at 2000 rps per API container | p99 29 632.4 ms, 44 errors, only 1331 rps reached | fail |
| Propagation across API replicas | p95 < 5 s | p95 5.31 s, 0 of 600 updates missed | fail |
| Propagation to worker nodes | p95 < 5 s | p95 0.62 s, 0 of 600 updates missed | pass |
| 100k streams per cluster | 100 000 streams, none dropped, p95 < 5 s, 512 MiB per worker | 17 721 streams on 12 workers, 0 dropped, p95 4.56 s, up to 469.9 MiB | reduced |
| Single-node footprint (2 vCPU / 2 GB) | no restart or OOM kill, p99 < 50 ms, p95 < 5 s | 0 restarts, p99 674.6 ms at 50 rps, p95 0.71 s with 230 of 12 000 updates missed | fail |
| Worker footprint (1 vCPU / 512 MB) | as the 100k scenario | inside the limit at 17 721 streams | reduced |
| 10k flags per project | the gates above on a 10k-flag project | measured on 10 000 flags; ruleset 4.6 MiB, SDK start 4354.7 ms | as above |

**Time to first flag.** `bash scripts/quickstart_test.sh`, run twice on 2026-11-18, printed
`quick start: 30s` and `quick start: 29s`: from
`docker compose up -d` to the first flag evaluated through OFREP, with the images already built locally
(the `quickstart` job measures the same, and fails at five minutes). Downloading the release images and
reading the page are not included.

## 3. Competitor matrix

PRD section 5, re-checked on 2026-11-18 against each project's public docs, for the free self-hosted
edition. "PRD" is the value in the PRD; **bold** marks a changed cell. No source was unreachable.

### Unleash (open source)

| Capability | PRD | Now | Source (checked 2026-11-18) |
|---|---|---|---|
| Boolean & multivariate flags | ✅ | ✅ | https://docs.getunleash.io/reference/sdks |
| Segments / targeting rules | ✅ | **Partial**: "advanced segmentation" and extra strategy constraints are listed for paid plans | https://github.com/Unleash/unleash |
| Percentage rollouts | ✅ | ✅ | https://github.com/Unleash/unleash |
| Local evaluation in SDKs | ✅ | ✅ | https://docs.getunleash.io/understanding-unleash/unleash-overview |
| OpenFeature providers | Partial | Partial: Node.js, PHP, Python, Ruby, Rust, Swift and Android only | https://docs.getunleash.io/reference/sdks |
| Experimentation / A/B stats | Limited | Limited | https://www.getunleash.io/pricing |
| SSO / RBAC in free edition | ❌ Paid | ❌ Paid | https://www.getunleash.io/pricing |
| Change requests / approvals free | ❌ Paid | ❌ Paid | https://www.getunleash.io/pricing |
| GitOps (flags as code) | Partial | Partial: API automation and export/import only | https://github.com/Unleash/unleash |
| Single-binary deploy | ❌ | ❌ | https://docs.getunleash.io/understanding-unleash/unleash-overview |

### Flagsmith (open source)

| Capability | PRD | Now | Source (checked 2026-11-18) |
|---|---|---|---|
| Boolean & multivariate flags | ✅ | ✅ | https://docs.flagsmith.com/managing-flags/core-management |
| Segments / targeting rules | ✅ | ✅ | https://docs.flagsmith.com/flagsmith-concepts/segments |
| Percentage rollouts | ✅ | ✅ (`% Split` rule, weighted variants) | https://docs.flagsmith.com/flagsmith-concepts/segments |
| Local evaluation in SDKs | ✅ | ✅ | https://docs.flagsmith.com/integrating-with-flagsmith/CLI |
| OpenFeature providers | ✅ | ✅ | https://docs.flagsmith.com/integrating-with-flagsmith/openfeature |
| Experimentation / A/B stats | Limited | **Partial**: Bayesian experimentation (beta) in the open source edition, with your own warehouse | https://docs.flagsmith.com/experimentation/self-hosting |
| SSO / RBAC in free edition | Partial | **❌ Paid** for SSO (SAML and LDAP in the Enterprise image); roles and permissions listed as paid governance | https://docs.flagsmith.com/deployment-self-hosting/enterprise-edition |
| Change requests / approvals free | ❌ Paid | ❌ Paid | https://docs.flagsmith.com/deployment-self-hosting/enterprise-edition |
| GitOps (flags as code) | Partial | Partial: CLI with a checked-in `flagsmith.json`, no declarative apply | https://docs.flagsmith.com/integrating-with-flagsmith/CLI |
| Single-binary deploy | ❌ | ❌ | https://docs.flagsmith.com/deployment-self-hosting/ |

### GrowthBook (open source)

| Capability | PRD | Now | Source (checked 2026-11-18) |
|---|---|---|---|
| Boolean & multivariate flags | ✅ | ✅ | https://www.growthbook.io/pricing |
| Segments / targeting rules | ✅ | ✅ | https://github.com/growthbook/growthbook |
| Percentage rollouts | ✅ | ✅ | https://www.growthbook.io/pricing |
| Local evaluation in SDKs | ✅ | ✅ | https://docs.growthbook.io/lib/ |
| OpenFeature providers | ✅ | ✅ | https://docs.growthbook.io/lib/openfeature.md |
| Experimentation / A/B stats | ✅ Strong | **Partial**: Bayesian, frequentist and SRM checks are free; sequential testing, CUPED, bandits and holdouts are Enterprise | https://www.growthbook.io/pricing |
| SSO / RBAC in free edition | Partial | Partial | https://www.growthbook.io/pricing |
| Change requests / approvals free | Partial | **❌ Paid**: flag approval workflows are Enterprise only | https://www.growthbook.io/pricing |
| GitOps (flags as code) | Partial | Partial: CLI over the REST API, no Git sync | https://docs.growthbook.io/tools/cli.md |
| Single-binary deploy | ❌ | ❌ (one image plus MongoDB) | https://docs.growthbook.io/self-host |

### Flipt (v2, the current version)

| Capability | PRD | Now | Source (checked 2026-11-18) |
|---|---|---|---|
| Boolean & multivariate flags | ✅ | ✅ | https://www.flipt.io/pricing |
| Segments / targeting rules | ✅ | ✅ | https://github.com/flipt-io/flipt |
| Percentage rollouts | ✅ | ✅ | https://github.com/flipt-io/flipt |
| Local evaluation in SDKs | ✅ | ✅ | https://github.com/flipt-io/flipt |
| OpenFeature providers | ✅ | ✅ (OpenFeature and OFREP) | https://github.com/flipt-io/flipt |
| Experimentation / A/B stats | ❌ | ❌ | https://docs.flipt.io/v2/ |
| SSO / RBAC in free edition | ✅ | **Partial**: OIDC, JWT and OAuth are free; no RBAC or SAML is documented | https://github.com/flipt-io/flipt |
| Change requests / approvals free | ❌ | **❌ Paid**: merge proposals are a Pro feature | https://docs.flipt.io/v2/ |
| GitOps (flags as code) | ✅ Strong | ✅ Strong | https://docs.flipt.io/v2/ |
| Single-binary deploy | ✅ | ✅ | https://docs.flipt.io/v2/ |

The Flipt v2 server is under the Fair Core License (MIT future license), not an OSI licence.

### FeatBit (open source)

| Capability | PRD | Now | Source (checked 2026-11-18) |
|---|---|---|---|
| Boolean & multivariate flags | ✅ | ✅ | https://docs.featbit.co/ |
| Segments / targeting rules | ✅ | ✅ (shareable segments need a licence) | https://docs.featbit.co/licenses/overview |
| Percentage rollouts | ✅ | ✅ | https://github.com/featbit/featbit |
| Local evaluation in SDKs | ✅ | ✅ | https://docs.featbit.co/sdk/overview |
| OpenFeature providers | ✅ | ✅ | https://docs.featbit.co/sdk/overview |
| Experimentation / A/B stats | Limited | Limited | https://www.featbit.co/pricing |
| SSO / RBAC in free edition | ✅ | **Partial**: SSO is Enterprise only; fine-grained access control needs a licence | https://docs.featbit.co/integrations/single-sign-on/single-sign-on |
| Change requests / approvals free | ✅ | **❌ Paid**: flag change requests need a licence key | https://docs.featbit.co/licenses/overview |
| GitOps (flags as code) | Partial | **✅**: flags, segments and environments as Terraform code | https://docs.featbit.co/integrations/terraform-provider |
| Single-binary deploy | ❌ | ❌ | https://github.com/featbit/featbit |

### MegooFlags

The MegooFlags column holds: OpenFeature providers in every SDK (S-3), SSO and RBAC (G-1, G-3) and change
requests (`api/tests/test_change_requests.py`) in the open source edition, flags as code
(`cli/internal/flagfiles`, `cli/internal/sync`) and the one-command Compose bundle (`deploy/docker-compose.yml`).
Experimentation stays in Phase 2. The changes above make the "everything open" message stronger: none of
the five offers free approvals any more (GrowthBook and FeatBit moved them to paid plans), and none offers
both SSO and RBAC in full for free. Flagsmith and GrowthBook narrowed the gap on free experimentation.

## 4. Differences from the PRD

- **No telemetry.** MegooFlags collects no usage data (the maintainer's decision for this release), so the
  PRD 10 metric "monthly active self-hosted instances (opt-in telemetry)" cannot be measured. Update
  notices ask GitHub for the latest release and send nothing about the instance
  (`api/src/megooflags_api/services/updates.py`, off with `MEGOOFLAGS_UPDATE_CHECK=false`).
- **The bundle also runs Valkey.** PRD 8.2 lists Caddy, web, migrate, API and PostgreSQL; the bundle adds
  Valkey for the fan-out of flag changes between API replicas (open question 4) in
  `deploy/docker-compose.yml`.
- **SDKs are versioned apart and still Alpha.** Every SDK is at 0.1.0 (Alpha) with its own tags
  (`sdks/README.md`), which answers open question 6.
- **No separate `equals` operator.** T-3's "equals" is `in` with one value
  (`api/src/megooflags_api/models/enums.py`).
- **Remote evaluation needs 8 Uvicorn workers.** The report's remote runs use 8 workers in one container
  with no CPU limit; the PRD names no worker count or container size.
- **Docs on GitHub Pages.** The site is at https://mmhy2003.github.io/MegooFlags/, built from `site/` by
  `.github/workflows/docs.yml`.

## 5. Maintainer steps

1. Enable GitHub Pages with the source "GitHub Actions" (Settings → Pages).
2. Enable private vulnerability reporting (Settings → Code security) and content reports (Settings →
   Moderation options → Reported content), which `SECURITY.md` and `CODE_OF_CONDUCT.md` rely on.
3. Decide the versions for GA of the SDKs, the CLI and the Terraform provider (all at 0.1.0 today; see
   `sdks/RELEASING.md` and AGENTS.md section 12).
4. Update the PRD section 5 matrix in the Knowledge Base with section 3 above.
5. Run `.github/workflows/docs.yml` once (Actions → Docs → Run workflow). The README links to the docs
   site return 404 until then.
6. When the blockers below are closed: bump to 1.0.0 as AGENTS.md section 5 describes (`VERSION`,
   `api/pyproject.toml`, `web/package.json` and `CHANGELOG.md` in one commit), open the release pull
   request, and after it merges tag `v1.0.0`.

## 6. Open blockers

1. **Remote evaluation misses its target.** OFREP p99 266.1 ms and `/sdk/v1/evaluate` p99 29.6 s (1331 rps,
   44 errors) against p99 < 50 ms at 2000 rps per container ([`perf/REPORT.md`](../perf/REPORT.md)). Tuning
   settings did not close it; it needs work on the evaluation path or a changed target.
2. **Propagation across API replicas** is p95 5.31 s against < 5 s, which also leaves F-6 partial.
3. **The single-node footprint** fails: p99 674.6 ms at 50 rps, and 230 of 12 000 updates missed.
4. **100k streams per cluster ran reduced** (17 721 streams on 12 workers on this host). A full-size run on
   a larger host is still needed.

No competitor matrix cell is unverified.
