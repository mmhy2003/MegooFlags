# MF-88 plan: Helm chart for MegooFlags

## Goal

`helm install` from `oci://ghcr.io/mmhy2003/charts/megooflags` runs MegooFlags on Kubernetes in any of the three roles the Compose bundles cover (standalone, control plane, workers), with the chart versioned like the server and published by `release.yml`.

## Architecture

One chart in `deploy/helm/megooflags/`. A release has one `role`. `standalone` and `control-plane` render web, api, a migration Job per Helm revision, and optionally PostgreSQL and Valkey. `worker` renders a StatefulSet in which each replica reads its own `token-<ordinal>` from a Secret. Routing is Kubernetes-native (Ingress and/or Gateway API HTTPRoute) with path lists copied from `deploy/Caddyfile` and `deploy/Caddyfile.worker`; tests keep them in sync. Render tests (pytest + `helm template`), `helm lint`, `kubeconform` and an end-to-end test on kind (`scripts/helm_test.sh`) run in a new `helm` CI job. `release.yml` packages and pushes the chart with the tag's image version.

No API, database, web or environment variable changes.

## Facts from the repository this plan relies on

- Scripts tests run with `uv run --frozen --project api pytest scripts/tests`. `yaml` (PyYAML) is importable there (it comes with `uvicorn[standard]`; `test_workflows.py` already imports it). `scripts/check_version.py` runs under the system `python3` in `release.yml` (`meta` job) without uv, so it **must not import yaml**: parse `Chart.yaml` with a regex.
- `ruff check . ../scripts` and `ruff format --check . ../scripts` run from `api/`, so new Python under `scripts/` must pass ruff. mypy covers only `api/src`.
- The api image (`python:3.12-slim`) has `/bin/sh`, `alembic` on `PATH`, `WORKDIR /app` (where `alembic.ini` is), runs as uid 10001, exposes 8000 and keeps `/var/lib/megooflags` owned by 10001. The web image runs as uid 10001 on port 3000 and needs only `MEGOOFLAGS_API_URL`.
- `alembic current` connects through `MEGOOFLAGS_DATABASE_URL` (`api/alembic/env.py`), prints `<rev> (head)` when the database is at the image's head, prints nothing on an empty database, and fails when the database cannot be reached or holds a revision the image does not know.
- A worker reads `MEGOOFLAGS_NODE_TOKEN` (must start with `mf_node_`), `MEGOOFLAGS_CONTROL_PLANE_URL` (https unless `MEGOOFLAGS_CONTROL_PLANE_INSECURE=true`), `MEGOOFLAGS_CONTROL_PLANE_CA_FILE` (`api/src/megooflags_api/services/cluster/config.py`). `serve.py` logs a line when `MEGOOFLAGS_API_WORKERS` is set on a worker, so the chart does not pass it to workers.
- The CSRF check accepts an `Origin` equal to `MEGOOFLAGS_PUBLIC_URL` or the request's own host (`api/src/megooflags_api/auth/csrf.py`). Traefik passes the `Host` header through, so `curl -H "Origin: http://localhost:<port>"` against the Ingress works.
- `scripts/proxy_stream_check.py --proxy <entry> --api <entry> --token <token>` loads the UI, holds an SDK stream past the heartbeat and times a flag change. `scripts/cluster/lib.sh` and `scripts/proxy_samples_test.sh` show the setup and token bootstrap (`POST /api/setup`, then `POST /api/tokens`). Nodes come from `POST /api/nodes {"name": ...}`, which returns `token`. SDK keys come from `POST /api/projects/<p>/environments/production/sdk-keys {"name":..., "kind":"server"}`, which returns `key`.
- Synced docs pages are listed in `site/sync.manifest.json` and must also be in `site/.gitignore` (tested by `site/tests/sync.test.ts`) and `site/.prettierignore`.
- **Traefik check (asked for by the design):** Traefik v3.7's Gateway API conformance report lists `HTTPRouteResponseHeaderModification` as supported. `HTTPRouteRequestTimeout` is listed as unsupported in the v3.7.0 report, but Traefik's provider code reads `rules[].timeouts.request` (zero disables the deadline) and never rejects a route because of it. Traefik's own default write timeout is 0, so streams are not cut either way. The e2e test therefore proves the behaviour that matters (a stream held past the heartbeat through the HTTPRoute) and checks that the route is `Accepted`. It does not claim that Traefik enforces the timeout. Pin Traefik image `v3.7.15`.
- **Correction to one test expectation in the design.** The design says "`/metrics` is 404 from outside". On a control-plane release the catch-all `/` rule sends `/metrics` to the web app. Its `proxy.ts` matcher does not exclude `/metrics`, so an anonymous request gets a redirect to `/setup` or `/sign-in`, not a 404. The metrics stay private either way, because the api's `/metrics` is never routed. The tests therefore assert: on control-plane routes, `/metrics` never returns the Prometheus text (`megooflags_http_requests_total` is absent from the body and the status is not 200 with `text/plain`); on worker routes, which have no catch-all, `/metrics` is 404. This keeps the design's promise ("`/metrics` stays private") without a web change. If the reviewer wants a literal 404, that needs a one-line web change (add `metrics` to the `proxy.ts` matcher), which the design ruled out.

## File map

| File | Responsibility |
|---|---|
| `megoocolab.toml` (new) | Test service sets for the runner (asked for by the runner, not the design) |
| `scripts/test-services/db.yml`, `scripts/test-services/keycloak.yml` (new) | Compose files for those sets: PostgreSQL + Valkey; Keycloak |
| `deploy/helm/megooflags/Chart.yaml` | `apiVersion: v2`, `name: megooflags`, `version` and `appVersion` = `VERSION` |
| `deploy/helm/megooflags/.helmignore` | Keeps `README.md` in the package and drops editor files |
| `deploy/helm/megooflags/values.yaml` | All values (listed in full below) |
| `deploy/helm/megooflags/values.schema.json` | Types and enums for the values |
| `deploy/helm/megooflags/templates/_helpers.tpl` | Names, labels, images, Secret and ConfigMap names, URLs |
| `deploy/helm/megooflags/templates/_validate.tpl` | `megooflags.validate`: every required-value failure |
| `templates/configmap.yaml`, `templates/secret.yaml` | Settings and secrets per role |
| `templates/api.yaml`, `templates/web.yaml`, `templates/migrate-job.yaml` | Control-plane workloads and Services |
| `templates/postgresql.yaml`, `templates/valkey.yaml` | Bundled data stores |
| `templates/worker.yaml` | Worker StatefulSet, Service and headless Service |
| `templates/ingress.yaml`, `templates/httproute.yaml`, `templates/servicemonitor.yaml` | Routing and metrics |
| `deploy/helm/megooflags/README.md` | User docs for the chart, synced to the site |
| `scripts/check_version.py`, `scripts/tests/conftest.py`, `scripts/tests/test_check_version.py` | Chart version check |
| `scripts/tests/test_helm_chart.py` (new) | Render, lint, kubeconform and README tests |
| `scripts/helm_test.sh` (new) | End to end on kind; prints `helm OK` |
| `scripts/tests/test_workflows.py`, `scripts/tests/test_compose_files.py` | Workflow assertions |
| `.github/workflows/ci.yml`, `.github/workflows/release.yml` | `helm` job; `chart` job and the `.tgz` on the release |
| `site/sync.manifest.json`, `site/.gitignore`, `site/.prettierignore`, `site/astro.config.mjs` | "Kubernetes (Helm)" page |
| `site/src/content/docs/deploy/{multi-node,upgrades,observability,security}.md` | Short pointers |
| `AGENTS.md`, `CHANGELOG.md`, `ROADMAP.md`, `README.md` | Project docs |

## Shared contracts (all steps use these names)

**Names** (release `mf` gives fullname `mf-megooflags`; a release name that already contains `megooflags` is used as is; truncate to 63 characters and trim a trailing `-`):

| Resource | Name |
|---|---|
| ConfigMap | `<fullname>-config` |
| Secret (chart-managed) | `<fullname>` (or `existingSecret`) |
| api Deployment / Service (port name `http`, 8000) | `<fullname>-api` |
| web Deployment / Service (port name `http`, 3000) | `<fullname>-web` |
| migration Job | `<fullname>-migrate-<.Release.Revision>` |
| PostgreSQL StatefulSet / Service (5432) | `<fullname>-postgresql` |
| Valkey Deployment / Service (6379) | `<fullname>-valkey` |
| worker StatefulSet / Service (`http`, 8000) / headless Service | `<fullname>-worker`, `<fullname>-worker`, `<fullname>-worker-headless` |
| Ingress, HTTPRoute, ServiceMonitor | `<fullname>` |

**Labels:** `app.kubernetes.io/name: megooflags`, `app.kubernetes.io/instance: <release>`, `app.kubernetes.io/component: api|web|migrate|postgresql|valkey|worker`, plus `app.kubernetes.io/version` and `helm.sh/chart` on metadata only (not in selectors). Helper `megooflags.selectorLabels` takes `(dict "ctx" $ "component" "api")`.

**Images:** helper `megooflags.image` takes `(dict "ctx" $ "name" "megooflags-api")` and returns `<registry>/<name>:<tag>`, or `<name>:<tag>` when `image.registry` is empty. `tag` defaults to `.Chart.AppVersion`.

**Secret keys:** `MEGOOFLAGS_SECRET_KEY`, `MEGOOFLAGS_DATABASE_URL`, `MEGOOFLAGS_VALKEY_URL`, `POSTGRES_PASSWORD` (only when `postgresql.enabled`); worker: `token-0` … `token-<n-1>` only. The bundled database URL is `postgresql+asyncpg://megooflags:<password | urlquery>@<fullname>-postgresql:5432/megooflags`; the bundled Valkey URL is `redis://<fullname>-valkey:6379/0`.

**Validation messages** (`fail` in `megooflags.validate`, included at the top of `configmap.yaml`, which every role renders):

| Condition | Message |
|---|---|
| unknown role | `role must be standalone, control-plane or worker` |
| non-worker, no `existingSecret`, empty `secretKey` | `secretKey is required (32+ characters, e.g. openssl rand -hex 32), or set existingSecret` |
| `secretKey` shorter than 32 | `secretKey must be at least 32 characters` |
| no `existingSecret`, `postgresql.enabled: false`, empty `externalDatabase.url` | `externalDatabase.url is required when postgresql.enabled is false` |
| `externalDatabase.url` without the `postgresql+asyncpg://` prefix | `externalDatabase.url must use the postgresql+asyncpg:// driver` |
| no `existingSecret`, `valkey.enabled: false`, empty `externalValkey.url` | `externalValkey.url is required when valkey.enabled is false` |
| worker, empty `worker.controlPlaneUrl` | `worker.controlPlaneUrl is required when role is worker` |
| worker, no tokens and not (`existingSecret` and `worker.replicas` > 0) | `worker.nodeTokens is required when role is worker (one token per replica, from the Nodes page), or set existingSecret and worker.replicas` |

The schema also enforces the `role` enum. Either way the error names `role`.

**ConfigMap keys per role.** It always holds `MEGOOFLAGS_ROLE: <role>`, then every key of `config`, rendered with `quote`, except:
- for `worker`, these control-plane-only keys are dropped: `MEGOOFLAGS_PUBLIC_URL`, `MEGOOFLAGS_PERSIST_CONTEXT`, `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS`, `MEGOOFLAGS_STREAM_RECHECK_SECONDS`, `MEGOOFLAGS_API_WORKERS`, `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE`, `MEGOOFLAGS_WEBHOOK_TIMEOUT_SECONDS`, `MEGOOFLAGS_WEBHOOK_RETENTION_DAYS`, `MEGOOFLAGS_UPDATE_CHECK`;
- for `standalone` and `control-plane`, these are dropped: `MEGOOFLAGS_WORKER_BUFFER_MAX_MB`, `MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS`.

`OTEL_SERVICE_NAME: ""` renders as `megooflags-worker` for workers and `megooflags-api` otherwise. Workers also get `MEGOOFLAGS_CONTROL_PLANE_URL`, `MEGOOFLAGS_CONTROL_PLANE_INSECURE` (from `worker.controlPlaneInsecure`) and, when a CA is set, `MEGOOFLAGS_CONTROL_PLANE_CA_FILE=/etc/megooflags/certs/<key>`.

### `values.yaml` (complete)

```yaml
# standalone (one node), control-plane, or worker. One role per release.
role: standalone

image:
  registry: ghcr.io/mmhy2003
  tag: ""            # default: the chart's appVersion
  pullPolicy: IfNotPresent
imagePullSecrets: []

# MEGOOFLAGS_SECRET_KEY, 32+ characters. Required unless role is worker or existingSecret is set.
secretKey: ""
# A Secret with the same keys as the chart's own (see README).
existingSecret: ""

# Environment for the api or worker pods, with the defaults of the Compose files.
config:
  MEGOOFLAGS_PUBLIC_URL: ""
  MEGOOFLAGS_LOG_LEVEL: INFO
  FORWARDED_ALLOW_IPS: "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1"
  OTEL_EXPORTER_OTLP_ENDPOINT: ""
  OTEL_SERVICE_NAME: ""          # default: megooflags-api, or megooflags-worker
  OTEL_TRACES_SAMPLER_ARG: "1.0"
  MEGOOFLAGS_PERSIST_CONTEXT: "false"
  MEGOOFLAGS_EXPOSURE_RETENTION_DAYS: "30"
  MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE: "600"
  MEGOOFLAGS_SSE_HEARTBEAT_SECONDS: "15"
  MEGOOFLAGS_STREAM_RECHECK_SECONDS: "30"
  # Not 0 (one per CPU): that counts the node's CPUs, not the pod's limit.
  MEGOOFLAGS_API_WORKERS: "2"
  MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE: "false"
  MEGOOFLAGS_WEBHOOK_TIMEOUT_SECONDS: "10"
  MEGOOFLAGS_WEBHOOK_RETENTION_DAYS: "7"
  MEGOOFLAGS_UPDATE_CHECK: "true"
  MEGOOFLAGS_WORKER_BUFFER_MAX_MB: "64"
  MEGOOFLAGS_WORKER_DRAIN_GRACE_SECONDS: "30"

api:
  replicas: 1
  resources: {}
  extraEnv: []
  podAnnotations: {}
  nodeSelector: {}
  tolerations: []
  affinity: {}

web:
  replicas: 1
  resources: {}
  extraEnv: []
  podAnnotations: {}
  nodeSelector: {}
  tolerations: []
  affinity: {}

migrate:
  annotations: {}    # e.g. Argo CD: argocd.argoproj.io/hook: Sync
  resources: {}

postgresql:
  enabled: true
  image: postgres:16
  password: megooflags
  persistence:
    size: 8Gi
    storageClass: ""
  resources: {}
externalDatabase:
  url: ""            # postgresql+asyncpg://user:password@host:5432/db

valkey:
  enabled: true
  image: valkey/valkey:8
  resources: {}
externalValkey:
  url: ""            # redis://host:6379/0

worker:
  controlPlaneUrl: ""
  nodeTokens: []     # one replica per token, from the Nodes page
  replicas: 0        # only with existingSecret (keys token-0 ... token-N-1)
  controlPlaneInsecure: false
  controlPlaneCa:
    configMap: ""
    secret: ""
    key: ca.crt
  persistence:
    size: 1Gi
    storageClass: ""
  resources: {}
  extraEnv: []
  podAnnotations: {}
  nodeSelector: {}
  tolerations: []
  affinity: {}

ingress:
  enabled: false
  className: ""
  annotations: {}
  host: ""
  tls: []

httpRoute:
  enabled: false
  parentRefs: []
  hostnames: []
  apiResponseHeaders: true
  timeouts:
    request: "0s"    # "" leaves timeouts out

metrics:
  serviceMonitor:
    enabled: false
    interval: 30s
    labels: {}
```

### Pod scripts (inline `sh -c` in the templates)

API init container `wait-for-migrations` (api image, same `envFrom` as api):

```sh
until alembic current 2>/dev/null | grep -q '(head)'; do
  echo "waiting for the database to reach this image's migration head"
  sleep 5
done
```

Migration Job container `migrate` (`restartPolicy: Never`, `backoffLimit: 0`, annotations from `migrate.annotations`):

```sh
i=0
until alembic current >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 150 ]; then
    echo "the database did not accept connections within 5 minutes" >&2
    exit 1
  fi
  sleep 2
done
exec alembic upgrade head
```

Worker container `worker` (Secret mounted read-only at `/etc/megooflags/tokens`, `defaultMode: 0440`, pod `fsGroup: 10001`):

```sh
ordinal="${HOSTNAME##*-}"
file="/etc/megooflags/tokens/token-${ordinal}"
if [ ! -s "$file" ]; then
  echo "node token token-${ordinal} is missing from Secret <secret name>: add it, or lower the replica count" >&2
  exit 1
fi
MEGOOFLAGS_NODE_TOKEN="$(cat "$file")"
export MEGOOFLAGS_NODE_TOKEN
exec python -m megooflags_api.serve
```

`<secret name>` is filled in by the template (`megooflags.secretName`). In Helm templates, write the script with a YAML block scalar (`- |`) so that `$` and quotes need no escaping.

### Pod settings shared by every workload

- `automountServiceAccountToken: false`. Pod `securityContext`: `runAsNonRoot: true`, `runAsUser`/`runAsGroup`/`fsGroup` 10001 for api, web, migrate and worker, and 999 for postgresql and valkey. Container `securityContext`: `allowPrivilegeEscalation: false`, `capabilities: {drop: [ALL]}`.
- `envFrom`: the ConfigMap, then the Secret (api, migrate init, worker; the worker Secret holds only tokens, so workers use the ConfigMap only and mount the tokens).
- Pod annotations `checksum/config` (sha256 of the rendered `configmap.yaml`) and `checksum/secret` (sha256 of `secret.yaml`; omitted with `existingSecret`), merged with `<component>.podAnnotations`.
- Probes: api and worker use `httpGet /healthz` (liveness, period 10 s, failureThreshold 3) and `httpGet /readyz` (readiness, period 5 s). The worker gets a startupProbe on `/healthz`. web uses `tcpSocket 3000` for both, because its pages call the API.
- `extraEnv` is appended to `env`. `nodeSelector`, `tolerations` and `affinity` are copied when non-empty.

### Routing

- Paths from `deploy/Caddyfile` (`@api path /api/* /sdk/* /ofrep/* /healthz /readyz`) become `/api`, `/sdk`, `/ofrep` with type Prefix, and `/healthz`, `/readyz` with type Exact. They go to the api Service; then `/` Prefix goes to web.
- Paths from `deploy/Caddyfile.worker` (`@api path /sdk/* /ofrep/* /healthz /readyz`) go to the worker Service, with no catch-all.
- Ingress: `networking.k8s.io/v1`, `ingressClassName` when set, `ingress.annotations` copied, one rule for `ingress.host` (or no host), `ingress.tls` copied.
- HTTPRoute: `gateway.networking.k8s.io/v1`, `parentRefs` and `hostnames` copied. One rule for the API paths (all five matches) with, when `apiResponseHeaders`, a `ResponseHeaderModifier` filter that sets `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin` and `X-Frame-Options: DENY`, and, when `timeouts.request` is non-empty, `timeouts: {request: <value>}`. For control-plane releases, a second rule `/` goes to web without filters.
- ServiceMonitor (`monitoring.coreos.com/v1`): selects the api (or worker) Service by selector labels, endpoint `port: http`, `path: /metrics`, `interval`; `metrics.serviceMonitor.labels` go on its metadata.

### `scripts/tests/test_helm_chart.py` helpers

```python
ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "helm" / "megooflags"
SECRET = "s" * 32
REQUIRE = bool(os.environ.get("MEGOOFLAGS_REQUIRE_HELM"))
pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None and not REQUIRE, reason="helm is not installed"
)

def _helm(values: dict[str, Any], tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # writes values as JSON (valid YAML) to tmp_path/values.json and runs
    # helm template mf CHART --namespace mf -f values.json *args
def render(tmp_path, values) -> list[dict[str, Any]]      # asserts returncode 0, yaml.safe_load_all, drops None
def render_error(tmp_path, values) -> str                 # asserts returncode != 0, returns stderr
def by_kind(docs) -> set[tuple[str, str]]                 # {(kind, metadata.name)}
def find(docs, kind, name) -> dict[str, Any]
def caddy_paths(name: str) -> set[tuple[str, str]]        # parses the "@api path" line: "/x/*" -> ("Prefix", "/x"), else ("Exact", p)
def compose_api_env(name: str) -> dict[str, str]          # reuses the line parsing of test_compose_files._api_environment;
                                                          # "KEY: ${KEY:-default}" -> {KEY: default}; "KEY: value" -> {KEY: value}
```

`kubeconform` tests skip the same way when `kubeconform` is missing and `MEGOOFLAGS_REQUIRE_HELM` is unset.

### `scripts/helm_test.sh` outline

Bash, `set -euo pipefail`, with a header comment in the style of `scripts/cluster_test.sh`. Settings: `MF_HELM_CLUSTER` (default `megooflags-helm`), `MF_HELM_INGRESS_PORT` (18480), `MF_HELM_GATEWAY_PORT` (18481), `MF_HELM_LOGS` (`/tmp/megooflags-helm-logs`). Pinned: `KIND_NODE=kindest/node:v1.34.0`, `GATEWAY_API=v1.5.1`, `TRAEFIK_IMAGE_TAG=v3.7.15`, and `TRAEFIK_CHART_VERSION` set to the newest `traefik/traefik` chart whose `appVersion` is `v3.7.x` (find it with `helm search repo traefik/traefik --versions`, then write the number into the script).

1. `kind create cluster` from a config written to a temp dir, with `extraPortMappings` from NodePorts 30080 and 30081 to the two host ports on 127.0.0.1. `kind load docker-image megooflags-api:ci megooflags-web:ci`.
2. `kubectl apply` the Gateway API standard CRDs (`https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API}/standard-install.yaml`). Install Traefik with its Helm chart in namespace `traefik`: `service.type=NodePort`; entrypoint `web` with nodePort 30080 and an extra entrypoint `gw` with nodePort 30081; `providers.kubernetesIngress.enabled=true`; `providers.kubernetesGateway.enabled=true`; the chart's Gateway enabled with its listener on the `gw` entrypoint's port and `namespacePolicy.from=All`; `image.tag=$TRAEFIK_IMAGE_TAG`; `--wait`.
3. `helm install cp deploy/helm/megooflags -n megooflags --create-namespace --wait --timeout 10m` with `role=control-plane`, `image.registry=`, `image.tag=ci`, `image.pullPolicy=Never`, a random `secretKey`, `api.replicas=2`, `config.MEGOOFLAGS_PUBLIC_URL=http://localhost:$MF_HELM_INGRESS_PORT`, `ingress.enabled=true`, `ingress.annotations."traefik\.ingress\.kubernetes\.io/router\.entrypoints"=web`, `httpRoute.enabled=true`, `httpRoute.parentRefs[0].name=traefik-gateway`, `httpRoute.parentRefs[0].namespace=traefik` (use a values file written to the temp dir rather than `--set` escapes).
4. For each entry point (`ingress=http://localhost:$MF_HELM_INGRESS_PORT`, `gateway=http://localhost:$MF_HELM_GATEWAY_PORT`): `/healthz` 200, `/readyz` 200, `/sign-in` 200, `/metrics` not serving metrics (see the correction above). Through the gateway, `/healthz` carries `X-Frame-Options: DENY`. `kubectl get httproute cp-megooflags -o jsonpath` shows `Accepted=True`.
5. First-run setup and an org admin token through the Ingress (same curl calls as `scripts/proxy_samples_test.sh`), then `python3 scripts/proxy_stream_check.py --proxy <entry> --api <ingress> --token <token>` for both entry points.
6. Create nodes `helm-worker-1` and `helm-worker-2` (`POST /api/nodes`). `helm install workers ... --wait --timeout 10m` with `role=worker`, `worker.controlPlaneUrl=http://cp-megooflags-api.megooflags.svc:8000`, `worker.controlPlaneInsecure=true`, `worker.nodeTokens=[t1,t2]`, `ingress.enabled=true`, `ingress.host=workers.megooflags.test`, the same entrypoint annotation, `httpRoute.enabled=true`, `httpRoute.hostnames=[workers.megooflags.test]`, the same parentRefs. Check `kubectl rollout status statefulset/workers-megooflags-worker`, and that both pods are Ready.
7. Create project `helm` (environment `production`) and a server SDK key through the control plane. Poll (up to 60 s) `GET /sdk/v1/ruleset` with `-H 'Host: workers.megooflags.test'` and the key, through both host ports, until 200. Through the worker route, `/metrics` is 404.
8. `kubectl scale deployment/cp-megooflags-api --replicas=0` and wait until no api pods remain. The ruleset through the worker Ingress is still 200. Scale back to 2 and wait until ready.
9. Record the api pod names. Run `helm upgrade cp ... --reuse-values --set config.MEGOOFLAGS_LOG_LEVEL=DEBUG --wait`. Then `job/cp-megooflags-migrate-2` exists and has `succeeded=1`, `job/cp-megooflags-migrate-1` is gone, and the api pod names are all new.
10. Print `helm OK`. An EXIT trap writes `kubectl get all -A`, `kubectl describe pods -n megooflags` and the logs of every pod to `$MF_HELM_LOGS` when the run fails, then deletes the kind cluster.

## Steps

### 1. Add megoocolab.toml (asked for by the runner)

The runner asked for this step, not the design. The repository's checks need containers: the api tests need PostgreSQL, Valkey and Keycloak, `scripts/check_migrations.sh` needs PostgreSQL, and the web e2e needs PostgreSQL and Valkey. This step gives each worktree its own copies of them.

Create `scripts/test-services/db.yml`:

```yaml
# PostgreSQL and Valkey for the api tests, scripts/check_migrations.sh and the web e2e
# (megoocolab.toml; the runner picks the ports).
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: megooflags
      POSTGRES_PASSWORD: megooflags
      POSTGRES_DB: megooflags
    ports: ["127.0.0.1:${PG_PORT}:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U megooflags -d megooflags"]
      interval: 2s
      timeout: 5s
      retries: 30
  valkey:
    image: valkey/valkey:8
    ports: ["127.0.0.1:${VALKEY_PORT}:6379"]
    healthcheck:
      test: ["CMD", "valkey-cli", "ping"]
      interval: 2s
      timeout: 5s
      retries: 30
```

Create `scripts/test-services/keycloak.yml`. It holds the `keycloak` service from `deploy/compose.ci.yml` (same image, command, environment and healthcheck), without `profiles`, with `ports: ["127.0.0.1:${KEYCLOAK_PORT}:8080"]` and the volume `../../deploy/keycloak:/opt/keycloak/data/import:ro`.

Create `megoocolab.toml`:

```toml
[services.db]
description = "PostgreSQL and Valkey for scripts/check_migrations.sh and the web e2e"
compose = ["scripts/test-services/db.yml"]
ports = ["PG_PORT", "VALKEY_PORT"]

[services.db.env]
MEGOOFLAGS_DATABASE_URL = "postgresql+asyncpg://megooflags:megooflags@localhost:${PG_PORT}/megooflags"
MEGOOFLAGS_VALKEY_URL = "redis://localhost:${VALKEY_PORT}/0"

[services.api]
description = "PostgreSQL, Valkey and Keycloak for the full api test suite"
compose = ["scripts/test-services/db.yml", "scripts/test-services/keycloak.yml"]
ports = ["PG_PORT", "VALKEY_PORT", "KEYCLOAK_PORT"]

[services.api.env]
MEGOOFLAGS_DATABASE_URL = "postgresql+asyncpg://megooflags:megooflags@localhost:${PG_PORT}/megooflags"
MEGOOFLAGS_VALKEY_URL = "redis://localhost:${VALKEY_PORT}/0"
MEGOOFLAGS_KEYCLOAK_URL = "http://localhost:${KEYCLOAK_PORT}"
```

In `AGENTS.md` section 9, add the runner commands next to the existing `docker run` instructions ("With megoocolab-runner, each worktree gets its own containers:"):
- `megoocolab-runner services run api -- bash -c 'cd api && uv run pytest'`
- `megoocolab-runner services run db -- bash scripts/check_migrations.sh`

Keep the `docker run` commands for humans without the runner.

**Prove it:** run both commands. The api suite passes (report the count, about 2,100) and the migration check passes.

**Commit:** `chore: megoocolab.toml test service sets (MF-88)`.

### 2. Chart skeleton and version check

**Failing tests first** (`scripts/tests/test_check_version.py`, `conftest.py`):
- `make_repo(version, api, web, chart="0.1.0", app="0.1.0")` also writes `deploy/helm/megooflags/Chart.yaml`:

  ```yaml
  apiVersion: v2
  name: megooflags
  version: <chart>
  appVersion: "<app>"
  ```
- `test_matching_versions_pass` expects the two extra keys `"deploy/helm/megooflags/Chart.yaml (version)": "0.1.0"` and `"deploy/helm/megooflags/Chart.yaml (appVersion)": "0.1.0"`.
- `test_chart_version_mismatch_fails` (`chart="0.2.0"`) → `["deploy/helm/megooflags/Chart.yaml (version) has 0.2.0, VERSION has 0.1.0"]`.
- `test_chart_app_version_mismatch_fails`, the same for `appVersion`.
- `test_missing_chart_fails` → both keys are `<missing>`.
- `test_chart_app_version_unquoted` (`appVersion: 0.1.0` without quotes) passes.

Run `uv run --frozen --project api pytest scripts/tests/test_check_version.py`. It fails: the keys are missing.

**Implement:** in `read_versions`, read `root / "deploy/helm/megooflags/Chart.yaml"` with `re.compile(r'^(version|appVersion):\s*["\']?([^"\'\s]+)["\']?\s*$', re.M)`. No yaml import: `release.yml` runs this script under the system `python3`. Update the module docstring.

Create the chart:
- `deploy/helm/megooflags/Chart.yaml`: `apiVersion: v2`, `name: megooflags`, `description: Open source feature flag platform: web UI, API, and worker nodes.`, `type: application`, `version: 0.1.0`, `appVersion: "0.1.0"`, `home`, `sources` (the GitHub URL), `kubeVersion: ">=1.28.0-0"`.
- `.helmignore` with the usual editor patterns.
- `values.yaml` in full (see "Shared contracts").
- `templates/_helpers.tpl` with `megooflags.name`, `fullname`, `labels`, `selectorLabels`, `image`, `secretName`, `configName`.
- `templates/_validate.tpl`, empty for now.

Run `helm lint deploy/helm/megooflags --set secretKey=$(openssl rand -hex 32)`. If `helm` is missing, install Helm v3.19.0 into `~/.local/bin` (the commands are in step 14).

In `AGENTS.md` section 5, add item `deploy/helm/megooflags/Chart.yaml` (`version` and `appVersion`) to the bump list.

**Pass:** `python3 scripts/check_version.py` prints `version OK: 0.1.0`; `uv run --frozen --project api pytest scripts/tests`; ruff check and format from `api/`.

**Commit:** `feat(helm): chart skeleton versioned with VERSION (MF-88)`.

### 3. Values schema, validation, ConfigMap and Secret

**Failing tests** in the new `scripts/tests/test_helm_chart.py`, using the helpers from "Shared contracts":
- `test_required_values[...]` is parametrized over every row of the validation table: the values given, and the message expected in `render_error`.
- `test_unknown_role_fails` (`role: edge`): stderr contains `role`.
- `test_config_defaults_match_compose[standalone|control-plane|worker]`. Compare with `compose_api_env` of `docker-compose.yml`, `docker-compose.control-plane.yml` and `docker-compose.worker.yml`. Ignore the keys the chart takes elsewhere: `MEGOOFLAGS_SECRET_KEY`, `MEGOOFLAGS_DATABASE_URL`, `MEGOOFLAGS_VALKEY_URL`, `MEGOOFLAGS_NODE_TOKEN`, `MEGOOFLAGS_CONTROL_PLANE_URL` and `MEGOOFLAGS_CONTROL_PLANE_CA_FILE`. Every remaining compose key is in the ConfigMap with the compose default, except `MEGOOFLAGS_API_WORKERS == "2"`. `MEGOOFLAGS_ROLE` equals the role.
- `test_worker_config_has_no_control_plane_keys`.
- `test_secret_keys_standalone` covers the keys and the bundled URLs, and that the password is URL-quoted (`postgresql.password: "p@ss"` → `p%40ss`).
- `test_existing_secret_renders_no_secret`.
- `test_external_urls_used` (`postgresql.enabled: false` with an external URL → that URL in the Secret, no `POSTGRES_PASSWORD`).
- `test_worker_secret_has_one_token_per_replica`.

Run `uv run --frozen --project api pytest scripts/tests/test_helm_chart.py -v`. It fails (no templates).

**Implement** `values.schema.json` (draft-07: `role` enum, `secretKey` string, `config` values of type string, number or boolean, booleans, integers ≥ 0 for replicas, arrays and objects), `_validate.tpl`, `configmap.yaml` (includes the validation first) and `secret.yaml` (`type: Opaque`, `stringData`), as described in "Shared contracts".

**Pass:** the same command, ruff, and `helm lint` for the three roles.

**Commit:** `feat(helm): values, validation, settings and secrets (MF-88)`.

### 4. API, web and migration Job

**Failing tests:**
- `test_standalone_resources` and `test_control_plane_resources`: `by_kind` equals exactly ConfigMap, Secret, the Deployments `mf-megooflags-api` and `-web`, their Services, Job `mf-megooflags-migrate-1`, StatefulSet and Service `-postgresql`, Deployment and Service `-valkey`. Add the stores to this test in step 5 if you do this step first: for now, assert only the subset this step creates.
- `test_image_tag_defaults_to_app_version` (`ghcr.io/mmhy2003/megooflags-api:<Chart appVersion>`, read from `Chart.yaml`); `test_image_registry_empty`.
- `test_api_waits_for_migrations` (init container command contains `alembic current` and `(head)`).
- `test_migrate_job` (`alembic upgrade head`, `backoffLimit: 0`, `restartPolicy: Never`, `migrate.annotations` copied).
- `test_checksum_annotations_change` (changing `config.MEGOOFLAGS_LOG_LEVEL` changes `checksum/config` on the api pod template).
- `test_web_points_at_api_service` (`MEGOOFLAGS_API_URL=http://mf-megooflags-api:8000`).
- `test_pods_non_root` (uid 10001, `allowPrivilegeEscalation: false`).
- `test_api_replicas` (`api.replicas: 3`).

**Implement** `templates/api.yaml`, `web.yaml` and `migrate-job.yaml` (scripts, probes and pod settings as described in "Shared contracts"). They are rendered only when `role` is not `worker`. Set `strategy.rollingUpdate` to `maxUnavailable: 0`, `maxSurge: 1` on both Deployments.

**Pass:** pytest on the file, `helm lint`, ruff.

**Commit:** `feat(helm): api, web and migration job (MF-88)`.

### 5. Bundled PostgreSQL and Valkey

**Failing tests:**
- `test_postgresql_statefulset` (image `postgres:16`, PVC template 8Gi, `storageClassName` only when set, `POSTGRES_PASSWORD` from the Secret, `PGDATA=/var/lib/postgresql/data/pgdata`, readiness `pg_isready -U megooflags -d megooflags`, uid 999).
- `test_valkey_no_persistence` (args `valkey-server --save "" --appendonly no`, no volume claims).
- `test_stores_disabled` (with external URLs, neither store renders).
- `test_worker_has_no_stores`.
- The full resource sets in `test_standalone_resources` and `test_control_plane_resources`.

**Implement** `templates/postgresql.yaml` (StatefulSet with `serviceName` `<fullname>-postgresql`, plus a ClusterIP Service) and `templates/valkey.yaml` (Deployment and Service, readiness `valkey-cli ping`), rendered only when the role is not `worker` and `.enabled` is true.

**Pass:** pytest on the file, `helm lint`.

**Commit:** `feat(helm): bundled PostgreSQL and Valkey (MF-88)`.

### 6. Worker role

**Failing tests:**
- `test_worker_resources`: exactly ConfigMap, Secret, StatefulSet `mf-megooflags-worker`, Services `mf-megooflags-worker` and `-worker-headless`. No web, api, Job, PostgreSQL, Valkey or `MEGOOFLAGS_SECRET_KEY`.
- `test_worker_replicas_follow_tokens` (3 tokens → `replicas: 3`); `test_worker_existing_secret_replicas`.
- `test_worker_picks_token_by_ordinal` (command contains `token-${ordinal}` and the Secret's name; the tokens volume has `defaultMode: 288` (0440)).
- `test_worker_volume` (claim `data` 1Gi mounted at `/var/lib/megooflags`).
- `test_worker_ca_mount` (`controlPlaneCa.configMap: cp-ca` → mounted at `/etc/megooflags/certs`, and `MEGOOFLAGS_CONTROL_PLANE_CA_FILE=/etc/megooflags/certs/ca.crt` in the ConfigMap).
- `test_worker_insecure_flag`.
- `test_worker_parallel_pods` (`podManagementPolicy: Parallel`, `updateStrategy: RollingUpdate`).
- `test_worker_probes` (`/healthz` and `/readyz`).

**Implement** `templates/worker.yaml`, rendered only for `role: worker`: the StatefulSet with the token script, `serviceName: <fullname>-worker-headless`, `volumeClaimTemplates` named `data`, and pod settings as described in "Shared contracts"; the ClusterIP Service; the headless Service (`clusterIP: None`, `publishNotReadyAddresses: true`).

**Pass:** pytest on the file, `helm lint deploy/helm/megooflags --set role=worker --set worker.controlPlaneUrl=https://cp.example.com --set 'worker.nodeTokens[0]=mf_node_x'`.

**Commit:** `feat(helm): worker StatefulSet with one node token per replica (MF-88)`.

### 7. Ingress and HTTPRoute

**Failing tests:**
- `test_ingress_paths_match_caddyfile[control-plane|worker]`. The set of `(pathType, path)` routed to the api or worker Service equals `caddy_paths("Caddyfile")` or `caddy_paths("Caddyfile.worker")`. For control planes, the only other path is `("Prefix", "/")` → web; workers have no other path. `/metrics` appears in no path.
- `test_httproute_paths_match_caddyfile[...]`, the same over `matches[].path`.
- `test_httproute_api_headers_and_timeout` (filter `ResponseHeaderModifier` sets the three headers; `timeouts.request == "0s"`; the web rule has neither).
- `test_httproute_switches_off` (`apiResponseHeaders: false` → no filters; `timeouts.request: ""` → no `timeouts`).
- `test_ingress_passthrough` (className, annotations, host, tls).
- `test_routing_off_by_default`.

**Implement** `templates/ingress.yaml` and `templates/httproute.yaml` as described in "Routing".

**Pass:** pytest on the file, `helm lint` with `ingress.enabled=true` and `httpRoute.enabled=true`.

**Commit:** `feat(helm): Ingress and Gateway API HTTPRoute routing (MF-88)`.

### 8. ServiceMonitor, lint and kubeconform tests

**Failing tests:**
- `test_service_monitor[control-plane|worker]` (selects the api or worker component, `port: http`, `path: /metrics`, labels copied; off by default).
- `test_helm_lint[standalone|control-plane|worker]` runs `helm lint CHART -f values.json` with return code 0.
- `test_kubeconform[standalone|control-plane|worker]` pipes `helm template` with ingress, httpRoute and serviceMonitor enabled into `kubeconform -strict -summary -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'`, with return code 0. It skips when `kubeconform` is missing and `MEGOOFLAGS_REQUIRE_HELM` is unset.

**Implement** `templates/servicemonitor.yaml`. Fix anything `-strict` reports.

**Pass:** install kubeconform v0.7.0 locally (step 14 commands), then `MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest scripts/tests/test_helm_chart.py -v`.

**Commit:** `feat(helm): ServiceMonitor, and lint and schema checks (MF-88)`.

### 9. Chart README

**Failing test:** `test_readme_describes_every_value`. For each top-level key of `values.yaml` (loaded with yaml), `` `<key>` `` appears in `deploy/helm/megooflags/README.md`. Also check that the README contains `oci://ghcr.io/mmhy2003/charts/megooflags`.

**Write `README.md`** (starts with `# MegooFlags Helm chart`; links to the site are absolute `https://mmhy2003.github.io/MegooFlags/...`, links to repository files are relative). Sections:
1. Install a single node.
2. A control plane and workers: the control plane first; the Nodes page; the worker release; `controlPlaneUrl` as a public https URL or `http://<release>-megooflags-api.<ns>.svc:8000` with `controlPlaneInsecure`.
3. Values: one table row per top-level value.
4. Secrets and `existingSecret`, with its keys.
5. Routing: Ingress, and HTTPRoute with its Extended features and their off switches. A note for Traefik, HAProxy, ingress-nginx and cloud load balancers: no buffering, an idle timeout above 60 s, and the three API headers. API responses behind a plain Ingress lack them; web pages always have them.
6. Metrics: `/metrics` is never routed; the ServiceMonitor.
7. Upgrades: the control-plane release first, then the workers; migration failures and `kubectl logs job/<release>-megooflags-migrate-<revision>`; no `helm rollback` across a migration (restore the backup, as in upgrades.md).
8. PostgreSQL: the default password and how to change it by hand (`ALTER USER` through `kubectl exec`, then `helm upgrade` with the new password); a managed database for production.
9. Uninstall: the PVCs are kept, and how to delete them.
10. Argo CD (`migrate.annotations`, not tested) and Flux.
11. Several API replicas.

**Pass:** pytest on the file.

**Commit:** `docs(helm): chart README (MF-88)`.

### 10. End-to-end test on kind

Write `scripts/helm_test.sh` following the outline in "Shared contracts" (steps 1 to 10). It needs `docker`, `kind` v0.30.0, `kubectl`, `helm` v3.19.0, `curl`, `openssl` and `python3`, and the images `megooflags-api:ci` and `megooflags-web:ci` (`docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web`). Each check calls `fail "<what>"` with a precise message. The script is the test: it must fail before the chart works and pass after.

**Run:** `bash scripts/helm_test.sh` prints `helm OK`. On a shared machine, set `MF_HELM_CLUSTER` and the two port variables to avoid clashes. Fix the chart for anything the cluster reveals; every chart fix needs a render test that fails without it. Add `# shellcheck` comments in the style of the other scripts.

**Commit:** `test(helm): end-to-end install on kind (MF-88)`.

### 11. CI helm job

**Failing tests** in `scripts/tests/test_workflows.py`:
- `test_ci_has_helm_job`: `jobs.helm.needs == "images"`; `env.MEGOOFLAGS_REQUIRE_HELM` is set; a step uses `azure/setup-helm@v4` with `version: v3.19.0`; the run steps contain `uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py` and `bash scripts/helm_test.sh`.

**Implement** the `helm` job in `.github/workflows/ci.yml`, after `cluster`, with the comment `# The Helm chart (deploy/helm/megooflags): render tests, lint, kubeconform, and an install on kind.` and `timeout-minutes: 30`. Steps:
1. Checkout, the `v` output, buildx, and loading both images exactly as in the `cluster` job.
2. `astral-sh/setup-uv@v6` and the SAML apt step, as in the `version` job.
3. `azure/setup-helm@v4` with `version: v3.19.0`.
4. A step that downloads `kind` v0.30.0 (`https://kind.sigs.k8s.io/dl/v0.30.0/kind-linux-amd64`) and kubeconform v0.7.0 (`https://github.com/yannh/kubeconform/releases/download/v0.7.0/kubeconform-linux-amd64.tar.gz`) into `$RUNNER_TEMP/bin` and adds it to `$GITHUB_PATH`.
5. The pytest command, then `bash scripts/helm_test.sh` with `MF_HELM_LOGS: ${{ runner.temp }}/helm-logs`.
6. On failure, upload `helm-logs`.

**Pass:** pytest on `scripts/tests`, and `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest`.

**Commit:** `ci: helm job for the chart (MF-88)`.

### 12. Release: package and push the chart

**Failing tests:**
- `test_release_chart_job` in `test_workflows.py`: `jobs.chart.needs == ["meta", "merge"]`; `permissions.packages == "write"`; a run step contains `helm package deploy/helm/megooflags --version "$VERSION" --app-version "$VERSION"`; `env.VERSION` is `${{ needs.meta.outputs.image_version }}`; a run contains `helm push` and `oci://ghcr.io/mmhy2003/charts`; an `upload-artifact` step has `name: chart`.
- `test_release_job_attaches_chart`: `release.needs` includes `chart`, and it downloads the `chart` artifact.
- In `test_compose_files.py`, extend `test_release_attaches_cluster_files` (or add `test_release_attaches_chart`) to assert that `megooflags-${{ needs.meta.outputs.image_version }}.tgz` is in `release.yml`.

**Implement** in `.github/workflows/release.yml`:
- The `chart` job, with the comment `# The Helm chart, versioned like the images: oci://ghcr.io/mmhy2003/charts/megooflags.`. Steps: checkout; `azure/setup-helm@v4` v3.19.0; package into `dist/`; `echo "$GH_TOKEN" | helm registry login ghcr.io --username "$GITHUB_ACTOR" --password-stdin` with `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`; `helm push "dist/megooflags-$VERSION.tgz" oci://ghcr.io/mmhy2003/charts`; upload `dist/megooflags-*.tgz` as `chart` with `retention-days: 1`.
- In `release`: `needs: [meta, merge, chart]`, a `download-artifact` step for `chart`, and `"megooflags-${{ needs.meta.outputs.image_version }}.tgz"` appended to the `gh release create` file list.

**Pass:** pytest on `scripts/tests`, actionlint. Agents never push tags or charts.

**Commit:** `ci(release): publish the Helm chart to GHCR and attach it to the release (MF-88)`.

### 13. Docs site page and pointers

**Changes:**
- `site/sync.manifest.json`: add `{"source": "deploy/helm/megooflags/README.md", "dest": "deploy/kubernetes.md", "title": "Kubernetes (Helm)", "description": "Install MegooFlags on Kubernetes with the Helm chart: single node, control plane and workers."}` before the `perf/REPORT.md` entry.
- Add `src/content/docs/deploy/kubernetes.md` to `site/.gitignore` and `site/.prettierignore`, under the synced block.
- `site/astro.config.mjs`: add `"deploy/kubernetes"` after `"deploy/multi-node-migration"`.
- Pointers, one or two sentences each, linking `/MegooFlags/deploy/kubernetes/`:
  - `multi-node.md`, after the roles section: "On Kubernetes, see Kubernetes (Helm)".
  - `upgrades.md`, "Upgrade a multi-node deployment": with Helm, upgrade the control-plane release first, then the worker release; image tags follow the chart's `appVersion`.
  - `observability.md`, Prometheus metrics: `metrics.serviceMonitor.enabled`.
  - `security.md`, Operator notes: without Caddy, the API's three headers come from the HTTPRoute filter or the ingress controller.

**Test:** `site/tests/sync.test.ts` already checks that each manifest entry is git-ignored (it fails before the `.gitignore` line).

**Run:** `cd site && pnpm install --frozen-lockfile && pnpm format:check && pnpm check && pnpm build && pnpm test`. On the no-AVX2 build machine, use `npx -y pnpm@10.34.9` and the workaround in the team memory. Check that the build output ends with `[build] Complete!`.

**Commit:** `docs(site): Kubernetes (Helm) page and pointers (MF-88)`.

### 14. Project docs: AGENTS.md, CHANGELOG, ROADMAP, README

- **AGENTS.md:**
  - Section 1 layout: in the `deploy/` row, mention `helm/megooflags/` (the Helm chart, see its README).
  - Section 9: a `# helm` block. Install the tools once into `~/.local/bin`: Helm v3.19.0 (`curl -fsSL https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz | tar -xz -C /tmp && install /tmp/linux-amd64/helm ~/.local/bin/`), kind v0.30.0, kubeconform v0.7.0, and kubectl. Then run `MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py` and `bash scripts/helm_test.sh` (prints `helm OK`; needs the `:ci` images).
  - Section 10: the release also pushes `oci://ghcr.io/mmhy2003/charts/megooflags:<image version>` and attaches `megooflags-<version>.tgz`. A maintainer makes the GHCR package `charts/megooflags` public after the first push (once). Agents never push charts.
- **CHANGELOG.md** `[Unreleased]` / Added: "Helm chart for Kubernetes (`oci://ghcr.io/mmhy2003/charts/megooflags`): a single node, a control plane, or worker nodes (one node token per replica), with Ingress or Gateway API routing and bundled or external PostgreSQL and Valkey; see https://mmhy2003.github.io/MegooFlags/deploy/kubernetes/."
- **ROADMAP.md:** remove "- A Helm chart." from Phase 2, and add "and a Helm chart for Kubernetes" to the 1.0 Deployment bullet.
- **README.md:** a short "Kubernetes" section after "Multi-node" with the `helm install` command and a link to the page; a `deploy/` layout line that mentions the Helm chart.

**Run:** `python3 scripts/check_version.py`; `uv run --frozen --project api pytest scripts/tests`; the site build and tests (ROADMAP is synced).

**Commit:** `docs: Helm chart in AGENTS, CHANGELOG, ROADMAP and README (MF-88)`.

## Final verification

```bash
python3 scripts/check_version.py                                        # version OK: 0.1.0
uv run --frozen --project api pytest scripts/tests                       # all pass
MEGOOFLAGS_REQUIRE_HELM=1 uv run --frozen --project api pytest -v scripts/tests/test_helm_chart.py
(cd api && uv run ruff check . ../scripts && uv run ruff format --check . ../scripts)
docker build -t megooflags-api:ci api && docker build -t megooflags-web:ci web && bash scripts/helm_test.sh   # helm OK
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest
(cd site && pnpm install --frozen-lockfile && pnpm format:check && pnpm check && pnpm build && pnpm test)
megoocolab-runner services run api -- bash -c 'cd api && uv run pytest'   # unchanged API, still green
megoocolab-runner services run db -- bash scripts/check_migrations.sh
helm package deploy/helm/megooflags --version 0.1.0-rc.1 --app-version 0.1.0-rc.1 -d /tmp/chart  # packages; never pushed by an agent
```
