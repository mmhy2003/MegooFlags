# shellcheck shell=bash
# Compose helpers for the multi-node stacks, shared by scripts/cluster_test.sh and scripts/perf.sh.
# Source it after setting `root` (the repository), `network` (the shared docker network), `workers`
# (how many workers wait_streams checks) and MF_CLUSTER_STATE (the directory with the env files).

cp_compose() {
  docker compose -p mf-cp --env-file "$MF_CLUSTER_STATE/cp.env" \
    -f "$root/deploy/docker-compose.control-plane.yml" -f "$root/deploy/compose.ci.yml" \
    -f "$root/scripts/cluster/cp.override.yml" "$@"
}

worker_compose() {
  local n=$1
  shift
  docker compose -p "mf-w$n" --env-file "$MF_CLUSTER_STATE/w$n.env" \
    -f "$root/deploy/docker-compose.worker.yml" -f "$root/scripts/cluster/worker.override.yml" "$@"
}

lb_compose() {
  docker compose --env-file "$MF_CLUSTER_STATE/lb.env" -f "$root/scripts/cluster/lb.yml" "$@"
}

# Attach a service's container to the cluster network, known there by `alias` only (or by its
# container name when the alias is empty). A compose network would also register the service name:
# the control plane's Caddy resolves `api` on every network it is on, so it would proxy to the
# workers' api containers, and each worker's Caddy to the other workers'.
join() {
  local alias=$1 service=$2 id
  shift 2
  id=$("$@" ps -q "$service")
  local flags=()
  [ -z "$alias" ] || flags=(--alias "$alias")
  docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$id" \
    | grep -qw "$network" || docker network connect "${flags[@]}" "$network" "$id"
}

cp_up() {
  cp_compose up -d --wait --wait-timeout 180 caddy
  join cp.megooflags.internal caddy cp_compose
}

worker_up() {
  local n=$1
  worker_compose "$n" up -d --wait --wait-timeout 120
  join "worker-$n" caddy worker_compose "$n"
  join "" api worker_compose "$n"
}

# Wait until each worker's snapshot stream is open again. Heartbeats resume as soon as the control
# plane answers, so the nodes show healthy, while the stream reconnects after its backoff (up to
# 30 s): a change made in between reaches the worker only once the stream is back.
wait_streams() {
  local n
  for n in $(seq "$workers"); do
    for _ in $(seq 60); do
      worker_compose "$n" exec -T api python -c "import urllib.request; \
print(urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=5).read().decode())" \
        2>/dev/null | grep -q '^megooflags_worker_control_plane_connected 1' && continue 2
      sleep 1
    done
    echo "worker-$n did not reopen its stream to the control plane" >&2
    return 1
  done
}

# The memory of a compose project's containers, in MiB.
mem_mib() {
  # shellcheck disable=SC2046  # one argument per container id
  docker stats --no-stream --format '{{.MemUsage}}' $("$@" ps -q) | awk '
    { v = $1; u = v; gsub(/[0-9.]/, "", u); sub(/[A-Za-z]+$/, "", v)
      if (u == "GiB") v *= 1024; else if (u == "KiB") v /= 1024; else if (u == "B") v /= 1048576
      total += v }
    END { printf "%.1f\n", total }'
}

# `bootstrap_admin <base> <curl-tls-args...>` runs first-run setup, which makes the first admin and
# signs them in, then uses that session to mint an org admin service token for the management API.
# It prints the token.
bootstrap_admin() {
  local base=$1 jar password created
  shift
  jar="$MF_CLUSTER_STATE/cookies"
  password="cluster-$(openssl rand -hex 16)"
  curl -fsS "$@" -c "$jar" -o /dev/null -H "Origin: ${base}" -H 'Content-Type: application/json' \
    -d "{\"email\":\"admin@example.com\",\"name\":\"Cluster Admin\",\"password\":\"${password}\"}" \
    "${base}/api/setup" || { echo "POST /api/setup failed" >&2; return 1; }
  created=$(curl -fsS "$@" -b "$jar" -H "Origin: ${base}" -H 'Content-Type: application/json' \
    -d '{"name":"cluster","bindings":[{"role":"admin","scope":"org"}]}' "${base}/api/tokens") \
    || { echo "POST /api/tokens failed" >&2; return 1; }
  python3 -c 'import json, sys; print(json.load(sys.stdin)["secret"])' <<<"$created"
}
