#!/usr/bin/env bash
# Tests the reverse proxy samples in docs/examples/proxies/ against megooflags-{api,web}:ci: the bundle
# runs in plain-HTTP mode on port 8080, and each proxy in turn terminates TLS on 443 with a self-signed
# certificate for localhost (Caddy with its internal CA). scripts/proxy_stream_check.py then loads the
# UI through the proxy, holds an SDK stream past the heartbeat and times a flag change through it.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
samples="$root/docs/examples/proxies"
export MEGOOFLAGS_SITE_ADDRESS="" MEGOOFLAGS_HTTP_PORT=8080 MEGOOFLAGS_TRUSTED_PROXIES=private_ranges
export MEGOOFLAGS_PUBLIC_URL=https://localhost
export MEGOOFLAGS_SECRET_KEY="${MEGOOFLAGS_SECRET_KEY:-$(openssl rand -hex 32)}"
compose=(docker compose -p megooflags-proxies -f "$root/deploy/docker-compose.yml" -f "$root/deploy/compose.ci.yml")
proxy=megooflags-proxy-test
workdir="$(mktemp -d)"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    docker logs "$proxy" 2>&1 | tail -n 50 || true
    "${compose[@]}" logs --no-color || true
  fi
  docker rm -f "$proxy" >/dev/null 2>&1 || true
  "${compose[@]}" down -v --remove-orphans || true
  rm -rf "$workdir"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

"${compose[@]}" up -d --wait --wait-timeout 180 caddy
base="http://localhost:${MEGOOFLAGS_HTTP_PORT}"

# The first admin and an org admin token, as in scripts/smoke.sh.
password="proxies-$(openssl rand -hex 16)"
curl -fsS -c "$workdir/cookies" -o /dev/null -H "Origin: ${base}" -H 'Content-Type: application/json' \
  -d "{\"email\":\"admin@example.com\",\"name\":\"Proxy Admin\",\"password\":\"${password}\"}" \
  "${base}/api/setup" || fail "POST /api/setup failed"
created=$(curl -fsS -b "$workdir/cookies" -H "Origin: ${base}" -H 'Content-Type: application/json' \
  -d '{"name":"proxies","bindings":[{"role":"admin","scope":"org"}]}' "${base}/api/tokens") \
  || fail "POST /api/tokens failed"
token=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["secret"])' <<<"$created")

certs="$workdir/certs"
mkdir "$certs"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -addext subjectAltName=DNS:localhost -keyout "$certs/privkey.pem" -out "$certs/fullchain.pem" 2>/dev/null
chmod 644 "$certs/privkey.pem"
tls_mount=(-v "$certs:/etc/megooflags-proxy:ro")

# Port 80 serves only Caddy's HTTP -> HTTPS redirect here, so move it off a port the host may use.
proxies=(
  "nginx|nginx:1.27|-v $samples/nginx.conf:/etc/nginx/conf.d/default.conf:ro"
  "caddy|caddy:2|-e MF_DOMAIN=localhost -e MF_HTTP_PORT=8081 -v $samples/Caddyfile:/etc/caddy/Caddyfile:ro"
  "traefik|traefik:v3|-e MF_DOMAIN=localhost -v $samples/traefik.yml:/etc/traefik/traefik.yml:ro -v $samples/traefik-dynamic.yml:/etc/traefik/dynamic.yml:ro"
  # The image runs as an unprivileged user, which cannot bind 443 on the host network.
  "haproxy|haproxy:3.0|--user root -v $samples/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro"
)

for entry in "${proxies[@]}"; do
  IFS='|' read -r name image args <<<"$entry"
  echo "== $name ($image)"
  # shellcheck disable=SC2086 # args holds several options
  docker run -d --name "$proxy" --network host "${tls_mount[@]}" $args "$image" >/dev/null
  curl -fsk -o /dev/null --retry 15 --retry-delay 2 --retry-all-errors https://localhost/healthz \
    || fail "$name: https://localhost/healthz did not answer"
  python3 "$root/scripts/proxy_stream_check.py" --insecure --proxy https://localhost --api "$base" \
    --token "$token" || fail "$name: the stream check failed"
  docker rm -f "$proxy" >/dev/null
done

echo "proxies OK"
