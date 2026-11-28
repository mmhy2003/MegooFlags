# MF-497 plan: the visual matrix masks only what is visible

## Goal

`web/e2e/matrix.spec.ts` masks only visible elements, so hidden text (the "Created" `<time>` inside a closed "More" `<details>` below 1024 px) can no longer change a screenshot. This fixes MF-497 and also MF-498, MF-499 and MF-500.

## Architecture

There is one code change. In `toHaveScreenshot`, the `mask` list is built as it is today and then each locator is mapped through `.filter({ visible: true })`. The spec already uses this API at line 173, so Playwright 1.65 supports it. The comment above `PAGES` explains the rule. Every matrix baseline is then regenerated in one run in the pinned image (`--update-snapshots=all`), and the PNGs that git reports as changed are committed. `fixTimes` / `fixAuditTimes` stay unchanged. No application code, API, config or docs change.

## File map

| File | Change |
|---|---|
| `web/e2e/matrix.spec.ts` | Mask locators filtered to visible elements, and the comment above `PAGES` explains why. |
| `web/e2e/matrix.spec.ts-snapshots/*-chromium-linux.png` | Regenerated. Commit only the files git reports as changed. |

The temporary reproduction routes (Steps 1 and 3) are **never committed**.

## Shared facts for every step

**The e2e command.** Run it from the repository root. Each `megoocolab-runner services run db` starts its own fresh Postgres and Valkey. The API is started as AGENTS.md §9 says, and a trap stops it so that port 8000 is free afterwards:

```bash
megoocolab-runner services run db -- bash -c 'cd api && uv run alembic upgrade head && export MEGOOFLAGS_SECRET_KEY=$(openssl rand -hex 32) && (MEGOOFLAGS_PUBLIC_URL=http://localhost:3000 MEGOOFLAGS_API_WORKERS=1 MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true MEGOOFLAGS_ROLE=control-plane uv run python -m megooflags_api.serve) & PID=$!; trap "pkill -P $PID; kill $PID" EXIT; cd web && pnpm e2e:docker PLAYWRIGHT_ARGS'
```

`PLAYWRIGHT_ARGS` is replaced with the exact arguments that each step gives. Notes:
- `e2e/docker.sh` passes `$*` into `bash -c "... pnpm e2e $*"`, which splits a quoted `-g "a b"` on its spaces. Use dots instead of spaces in `-g` patterns, for example `-g change-requests.at.280`. That pattern does not match `change-request at 280`.
- The image sets `CI=1`, so `retries: 1` applies. Add `--retries 0` when the goal is to see a failure.
- Before you start, check that nothing holds port 8000 (`ss -ltnp | grep 8000`). A stray API from an earlier run makes global setup fail with "API not ready after 90 s".

**After every `pnpm e2e:docker` run**, the files it wrote are owned by root. Restore ownership and remove the stray store:

```bash
docker run --rm -v "$PWD:/w" mcr.microsoft.com/playwright:v1.65.0-noble chown -R "$(id -u):$(id -g)" /w/web /w/.pnpm-store
rm -rf .pnpm-store
```

**Temporary route for change-requests** (for reproduction only, never committed). Add it to the test body in `matrix.spec.ts`, right after `if (fixTimes) await fixAuditTimes(page);`:

```ts
if (name === "change-requests") {
  await page.route(/\/api\/projects\/gov\/change-requests(\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { items: { created_at: string }[] };
    for (const cr of body.items) cr.created_at = FIXED_TIME;
    await route.fulfill({ response, json: body });
  });
}
```

**Temporary route for keys** (never committed). The SDK keys list returns a plain array (`SdkKeyOut[]`):

```ts
if (name === "keys") {
  await page.route(/\/api\/projects\/gov\/environments\/development\/sdk-keys(\?|$)/, async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as { created_at: string }[];
    for (const k of body) k.created_at = FIXED_TIME;
    await route.fulfill({ response, json: body });
  });
}
```

`FIXED_TIME` is `2026-12-28T12:28:28Z`. It renders as "Dec 28, 2026, 12:28 PM" (22 characters), which wraps at 280 px.

**The mask change** (the final code of Step 2):

```ts
// `mask` hides <time> elements, whose relative text changes from run to run. demo is never
// changed and has no evaluations, so the flag pages (Insights too) are stable.
// Only visible elements are masked: Playwright masks hidden ones too, and their boxes follow
// text that is not on the page (a <time> inside a closed "More" below 1024 px).
const PAGES: ...
```

```ts
            // The running version changes with every release.
            mask: [
              page.getByTestId("app-version"),
              ...(mask ? [page.locator("time")] : []),
              ...maskText.map((text) => page.getByText(text)),
            ].map((locator) => locator.filter({ visible: true })),
```

## Steps

### 1. Reproduce the 280 px failure before the fix
This step makes no commit. Only its findings are recorded.

1. Make sure `git status` is clean.
2. Add the temporary change-requests route (see Shared facts) to `web/e2e/matrix.spec.ts`. Leave the spec otherwise unchanged.
3. Run the e2e command with `PLAYWRIGHT_ARGS` = `e2e/matrix.spec.ts -g change-requests.at.280 --retries 0`. Expected: `light › change-requests at 280 px` and `dark › change-requests at 280 px` fail on the screenshot. In `web/test-results/**/…-diff.png` the differences are the pink box under "More". Note which tests failed and the diff ratio.
4. Remove the route with `git checkout -- web/e2e/matrix.spec.ts`, then fix ownership.
5. Run the e2e command with `PLAYWRIGHT_ARGS` = `e2e/matrix.spec.ts -g change-requests.at.280 --repeat-each 10 --retries 0`. Note whether any run failed. The seed time of this database decides the outcome, so all 10 will likely pass or fail together.
6. Fix ownership again. `git status` must be clean. If anything under `web/e2e/matrix.spec.ts-snapshots` changed, restore it with `git checkout`.

Acceptance: the step summary says which of the two runs reproduced the failure (or that neither did). If neither did, carry on anyway: the hidden boxes are wrong either way, and the pull request must say plainly that the flake was not reproduced.

### 2. Mask only visible elements and regenerate every matrix baseline
1. Edit `web/e2e/matrix.spec.ts` exactly as "The mask change" shows: add the comment lines above `PAGES`, and add `.map((locator) => locator.filter({ visible: true }))` to the `mask` array. Leave `fixTimes`, `fixAuditTimes`, `FIXED_TIME` and `PAGES` entries as they are.
2. Run `cd web && pnpm exec prettier --check e2e/matrix.spec.ts && pnpm typecheck && pnpm lint`. If Prettier objects, apply `pnpm exec prettier --write e2e/matrix.spec.ts`.
3. Run the e2e command with `PLAYWRIGHT_ARGS` = `e2e/matrix.spec.ts --update-snapshots=all`. Expected: every test passes, and the PNGs are rewritten.
4. Fix ownership. `git status --porcelain` must show only `web/e2e/matrix.spec.ts` and `.png` files under `web/e2e/matrix.spec.ts-snapshots/`. Run `git clean -n` to check for untracked files, and remove `web/test-results` / `.pnpm-store` if they show up. No new PNG files may appear (`git status --porcelain | grep '^??'` prints nothing).
5. The list of changed PNGs must include `change-requests-{light,dark}-280`, `keys-{light,dark}-280`, `change-requests-light-880` and `flags-light-280`. Note any of these that is missing.
6. Run the matrix again without `--update-snapshots`: `PLAYWRIGHT_ARGS` = `e2e/matrix.spec.ts`. Expected: everything passes. Fix ownership.

Commit: `fix(web): the visual matrix masks only visible elements (MF-497)`, with the spec and the changed PNGs.

### 3. Prove the fix with pinned created_at, check the PNGs, and run all gates
1. Add both temporary routes to `matrix.spec.ts` (do not commit them). Run the e2e command with `PLAYWRIGHT_ARGS` = `e2e/matrix.spec.ts -g '(change-requests|keys).at.280' --retries 0`. Expected: 4 tests pass (light and dark, for change-requests and keys). If the shell splits the pattern, run `-g change-requests.at.280` and `-g keys.at.280` separately. Then remove the routes (`git checkout -- web/e2e/matrix.spec.ts`) and fix ownership.
2. Open the changed PNGs with `git diff HEAD~1 --stat`, and view them with the image reader next to `git show HEAD~1:<path>`. Each one must differ only by a hidden pink box that is gone, or by a few pixels of a visible masked box. Spot checks: `change-requests-*-280` and `keys-*-280` have no box under "More"; `change-requests-*-880` has no strip at the right edge; `flags-*-280` shows the full flag keys. A difference of any other kind is a failure to investigate. Do not commit over it.
3. Run the full suite on a fresh database: the e2e command with an empty `PLAYWRIGHT_ARGS`. Expected: all tests pass. Fix ownership.
4. `cd web && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test`. All must pass.
5. `git status` is clean, and nothing listens on port 8000.

The step summary records: whether the failure was reproduced in Step 1, the result of the pinned-route runs, and the full-suite count. It also notes for the pull request that the same change fixes MF-498, MF-499 and MF-500, which a person closes. No commit is needed unless a gate needed a fix. Such a fix would be a formatting fix to `matrix.spec.ts`, committed as `style(web): format matrix spec (MF-497)`.

## Final verification

- E2E command with `e2e/matrix.spec.ts`: all pass, with no `--update-snapshots`.
- E2E command with no arguments (full suite, fresh database): all pass.
- `cd web && pnpm lint && pnpm format:check && pnpm typecheck && pnpm test`.
- `git diff main --stat` shows only `web/e2e/matrix.spec.ts` and PNGs under `web/e2e/matrix.spec.ts-snapshots/`.
