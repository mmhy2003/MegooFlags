#!/usr/bin/env bash
# Runs the commands of site/src/content/docs/start/quick-start.mdx against megooflags-{api,web}:ci
# and fails if the time from `docker compose up` to the first evaluated flag is five minutes or more.
# The files of deploy/ stand in for the release download, compose.ci.yml selects the local images,
# and the stack listens on port 8080 instead of 80. scripts/tests/test_quickstart_doc.py checks that
# the documented cp, sed, docker compose up and curl lines appear here verbatim.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
workdir="$(mktemp -d)"
compose=(docker compose -p mf-quickstart -f docker-compose.yml -f compose.ci.yml)

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    "${compose[@]}" ps -a || true
    "${compose[@]}" logs --no-color || true
  fi
  "${compose[@]}" down -v --remove-orphans || true
  cd /
  rm -rf "$workdir"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

cd "$workdir"
cp "$root/deploy/docker-compose.yml" "$root/deploy/Caddyfile" "$root/deploy/.env.example" .
cp "$root/deploy/compose.ci.yml" compose.ci.yml

cp .env.example .env
sed -i "s/^MEGOOFLAGS_SECRET_KEY=.*/MEGOOFLAGS_SECRET_KEY=$(openssl rand -hex 32)/" .env
sed -i "s/^MEGOOFLAGS_HTTP_PORT=.*/MEGOOFLAGS_HTTP_PORT=8080/" .env
grep -q '^MEGOOFLAGS_SECRET_KEY=[0-9a-f]\{64\}$' .env || fail "the sed line did not set the secret key"

start=$(date +%s)
docker compose -p mf-quickstart -f docker-compose.yml -f compose.ci.yml up -d --wait

# The UI steps, through the API as in scripts/smoke.sh: the first admin, a token, the project,
# the flag turned on in Production and a server SDK key.
base="http://localhost:8080"
password="quickstart-$(openssl rand -hex 16)"
curl -fsS -c cookies -o /dev/null -H "Origin: ${base}" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@example.com\",\"name\":\"Quick Start\",\"password\":\"${password}\"}" \
  "${base}/api/setup" || fail "POST /api/setup failed"
MF_TOKEN=$(curl -fsS -b cookies -H "Origin: ${base}" -H 'Content-Type: application/json' \
  -d '{"name":"quick-start","bindings":[{"role":"admin","scope":"org"}]}' \
  "${base}/api/tokens" | jq -r .secret)
curl -fsS -o /dev/null -H "Authorization: Bearer $MF_TOKEN" -H 'Content-Type: application/json' \
  -d '{"key":"shop","name":"Shop"}' "${base}/api/projects" || fail "POST /api/projects failed"
curl -fsS -o /dev/null -H "Authorization: Bearer $MF_TOKEN" -H 'Content-Type: application/json' \
  -d '{"key":"new-checkout","name":"New checkout","type":"boolean"}' \
  "${base}/api/projects/shop/flags" || fail "creating the flag failed"
curl -fsS -o /dev/null -H "Authorization: Bearer $MF_TOKEN" -H 'Content-Type: application/json' \
  -d '{"enabled":true}' \
  "${base}/api/projects/shop/flags/new-checkout/environments/production/toggle" \
  || fail "turning the flag on failed"
MF_SDK_KEY=$(curl -fsS -H "Authorization: Bearer $MF_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"quick-start","kind":"server"}' \
  "${base}/api/projects/shop/environments/production/sdk-keys" | jq -r .key)
[[ "$MF_SDK_KEY" == mf_server_* ]] || fail "no server SDK key: $MF_SDK_KEY"

body=$(curl -s -X POST http://localhost:8080/ofrep/v1/evaluate/flags/new-checkout -H "Authorization: Bearer $MF_SDK_KEY" -H 'Content-Type: application/json' -d '{"context":{"targetingKey":"user-1"}}')
grep -q '"value":true' <<<"$body" || fail "expected \"value\":true, got: $body"

elapsed=$(( $(date +%s) - start ))
echo "quick start: ${elapsed}s"
[ "$elapsed" -lt 300 ] || fail "the quick start took ${elapsed}s, limit 300s"
