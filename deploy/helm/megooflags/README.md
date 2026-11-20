# MegooFlags Helm chart

The chart installs MegooFlags on Kubernetes 1.28 or later in one of the roles of the
[Compose bundles](https://mmhy2003.github.io/MegooFlags/deploy/multi-node/#roles): a single node
(`standalone`), a control plane, or a set of workers. The chart's version is the server's version:
chart `X.Y.Z` installs the images of release `X.Y.Z`.

A `standalone` or `control-plane` release runs the web UI, the API, a migration Job per Helm
revision and, unless you turn them off, PostgreSQL and Valkey. A `worker` release runs only the
workers. The chart has no reverse proxy: it routes with an Ingress or a Gateway API HTTPRoute, and
TLS comes from your cluster.

## Install a single node

```bash
helm install megooflags oci://ghcr.io/mmhy2003/charts/megooflags --version <v> \
  --namespace megooflags --create-namespace \
  --set secretKey=$(openssl rand -hex 32)
```

`<v>` is a release such as `0.1.0` (see the
[release notes](https://mmhy2003.github.io/MegooFlags/project/release-notes/)). Keep the secret key:
it encrypts SSO and TOTP secrets in the database, and a restored database is unreadable without it.
Use a values file or `existingSecret` (see [Secrets](#secrets-and-existingsecret)) rather than
`--set` on later upgrades.

Nothing is reachable from outside the cluster until you turn on routing. With an Ingress controller:

```yaml
# values.yaml
secretKey: "<the key>"
config:
  MEGOOFLAGS_PUBLIC_URL: https://flags.example.com
ingress:
  enabled: true
  className: traefik
  host: flags.example.com
  tls:
    - hosts: [flags.example.com]
      secretName: flags-example-com-tls
```

```bash
helm install megooflags oci://ghcr.io/mmhy2003/charts/megooflags --version <v> \
  --namespace megooflags --create-namespace -f values.yaml --wait
```

Then open `https://flags.example.com/setup` and create the first admin. To try it without routing:
`kubectl port-forward -n megooflags svc/megooflags-web 3000:3000` and open
`http://localhost:3000/setup` (the web app passes `/api` on to the API). SDKs need routing.

Set `MEGOOFLAGS_PUBLIC_URL` to the address users reach: passkeys and SSO are bound to it.
[Routing](#routing) says what your Ingress controller needs for the SDK streams.

## A control plane and workers

Workers serve SDK and OFREP traffic from a signed copy of the flags, close to your applications,
and keep serving while the control plane is down. See
[Multi-node](https://mmhy2003.github.io/MegooFlags/deploy/multi-node/) for how they work. With Helm
it takes two releases, because the node tokens exist only once the control plane runs.

1. **Install the control plane** like a single node, with `role: control-plane`:

   ```bash
   helm install cp oci://ghcr.io/mmhy2003/charts/megooflags --version <v> \
     --namespace megooflags --create-namespace -f values.yaml --set role=control-plane --wait
   ```

2. **Add the workers on the Nodes page** (in the sidebar, for users with `org.manage`): click
   **Add worker** once per worker replica, and copy each token (`mf_node_...`). A token is shown
   only once.

3. **Install the worker release** with one token per replica, in the same cluster or another one:

   ```yaml
   # workers.yaml
   role: worker
   worker:
     controlPlaneUrl: https://flags.example.com
     nodeTokens:
       - mf_node_...   # replica 0
       - mf_node_...   # replica 1
   ingress:
     enabled: true
     host: sdk.example.com
   ```

   ```bash
   helm install workers oci://ghcr.io/mmhy2003/charts/megooflags --version <v> \
     --namespace megooflags -f workers.yaml --wait
   ```

   `--wait` returns once every worker has its first snapshot. Each replica `workers-megooflags-worker-<n>`
   uses token `n` and keeps its snapshots on its own volume. To add a worker, add a node on the Nodes
   page and its token at the end of the list. A replica whose token is missing (for example
   `worker.replicas: 3` with an `existingSecret` that has two tokens) exits with a message that
   names the missing key; the others keep serving.

`worker.controlPlaneUrl` is either:

- the control plane's public `https://` address, which works from any cluster; or
- inside the same cluster, its API Service:
  `http://<release>-megooflags-api.<namespace>.svc:8000`, for example
  `http://cp-megooflags-api.megooflags.svc:8000`, with `worker.controlPlaneInsecure: true`
  because it is plain HTTP. Traffic then stays inside the cluster.

If the control plane's certificate comes from a private CA, put the PEM in a ConfigMap or Secret and
point `worker.controlPlaneCa` at it:

```yaml
worker:
  controlPlaneCa:
    configMap: company-ca   # or secret: company-ca
    key: ca.crt
```

Point the SDKs at the workers' address (`https://sdk.example.com`), and keep the control plane's
address for the UI, the API, the CLI and Terraform.

## Values

Every top-level value, with its default. `values.yaml` has the nested keys and
`values.schema.json` checks their types.

| Value | Default | Meaning |
|---|---|---|
| `role` | `standalone` | `standalone`, `control-plane` or `worker`. One role per release. |
| `image` | `registry: ghcr.io/mmhy2003`, `tag:` the chart's `appVersion`, `pullPolicy: IfNotPresent` | Where the `megooflags-api` and `megooflags-web` images come from. An empty `registry` uses the bare image names. |
| `imagePullSecrets` | `[]` | Pull secrets for every pod, e.g. `[{name: regcred}]`. |
| `secretKey` | none | `MEGOOFLAGS_SECRET_KEY`, at least 32 characters (`openssl rand -hex 32`). Required unless `role` is `worker` or `existingSecret` is set. The chart never generates it. |
| `existingSecret` | none | The name of a Secret that replaces the chart's own (see [Secrets](#secrets-and-existingsecret)). |
| `config` | the defaults of the Compose files | `MEGOOFLAGS_*`, `OTEL_*` and `FORWARDED_ALLOW_IPS` variables for the api or worker pods, described in [Configuration](https://mmhy2003.github.io/MegooFlags/deploy/configuration/). `MEGOOFLAGS_API_WORKERS` defaults to `2`, because `0` (one per CPU) counts the node's CPUs, not the pod's limit. Workers get only the settings a worker reads. |
| `api` | 1 replica | The API pods: `replicas`, `resources`, `extraEnv`, `podAnnotations`, `nodeSelector`, `tolerations`, `affinity`. |
| `web` | 1 replica | The web UI pods, with the same keys as `api`. |
| `migrate` | none | The migration Job: `annotations` (see [Argo CD](#argo-cd-and-flux)) and `resources`. |
| `postgresql` | enabled, `postgres:16`, password `megooflags`, 8Gi | The bundled PostgreSQL: `enabled`, `image`, `password`, `persistence.size`, `persistence.storageClass`, `resources`. See [PostgreSQL](#postgresql). |
| `externalDatabase` | none | `url`, used when `postgresql.enabled` is false: `postgresql+asyncpg://user:password@host:5432/db`. |
| `valkey` | enabled, `valkey/valkey:8`, no persistence | The bundled Valkey: `enabled`, `image`, `resources`. It holds nothing that needs a backup. |
| `externalValkey` | none | `url`, used when `valkey.enabled` is false: `redis://host:6379/0`. |
| `worker` | none | For `role: worker`: `controlPlaneUrl` (required), `nodeTokens` (one replica per token), `replicas` (only with `existingSecret`), `controlPlaneInsecure`, `controlPlaneCa` (`configMap` or `secret`, and `key`), `persistence.size` (1Gi) and `persistence.storageClass`, and the pod keys of `api`. |
| `ingress` | off | `enabled`, `className`, `annotations`, `host`, `tls`. See [Routing](#routing). |
| `httpRoute` | off | `enabled`, `parentRefs`, `hostnames`, `apiResponseHeaders` (true), `timeouts.request` (`"0s"`). See [Routing](#routing). |
| `metrics` | off | `serviceMonitor.enabled`, `serviceMonitor.interval` (30s), `serviceMonitor.labels`. See [Metrics](#metrics). |

A missing or wrong value fails `helm install` with a message that names it, before anything is
created.

## Secrets and `existingSecret`

By default the chart writes a Secret named `<release>-megooflags` from your values. To keep secrets
out of values files (sealed secrets, an external secrets operator), create the Secret yourself and
set `existingSecret` to its name. It must have the same keys as the chart's own:

| Key | Role | Value |
|---|---|---|
| `MEGOOFLAGS_SECRET_KEY` | standalone, control-plane | The secret key, 32+ characters. |
| `MEGOOFLAGS_DATABASE_URL` | standalone, control-plane | `postgresql+asyncpg://megooflags:<password>@<release>-megooflags-postgresql:5432/megooflags` for the bundled database (URL-encode the password), or your own database. |
| `MEGOOFLAGS_VALKEY_URL` | standalone, control-plane | `redis://<release>-megooflags-valkey:6379/0` for the bundled Valkey, or your own. |
| `POSTGRES_PASSWORD` | standalone, control-plane | The bundled PostgreSQL's password, when `postgresql.enabled`. |
| `token-0`, `token-1`, … | worker | One node token per replica. Set `worker.replicas` to the number of tokens. |

```bash
kubectl create secret generic megooflags-secrets -n megooflags \
  --from-literal=MEGOOFLAGS_SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=MEGOOFLAGS_DATABASE_URL='postgresql+asyncpg://megooflags:...@db.example.com:5432/megooflags' \
  --from-literal=MEGOOFLAGS_VALKEY_URL='redis://megooflags-valkey:6379/0'
```

With the chart's own Secret, the pods restart when a secret value changes. With `existingSecret`
they do not: run `kubectl rollout restart` after you change it.

## Routing

The chart routes the same paths as the Compose bundles' Caddyfiles
([`deploy/Caddyfile`](../../Caddyfile), [`deploy/Caddyfile.worker`](../../Caddyfile.worker)):

- control plane and single node: `/api`, `/sdk`, `/ofrep`, `/healthz` and `/readyz` go to the API,
  everything else to the web UI;
- workers: `/sdk`, `/ofrep`, `/healthz` and `/readyz`, nothing else.

Turn on an Ingress (`ingress.enabled`), a Gateway API HTTPRoute (`httpRoute.enabled`), both, or
neither if you route some other way.

### Gateway API HTTPRoute

```yaml
httpRoute:
  enabled: true
  parentRefs:
    - name: public-gateway
      namespace: gateway-system
  hostnames: [flags.example.com]
```

The API rule of the route uses two Extended features of the Gateway API:

- a `ResponseHeaderModifier` filter that sets `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin` and `X-Frame-Options: DENY` on API responses
  (`httpRoute.apiResponseHeaders`);
- `timeouts.request: "0s"`, so the gateway does not cut SDK streams (`httpRoute.timeouts.request`).

If your gateway does not support one of them, the route is not accepted. Turn it off with
`httpRoute.apiResponseHeaders: false` or `httpRoute.timeouts.request: ""`, and set the equivalent
on the gateway itself.

### Ingress

Headers, buffering and timeouts on an Ingress are controller-specific, so the chart passes
`ingress.annotations` through as they are. Whatever the controller, the SDK streams on
`/sdk/v1/stream` need (as in the
[reverse proxy guide](https://mmhy2003.github.io/MegooFlags/deploy/reverse-proxy/#the-rules-for-sdk-streams)):

- **no response buffering**, so each event is passed on as it arrives;
- **an idle timeout above 60 s**; the API sends a heartbeat after 15 s without traffic, but the
  first one can take up to 45 s;
- for API responses, the three headers `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin` and `X-Frame-Options: DENY`.

Behind a plain Ingress, API responses do not carry these headers unless the controller adds them.
Web pages always do: the web UI sets them itself.

**Traefik** streams without buffering and its default timeouts are long enough. Add the headers with
a Middleware:

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: megooflags-headers
  namespace: megooflags
spec:
  headers:
    customResponseHeaders:
      X-Content-Type-Options: nosniff
      Referrer-Policy: strict-origin-when-cross-origin
      X-Frame-Options: DENY
```

```yaml
ingress:
  annotations:
    traefik.ingress.kubernetes.io/router.middlewares: megooflags-megooflags-headers@kubernetescrd
```

**HAProxy** (the HAProxy Kubernetes Ingress Controller) does not buffer streams. Raise the server
timeout and add the headers:

```yaml
ingress:
  annotations:
    haproxy.org/timeout-server: 3600s
    haproxy.org/response-set-header: |
      X-Content-Type-Options "nosniff"
      Referrer-Policy "strict-origin-when-cross-origin"
      X-Frame-Options "DENY"
```

**ingress-nginx** buffers by default and closes idle connections after 60 s:

```yaml
ingress:
  annotations:
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
```

Its per-Ingress header annotations need snippets, which are off by default; add the headers in the
controller's `add-headers` ConfigMap instead, or accept that API responses lack them.

**Cloud load balancers** (AWS ALB, Google Cloud, Azure Application Gateway) pass server-sent events
on, but their idle timeout is often 60 s: set it to 120 s or more, for example
`alb.ingress.kubernetes.io/load-balancer-attributes: idle_timeout.timeout_seconds=120` on AWS.

Behind a proxy or load balancer, MegooFlags trusts `X-Forwarded-For` and `X-Forwarded-Proto` only
from the addresses in `config.FORWARDED_ALLOW_IPS` (private ranges by default).

## Metrics

The API and the workers serve Prometheus metrics on `/metrics` (port `http`, 8000). The chart never
routes `/metrics`, so it is reachable only inside the cluster. With the Prometheus Operator, turn on
a ServiceMonitor for the api or worker Service:

```yaml
metrics:
  serviceMonitor:
    enabled: true
    interval: 30s
    labels:
      release: kube-prometheus-stack   # whatever your Prometheus selects on
```

The metrics are listed in
[Observability](https://mmhy2003.github.io/MegooFlags/deploy/observability/#prometheus-metrics).

## Upgrades

Read the release notes of every release in between, back up the database (see
[PostgreSQL](#postgresql)), then:

```bash
helm upgrade cp oci://ghcr.io/mmhy2003/charts/megooflags --version <new> \
  --namespace megooflags -f values.yaml --wait
```

Each upgrade runs a new migration Job, `<release>-megooflags-migrate-<revision>`, with the new image.
New API pods wait in their `wait-for-migrations` init container until the database is at their
schema; the old pods keep serving until then.

With workers, upgrade **the control-plane release first, then the worker release**, because a worker
never accepts an older control plane (see
[Rolling upgrades](https://mmhy2003.github.io/MegooFlags/deploy/multi-node/#rolling-upgrades)). The
StatefulSet replaces the workers one at a time; a stopped worker closes its SDK streams within a few
seconds and the SDKs reconnect to the others. Before 1.0, a minor upgrade makes the old workers
Incompatible until they are upgraded, so upgrade them right away.

**If a migration fails**, the Job fails, the new API pods stay in their init container, the old ones
keep serving, and `helm upgrade --wait` fails. The Job's log says why:

```bash
kubectl logs -n megooflags job/<release>-megooflags-migrate-<revision>
```

**`helm rollback` across a migration does not work**: the old image does not know the newer schema,
so its pods never become ready. As in [Upgrades](https://mmhy2003.github.io/MegooFlags/deploy/upgrades/),
going back means restoring the backup: scale the API to zero
(`kubectl scale deployment/<release>-megooflags-api --replicas=0`), restore the dump taken before
the upgrade, then `helm rollback <release> <revision>`.

## PostgreSQL

The bundled PostgreSQL is a single pod with a volume, for trials and small installs. Its Service is
reachable only inside the cluster. Its password defaults to `megooflags`, as in the Compose bundle;
set `postgresql.password` on the first install to choose another.

Back it up with `pg_dump`, and restore with `pg_restore`:

```bash
kubectl exec -n megooflags <release>-megooflags-postgresql-0 -- \
  pg_dump -U megooflags -Fc megooflags > megooflags-$(date +%F).dump
kubectl exec -i -n megooflags <release>-megooflags-postgresql-0 -- \
  pg_restore -U megooflags -d megooflags --clean --if-exists < megooflags-2026-11-17.dump
```

**Changing the password later**: `postgresql.password` is read only when the volume is empty, so
changing the value alone breaks the API's connection. Change it in the database first, then in the
release:

```bash
kubectl exec -n megooflags <release>-megooflags-postgresql-0 -- \
  psql -U megooflags -d megooflags -c "ALTER USER megooflags PASSWORD 'new-password'"
helm upgrade <release> oci://ghcr.io/mmhy2003/charts/megooflags --version <v> \
  --namespace megooflags --reuse-values --set postgresql.password='new-password' --wait
```

**For production**, use a managed database (Amazon RDS, Cloud SQL, Azure Database for PostgreSQL)
or a PostgreSQL operator, with backups and failover. Set `postgresql.enabled: false` and
`externalDatabase.url` (with the `postgresql+asyncpg://` driver; the bundle runs PostgreSQL 16). The same
goes for Valkey with `valkey.enabled: false` and `externalValkey.url`.

## Uninstall

```bash
helm uninstall <release> --namespace megooflags
```

The volumes of PostgreSQL and the workers are kept, so a new install with the same release name
finds the old data. To delete them too:

```bash
kubectl get pvc -n megooflags
kubectl delete pvc -n megooflags data-<release>-megooflags-postgresql-0
kubectl delete pvc -n megooflags data-<release>-megooflags-worker-0 data-<release>-megooflags-worker-1
```

Deleting the PostgreSQL volume deletes every flag, user and audit entry.

## Argo CD and Flux

**Flux** installs the chart with Helm itself (a `HelmRelease` with an `OCIRepository` for
`oci://ghcr.io/mmhy2003/charts/megooflags`), so it needs nothing special.

**Argo CD** renders the chart without Helm revisions, so the migration Job's name does not change
between versions and the Job would not run again. Mark it as a sync hook instead:

```yaml
migrate:
  annotations:
    argocd.argoproj.io/hook: Sync
    argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
```

This setup is documented but not tested in CI.

## Several API replicas

Set `api.replicas` above 1 to run more API pods, as `docker compose --scale api=N` does. Only one
of them runs the background scheduler at a time (a PostgreSQL advisory lock), and they keep each
other up to date through Valkey, so every replica must reach the same Valkey. Each pod runs
`config.MEGOOFLAGS_API_WORKERS` processes; size `api.resources` for that.
