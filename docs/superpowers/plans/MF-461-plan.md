# MF-461 plan: Helm chart fixes for long release names, ServiceMonitor labels, worker replicas and `/metrics`

## Goal

A release with a name of up to 53 characters installs and runs. A ServiceMonitor label that repeats a chart label, a `worker.replicas` that disagrees with `worker.nodeTokens`, and `/metrics` on the control plane all behave as the approved design says.

## Architecture

All resource names come from one helper, `megooflags.fullname` in `deploy/helm/megooflags/templates/_helpers.tpl`. Cutting its result to 41 characters and trimming every trailing `-` or `.` fixes every name at once. The ServiceMonitor builds one label map (the user's labels win), and `_validate.tpl` gets one new `fail`. The web proxy's matcher skips `/metrics`, so Next.js answers 404. The kind e2e (`scripts/helm_test.sh`) runs both releases with 50-character names. The docs live in the chart README, which the docs site syncs to `deploy/kubernetes.md`.

## File map

| File | Change |
|---|---|
| `deploy/helm/megooflags/templates/_helpers.tpl` | `megooflags.fullname`: cut to 41 characters, trim all trailing `-`/`.`; fix the comments of `fullname` and `apiPaths` |
| `deploy/helm/megooflags/templates/servicemonitor.yaml` | Merge the chart labels with `metrics.serviceMonitor.labels` (the user wins) and print the map once |
| `deploy/helm/megooflags/templates/_validate.tpl` | Fail when `worker.replicas` > 0, tokens are set, and the two differ |
| `deploy/helm/megooflags/templates/ingress.yaml`, `httproute.yaml` | The "/metrics is never routed" comment becomes "falls to the web UI, which answers 404" |
| `web/proxy.ts` | Add `metrics` to the matcher's skipped prefixes |
| `web/tests/unit/proxy-matcher.test.ts` | Test that the matcher skips `/healthz`, `/readyz` and `/metrics` |
| `scripts/tests/test_helm_chart.py` | A `release` parameter in the render helpers; tests for long names, the `--` trim, the exact-41 base, ServiceMonitor labels, and replicas vs tokens |
| `scripts/helm_test.sh` | 50-character release names, derived base names, and a `/metrics` 404 check on the control plane |
| `deploy/helm/megooflags/README.md` | A new Names section and one pointer to it; the worker row in the values table; Metrics; Routing |
| `CHANGELOG.md` | Entries under `[Unreleased]` |

No changes to `values.yaml` or `values.schema.json`.

## Shared contracts

- Base name: `B = (contains "megooflags" release) ? release : release + "-megooflags"`, then `B[:41]`, then trailing `[-.]+` removed.
- Render helpers in the tests: `_helm(values, tmp_path, *args, release="mf")`, `render(tmp_path, values, release="mf")` and `render_error(tmp_path, values, release="mf")`. The default keeps every existing test unchanged.
- The new validation message is `worker.replicas (<r>) must equal the number of worker.nodeTokens (<n>), or be left at 0`.
- Release names in the e2e: `cp_release=control-plane-with-a-fifty-character-release-name1` (base `control-plane-with-a-fifty-character-rele`) and `workers_release=workers-with-a-fifty-character-release-name-for-ci` (base `workers-with-a-fifty-character-release-na`).

## Code referenced by the steps

### A. `megooflags.fullname` (step 1)

```
{{/*
The release's base name: the release name when it already contains "megooflags", else
"<release>-megooflags", cut to 41 characters and without trailing "-" or ".". 41 leaves room for
the longest suffix on a StatefulSet ("-postgresql", 11), whose name may have 52 characters because
Kubernetes adds "-<hash>" (up to 11) in the 63-character controller-revision-hash label. It also
leaves room for "-migrate-<revision>" and the worker pods' "-worker-<ordinal>".
*/}}
{{- define "megooflags.fullname" -}}
{{- $name := include "megooflags.name" . -}}
{{- $base := ternary .Release.Name (printf "%s-%s" .Release.Name $name) (contains $name .Release.Name) -}}
{{- regexReplaceAll "[-.]+$" ($base | trunc 41) "" -}}
{{- end -}}
```

### B. Long-name render tests (step 1), in `scripts/tests/test_helm_chart.py` after `ROLES = [...]`

```python
LONG_RELEASES = [
    pytest.param("a-fifty-character-release-name-for-the-chart-tests", id="50"),
    pytest.param("megooflags-" + "b" * 42, id="53-with-megooflags"),
]


def _everything(role: str) -> dict[str, Any]:
    return {
        **(WORKERS if role == "worker" else _role_values(role)),
        "ingress": {"enabled": True},
        "httpRoute": {"enabled": True, "parentRefs": [{"name": "gw"}]},
        "metrics": {"serviceMonitor": {"enabled": True}},
    }


def _of_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in docs if doc["kind"] == kind]


@pytest.mark.parametrize("release", LONG_RELEASES)
@pytest.mark.parametrize("role", ROLES)
def test_long_release_names_fit(tmp_path: Path, role: str, release: str) -> None:
    docs = render(tmp_path, _everything(role), release=release)
    for kind, name in by_kind(docs):
        # A StatefulSet's name goes into the 63-character controller-revision-hash label with
        # "-<hash of up to 10>".
        assert len(name) <= (52 if kind == "StatefulSet" else 63), (kind, name)
    for job in _of_kind(docs, "Job"):
        name = job["metadata"]["name"]
        assert name.endswith("-migrate-1")
        assert len(name) - 1 + 5 <= 63, name  # room for a 5-digit revision
    for statefulset in _of_kind(docs, "StatefulSet"):
        assert len(statefulset["metadata"]["name"] + "-99999") <= 63  # pod names, hostnames
    for doc in docs:
        labels = doc["metadata"].get("labels", {})
        assert labels.get("app.kubernetes.io/instance", release) == release
    services = {doc["metadata"]["name"] for doc in _of_kind(docs, "Service")}
    for statefulset in _of_kind(docs, "StatefulSet"):
        assert statefulset["spec"]["serviceName"] in services
    (ingress,) = _of_kind(docs, "Ingress")
    for path in ingress["spec"]["rules"][0]["http"]["paths"]:
        assert path["backend"]["service"]["name"] in services
    (route,) = _of_kind(docs, "HTTPRoute")
    for rule in route["spec"]["rules"]:
        for backend in rule["backendRefs"]:
            assert backend["name"] in services
    if role != "worker":
        (secret,) = _of_kind(docs, "Secret")
        for key in ("MEGOOFLAGS_DATABASE_URL", "MEGOOFLAGS_VALKEY_URL"):
            assert urlsplit(secret["stringData"][key]).hostname in services, key
        web = next(d for d in _of_kind(docs, "Deployment") if d["metadata"]["name"].endswith("-web"))
        env = {e["name"]: e.get("value") for e in _container(web["spec"]["template"], "web")["env"]}
        assert urlsplit(env["MEGOOFLAGS_API_URL"]).hostname in services


def test_cut_base_name_has_no_trailing_dash(tmp_path: Path) -> None:
    # The 41st character of "<release>-megooflags" is "-" (and the 40th as well).
    docs = render(tmp_path, _everything("standalone"), release="c" * 39 + "--d")
    names = [name for _, name in by_kind(docs)]
    assert not [name for name in names if "--" in name]
    assert ("Secret", "c" * 39) in by_kind(docs)


def test_base_name_of_41_characters_is_not_cut(tmp_path: Path) -> None:
    release = "e" * 30  # "<release>-megooflags" has exactly 41 characters
    docs = render(tmp_path, {"secretKey": SECRET}, release=release)
    assert ("ConfigMap", f"{release}-megooflags-config") in by_kind(docs)
    assert ("StatefulSet", f"{release}-megooflags-postgresql") in by_kind(docs)
```

Add `from urllib.parse import urlsplit` to the imports. Helper signatures:

```python
def _helm(
    values: dict[str, Any], tmp_path: Path, *args: str, release: str = "mf"
) -> subprocess.CompletedProcess[str]:
    ...
    return _run_helm("template", release, str(CHART), "--namespace", "mf", "-f", str(path), *args)


def render(tmp_path: Path, values: dict[str, Any], release: str = "mf") -> list[dict[str, Any]]:
    result = _helm(values, tmp_path, release=release)
    ...


def render_error(tmp_path: Path, values: dict[str, Any], release: str = "mf") -> str:
    result = _helm(values, tmp_path, release=release)
    ...
```

### C. ServiceMonitor (step 2)

```
{{- if .Values.metrics.serviceMonitor.enabled }}
{{- $monitor := .Values.metrics.serviceMonitor }}
{{- /* One map, so a user label with a chart label's key replaces it instead of repeating it. */}}
{{- $labels := mergeOverwrite (include "megooflags.labels" . | fromYaml) (deepCopy (default dict $monitor.labels)) }}
# Scrapes /metrics of the api (or worker) Service for the Prometheus Operator.
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {{ include "megooflags.fullname" . }}
  labels:
    {{- toYaml $labels | nindent 4 }}
spec:
  ... unchanged ...
```

Test:

```python
def test_service_monitor_user_labels_replace_chart_labels(tmp_path: Path) -> None:
    labels = {"app.kubernetes.io/name": "custom", "release": "prom"}
    monitor = {"serviceMonitor": {"enabled": True, "labels": labels}}
    values = {**_role_values("control-plane"), "metrics": monitor}
    result = _helm(values, tmp_path, "--show-only", "templates/servicemonitor.yaml")
    assert result.returncode == 0, result.stderr
    # yaml.safe_load keeps the last of two equal keys, so count them in the text (metadata labels
    # are indented by 4, the selector's by 6).
    assert len(re.findall(r"^    app\.kubernetes\.io/name:", result.stdout, re.M)) == 1
    assert len(re.findall(r"^    release:", result.stdout, re.M)) == 1
    doc = yaml.safe_load(result.stdout)
    metadata = doc["metadata"]["labels"]
    assert metadata["app.kubernetes.io/name"] == "custom"
    assert metadata["release"] == "prom"
    assert metadata["app.kubernetes.io/instance"] == "mf"
    assert metadata["app.kubernetes.io/managed-by"] == "Helm"
    assert metadata["app.kubernetes.io/version"] == _app_version()
    assert metadata["helm.sh/chart"].startswith("megooflags-")
    assert doc["spec"]["selector"] == {
        "matchLabels": {
            "app.kubernetes.io/name": "megooflags",
            "app.kubernetes.io/instance": "mf",
            "app.kubernetes.io/component": "api",
        }
    }
```

### D. Validation (step 3), in the `role: worker` branch of `_validate.tpl`, after the nodeTokens check

```
{{- $tokens := len $v.worker.nodeTokens -}}
{{- $replicas := int $v.worker.replicas -}}
{{- if and (gt $tokens 0) (gt $replicas 0) (ne $replicas $tokens) -}}
{{- fail (printf "worker.replicas (%d) must equal the number of worker.nodeTokens (%d), or be left at 0" $replicas $tokens) -}}
{{- end -}}
```

### E. kind e2e (step 5), in `scripts/helm_test.sh`

After `ns=megooflags`:

```bash
# 50-character release names (Helm allows 53): the chart cuts the base name to 41 characters.
cp_release=control-plane-with-a-fifty-character-release-name1
workers_release=workers-with-a-fifty-character-release-name-for-ci
```

After `fail() {...}`:

```bash
# base_name <release>: the chart's base name, as megooflags.fullname in _helpers.tpl builds it.
base_name() {
  local base="$1-megooflags"
  case "$1" in *megooflags*) base=$1 ;; esac
  base=${base:0:41}
  while [[ $base == *[-.] ]]; do base=${base%?}; done
  echo "$base"
}
for release in "$cp_release" "$workers_release"; do
  [ "${#release}" -eq 50 ] || fail "release name $release has ${#release} characters, not 50"
done
cp_base=$(base_name "$cp_release")
workers_base=$(base_name "$workers_release")
```

Step 4 of the script:

```bash
# 4. The control-plane routes, through both entry points. The api's /metrics is never routed: the
# catch-all sends /metrics to the web app, which answers 404.
for entry in "$ingress" "$gateway"; do
  for path in /healthz /readyz /sign-in; do
    wait_200 "$entry$path"
  done
  code=$(status "$entry/metrics")
  [ "$code" = 404 ] || fail "GET $entry/metrics on the control plane returned $code, not 404"
  body=$(curl -s --max-time 10 "$entry/metrics" || true)
  case "$body" in
    *megooflags_http_requests_total*) fail "GET $entry/metrics served the Prometheus metrics" ;;
  esac
done
```

Replacements (every one):

| Where | Old | New |
|---|---|---|
| header comment, lines 7-8 | release `cp`, release `workers` | two releases with 50-character names (`$cp_release`, `$workers_release`) |
| `api_pods` | `app.kubernetes.io/instance=cp` | `app.kubernetes.io/instance=${cp_release}` |
| step 3 | `helm install cp` | `helm install "$cp_release"`, fail message `helm install $cp_release failed` |
| step 4 | `httproute cp-megooflags` (two places) | `httproute "$cp_base"` / `HTTPRoute $cp_base` |
| step 6 | `controlPlaneUrl: http://cp-megooflags-api.${ns}.svc:8000` | `controlPlaneUrl: http://${cp_base}-api.${ns}.svc:8000` |
| step 6 | `helm install workers` | `helm install "$workers_release"` |
| step 6 | `statefulset/workers-megooflags-worker` (two places) | `statefulset/${workers_base}-worker` |
| step 6 | `workers-megooflags-worker-$n` (two places) | `${workers_base}-worker-$n` |
| step 8 | `deployment/cp-megooflags-api` (four places) | `deployment/${cp_base}-api` |
| step 9 | `helm upgrade cp` | `helm upgrade "$cp_release"` |
| step 9 | `cp-megooflags-migrate-2` (three places), `cp-megooflags-migrate-1` (two places) | `${cp_base}-migrate-2`, `${cp_base}-migrate-1` |

Afterwards `grep -n 'cp-megooflags\|workers-megooflags\|instance=cp\b' scripts/helm_test.sh` finds nothing.

### F. README Names section (step 6), inserted before `## Secrets and \`existingSecret\``

````markdown
## Names

Every resource name starts with the release's base name: the release name when it contains
`megooflags`, else `<release>-megooflags`. The base name is cut to 41 characters, and a `-` or `.`
left at its end is dropped, so that the longest names (the `<base>-postgresql` StatefulSet, the
`<base>-migrate-<revision>` Jobs and the `<base>-worker-<n>` pods) stay within the Kubernetes
limits. For example, the release `payments-production-eu-west-1-flags-primary` (43 characters) gets
the base name `payments-production-eu-west-1-flags-prima`, and its API Service is
`payments-production-eu-west-1-flags-prima-api`.

Keep release names at 30 characters or fewer and nothing is cut: release `cp` gives
`cp-megooflags-api`, `cp-megooflags-web` and so on, as in the examples on this page. Two long
release names with the same first 41 characters cannot share a namespace: the second install fails,
because its resources already exist and belong to the other release. To see the real names of a
release:

```bash
kubectl get all -n megooflags -l app.kubernetes.io/instance=<release>
```
````

### G. CHANGELOG (step 6), under `## [Unreleased]`

```markdown
### Changed
- Helm chart: resource names are built from at most 41 characters of the base name (`<release>-megooflags`, or the release name when it contains `megooflags`), so that the longest names fit the Kubernetes limits. This only affects a 1.0.0 install whose base name has 42 characters or more and works today, which is possible only with an external database or a worker release. The upgrade creates its resources under the shorter names and removes the old ones. Before upgrading, update whatever names the old Services: `worker.controlPlaneUrl` of worker releases, and the URLs in an `existingSecret`. After upgrading a worker release, delete the old `data-<old name>-worker-<n>` volumes. See https://mmhy2003.github.io/MegooFlags/deploy/kubernetes/#names.
- Helm chart: `helm install` and `helm upgrade` fail when `worker.replicas` is set and differs from the number of `worker.nodeTokens`.

### Fixed
- Helm chart: release names of up to 53 characters install; before, names of about 50 characters gave resource names over the Kubernetes limits.
- Helm chart: a `metrics.serviceMonitor.labels` key that repeats a chart label replaces it, instead of producing a duplicate key that broke the manifest.
- The web app answers `/metrics` with 404 instead of redirecting to sign-in.
```

## Steps

### 1. Cut the chart's base name to 41 characters

**Files:** `deploy/helm/megooflags/templates/_helpers.tpl`, `scripts/tests/test_helm_chart.py`.

1. In the test file, give `_helm`, `render` and `render_error` a `release: str = "mf"` keyword (signatures in B) and import `urlsplit`. Add `LONG_RELEASES`, `_everything`, `_of_kind`, `test_long_release_names_fit`, `test_cut_base_name_has_no_trailing_dash` and `test_base_name_of_41_characters_is_not_cut` from B, right after `ROLES = [...]`.
2. Run `MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py -k 'long_release or trailing_dash or 41_characters'`. Expected: the long-name cases fail on the length asserts (for example `('StatefulSet', ...-postgresql)` with 72 characters), and the dash test fails on a name with `--`. The 41-character test passes already.
3. Replace `megooflags.fullname` and its comment with A. No other template changes: they all name their resources through this helper.
4. Run the same command again: all pass. Then run the whole file: `MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py`. Every existing test (release `mf`) still passes unchanged.
5. Lint: `(cd api && uv run ruff check ../scripts && uv run ruff format --check ../scripts && uv run mypy)`.
6. Commit `fix(helm): cut the base name to 41 characters so long release names fit (MF-461)`.

### 2. Merge the ServiceMonitor labels, the user's label wins

**Files:** `deploy/helm/megooflags/templates/servicemonitor.yaml`, `scripts/tests/test_helm_chart.py`.

1. Add `test_service_monitor_user_labels_replace_chart_labels` from C, after `test_service_monitor`.
2. Run `MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py -k service_monitor`. Expected: the new test fails, because `app.kubernetes.io/name:` appears twice at indent 4.
3. Change the template as in C: one `$labels` map, `mergeOverwrite` with the chart labels as the destination, then `toYaml $labels | nindent 4`. `spec` does not change.
4. Run it again: the new test and `test_service_monitor` pass. Run the whole file, including `test_kubeconform` and `test_helm_lint`.
5. Lint as in step 1 and commit `fix(helm): merge ServiceMonitor labels instead of repeating keys (MF-461)`.

### 3. Fail when worker.replicas disagrees with worker.nodeTokens

**Files:** `deploy/helm/megooflags/templates/_validate.tpl`, `scripts/tests/test_helm_chart.py`.

1. Add two `pytest.param` cases to `test_required_values`:
   - `id="replicas-differ-from-tokens"`: values `{"role": "worker", "worker": {"controlPlaneUrl": "https://cp.example.com", "nodeTokens": ["mf_node_a", "mf_node_b"], "replicas": 3}}`, with the message `"worker.replicas (3) must equal the number of worker.nodeTokens (2), or be left at 0"`.
   - `id="replicas-differ-from-tokens-existing-secret"`: the same values plus `"existingSecret": "tokens"`, with the same message.
   Also add:
   ```python
   def test_worker_replicas_equal_to_tokens(tmp_path: Path) -> None:
       assert _worker_set(render(tmp_path, _workers(replicas=2)))["spec"]["replicas"] == 2
   ```
2. Run `MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py -k 'required_values or worker_replicas'`. Expected: the two new cases fail (`render_error` sees exit code 0).
3. Add the check from D to the `role: worker` branch of `_validate.tpl`, after the nodeTokens check. `worker.yaml` stays as it is.
4. Run it again: all pass, as do `test_worker_replicas_follow_tokens` and `test_worker_existing_secret_replicas`. Run the whole file.
5. Lint as in step 1 and commit `fix(helm): fail when worker.replicas disagrees with nodeTokens (MF-461)`.

### 4. The web app answers 404 on /metrics; fix the path comments

**Files:** `web/proxy.ts`, `web/tests/unit/proxy-matcher.test.ts`, `deploy/helm/megooflags/templates/_helpers.tpl`, `ingress.yaml`, `httproute.yaml`.

1. In `proxy-matcher.test.ts`, add:
   ```ts
   it("skips the health and metrics paths", () => {
     expect(matcher.test("/healthz")).toBe(false);
     expect(matcher.test("/readyz")).toBe(false);
     expect(matcher.test("/metrics")).toBe(false);
   });
   ```
2. Run `(cd web && pnpm exec vitest run tests/unit/proxy-matcher.test.ts)`. Expected: the `/metrics` assert fails.
3. In `web/proxy.ts`, change the matcher to `"/((?!api|sdk|ofrep|_next|favicon.ico|icon.svg|healthz|readyz|metrics).*)"`. The sign-in redirect then does not run for `/metrics`, and Next.js answers 404, because no page has that path.
4. Run it again: it passes.
5. Comments, with no change to the rendered paths:
   - `_helpers.tpl`, the comment above `megooflags.apiPaths`: "The paths routed to the API, with the types deploy/Caddyfile and deploy/Caddyfile.worker give them ("/x/*" is a prefix). Workers serve no /api. A YAML list of {type, path}. The HTTPRoute uses the types as they are; the Ingress makes every path Prefix (see ingress.yaml)."
   - `ingress.yaml` and `httproute.yaml`: `# Everything else is the web UI. /metrics is never routed.` becomes `# Everything else is the web UI, also /metrics, which the web UI answers with 404.`
6. Gate: `(cd web && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test)`, then `MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py` (`test_ingress_paths_match_caddyfile` and `test_httproute_paths_match_caddyfile` still pin Prefix and Exact).
7. Commit `fix(web): answer /metrics with 404 instead of a sign-in redirect (MF-461)`.

### 5. kind e2e with 50-character release names and a /metrics 404 check

**Files:** `scripts/helm_test.sh`.

1. Make the edits in E: the release variables, `base_name`, the length check, step 4 of the script, and every replacement in the table.
2. `bash -n scripts/helm_test.sh`. Then `grep -n 'cp-megooflags\|workers-megooflags' scripts/helm_test.sh` finds nothing.
3. Build the images from this tree and run the test:
   ```bash
   docker build -t megooflags-api:ci --build-arg VERSION="$(cat VERSION)" api
   docker build -t megooflags-web:ci --build-arg VERSION="$(cat VERSION)" web
   bash scripts/helm_test.sh
   ```
   Expected: `helm OK`. This proves that `helm install --wait` passes with the bundled PostgreSQL StatefulSet, that both worker pods are Ready (each read its own token by ordinal), that `/metrics` answers 404 on the control plane through both entry points, and that the upgrade runs `<cp_base>-migrate-2`. Set `MF_HELM_CLUSTER`, `MF_HELM_INGRESS_PORT` and `MF_HELM_GATEWAY_PORT` if they clash with another cluster or port. On failure, read `MF_HELM_LOGS` and report it. Do not weaken the checks.
4. `uv run --frozen --project api pytest scripts/tests` (`test_workflows.py` still finds `bash scripts/helm_test.sh` in CI).
5. Commit `test(helm): run the kind e2e with 50-character release names (MF-461)`.

### 6. Docs: chart README (Names, worker replicas, Metrics, Routing) and CHANGELOG

**Files:** `deploy/helm/megooflags/README.md`, `CHANGELOG.md`.

1. README:
   - Insert the Names section from F before `## Secrets and \`existingSecret\``.
   - One pointer: in "A control plane and workers", the sentence "Each replica `workers-megooflags-worker-<n>` uses token `n` …" becomes "Each replica `workers-megooflags-worker-<n>` (see [Names](#names) for long release names) uses token `n` …".
   - Values table, `worker` row: replace "`replicas` (only with `existingSecret`)" with "`replicas` (0, or the number of `nodeTokens`; set it only with an `existingSecret` and no `nodeTokens`)".
   - Routing: after the list of paths, add "In the Ingress, `/healthz` and `/readyz` are `Prefix` paths (Traefik would otherwise send `/readyz` to the web UI), so `/healthz/x` reaches the API, which answers 404. In the HTTPRoute they match exactly."
   - Metrics: after "…reachable only inside the cluster.", add "On a control plane or single node, `/metrics` on the public address falls to the web UI, which answers 404." After the YAML block, add "A label in `serviceMonitor.labels` replaces a chart label with the same key."
2. CHANGELOG: add G under `## [Unreleased]`, with Changed before Fixed.
3. Checks: `uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py -k readme` and `(cd site && pnpm install --frozen-lockfile && pnpm format:check && pnpm check && pnpm test)`. `site/sync.manifest.json` copies the README to `deploy/kubernetes.md`, so `#names` resolves there. On the build machine (no AVX2) Astro and Pagefind may die with SIGILL. Apply the known local workaround, or report it; `pnpm build` passes only if it prints "[build] Complete!".
4. Commit `docs(helm): long release names, replicas, metrics and health paths (MF-461)`.

## Final verification

```bash
MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py
uv run --frozen --project api pytest scripts/tests
(cd api && uv run ruff check ../scripts && uv run ruff format --check ../scripts && uv run mypy)
bash -n scripts/helm_test.sh
docker build -t megooflags-api:ci --build-arg VERSION="$(cat VERSION)" api
docker build -t megooflags-web:ci --build-arg VERSION="$(cat VERSION)" web
bash scripts/helm_test.sh            # prints "helm OK"
(cd web && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test)
(cd site && pnpm format:check && pnpm check && pnpm build && pnpm test)
```
