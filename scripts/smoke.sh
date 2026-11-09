#!/usr/bin/env bash
# Compose smoke test: start the stack from locally built megooflags-{api,web}:ci images
# and hit the health endpoints and the web root through Caddy.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
export MEGOOFLAGS_HTTP_PORT="${MEGOOFLAGS_HTTP_PORT:-8080}"
compose=(docker compose -p megooflags-smoke -f "$root/deploy/docker-compose.yml" -f "$root/deploy/compose.ci.yml")

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    "${compose[@]}" ps -a || true
    "${compose[@]}" logs --no-color || true
  fi
  "${compose[@]}" down -v --remove-orphans || true
  exit "$status"
}
trap cleanup EXIT

"${compose[@]}" up -d --wait --wait-timeout 180 caddy

check() {
  local path=$1 expected=$2 body
  body=$(curl -fsS --retry 10 --retry-delay 2 --retry-all-errors "http://localhost:${MEGOOFLAGS_HTTP_PORT}${path}")
  if ! grep -q -- "$expected" <<<"$body"; then
    echo "GET $path: expected '$expected', got: $body" >&2
    return 1
  fi
  echo "GET $path OK"
}

check /healthz '"status":"ok"'
check /readyz '"status":"ready"'
check / 'MegooFlags'
echo "smoke OK"
