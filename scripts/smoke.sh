#!/usr/bin/env bash
# Compose smoke test: start the stack from locally built megooflags-{api,web}:ci images and check it
# through Caddy. Mode "http" (default): plain HTTP with two api replicas. Mode "https": automatic HTTPS
# for localhost, with a certificate from Caddy's internal CA.
set -euo pipefail
mode="${1:-http}"
case "$mode" in
  http) export MEGOOFLAGS_SITE_ADDRESS="" replicas=2 ;;
  https) export MEGOOFLAGS_SITE_ADDRESS="localhost" replicas=1 ;;
  *) echo "usage: $0 [http|https]" >&2; exit 2 ;;
esac
root="$(cd "$(dirname "$0")/.." && pwd)"
export MEGOOFLAGS_HTTP_PORT="${MEGOOFLAGS_HTTP_PORT:-8080}"
export MEGOOFLAGS_HTTPS_PORT="${MEGOOFLAGS_HTTPS_PORT:-8443}"
compose=(docker compose -p megooflags-smoke -f "$root/deploy/docker-compose.yml" -f "$root/deploy/compose.ci.yml")
workdir="$(mktemp -d)"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    "${compose[@]}" ps -a || true
    "${compose[@]}" logs --no-color || true
  fi
  "${compose[@]}" down -v --remove-orphans || true
  rm -rf "$workdir"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

"${compose[@]}" up -d --wait --wait-timeout 180 --scale "api=$replicas" caddy

if [ "$mode" = https ]; then
  for _ in $(seq 30); do
    "${compose[@]}" cp caddy:/data/caddy/pki/authorities/local/root.crt "$workdir/root.crt" 2>/dev/null && break
    sleep 2
  done
  [ -s "$workdir/root.crt" ] || fail "Caddy's local CA root was not created"
  base="https://localhost:${MEGOOFLAGS_HTTPS_PORT}"
  tls=(--cacert "$workdir/root.crt")
else
  base="http://localhost:${MEGOOFLAGS_HTTP_PORT}"
  tls=()
fi

check() {
  local path=$1 expected=$2 body
  body=$(curl -fsS "${tls[@]}" --retry 10 --retry-delay 2 --retry-all-errors "${base}${path}")
  grep -q -- "$expected" <<<"$body" || fail "GET $path: expected '$expected', got: $body"
  echo "GET $path OK"
}

check /healthz '"status":"ok"'
check /readyz '"status":"ready"'
check / 'MegooFlags'

code=$(curl -sS "${tls[@]}" -o /dev/null -w '%{http_code}' "${base}/metrics")
[ "$code" = 404 ] || fail "/metrics through Caddy returned $code, expected 404"
echo "GET /metrics via Caddy is 404 OK"

# Spread requests over the replicas; /api/* 404s are access-logged at INFO with route "other".
# Separate loops: /healthz and /api/smoke share one round-robin, so alternating them would send
# every /api/smoke to the same replica.
for _ in $(seq 10); do
  curl -fsS "${tls[@]}" -o /dev/null "${base}/healthz"
done
for _ in $(seq 10); do
  curl -sS "${tls[@]}" -o /dev/null "${base}/api/smoke"
done
echo "repeated requests OK"

"${compose[@]}" exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=5).read().decode())" \
  | grep -q '^megooflags_http_requests_total' || fail "api /metrics has no megooflags_http_requests_total"
echo "internal /metrics OK"

ids=$("${compose[@]}" ps -q api)
[ "$(wc -w <<<"$ids")" -eq "$replicas" ] || fail "expected $replicas api replicas, got: $ids"
for id in $ids; do
  docker logs "$id" 2>&1 | grep -q '"route": "other"' || fail "api container $id has no JSON access-log line"
done
echo "JSON access logs on $replicas replica(s) OK"

if [ "$mode" = https ]; then
  redirect=$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' "http://localhost:${MEGOOFLAGS_HTTP_PORT}/")
  [[ "$redirect" =~ ^30[178]\ https:// ]] || fail "HTTP did not redirect to HTTPS: $redirect"
  echo "HTTP -> HTTPS redirect OK"
else
  if curl -sSk --max-time 5 -o /dev/null "https://localhost:${MEGOOFLAGS_HTTPS_PORT}/healthz" 2>/dev/null; then
    fail "something answers HTTPS on ${MEGOOFLAGS_HTTPS_PORT} in plain-HTTP mode"
  fi
  echo "no HTTPS listener OK"
fi

# Idle footprint: keep the whole stack under 1 GiB so a 2 GB VM has headroom.
mem_mib=$(docker stats --no-stream --format '{{.MemUsage}}' $("${compose[@]}" ps -q) | awk '
  { v = $1; u = v; gsub(/[0-9.]/, "", u); sub(/[A-Za-z]+$/, "", v)
    if (u == "GiB") v *= 1024; else if (u == "KiB") v /= 1024; else if (u == "B") v /= 1048576
    total += v }
  END { printf "%d", total }')
echo "idle memory: ${mem_mib} MiB"
[ "$mem_mib" -lt 1024 ] || fail "stack uses ${mem_mib} MiB, limit 1024 MiB"

echo "smoke OK ($mode)"
