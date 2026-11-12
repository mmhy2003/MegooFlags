#!/usr/bin/env bash
# Runs the e2e suite inside the pinned Playwright image, the same one CI uses, so visual
# baselines match. Needs the API on the host (AGENTS.md §9). Extra arguments go to
# `playwright test`, e.g. `pnpm e2e:docker --update-snapshots`.
set -euo pipefail

# Keep in step with @playwright/test in package.json (tests/unit/playwright-pin.test.ts).
PW_VERSION="1.65.0"

repo="$(cd "$(dirname "$0")/../.." && pwd)"

docker run --rm --network host --ipc=host \
  -v "$repo:/repo" -w /repo/web \
  -e CI=1 -e MEGOOFLAGS_UI_KIT=1 \
  -e MEGOOFLAGS_API_URL="${MEGOOFLAGS_API_URL:-http://localhost:8000}" \
  "mcr.microsoft.com/playwright:v$PW_VERSION-noble" \
  bash -c "corepack enable && pnpm install --frozen-lockfile && pnpm build && pnpm e2e $*"
