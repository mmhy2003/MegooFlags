#!/usr/bin/env bash
# Multi-node failure suite (PRD 8.5): start a control plane, three workers and an nginx load balancer
# from locally built megooflags-{api,web}:ci images, then run scripts/cluster_failures.py against them.
# The control plane serves HTTPS from Caddy's internal CA as cp.megooflags.internal; the workers join
# it over TLS with that CA and serve plain HTTP on host ports 18081..18083 behind the LB on 9443.
#
# `cluster_test.sh ctl <action> [N]` stops and starts the containers for the runner; it only works
# from inside a run, which exports MF_CLUSTER_STATE. On failure the logs are printed and written to
# MF_CLUSTER_LOGS (default /tmp/megooflags-cluster-logs).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
network=megooflags-cluster
workers=3
fresh=4

# shellcheck source=cluster/lib.sh
. "$root/scripts/cluster/lib.sh"

ctl() {
  local action=$1 n=${2:-}
  # Compose progress goes to stderr: the runner reads stdout (mem-worker prints MiB).
  case "$action" in
    stop-worker) worker_compose "$n" stop >&2 ;;
    start-worker) worker_up "$n" >&2 ;;
    restart-worker) worker_compose "$n" restart >&2 ;;
    stop-control-plane) cp_compose stop >&2 ;;
    start-control-plane) cp_up >&2 && wait_streams ;;
    stop-db) cp_compose stop db >&2 ;;
    start-db) cp_compose start db >&2 ;;
    # A worker that has never synced: a new volume each time.
    start-fresh-worker)
      worker_compose "$fresh" down -v >&2
      worker_up "$fresh" >&2
      ;;
    stop-fresh-worker) worker_compose "$fresh" down -v >&2 ;;
    mem-worker) mem_mib worker_compose "$n" ;;
    *) echo "unknown action: $action" >&2; return 2 ;;
  esac
}

if [ "${1:-}" = ctl ]; then
  [ -n "${MF_CLUSTER_STATE:-}" ] || { echo "ctl runs inside scripts/cluster_test.sh" >&2; exit 2; }
  shift
  ctl "$@"
  exit
fi

export MF_CLUSTER_STATE
MF_CLUSTER_STATE="$(mktemp -d)"
state=$MF_CLUSTER_STATE
logs="${MF_CLUSTER_LOGS:-/tmp/megooflags-cluster-logs}"
mkdir -p "$state/certs" "$state/tls"
# The worker api runs as uid 10001 and reads the CA from the mounted directory.
chmod 755 "$state" "$state/certs"

worker_env() {
  local n=$1 token=$2
  cat >"$state/w$n.env" <<EOF
MEGOOFLAGS_CONTROL_PLANE_URL=https://cp.megooflags.internal
MEGOOFLAGS_NODE_TOKEN=$token
MEGOOFLAGS_CONTROL_PLANE_CA_FILE=/etc/megooflags/certs/ca.crt
MF_CLUSTER_CERTS=$state/certs
MF_WORKER_PORT=1808$n
EOF
}

cat >"$state/cp.env" <<EOF
MEGOOFLAGS_SECRET_KEY=${MEGOOFLAGS_SECRET_KEY:-$(openssl rand -hex 32)}
MEGOOFLAGS_HTTP_PORT=${MEGOOFLAGS_HTTP_PORT:-8080}
MEGOOFLAGS_HTTPS_PORT=8443
EOF
echo "MF_CLUSTER_TLS=$state/tls" >"$state/lb.env"
for n in $(seq "$fresh"); do
  worker_env "$n" none
done

teardown() {
  lb_compose down -v --remove-orphans || true
  for n in $(seq "$fresh"); do
    worker_compose "$n" down -v --remove-orphans || true
  done
  cp_compose down -v --remove-orphans || true
  docker network rm "$network" >/dev/null 2>&1 || true
}

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    rm -rf "$logs"
    mkdir -p "$logs"
    for project in cp $(seq -f 'w%g' "$fresh") lb; do
      case "$project" in
        cp) run=(cp_compose) ;;
        lb) run=(lb_compose) ;;
        *) run=(worker_compose "${project#w}") ;;
      esac
      { "${run[@]}" ps -a && "${run[@]}" logs --no-color; } 2>&1 | tee "$logs/$project.log" || true
    done
  fi
  teardown
  rm -rf "$state"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

teardown
docker network create "$network" >/dev/null

cp_up
for _ in $(seq 30); do
  cp_compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$state/certs/ca.crt" 2>/dev/null && break
  sleep 2
done
[ -s "$state/certs/ca.crt" ] || fail "Caddy's local CA root was not created"
chmod 644 "$state/certs/ca.crt"
base="https://localhost:8443"
tls=(--cacert "$state/certs/ca.crt")
curl -fsS "${tls[@]}" --retry 10 --retry-delay 2 --retry-all-errors -o /dev/null "$base/readyz" \
  || fail "the control plane is not ready"
echo "control plane OK"

# First-run setup makes the first admin, whose session mints an org admin token.
MEGOOFLAGS_API_TOKEN=$(bootstrap_admin "$base" "${tls[@]}") || fail "the admin bootstrap failed"
export MEGOOFLAGS_API_TOKEN

# Nodes worker-1..3 and the fresh worker; each join token is shown only once.
for n in $(seq "$fresh"); do
  node=$(curl -fsS "${tls[@]}" -H "Authorization: Bearer ${MEGOOFLAGS_API_TOKEN}" \
    -H 'Content-Type: application/json' -d "{\"name\":\"worker-$n\"}" "${base}/api/nodes") \
    || fail "POST /api/nodes worker-$n failed"
  worker_env "$n" "$(python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])' <<<"$node")"
done
echo "nodes created"

for n in $(seq "$workers"); do
  worker_up "$n"
done
echo "workers started"

openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -addext subjectAltName=DNS:localhost -addext basicConstraints=critical,CA:TRUE \
  -addext keyUsage=critical,digitalSignature,keyCertSign \
  -keyout "$state/tls/privkey.pem" -out "$state/tls/fullchain.pem" 2>/dev/null
lb_compose up -d --wait --wait-timeout 60
# The runner trusts both the control plane's CA and the LB's self-signed certificate.
cat "$state/certs/ca.crt" "$state/tls/fullchain.pem" >"$state/ca-bundle.pem"
echo "load balancer started"

python3 "$root/scripts/cluster_failures.py" \
  --lb https://localhost:9443 --cp "$base" \
  --worker http://localhost:18081 --worker http://localhost:18082 --worker http://localhost:18083 \
  --fresh "http://localhost:1808$fresh" --ca "$state/ca-bundle.pem" \
  --ctl "bash $(printf '%q' "$root/scripts/cluster_test.sh") ctl" \
  || fail "a failure scenario did not hold"

echo "cluster OK"
