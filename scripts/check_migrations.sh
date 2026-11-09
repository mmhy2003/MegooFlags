#!/usr/bin/env bash
# Alembic checks used by CI: single head, upgrade, walk down to base, upgrade again.
set -euo pipefail
cd "$(dirname "$0")/../api"

heads=$(uv run --frozen alembic heads 2>/dev/null | grep -c .)
if [ "$heads" -ne 1 ]; then
  echo "expected exactly 1 alembic head, found $heads" >&2
  uv run --frozen alembic heads >&2
  exit 1
fi

uv run --frozen alembic upgrade head
revisions=$(uv run --frozen alembic history 2>/dev/null | grep -c -- '->')
for _ in $(seq "$revisions"); do
  uv run --frozen alembic downgrade -1
done
if [ -n "$(uv run --frozen alembic current 2>/dev/null)" ]; then
  echo "database not at base after walking down $revisions revisions" >&2
  exit 1
fi
uv run --frozen alembic upgrade head
echo "migrations OK ($revisions revisions)"
