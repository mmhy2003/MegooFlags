#!/usr/bin/env bash
# Print an org admin service token for the SDK end-to-end tests: first-run setup (or sign-in when
# the stack is already set up), then POST /api/tokens, as scripts/smoke.sh does.
#
# Usage: scripts/sdk_e2e_token.sh [base-url]
# Only the token goes to stdout; messages go to stderr.
set -euo pipefail

base="${1:-${MEGOOFLAGS_E2E_URL:-http://localhost:8080}}"
base="${base%/}"
email="${MEGOOFLAGS_E2E_EMAIL:-admin@example.com}"
password="${MEGOOFLAGS_E2E_PASSWORD:-}"
setup_password="${password:-sdk-e2e-$(openssl rand -hex 16)}"
jar="$(mktemp)"
trap 'rm -f "$jar"' EXIT

json() { python3 -c 'import json, sys; print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2]))))' "$@"; }
post() { curl -sS -o /dev/null -w '%{http_code}' -b "$jar" -c "$jar" -H "Origin: $base" -H 'Content-Type: application/json' -d "$2" "$base$1"; }

code="$(post /api/setup "$(json email "$email" name 'SDK E2E' password "$setup_password")")"
case "$code" in
  200 | 201) ;;
  409)
    [ -n "$password" ] || { echo "the stack is already set up; set MEGOOFLAGS_E2E_PASSWORD" >&2; exit 1; }
    code="$(post /api/auth/sign-in "$(json email "$email" password "$password")")"
    case "$code" in 2*) ;; *) echo "POST /api/auth/sign-in: status $code" >&2; exit 1 ;; esac
    ;;
  *) echo "POST /api/setup: status $code" >&2; exit 1 ;;
esac

created="$(curl -fsS -b "$jar" -H "Origin: $base" -H 'Content-Type: application/json' \
  -d '{"name":"sdk-e2e","bindings":[{"role":"admin","scope":"org"}]}' "$base/api/tokens")" \
  || { echo "POST /api/tokens failed" >&2; exit 1; }
python3 -c 'import json, sys; print(json.load(sys.stdin)["secret"])' <<<"$created"
