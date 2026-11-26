#!/usr/bin/env bash
# Load tests against the PRD NFRs (section 7), from locally built megooflags-{api,web}:ci images:
#
#   bash scripts/perf.sh [tiny|full]
#
# It starts size-limited stacks from the same compose files as the smoke and cluster tests (with the
# overrides in scripts/perf/), runs the mfload scenarios from a container on the stack's network and
# the SDKs' local evaluation guards, then merges the run into perf/results/<VERSION>.json ("full",
# run on the build machine for a release) or perf/results/tiny.json ("tiny", the default, a few
# minutes long, which CI runs; git-ignored). It gates nothing: scripts/perf_report.py renders the
# verdicts. A scenario that does not fit the host runs at the largest size that does, marked
# "reduced". On failure the stacks' logs are written to MF_PERF_LOGS (default /tmp/megooflags-perf-logs).
#
# MF_PERF_ONLY=<section,...> (remote replicas cluster streams footprint local_eval) runs only those
# sections and writes the git-ignored perf/results/only.json instead. MF_PERF_PROFILE=1 records py-spy
# profiles of the api in the remote and footprint sections (scripts/perf/profile.override.yml), with
# the 10k ruleset and the api logs, to MF_PERF_PROFILE_DIR (default /tmp/megooflags-perf-profiles).
set -euo pipefail
profile="${1:-tiny}"
case "$profile" in
  full | tiny) ;;
  *) echo "usage: $0 [tiny|full]" >&2; exit 2 ;;
esac
# MF_PERF_ONLY=<section,...> runs only those sections and writes the git-ignored perf/results/only.json,
# never perf/results/<VERSION>.json. Sections: remote replicas cluster streams footprint local_eval.
only="${MF_PERF_ONLY:-}"
sections="remote replicas cluster streams footprint local_eval"
for s in ${only//,/ }; do
  case " $sections " in
    *" $s "*) ;;
    *) echo "MF_PERF_ONLY: unknown section '$s' (one of: $sections)" >&2; exit 2 ;;
  esac
done
wants() { [ -z "$only" ] || case ",$only," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
profile_on="${MF_PERF_PROFILE:-0}"
profile_dir="${MF_PERF_PROFILE_DIR:-/tmp/megooflags-perf-profiles}"
export MF_PERF_PROFILE_DIR=$profile_dir
[ "$profile_on" != 1 ] || mkdir -p "$profile_dir"
root="$(cd "$(dirname "$0")/.." && pwd)"
version="$(cat "$root/VERSION")"
network=megooflags-perf
http_port="${MEGOOFLAGS_HTTP_PORT:-8080}"
https_port="${MEGOOFLAGS_HTTPS_PORT:-8443}"
secret="${MEGOOFLAGS_SECRET_KEY:-$(openssl rand -hex 32)}"
logs="${MF_PERF_LOGS:-/tmp/megooflags-perf-logs}"

export MF_CLUSTER_STATE
MF_CLUSTER_STATE="$(mktemp -d)"
state=$MF_CLUSTER_STATE
run="$state/run"
mkdir -p "$run" "$state/bin" "$state/certs"
# The worker api runs as uid 10001 and reads the CA from the mounted directory.
chmod 755 "$state" "$state/certs"

# shellcheck source=cluster/lib.sh
. "$root/scripts/cluster/lib.sh"

if [ "$profile" = full ]; then
  nflags=10000 rps=2000 duration=60 warmup=15
  whole_duration=30 whole_warmup=5
  # 20 flips, each longer apart than mfload's 30 s miss window: an update slower than the interval
  # would otherwise count as missed, with no sample, and cut the p95 off at the interval.
  prop_flip=35 prop_hold=700
  big_streams=100000 big_ramp=300 big_hold=600 big_flip=30
  foot_hold=600
else
  nflags=100 rps=50 duration=10 warmup=2
  whole_duration=5 whole_warmup=1
  prop_flip=2 prop_hold=6        # 3 flips
  big_streams=200 big_ramp=5 big_hold=10 big_flip=5
  foot_hold=30
fi

fail() { echo "FAIL: $*" >&2; exit 1; }

# The single-node bundle, with the override in $single_override (scripts/perf/<name>.override.yml),
# plus the py-spy sidecar while profiling=1.
single_override=remote
profiling=0
single() {
  local extra=()
  [ "$profiling" != 1 ] || extra=(-f "$root/scripts/perf/profile.override.yml")
  MEGOOFLAGS_SITE_ADDRESS="" MEGOOFLAGS_SECRET_KEY="$secret" MEGOOFLAGS_HTTP_PORT="$http_port" \
    MEGOOFLAGS_HTTPS_PORT="$https_port" docker compose -p megooflags-perf \
    -f "$root/deploy/docker-compose.yml" -f "$root/deploy/compose.ci.yml" \
    -f "$root/scripts/perf/$single_override.override.yml" "${extra[@]}" "$@"
}
# The services `single up` starts: Caddy (and its dependencies), plus py-spy while profiling.
single_services() {
  if [ "$profiling" = 1 ]; then echo caddy pyspy; else echo caddy; fi
}

# `profile_record <name> <seconds>` samples the api and its Uvicorn workers (PID 1's subprocesses) at
# 50 Hz into $profile_dir/<name>.txt in the background (py-spy's output in <name>.log); a no-op unless
# profiling. py-spy stops after <seconds> x 50 samples, so when it falls behind it runs past the load
# and samples the next one: at 200 Hz it fell minutes behind on 8 busy workers.
profile_record() {
  [ "$profiling" = 1 ] || return 0
  single exec -T pyspy sh -c 'until command -v py-spy >/dev/null; do sleep 1; done'
  # shellcheck disable=SC2016  # expanded by the sidecar's shell; py-spy's own output goes to <name>.log
  single exec -d -T pyspy sh -c 'exec py-spy record --pid 1 --subprocesses --nonblocking --rate 50 \
    --duration "$1" --format raw --output "/out/$2.txt" >"/out/$2.log" 2>&1' sh "$2" "$1"
}
# `profile_wait` waits up to 10 minutes for the recordings to be written: py-spy writes when its
# duration ends, which can be after the load does. A no-op unless profiling.
profile_wait() {
  [ "$profiling" = 1 ] || return 0
  # shellcheck disable=SC2016  # expanded by the sidecar's shell
  single exec -T pyspy sh -c 'for _ in $(seq 600); do
      grep -qx py-spy /proc/[0-9]*/comm 2>/dev/null || exit 0; sleep 1; done' || true
}
# `profile_logs <section>` saves the api log to $profile_dir/<section>_api.log; a no-op unless profiling.
profile_logs() {
  [ "$profile_on" = 1 ] || return 0
  single logs --no-color api >"$profile_dir/$1_api.log" 2>&1 || true
}

# A worker with the worker footprint limits; host port 19000+n, `worker-<n>` on the cluster network.
perf_worker() {
  local n=$1
  shift
  worker_compose "$n" -f "$root/scripts/perf/worker.override.yml" "$@"
}

# mfload runs from a container on $load_net, with room for many sockets. It writes to $run (/out).
load_net=""
loads=()
load_start() {
  local name=$1
  shift
  docker run -d --name "mf-perf-$name" --network "$load_net" --user "$(id -u):$(id -g)" \
    --ulimit nofile=1048576:1048576 --sysctl net.ipv4.ip_local_port_range="1024 65535" \
    -v "$state/bin/mfload:/usr/local/bin/mfload:ro" -v "$run:/out" debian:stable-slim mfload "$@" >/dev/null
  loads+=("mf-perf-$name")
}
load_wait() {
  local name=mf-perf-$1 code
  code=$(docker wait "$name")
  docker logs "$name" 2>&1 | tail -n 20
  docker rm "$name" >/dev/null
  [ "$code" -eq 0 ] || fail "mfload $1 exited with $code"
}
load() {
  load_start "$@"
  load_wait "$1"
}

# `field <file> <key>` prints a top-level value of a JSON file; `metric <file> <key>` one of its metrics.
field() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"; }
metric() { python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["metrics"][sys.argv[2]])' "$1" "$2"; }

# `meta <scenario> <status> <reduced_to> <limits-json> [notes]` records how a scenario ran.
meta() {
  python3 - "$run/meta.json" "$@" <<'EOF'
import json, os, sys
path, name, status, reduced, limits = sys.argv[1:6]
meta = json.load(open(path)) if os.path.exists(path) else {}
meta[name] = {"status": status, "reduced_to": reduced or None, "limits": json.loads(limits),
              "notes": sys.argv[6] if len(sys.argv) > 6 else ""}
with open(path, "w") as f:
    json.dump(meta, f, indent=2)
EOF
}

# `combine <dest> <scenario.json | key=number>...` writes one scenario from mfload results and values.
combine() {
  python3 - "$@" <<'EOF'
import json, sys
dest, *parts = sys.argv[1:]
out = {"metrics": {}, "errors": 0}
for part in parts:
    if part.endswith(".json"):
        data = json.load(open(part))
        out["metrics"].update(data.get("metrics", {}))
        out["errors"] += data.get("errors", 0)
    else:
        key, value = part.split("=", 1)
        out["metrics"][key] = float(value)
out["metrics"]["errors"] = out["errors"]
with open(dest, "w") as f:
    json.dump(out, f, indent=2)
EOF
}

# `restarts <compose command...>` prints "<restarts> <oom-killed containers>" for a project.
restarts() {
  local ids
  ids=$("$@" ps -aq)
  [ -n "$ids" ] || { echo "0 0"; return; }
  # shellcheck disable=SC2086  # one argument per container id
  docker inspect -f '{{.RestartCount}} {{.State.OOMKilled}}' $ids \
    | awk '{ r += $1; if ($2 == "true") o++ } END { printf "%d %d\n", r, o }'
}

# `sample <file> <compose command...>` appends the project's memory in MiB every 5 s until killed.
sample() {
  local file=$1
  shift
  while :; do
    mem_mib "$@" >>"$file" 2>/dev/null || true
    sleep 5
  done
}

# `sample_workers <file>` appends "<n> <MiB>" for every running worker project every 5 s until killed.
sample_workers() {
  while :; do
    docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | awk '
      $1 ~ /^mf-w[0-9]+-/ { split($1, p, "-"); v = $2; u = v; gsub(/[0-9.]/, "", u); sub(/[A-Za-z]+$/, "", v)
        if (u == "GiB") v *= 1024; else if (u == "KiB") v /= 1024; else if (u == "B") v /= 1048576
        m[substr(p[2], 2)] += v }
      END { for (w in m) printf "%s %.1f\n", w, m[w] }' >>"$1" 2>/dev/null || true
    sleep 5
  done
}

column_max() { awk -v c="$2" '$c > m { m = $c } END { printf "%.1f\n", m + 0 }' "$1"; }

wait_ready() {
  curl -fsS "${@:2}" --retry 30 --retry-delay 2 --retry-all-errors -o /dev/null "$1/readyz" \
    || fail "$1 is not ready"
}

started=0 # workers created so far
cluster_down() {
  local n
  for n in $(seq "$started"); do
    worker_compose "$n" down -v --remove-orphans >/dev/null 2>&1 || true
  done
  cp_compose down -v --remove-orphans >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}

cleanup() {
  status=$?
  trap - EXIT
  [ "${#loads[@]}" -eq 0 ] || docker rm -f "${loads[@]}" >/dev/null 2>&1 || true
  [ -z "${sampler:-}" ] || kill "$sampler" 2>/dev/null || true
  if [ "$status" -ne 0 ]; then
    rm -rf "$logs"
    mkdir -p "$logs"
    { single ps -a && single logs --no-color; } >"$logs/single.log" 2>&1 || true
    { cp_compose ps -a && cp_compose logs --no-color; } >"$logs/cp.log" 2>&1 || true
    for n in $(seq "$started"); do
      { worker_compose "$n" ps -a && worker_compose "$n" logs --no-color; } >"$logs/w$n.log" 2>&1 || true
    done
    echo "logs in $logs" >&2
  fi
  single down -v --remove-orphans >/dev/null 2>&1 || true
  cluster_down
  rm -rf "$state"
  exit "$status"
}
trap cleanup EXIT

# 1. Host check.
cpus=$(nproc)
mem_total=$(awk '/^MemTotal:/ { printf "%d", $2 / 1024 }' /proc/meminfo)
python3 - "$run/host.json" "$cpus" "$mem_total" "$(docker version --format '{{.Server.Version}}')" <<'EOF'
import json, platform, sys
from datetime import datetime, timezone
dest, cpus, mem, docker = sys.argv[1:5]
model = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), "")
host = {"started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "cpus": int(cpus),
        "memory_mib": int(mem), "cpu_model": model, "kernel": platform.release(), "docker": docker}
with open(dest, "w") as f:
    json.dump(host, f, indent=2)
EOF
echo "host: $cpus CPUs, $mem_total MiB, profile $profile"

remote_status=full remote_reduced=""
if [ "$profile" = tiny ]; then
  remote_status=reduced remote_reduced="tiny: $rps rps for $duration s on $nflags flags"
elif [ "$cpus" -lt 6 ]; then
  # 2k RPS needs about 4 cores for the api and 2 for Caddy and the load generator.
  rps=$((2000 * cpus / 6))
  remote_status=reduced remote_reduced="$rps rps on $cpus CPUs"
fi

# 2. mfload: a static binary, run in debian:stable-slim.
if command -v go >/dev/null; then
  (cd "$root/perf" && CGO_ENABLED=0 go build -o "$state/bin/mfload" ./cmd/mfload)
else
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e GOCACHE=/tmp/go-cache -e GOPATH=/tmp/go \
    -e CGO_ENABLED=0 -v "$root/perf:/src:ro" -v "$state/bin:/out" -w /src golang:1.26 \
    go build -buildvcs=false -o /out/mfload ./cmd/mfload
fi
echo "mfload built"

# 3. Remote evaluation: one api container behind Caddy, without limits. The stack and the seed also
# serve section 4.
# 8 uvicorn workers measured best on the 16-CPU build machine: 4 reached only 1651 rps (p99 12.7 s),
# 12 added queueing and errors.
base="http://localhost:$http_port"
[ "$profile" = full ] && prop_status=full prop_reduced="" || prop_status=reduced prop_reduced="3 flips"
if wants remote || wants replicas; then
  api_workers="${MF_PERF_API_WORKERS:-8}"
  wants remote && profiling=$profile_on
  # shellcheck disable=SC2046  # one argument per service
  MF_PERF_API_WORKERS=$api_workers single up -d --wait --wait-timeout 180 $(single_services)
  wait_ready "$base"
  token=$(bootstrap_admin "$base") || fail "the admin bootstrap failed"
  load_net=megooflags-perf_default
  load seed seed --api http://caddy --token "$token" --project perf --flags "$nflags" --out /out/flags_10k.json
  key=$(field "$run/flags_10k.json" server_key)
  eval_key=$(field "$run/flags_10k.json" eval_key)
  project=$(field "$run/flags_10k.json" project)
  env=$(field "$run/flags_10k.json" env)
  flag=$(field "$run/flags_10k.json" flag)
  if [ "$profiling" = 1 ]; then
    curl -fsS -H "Authorization: Bearer $key" "$base/sdk/v1/ruleset" -o "$profile_dir/ruleset.json" \
      || fail "the ruleset download for the profile failed"
  fi
fi
if wants remote; then
  # The first full run, with 4 workers, for the notes.
  declare -A tuned_from=([ofrep]="1651 rps at p99 12.7 s" [evaluate]="712 rps at p99 107.5 s")
  for mode in ofrep evaluate; do
    [ "$mode" = ofrep ] && k=$key || k=$eval_key
    profile_record "remote_$mode" $((warmup + duration))
    load "$mode" evaluate --target http://caddy --key "$k" --mode "$mode" --flag "$flag" --rps "$rps" \
      --duration "$duration" --warmup "$warmup" --out "/out/remote_$mode.json"
    meta "remote_$mode" "$remote_status" "$remote_reduced" \
      "{\"api\": \"unlimited\", \"api_workers\": \"$api_workers\", \"replicas\": \"1\"}" \
      "tuned from 4 to 8 uvicorn workers (MF_PERF_API_WORKERS): with 4 it reached ${tuned_from[$mode]}"
  done
  [ "$nflags" -eq 10000 ] && flags_status=full flags_reduced="" || flags_status=reduced flags_reduced="$nflags flags"
  # Every flag of the big environment in one /sdk/v1/evaluate request: reported, not gated.
  load whole-env evaluate --target http://caddy --key "$key" --mode evaluate --rps 5 \
    --duration "$whole_duration" --warmup "$whole_warmup" --out /out/whole_env_10k.json
  whole_reduced=$flags_reduced
  [ "$profile" = full ] || whole_reduced="tiny: $nflags flags for $whole_duration s"
  meta whole_env_10k "$flags_status" "$whole_reduced" \
    "{\"api\": \"unlimited\", \"api_workers\": \"$api_workers\", \"replicas\": \"1\"}" \
    "every flag of the $nflags-flag environment in one request, 5 rps for $whole_duration s"
  meta flags_10k "$flags_status" "$flags_reduced" '{"api": "unlimited"}' \
    "ruleset_download_ms is the SDK start time: one ruleset download"
  if [ "$profiling" = 1 ]; then
    profile_wait
    profile_logs remote
    # Section 4 scales the api to 3, which a sidecar in one api's PID namespace cannot follow.
    single rm -sf pyspy
    profiling=0
  fi
  echo "remote evaluation done"
fi

# 4. Propagation across three api replicas behind Caddy. The remote scenario's worker count is per
# container; three replicas of it would share the host's CPUs (at 3 x 8 workers on 16 CPUs, p95 was
# 11.9 s against 5.6 s at 3 x 4), so the replicas keep 4 workers each.
if wants replicas; then
  replica_workers="${MF_PERF_REPLICA_API_WORKERS:-4}"
  MF_PERF_API_WORKERS=$replica_workers single up -d --wait --wait-timeout 180 --scale api=3 caddy
  wait_ready "$base"
  load prop-replicas streams --target http://caddy --key "$key" --streams 30 --ramp-seconds 3 \
    --hold "$prop_hold" --flip-every "$prop_flip" --api http://caddy --token "$token" --project "$project" \
    --env "$env" --flag "$flag" --out /out/propagation_replicas.json
  meta propagation_replicas "$prop_status" "$prop_reduced" \
    "{\"api\": \"unlimited\", \"api_workers\": \"$replica_workers\", \"replicas\": \"3\"}" \
    "30 streams on the $nflags-flag project, a flip every $prop_flip s; $replica_workers workers per replica (MF_PERF_REPLICA_API_WORKERS): at 3 x 8 on 16 CPUs p95 was 11.9 s"
  profile_logs replicas
  echo "propagation across replicas done"
fi
if wants remote || wants replicas; then
  single down -v --remove-orphans
fi

# 5. Propagation through the control plane to three workers.
worker_start() {
  local n=$1
  perf_worker "$n" up -d --wait --wait-timeout 120
  join "worker-$n" caddy perf_worker "$n"
  # The api reaches the control plane over the cluster network.
  join "" api perf_worker "$n"
  wait_ready "http://localhost:$((19000 + n))"
}
add_worker() {
  local n=$1 node
  node=$(curl -fsS "${tls[@]}" -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
    -d "{\"name\":\"perf-worker-$n\"}" "$cp_base/api/nodes") || fail "POST /api/nodes perf-worker-$n failed"
  cat >"$state/w$n.env" <<EOF
MEGOOFLAGS_CONTROL_PLANE_URL=$cp_api
MEGOOFLAGS_NODE_TOKEN=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["token"])' <<<"$node")
MEGOOFLAGS_CONTROL_PLANE_CA_FILE=/etc/megooflags/certs/ca.crt
MF_CLUSTER_CERTS=$state/certs
MF_WORKER_PORT=$((19000 + n))
EOF
  started=$n
  worker_start "$n"
}
worker_limits='{"worker": "1 vCPU / 512 MiB (caddy 0.35 / 256M, api 0.65 / 256M)"}'
if wants cluster || wants streams; then
  cat >"$state/cp.env" <<EOF
MEGOOFLAGS_SECRET_KEY=$secret
MEGOOFLAGS_HTTP_PORT=$http_port
MEGOOFLAGS_HTTPS_PORT=$https_port
EOF
  docker network rm "$network" >/dev/null 2>&1 || true
  docker network create "$network" >/dev/null
  cp_up
  for _ in $(seq 30); do
    cp_compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$state/certs/ca.crt" 2>/dev/null && break
    sleep 2
  done
  [ -s "$state/certs/ca.crt" ] || fail "Caddy's local CA root was not created"
  chmod 644 "$state/certs/ca.crt"
  cp_base="https://localhost:$https_port"
  tls=(--cacert "$state/certs/ca.crt")
  wait_ready "$cp_base" "${tls[@]}"
  token=$(bootstrap_admin "$cp_base" "${tls[@]}") || fail "the admin bootstrap failed"
  load_net=$network
  cp_api=https://cp.megooflags.internal
  load seed-cluster seed --api "$cp_api" --insecure --token "$token" --project cluster --flags 100 \
    --eval-flags 1 --out /out/cluster_seed.json
  ckey=$(field "$run/cluster_seed.json" server_key)
  cenv=$(field "$run/cluster_seed.json" env)
  cflag=$(field "$run/cluster_seed.json" flag)
  for n in 1 2 3; do
    add_worker "$n"
  done
  workers=3 wait_streams || fail "the workers did not connect to the control plane"
fi
if wants cluster; then
  load prop-cluster streams --target http://worker-1 --target http://worker-2 --target http://worker-3 \
    --key "$ckey" --streams 30 --ramp-seconds 3 --hold "$prop_hold" --flip-every "$prop_flip" \
    --api "$cp_api" --insecure --token "$token" --project cluster --env "$cenv" --flag "$cflag" \
    --out /out/propagation_cluster.json
  meta propagation_cluster "$prop_status" "$prop_reduced" "$worker_limits" \
    "30 streams over 3 workers, a flip every $prop_flip s"
  echo "propagation through the control plane done"
fi

# 6. Many streams: one worker's capacity inside its limits, then as many workers as 100k needs and
# the host fits, each at 80 % of that capacity. At least 8 workers make a full run.
if wants streams; then
  big_status=full big_reduced="" capacity="" need=""
  if [ "$profile" = full ]; then
    perf_worker 2 stop
    perf_worker 3 stop
    load_start capacity streams --target http://worker-1 --key "$ckey" --ramp-until-error --max 60000 \
      --ramp-seconds 300 --hold 0 --flip-every 0 --out /out/capacity.json
    # Stop opening streams once the worker uses 90 % of its 512 MiB.
    while [ "$(docker inspect -f '{{.State.Running}}' mf-perf-capacity)" = true ]; do
      if awk -v m="$(mem_mib perf_worker 1)" 'BEGIN { exit !(m >= 460.8) }'; then
        docker kill -s INT mf-perf-capacity >/dev/null || true
        break
      fi
      sleep 2
    done
    load_wait capacity
    capacity=$(metric "$run/capacity.json" capacity | cut -d. -f1)
    [ "$capacity" -gt 0 ] || fail "a worker held no streams"
    need=$(python3 -c "import math; print(math.ceil(100000 / (0.8 * $capacity)))")
    # The control plane and the load generator get about 4 CPUs and 4 GiB.
    fit=$(((cpus - 4) < (mem_total - 4096) / 512 ? (cpus - 4) : (mem_total - 4096) / 512))
    big_workers=$((need < fit ? need : fit))
    big_workers=$((big_workers < 1 ? 1 : big_workers))
    if [ "$need" -gt "$fit" ]; then
      big_streams=$((big_workers * capacity * 8 / 10))
    fi
    if [ "$big_workers" -ge 8 ]; then
      big_status=full
    else
      big_status=reduced big_reduced="$big_streams streams, $big_workers workers"
    fi
    echo "one worker holds $capacity streams; $big_workers workers for $big_streams streams"
    # Fresh processes for the run.
    perf_worker 1 down
    for n in 1 2 3; do
      worker_start "$n"
    done
    for n in $(seq 4 "$big_workers"); do
      add_worker "$n"
    done
    workers=$big_workers wait_streams || fail "the workers did not connect to the control plane"
  else
    big_workers=1
    big_status=reduced big_reduced="tiny: $big_streams streams, 1 worker"
  fi
  targets=()
  for n in $(seq "$big_workers"); do
    targets+=(--target "http://worker-$n")
  done
  sample_workers "$state/workers-mem.log" &
  sampler=$!
  load streams-100k streams "${targets[@]}" --key "$ckey" --streams "$big_streams" --ramp-seconds "$big_ramp" \
    --hold "$big_hold" --flip-every "$big_flip" --api "$cp_api" --insecure --token "$token" --project cluster \
    --env "$cenv" --flag "$cflag" --out /out/streams.json
  kill "$sampler" 2>/dev/null || true
  sampler=""
  touch "$state/workers-mem.log"
  read -r restart_count oom_count < <(
    for n in $(seq "$big_workers"); do restarts perf_worker "$n"; done \
      | awk '{ r += $1; o += $2 } END { printf "%d %d\n", r, o }'
  )
  combine "$run/streams_100k.json" "$run/streams.json" "workers=$big_workers" \
    "mem_max_mib=$(column_max "$state/workers-mem.log" 2)" "restarts=$restart_count" "oom_killed=$oom_count" \
    "streams_planned=$big_streams" ${capacity:+"capacity_per_worker=$capacity" "workers_for_100k=$need"}
  meta streams_100k "$big_status" "$big_reduced" "$worker_limits" \
    "sizing: one worker's capacity is measured, then min(workers for 100k, workers the host fits) run at 80 % of it, and at least 8 workers make a full run; mem_max_mib is the most any one worker used; a flip every $big_flip s through the control plane; limits re-split from caddy 0.2 / 96M, api 0.8 / 416M, where Caddy was OOM-killed at about 1000 streams"
fi
if wants cluster || wants streams; then
  cluster_down
  started=0
fi

# 7. Single-node footprint: the whole bundle inside 2 vCPU / 2 GB under a small-team load.
if wants footprint; then
  single_override=single
  profiling=$profile_on
  # shellcheck disable=SC2046  # one argument per service
  single up -d --wait --wait-timeout 180 $(single_services)
  wait_ready "$base"
  token=$(bootstrap_admin "$base") || fail "the admin bootstrap failed"
  load_net=megooflags-perf_default
  load seed-small seed --api http://caddy --token "$token" --project small --flags 100 --eval-flags 1 \
    --out /out/small_seed.json
  skey=$(field "$run/small_seed.json" server_key)
  sample "$state/single-mem.log" single &
  sampler=$!
  profile_record footprint "$foot_hold"
  load_start foot-streams streams --target http://caddy --key "$skey" --streams 20 --ramp-seconds 2 \
    --hold "$foot_hold" --flip-every 1 --api http://caddy --token "$token" --project small \
    --env "$(field "$run/small_seed.json" env)" --flag "$(field "$run/small_seed.json" flag)" \
    --out /out/foot_streams.json
  load_start foot-eval evaluate --target http://caddy --key "$skey" --mode ofrep \
    --flag "$(field "$run/small_seed.json" flag)" --rps 50 --duration "$foot_hold" --warmup 0 \
    --out /out/foot_eval.json
  load_wait foot-streams
  load_wait foot-eval
  kill "$sampler" 2>/dev/null || true
  sampler=""
  touch "$state/single-mem.log"
  read -r restart_count oom_count < <(restarts single)
  combine "$run/footprint_single.json" "$run/foot_streams.json" "$run/foot_eval.json" \
    "restarts=$restart_count" "oom_killed=$oom_count" "mem_max_mib=$(column_max "$state/single-mem.log" 1)"
  [ "$profile" = full ] && foot_status=full foot_reduced="" || foot_status=reduced foot_reduced="tiny: $foot_hold s"
  meta footprint_single "$foot_status" "$foot_reduced" \
    '{"caddy": "0.25 / 128M", "web": "0.25 / 256M", "api": "1.0 / 768M, 2 workers", "db": "0.4 / 640M", "valkey": "0.1 / 128M"}' \
    "100 flags, 20 streams, a flip every second and 50 rps for $foot_hold s; giving the api 1.2 CPU (db 0.3) or 3 workers made p99 worse, so the split is unchanged"
  profile_wait
  profile_logs footprint
  single down -v --remove-orphans
  profiling=0
  echo "single-node footprint done"
fi

# 8. Local evaluation in each server SDK.
if wants local_eval; then
  for sdk in go node python java dotnet ruby php; do
    if [ "$profile" = full ]; then
      meta "local_eval_$sdk" full "" '{}' "the SDK's p99 guard on 10 000 flags"
    else
      meta "local_eval_$sdk" skipped "" '{}' "not in the tiny profile"
    fi
  done
  if [ "$profile" = full ]; then
    bash "$root/scripts/perf/local_eval.sh" "$run" || echo "a local evaluation guard failed: see the results"
  fi
fi

# 9. Merge.
if [ -n "$only" ]; then
  dest="$root/perf/results/only.json"
elif [ "$profile" = full ]; then
  dest="$root/perf/results/$version.json"
else
  dest="$root/perf/results/tiny.json"
fi
python3 "$root/scripts/perf_report.py" merge --dir "$run" --version "$version" --profile "$profile" --out "$dest"
if [ -n "$only" ]; then
  echo "perf OK ($profile, only $only)"
else
  echo "perf OK ($profile)"
fi
