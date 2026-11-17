#!/usr/bin/env bash
# Local evaluation (scripts/perf.sh): run each server SDK's p99 guard (AGENTS.md section 9) on
# 10 000 flags in its language's image, and write <out>/local_eval_<sdk>.json with `passed` (1 or 0)
# and, when the guard prints it, `p99_ms`.
#
#   bash scripts/perf/local_eval.sh <out-dir> [sdk...]    # default: every server SDK
#
# The SDK is copied inside the container, so nothing is written to the checkout. It exits 1 when a
# guard fails, after running the others.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
out=${1:?usage: local_eval.sh <out-dir> [sdk...]}
shift
mkdir -p "$out"
sdks=("$@")
[ "${#sdks[@]}" -gt 0 ] || sdks=(go node python java dotnet ruby php)

image() {
  case "$1" in
    go) echo golang:1.26 ;;
    node) echo node:22 ;;
    python) echo ghcr.io/astral-sh/uv:python3.12-bookworm ;;
    java) echo maven:3-eclipse-temurin-21 ;;
    # sdks/dotnet targets net8.0 and has no global.json.
    dotnet) echo mcr.microsoft.com/dotnet/sdk:8.0 ;;
    # megooflags.gemspec: required_ruby_version >= 3.4.
    ruby) echo ruby:3.4 ;;
    php) echo composer:2 ;;
    *) return 1 ;;
  esac
}

guard() {
  case "$1" in
    go) echo "go test -count=1 -run TestEvaluateP99 -v ./..." ;;
    node) echo "corepack enable && pnpm install --frozen-lockfile && pnpm test:p99" ;;
    python) echo "uv run pytest perf -s" ;;
    java) echo "mvn -B -ntp test -Pp99" ;;
    dotnet) echo 'dotnet test -c Release --filter "Category=P99"' ;;
    ruby) echo "bundle install && bundle exec rake p99" ;;
    php) echo "composer install --no-interaction && composer p99" ;;
  esac
}

status=0
for sdk in "${sdks[@]}"; do
  img=$(image "$sdk") || { echo "unknown SDK: $sdk" >&2; exit 2; }
  log="$out/local_eval_$sdk.log"
  echo "local_eval $sdk ($img)"
  code=0
  docker run --rm -e CI=true -v "$root/sdks/$sdk:/src:ro" "$img" \
    sh -c "cp -a /src /work && cd /work && $(guard "$sdk")" >"$log" 2>&1 || code=$?
  tail -n 20 "$log"
  [ "$code" -eq 0 ] || status=1
  python3 - "$log" "$code" "$out/local_eval_$sdk.json" <<'EOF'
import json, re, sys

log, code, dest = sys.argv[1], int(sys.argv[2]), sys.argv[3]
text = open(log, encoding="utf-8", errors="replace").read()
metrics = {"passed": 1 if code == 0 else 0}
match = re.search(r"p99[^0-9]*([0-9.]+) ?(ns|µs|us|ms)", text)
if match:
    scale = {"ns": 1e-6, "µs": 1e-3, "us": 1e-3, "ms": 1.0}[match.group(2)]
    metrics["p99_ms"] = round(float(match.group(1)) * scale, 6)
with open(dest, "w", encoding="utf-8") as f:
    json.dump({"metrics": metrics, "errors": 0 if code == 0 else 1}, f, indent=2)
    f.write("\n")
EOF
  echo "local_eval $sdk: $([ "$code" -eq 0 ] && echo pass || echo fail)"
done
exit "$status"
