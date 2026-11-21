#!/usr/bin/env bash
# End-to-end test of the Helm chart (deploy/helm/megooflags) on a kind cluster, from locally built
# megooflags-{api,web}:ci images. Traefik routes both the chart's Ingress (entrypoint `web`, NodePort
# 30080 -> 127.0.0.1:MF_HELM_INGRESS_PORT) and its Gateway API HTTPRoute (Gateway `traefik-gateway`
# on entrypoint `gw`, NodePort 30081 -> 127.0.0.1:MF_HELM_GATEWAY_PORT).
#
# It installs a control plane (release `cp`, two api replicas) and two workers (release `workers`),
# checks the routes, an SDK stream through both entry points, the workers serving with the api scaled
# to zero, and a `helm upgrade` that runs the next migration Job and rolls every api pod.
#
# Needs docker, kind v0.30.0, kubectl, helm v3.19.0, curl, openssl and python3. On failure the
# cluster state and every pod's logs are written to MF_HELM_LOGS (default /tmp/megooflags-helm-logs).
# The kind cluster MF_HELM_CLUSTER (default megooflags-helm) is deleted at the end either way.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cluster="${MF_HELM_CLUSTER:-megooflags-helm}"
ingress_port="${MF_HELM_INGRESS_PORT:-18480}"
gateway_port="${MF_HELM_GATEWAY_PORT:-18481}"
logs="${MF_HELM_LOGS:-/tmp/megooflags-helm-logs}"

KIND_NODE=kindest/node:v1.34.0
GATEWAY_API=v1.5.1
# The newest traefik/traefik chart with appVersion v3.7.x; the image is pinned separately.
TRAEFIK_CHART_VERSION=42.4.1
TRAEFIK_IMAGE_TAG=v3.7.15

chart="$root/deploy/helm/megooflags"
ns=megooflags
workers_host=workers.megooflags.test
ingress="http://localhost:${ingress_port}"
gateway="http://localhost:${gateway_port}"
work="$(mktemp -d)"
export KUBECONFIG="$work/kubeconfig"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ -s "$KUBECONFIG" ]; then
    rm -rf "$logs"
    mkdir -p "$logs"
    kubectl get all -A >"$logs/all.txt" 2>&1 || true
    kubectl get ingress,httproute,gateway -A -o yaml >"$logs/routes.yaml" 2>&1 || true
    kubectl describe pods -n "$ns" >"$logs/describe.txt" 2>&1 || true
    kubectl describe pods -n traefik >>"$logs/describe.txt" 2>&1 || true
    for target in "$ns" traefik; do
      for pod in $(kubectl get pods -n "$target" -o name 2>/dev/null); do
        kubectl logs -n "$target" "$pod" --all-containers --prefix >"$logs/${target}_${pod#pod/}.log" 2>&1 || true
      done
    done
    echo "logs written to $logs" >&2
  fi
  kind delete cluster --name "$cluster" >/dev/null 2>&1 || true
  rm -rf "$work"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

# status <url> [curl options...]: the HTTP status code, or 000 when nothing answered.
status() {
  local url=$1
  shift
  curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@" "$url" || true
}

# wait_200 <url> [curl options...]: polls for up to 60 s, since Traefik picks up a new route a few
# seconds after it is created, and fails with the last status.
wait_200() {
  local url=$1 code=000
  for _ in $(seq 30); do
    code=$(status "$@")
    [ "$code" = 200 ] && return
    sleep 2
  done
  fail "GET $url returned $code, not 200, for 60 s"
}

json_field() { python3 -c 'import json, sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }

api_pods() {
  kubectl get pods -n "$ns" -l app.kubernetes.io/instance=cp,app.kubernetes.io/component=api \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort
}

# 1. The kind cluster, with the two NodePorts on the host, and the images.
cat >"$work/kind.yaml" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080
        hostPort: ${ingress_port}
        listenAddress: 127.0.0.1
      - containerPort: 30081
        hostPort: ${gateway_port}
        listenAddress: 127.0.0.1
EOF
kind delete cluster --name "$cluster" >/dev/null 2>&1 || true
kind create cluster --name "$cluster" --image "$KIND_NODE" --config "$work/kind.yaml" --wait 120s \
  || fail "kind create cluster failed"
kind load docker-image --name "$cluster" megooflags-api:ci megooflags-web:ci \
  || fail "kind load docker-image failed (build megooflags-api:ci and megooflags-web:ci first)"
echo "cluster OK"

# 2. Gateway API CRDs and Traefik, with the Ingress provider on `web` and the Gateway on `gw`.
# kubectl warns about every int32 and int64 format in the CRDs: keep its output for failures only.
kubectl apply --server-side -f \
  "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API}/standard-install.yaml" \
  >"$work/crds.log" 2>&1 || { cat "$work/crds.log" >&2; fail "the Gateway API CRDs did not apply"; }
cat >"$work/traefik.yaml" <<EOF
image:
  tag: ${TRAEFIK_IMAGE_TAG}
service:
  spec:
    type: NodePort
ports:
  web:
    nodePort: 30080
  websecure:
    expose:
      default: false
  gw:
    port: 8001
    exposedPort: 8001
    expose:
      default: true
    protocol: TCP
    nodePort: 30081
providers:
  kubernetesIngress:
    enabled: true
  kubernetesGateway:
    enabled: true
gateway:
  listeners:
    web: null
    gw:
      port: 8001
      protocol: HTTP
      namespacePolicy:
        from: All
EOF
helm install traefik traefik --repo https://traefik.github.io/charts --version "$TRAEFIK_CHART_VERSION" \
  -n traefik --create-namespace -f "$work/traefik.yaml" --wait --timeout 5m >"$work/traefik.log" 2>&1 \
  || { cat "$work/traefik.log" >&2; fail "Traefik did not install"; }
echo "traefik OK"

# 3. The control plane: two api replicas, an Ingress and an HTTPRoute, both for host localhost so
# that requests for the workers' host match only the worker routes.
cat >"$work/cp.yaml" <<EOF
role: control-plane
image:
  registry: ""
  tag: ci
  pullPolicy: Never
secretKey: $(openssl rand -hex 32)
config:
  MEGOOFLAGS_PUBLIC_URL: ${ingress}
api:
  replicas: 2
ingress:
  enabled: true
  host: localhost
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
httpRoute:
  enabled: true
  hostnames:
    - localhost
  parentRefs:
    - name: traefik-gateway
      namespace: traefik
EOF
helm install cp "$chart" -n "$ns" --create-namespace -f "$work/cp.yaml" --wait --timeout 10m \
  || fail "helm install cp failed"
echo "control plane installed"

# 4. The control-plane routes, through both entry points. The api's /metrics is never routed: the
# catch-all sends /metrics to the web app, which redirects an anonymous visitor.
for entry in "$ingress" "$gateway"; do
  for path in /healthz /readyz /sign-in; do
    wait_200 "$entry$path"
  done
  body=$(curl -s --max-time 10 "$entry/metrics" || true)
  case "$body" in
    *megooflags_http_requests_total*) fail "GET $entry/metrics served the Prometheus metrics" ;;
  esac
done
curl -fsS -D "$work/headers" -o /dev/null "$gateway/healthz" || fail "GET $gateway/healthz failed"
grep -qi '^X-Frame-Options: DENY' "$work/headers" \
  || fail "GET $gateway/healthz has no X-Frame-Options: DENY from the HTTPRoute filter"
accepted=$(kubectl get httproute cp-megooflags -n "$ns" \
  -o jsonpath='{.status.parents[0].conditions[?(@.type=="Accepted")].status}')
[ "$accepted" = True ] || fail "HTTPRoute cp-megooflags is not Accepted (status: '$accepted')"
echo "control-plane routes OK"

# 5. The first admin and an org admin token, as in scripts/proxy_samples_test.sh, then an SDK stream
# held past the heartbeat and a flag change, through each entry point.
password="helm-$(openssl rand -hex 16)"
curl -fsS -c "$work/cookies" -o /dev/null -H "Origin: ${ingress}" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@example.com\",\"name\":\"Helm Admin\",\"password\":\"${password}\"}" \
  "${ingress}/api/setup" || fail "POST /api/setup failed"
created=$(curl -fsS -b "$work/cookies" -H "Origin: ${ingress}" -H 'Content-Type: application/json' \
  -d '{"name":"helm","bindings":[{"role":"admin","scope":"org"}]}' "${ingress}/api/tokens") \
  || fail "POST /api/tokens failed"
token=$(json_field secret <<<"$created")
auth=(-H "Authorization: Bearer ${token}" -H 'Content-Type: application/json')
for entry in "$ingress" "$gateway"; do
  python3 "$root/scripts/proxy_stream_check.py" --proxy "$entry" --api "$ingress" --token "$token" \
    || fail "the stream check through $entry failed"
done
echo "streams OK"

# 6. Two nodes, and a worker release with one node token per replica.
node_tokens=()
for n in 1 2; do
  node=$(curl -fsS "${auth[@]}" -d "{\"name\":\"helm-worker-$n\"}" "${ingress}/api/nodes") \
    || fail "POST /api/nodes helm-worker-$n failed"
  node_tokens+=("$(json_field token <<<"$node")")
done
cat >"$work/workers.yaml" <<EOF
role: worker
image:
  registry: ""
  tag: ci
  pullPolicy: Never
worker:
  controlPlaneUrl: http://cp-megooflags-api.${ns}.svc:8000
  controlPlaneInsecure: true
  nodeTokens:
    - ${node_tokens[0]}
    - ${node_tokens[1]}
ingress:
  enabled: true
  host: ${workers_host}
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
httpRoute:
  enabled: true
  hostnames:
    - ${workers_host}
  parentRefs:
    - name: traefik-gateway
      namespace: traefik
EOF
helm install workers "$chart" -n "$ns" -f "$work/workers.yaml" --wait --timeout 10m \
  || fail "helm install workers failed"
kubectl rollout status statefulset/workers-megooflags-worker -n "$ns" --timeout 5m \
  || fail "statefulset/workers-megooflags-worker did not roll out"
for n in 0 1; do
  ready=$(kubectl get pod "workers-megooflags-worker-$n" -n "$ns" \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')
  [ "$ready" = True ] || fail "pod workers-megooflags-worker-$n is not Ready"
done
echo "workers installed"

# 7. A project and a server SDK key; the workers serve its ruleset through both entry points.
curl -fsS "${auth[@]}" -o /dev/null -d '{"key":"helm","name":"Helm"}' "${ingress}/api/projects" \
  || fail "POST /api/projects helm failed"
created=$(curl -fsS "${auth[@]}" -d '{"name":"helm","kind":"server"}' \
  "${ingress}/api/projects/helm/environments/production/sdk-keys") || fail "POST sdk-keys failed"
sdk_key=$(json_field key <<<"$created")
ruleset=(-H "Host: ${workers_host}" -H "Authorization: Bearer ${sdk_key}")
for entry in "$ingress" "$gateway"; do
  wait_200 "$entry/sdk/v1/ruleset" "${ruleset[@]}"
  code=$(status "$entry/metrics" -H "Host: ${workers_host}")
  [ "$code" = 404 ] || fail "GET $entry/metrics on the worker route returned $code, not 404"
done
echo "worker routes OK"

# 8. The workers keep serving with no api pod at all.
kubectl scale deployment/cp-megooflags-api -n "$ns" --replicas=0 >/dev/null || fail "scale to 0 failed"
for _ in $(seq 60); do
  [ -z "$(api_pods)" ] && break
  sleep 2
done
[ -z "$(api_pods)" ] || fail "api pods remain after scaling to 0: $(api_pods | tr '\n' ' ')"
code=$(status "$ingress/sdk/v1/ruleset" "${ruleset[@]}")
[ "$code" = 200 ] || fail "GET /sdk/v1/ruleset through the workers returned $code with the api down"
kubectl scale deployment/cp-megooflags-api -n "$ns" --replicas=2 >/dev/null || fail "scale to 2 failed"
kubectl rollout status deployment/cp-megooflags-api -n "$ns" --timeout 5m \
  || fail "deployment/cp-megooflags-api did not come back"
echo "control plane outage OK"

# 9. An upgrade runs the next migration Job and replaces every api pod.
before=$(api_pods)
helm upgrade cp "$chart" -n "$ns" --reuse-values --set config.MEGOOFLAGS_LOG_LEVEL=DEBUG \
  --wait --timeout 10m || fail "helm upgrade cp failed"
succeeded=$(kubectl get job cp-megooflags-migrate-2 -n "$ns" -o jsonpath='{.status.succeeded}') \
  || fail "job/cp-megooflags-migrate-2 does not exist"
[ "$succeeded" = 1 ] || fail "job/cp-megooflags-migrate-2 has succeeded='$succeeded', not 1"
if kubectl get job cp-megooflags-migrate-1 -n "$ns" >/dev/null 2>&1; then
  fail "job/cp-megooflags-migrate-1 is still there after the upgrade"
fi
for _ in $(seq 60); do
  [ "$(api_pods | wc -l)" -eq 2 ] && break
  sleep 2
done
after=$(api_pods)
[ "$(wc -l <<<"$after")" -eq 2 ] || fail "expected 2 api pods after the upgrade, got: $(tr '\n' ' ' <<<"$after")"
kept=$(comm -12 <(echo "$before") <(echo "$after"))
[ -z "$kept" ] || fail "api pods were not replaced by the upgrade: $kept"
echo "upgrade OK"

echo "helm OK"
