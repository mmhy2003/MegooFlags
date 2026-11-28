# MF-461 design: Helm chart fixes for long release names, ServiceMonitor labels, worker replicas and `/metrics`

## Goal

A release with a long name (up to Helm's own limit of 53 characters) installs and runs, instead of failing on Kubernetes name limits. Three smaller surprises go away: a ServiceMonitor label that collides with a chart label, a `worker.replicas` value that is silently ignored, and a sign-in redirect on `/metrics`.

## Decisions

| # | Topic | Decision | Why, and what was rejected |
|---|---|---|---|
| 1 | Long release names | Cut the base name to **41 characters** (you chose this). | 41 is what the longest case leaves: a StatefulSet name may have 52 characters, because Kubernetes puts `<name>-<hash of up to 10>` in the 63-character `controller-revision-hash` label, and `-postgresql` takes 11. Rejected: cut plus an 8-character hash (names hard to read and to guess); failing over 41 characters (a 50-character release name would be refused, against the task's "done when"); a limit per role (the name would change with `role` or `postgresql.enabled`). |
| 2 | ServiceMonitor label collision | Merge into one map; **the user's label wins** (you chose this). | These labels exist so that the user's Prometheus selects the monitor. Nothing in the chart selects on them. Rejected: the chart's label wins, which drops the user's value silently. |
| 3 | `worker.replicas` with `worker.nodeTokens` | **Fail the render** when both are set and differ (you chose this). | A template cannot warn during a render. A `NOTES.txt` warning is never shown by Argo CD, Flux or `helm template`. |
| 4 | Health paths | Fix the comment and the docs only. | The Ingress keeps `Prefix` for `/healthz` and `/readyz` (the MF-88 decision for Traefik stands). The HTTPRoute keeps `Exact`. |
| 5 | `/metrics` on a control plane | **The web app answers 404** (you chose this). | An Ingress or HTTPRoute has no portable way to answer 404 by itself. The Compose bundle already answers 404 (Caddy). Rejected: keeping the redirect and documenting it. |
| 6 | Where the render tests go | `scripts/tests/test_helm_chart.py`. | The task names `scripts/helm_test.sh`, but that script is the kind end-to-end test. The chart's render tests live in the pytest file, and the CI `helm` job runs both. |
| 7 | Release names in the kind e2e | Both releases (control plane and workers) get 50-character names. | This covers the PostgreSQL StatefulSet, both migration Jobs, the worker ordinal and the headless Service. Short names stay covered by the render tests (release `mf`), where the cut does nothing. Rejected: a third release, which adds minutes to CI and does not cover the workers or the upgrade. |

## Architecture

### 1. Base name (`templates/_helpers.tpl`, `megooflags.fullname`)

The rule for the base stays: the release name when it contains `megooflags`, else `<release>-megooflags`. The cut changes from 63 to 41 characters, and then **every** trailing `-` or `.` is trimmed (today only one `-` is). Every template already takes its names from this helper, so no other template changes for this item.

Name budget at a 41-character base:

| Resource | Name | Limit | Longest |
|---|---|---|---|
| PostgreSQL StatefulSet | `<base>-postgresql` | 52 | 52 |
| Worker StatefulSet | `<base>-worker` | 52 | 48 |
| Worker pod (its hostname gives the token ordinal) | `<base>-worker-<n>` | 63 | 49 plus the ordinal's digits |
| Headless Service | `<base>-worker-headless` | 63 | 57 |
| Migration Job | `<base>-migrate-<revision>` | 63 | 50 plus the revision's digits (room for 13) |
| Other Services, the ConfigMap, the Secret, the Ingress, the HTTPRoute, the ServiceMonitor | `<base>` plus at most 11 | 63 or more | 52 |

Deployment pods need nothing: Kubernetes shortens generated pod names itself. The worker's `${HOSTNAME##*-}` stays as it is, because the pod name now always fits in a hostname.

Names of 41 characters or fewer do not change, so every install with a release name of up to 30 characters is untouched.

### 2. ServiceMonitor labels (`templates/servicemonitor.yaml`)

Build one map: the chart's labels (`megooflags.labels`, read with `fromYaml`), overwritten by `metrics.serviceMonitor.labels` (`mergeOverwrite`), and print it once with `toYaml`. `spec.selector.matchLabels` is not touched, so the monitor still selects the api or worker Service.

### 3. Worker replicas (`templates/_validate.tpl`)

A new check in the `role: worker` branch: when `worker.nodeTokens` is not empty, `worker.replicas` is above 0, and it differs from the number of tokens, fail with:

`worker.replicas (3) must equal the number of worker.nodeTokens (2), or be left at 0`

The numbers are the real values. The default `replicas: 0` with tokens stays valid, and so does an equal value. The replica count in `worker.yaml` is computed as before.

### 4. Health paths (comments and docs)

- `_helpers.tpl`, the `megooflags.apiPaths` comment: the list holds the Caddyfiles' types. The HTTPRoute uses them as they are; the Ingress makes every path `Prefix` (the reason is in `ingress.yaml`).
- README, Routing: one sentence. In the Ingress `/healthz` and `/readyz` are `Prefix` paths, so `/healthz/x` reaches the API (which answers 404). In the HTTPRoute they match exactly.

### 5. `/metrics` (`web/proxy.ts`)

Add `metrics` to the paths the proxy's matcher skips, next to `healthz` and `readyz`. The sign-in redirect then does not run for `/metrics`, and Next.js answers 404, because no page has that path. This holds behind any proxy, not only the chart's routes. Like the other entries, it is a prefix match, so `/metrics...` is skipped too; no page starts with it.

The comments "`/metrics` is never routed" in `ingress.yaml` and `httproute.yaml` become: it falls to the web UI, which answers 404.

### 6. Docs

`deploy/helm/megooflags/README.md` is the only source. The docs site builds `deploy/kubernetes.md` from it (`site/sync.manifest.json`), so one edit updates both.

- A new short **Names** section: how the base name is built, the 41-character cut with one example, the advice to keep release names at 30 characters or fewer so that names are not cut, the note that two long release names with the same first 41 characters cannot share a namespace, and `kubectl get all -l app.kubernetes.io/instance=<release>` to see the real names. The places that write `<release>-megooflags-...` point to it once.
- Values table, `worker`: `replicas` must be 0 or equal to the number of `nodeTokens`.
- Metrics: a label in `serviceMonitor.labels` replaces a chart label with the same key; `/metrics` on the public address answers 404.
- Routing: the sentence from item 4.

`CHANGELOG.md`, under `[Unreleased]`:

- **Changed** (first, with the upgrade steps): resource names are built from at most 41 characters of the base name. This only affects a 1.0.0 install whose base name has 42 characters or more and that works today, which is possible only with an external database or a worker release. The upgrade creates its resources under the shorter names and removes the old ones. Before upgrading, update what names the old Services (`worker.controlPlaneUrl` of worker releases, URLs in an `existingSecret`). After upgrading a worker release, delete the old `data-<old name>-worker-<n>` volumes.
- **Changed**: `helm install` and `helm upgrade` fail when `worker.replicas` and `worker.nodeTokens` disagree.
- **Fixed**: long release names install; a ServiceMonitor label that repeats a chart label no longer breaks the manifest; `/metrics` on the web app answers 404 instead of redirecting to sign-in.

## Data and interfaces

- No new value and no change to `values.yaml` or `values.schema.json`.
- One new validation message (item 3).
- Resource names change only for base names above 41 characters.
- The web app's `/metrics` answers 404 instead of a 307 redirect.

## Error handling and edge cases

- **The cut lands on a `-` or a `.`**: all trailing ones are trimmed, so no name has `--` before its suffix or ends its base with a dot.
- **Two long release names with the same first 41 characters in one namespace**: Helm refuses the second install ("already exists", owned by another release). Nothing is overwritten. Documented in Names.
- **A release name with a dot inside** breaks Service names today and still does. Not changed here.
- **`existingSecret` together with `nodeTokens`**: the tokens in the values are not used for the Secret, as today. The new check still applies to `replicas`.
- **Label values**: `app.kubernetes.io/instance` keeps the full release name (at most 53 characters, inside the 63 limit), so selectors do not change for any install.
- **User labels on the ServiceMonitor** can now replace `app.kubernetes.io/managed-by` or `helm.sh/chart`. That is the chosen behaviour.

## Testing

**Render tests** (`scripts/tests/test_helm_chart.py`; the helper gets an optional release name, default `mf`):

1. Long names, for the three roles, with a 50-character release name and a 53-character one that contains `megooflags`:
   - every StatefulSet name has at most 52 characters, every other name at most 63;
   - the Job name leaves room for a 5-digit revision, and the worker pod name for a 5-digit ordinal;
   - references still match: the database and Valkey URLs in the Secret, the web's `MEGOOFLAGS_API_URL`, the Ingress and HTTPRoute backends and each StatefulSet's `serviceName` all name a Service that is rendered.
2. A release name whose 41st character is `-`: no name contains `--`.
3. A base name of exactly 41 characters is not cut. The existing tests with `mf` prove short names are unchanged.
4. ServiceMonitor with `labels: {app.kubernetes.io/name: custom, release: prom}`: each key appears once in the raw output (checked on the text, because `yaml.safe_load` accepts duplicate keys), the user's value is used, the other chart labels remain, and `spec.selector` is unchanged.
5. `worker.replicas: 3` with two tokens fails with the message; `replicas: 2` with two tokens renders two replicas.
6. Paths: the existing `test_ingress_paths_match_caddyfile` and `test_httproute_paths_match_caddyfile` already pin Prefix and Exact. No new test, only the comment and docs.

**Web unit test** (`web/tests/unit/proxy-matcher.test.ts`): the matcher skips `/metrics`.

**kind e2e** (`scripts/helm_test.sh`):

- The two release names become variables holding 50-character names. The script checks their length and derives each base name with the same rule (first 41 characters of the base, trailing `-` trimmed). Every hard-coded `cp-megooflags-...` and `workers-megooflags-...` uses these variables: the worker's `controlPlaneUrl`, the HTTPRoute, the rollout and scale commands, the pod label selector, and the `migrate-1` and `migrate-2` Jobs.
- The existing steps then prove the fix: `helm install --wait` passes with the bundled PostgreSQL StatefulSet, both worker pods become Ready (each read its own token by ordinal), and the upgrade runs `<base>-migrate-2`.
- Step 4 checks that `/metrics` on the control plane answers **404** through the Ingress and the Gateway, as step 7 already does for the workers. The check that the body holds no Prometheus metrics stays.

**Gate**: the usual checks for the touched parts: the helm render tests with `MEGOOFLAGS_REQUIRE_HELM=1`, `bash scripts/helm_test.sh`, the scripts suite, web lint, typecheck and Vitest, the docs site tests (the README is synced), and `bash -n` on the script.

## Out of scope

- `nameOverride` and `fullnameOverride` values.
- Release names with a dot.
- Reading the worker ordinal from the pod-index label instead of the hostname.
- A warning when `nodeTokens` is set together with `existingSecret`.
- Changing the Ingress health paths back to `Exact`.
- A version bump or a release: the entries go under `[Unreleased]`.
