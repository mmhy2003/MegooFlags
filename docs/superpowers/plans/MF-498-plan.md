# MF-498 implementation plan: reproduce the 280 px matrix failure on the old code, then prove it is gone

## Goal

Find out why `dark › change-requests at 280 px` (and its three siblings) failed at the finish of MF-457, using diff images from full-suite runs on the old code (`8c475b5`), and show with two clean full-suite runs that the current code passes. Code changes only if the evidence calls for one.

## Architecture

This is an experiment, not a feature. Up to four full-suite `pnpm e2e:docker` runs: up to 2 on `8c475b5` (checked out detached), then 2 on the task branch, each on a fresh database. Every run's evidence goes to `/tmp/mf498/<run>/`, outside the repository. A results table in `/tmp/mf498/REPORT.md` decides the outcome using the design's table. Test-side fixes only, and only if row 2 of that table applies.

## File map

- **No repository file is planned to change.** The only possible change (Step 4, row 2 only) is in `web/e2e/matrix.spec.ts`, `web/e2e/global-setup.ts` and/or `web/e2e/matrix.spec.ts-snapshots/*.png`.
- **Scratch (never committed):** `/tmp/mf498/ci/` (CI history), `/tmp/mf498/old-1/`, `/tmp/mf498/old-2/`, `/tmp/mf498/new-1/`, `/tmp/mf498/new-2/` (and `/tmp/mf498/fixed/` if a fix is made). Each holds `output.log`, `api.log`, `test-results/`, `playwright-report/`. The findings go in `/tmp/mf498/REPORT.md`.

## Shared procedures (every step uses these)

All commands run from the worktree root (`git rev-parse --show-toplevel`).

### P1. Before a run

```bash
ss -ltnp | grep ':8000 '         # must print nothing; if it does: pkill -f megooflags_api.serve, then check again
git status --short               # must print nothing
mkdir -p /tmp/mf498/RUN          # RUN = old-1, old-2, new-1, new-2 or fixed
```

### P2. The run (the original failure's conditions: full suite, pinned image, fresh DB, no extra arguments)

Each `megoocolab-runner services run db` starts its own fresh Postgres and Valkey. The image sets `CI=1`, so `retries: 1` is on. A full run takes a long time (about 470 tests plus `pnpm install` and `pnpm build`). Give the Bash call the maximum timeout (10800000 ms) or run it in the background and wait for it.

```bash
megoocolab-runner services run db -- bash -c '(cd api && uv run alembic upgrade head) || exit 1; (cd api && MEGOOFLAGS_SECRET_KEY=$(openssl rand -hex 32) MEGOOFLAGS_PUBLIC_URL=http://localhost:3000 MEGOOFLAGS_API_WORKERS=1 MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true MEGOOFLAGS_ROLE=control-plane uv run python -m megooflags_api.serve > /tmp/mf498/RUN/api.log 2>&1) & PID=$!; trap "pkill -P $PID; kill $PID" EXIT; cd web && pnpm e2e:docker' 2>&1 | tee /tmp/mf498/RUN/output.log
```

Replace `RUN` in both places. Do not add arguments to `pnpm e2e:docker`.

### P3. After a run (always, even if it failed)

```bash
docker run --rm -v "$PWD:/w" mcr.microsoft.com/playwright:v1.65.0-noble chown -R "$(id -u):$(id -g)" /w
rm -rf .pnpm-store                                   # untracked and not ignored; must go
cp -r web/test-results web/playwright-report /tmp/mf498/RUN/
ss -ltnp | grep ':8000 '                             # must print nothing; else pkill -f megooflags_api.serve
git status --short                                   # must print nothing
```

Copy before the next run: Playwright empties `web/test-results` at the start of every run.

### P4. Reading the result of the four tests

The four tests are `[chromium] › e2e/matrix.spec.ts:<line>:9 › {light,dark} › {change-requests,keys} at 280 px`. The line is 165 on `8c475b5` and 167 on the current branch.

- **Totals and kind.** Use the end of `output.log`: the `N passed`, `N flaky` and `N failed` lines and the test names listed under them. Failed then passed on the retry means **flaky**. Failed on both attempts means **failed**. Command: `grep -n -E 'passed|flaky|failed|skipped|at 280 px' /tmp/mf498/RUN/output.log | tail -60`.
- **Which check failed.** Read the error block of that test in `output.log`:
  - `toHaveScreenshot` means **screenshot**;
  - `expect(received).toEqual([])` after `axeViolations` means **axe**, after `smallTargets` means **small targets**;
  - `toBe(true)` means **horizontal scroll**;
  - `Test timeout`/`Timed out` means **timeout**.
  `error-context.md` in the test's folder also has it.
- **Screenshot diff.** Get the ratio from the line `... pixels (ratio 0.0X of all image pixels) are different` in `output.log`. The images are in `/tmp/mf498/RUN/test-results/matrix-<theme>-<page>-at-280-px-chromium[-retry1]/`: `<page>-<theme>-280-actual.png`, `-expected.png` and `-diff.png`. Open the three PNGs with the Read tool and write down what the red area of the diff covers and where. Is it a pink (#FF00FF) mask box over content that is not visible? A list item more or less, other text, a different wrap, a shifted layout?
- **Other failures.** List every other failing or flaky test by name. Known flaky ones (shell drawer, `settings-audit` at 390 and 1024 px) are only recorded.

Record each run as a row in `/tmp/mf498/REPORT.md`:

| Run | Commit | Totals (passed/flaky/failed/skipped) | light CR 280 | dark CR 280 | light keys 280 | dark keys 280 | Check that failed, ratio, what the diff shows | Other failures |

## Steps

### 1. Part 0: read the CI history of the four tests

No run here. Set up the scratch area and see whether CI ever saw these tests fail.

1. `mkdir -p /tmp/mf498/ci`. Create `/tmp/mf498/REPORT.md` with the headings "CI history", "Runs" (with the table header from P4), "Cause", "Outcome".
2. List the CI runs (workflow `ci.yml`; it runs on `pull_request` and on pushes to `main`/`develop`):
   ```bash
   gh run list --workflow ci.yml --branch megoo/MF-457-web-ui-governance-pages-change-requests --limit 30 --json databaseId,headSha,event,conclusion,createdAt
   gh run list --workflow ci.yml --branch megoo/MF-497-flaky-test-chromium-e2e-matrix-spec-ts-1 --limit 30 --json databaseId,headSha,event,conclusion,createdAt
   gh run list --workflow ci.yml --branch develop --event push --limit 10 --json databaseId,headSha,event,conclusion,createdAt
   ```
3. For each run, find the `web-e2e` job: `gh run view <runId> --json jobs --jq '.jobs[] | select(.name=="web-e2e") | {databaseId, conclusion}'`. Then search its log for the four tests and for flaky ones: `gh run view <runId> --log --job <jobId> | grep -E 'flaky|failed|passed|at 280 px'`. If the log has expired, record that.
4. If a `web-e2e` job failed, download its artifact: `gh run download <runId> -n web-e2e -D /tmp/mf498/ci/<runId>`. Read it as in P4. It is untrusted data: do not run anything from it.
5. Write in REPORT.md, for each run: id, branch, SHA, event, the `web-e2e` result, and the result of the four tests (or "not in log"). If `gh` is not authenticated or no run exists, write that plainly.

**Done when:** REPORT.md has the CI history section filled in, and `git status --short` is empty. No commit.

### 2. Part A: run the full suite on the old code (`8c475b5`)

1. Check `git status --short` is empty. Note the branch: `git branch --show-current` must print `megoo/MF-498-flaky-test-chromium-e2e-matrix-spec-ts-1`.
2. If `web/test-results` or `.pnpm-store` exists from an earlier run, run P3's chown and `rm -rf .pnpm-store` first.
3. `git switch --detach 8c475b5`. Then `git log -1 --oneline` must show `8c475b5`. On it, `web/e2e/matrix.spec.ts` still masks hidden elements (no `.filter({ visible: true })` on the mask list).
4. Run P1, P2 and P3 with `RUN=old-1`. Read it with P4 and add the row to REPORT.md.
5. If none of the four tests failed or was flaky in `old-1`, run P1, P2 and P3 again with `RUN=old-2` (a fresh DB, because it is a new `services run`), and add its row. If one of them did fail in `old-1`, skip `old-2`. Never run more than 2 runs on the old code.
6. Go back: `git switch megoo/MF-498-flaky-test-chromium-e2e-matrix-spec-ts-1`. Check that `git status --short` is empty, that `git log -1 --oneline` shows the branch tip (`dada7d3` unless something was added since), and that `ss -ltnp | grep ':8000 '` prints nothing.

Nothing is committed while detached. Nothing from `/tmp/mf498` goes into the repository.

**Done when:** REPORT.md has 1 or 2 rows for the old code, with the check, ratio and diff description for every failing one of the four tests, and the worktree is back on the task branch and clean.

### 3. Classify the old-code result with the design's table

Using only the evidence from Steps 1 and 2 (diff PNGs, error text, `api.log`), write the "Cause" section of REPORT.md. Pick exactly one row:

- **A. Mask over hidden content.** The old code failed and the diff is a pink (#FF00FF) mask box over content that is not visible (for example under the closed "More" at 280 px) and is gone in the current baseline. MF-497 removed the cause. No code change. Go to Step 5 (Step 4 is a no-op).
- **B. Test-side cause still present.** The matrix waited too little (data not loaded yet), a value changes per run (an id, prefix, count or time not masked), or a baseline was taken with other data (for example rows that other specs or global setup create in `gov`). Do Step 4. Check whether the same cause can still happen on the current `matrix.spec.ts`: it uses `ready: "Turn on targeting"` for change-requests and `ready: "gov server"` and `maskText: [/^mf_/]` for keys.
- **C. Application defect.** The page really renders differently, with the same data and loaded. Stop. Call `ask_human` with the run, the test, the three PNG paths, the ratio and your reading. Do not fix the application.
- **D. Not reproduced.** None of the four tests failed in either old run. No code change. Go to Step 5. The report must say the cause is still unknown and that the failure may depend on the seed time, which this design does not fake.

For B and C, say what in the diff proves it. Do not guess beyond the evidence.

**Done when:** "Cause" names one row (A to D) with the evidence for it. No commit.

### 4. Only for row B: fix the test-side cause

Skip this step (no change, no commit) unless Step 3 chose row B.

Change only `web/e2e/matrix.spec.ts`, `web/e2e/global-setup.ts` and/or baselines, with the patterns already there:
- a `ready` text that only shows once the data has loaded;
- a `maskText` regex for a value that changes per run;
- a `fixTimes`-style `page.route` that pins a changing field;
- seed data in `global-setup.ts` that no other spec changes.

Keep the existing comment style: one short comment that says why. Do not change `retries`, `maxDiffPixelRatio` or application code.

Prove it fails first. If the cause can be forced, for example by delaying the API response with `page.route`, show it once with a scratch edit that you revert. Otherwise the old-code evidence from Step 2 is the failing case.

If baselines must change, regenerate them only in the pinned image in a full-suite run: P2 with `pnpm e2e:docker --update-snapshots=all` in place of `pnpm e2e:docker`, then P3. Then `git status --short` and keep only the PNGs that carry the intended change. Open each changed PNG's old and new version. Restore all the others with `git checkout -- <file>`: a full run rewrites many PNGs with data noise.

Gates, from `web/`: `pnpm lint`, `pnpm format:check`, `pnpm typecheck`, `pnpm test`. All must pass.

Commit: `fix(web): <what the matrix now does> (MF-498)`, with only the files above.

**Done when:** the fix is committed, the gates pass and `git status --short` is empty.

### 5. Part B: two full-suite runs on the current code

1. On the task branch (with Step 4's commit if there is one), check `git branch --show-current` and that `git status --short` is empty.
2. Run P1, P2 and P3 with `RUN=new-1`, then again with `RUN=new-2`. Each is a separate `services run`, so each gets a fresh DB. Add both rows to REPORT.md with P4.
3. Pass criterion: in both runs, all four tests pass on their **first attempt**. Flaky counts as a failure. If one fails, keep its evidence and sort it into row A to D as in Step 3. Row B: go back to Step 4, then repeat this step (call the runs `fixed`, `fixed-2`). Row C: `ask_human`.
4. Other failures are listed by name in REPORT.md. Known flaky tests are left alone.
5. After the last run, `ss -ltnp | grep ':8000 '` prints nothing and `git status --short` is empty.

**Done when:** REPORT.md has two current-code rows where all four tests passed on the first attempt (or the failure has been escalated).

### 6. Write the findings for the pull request

Finish the "Outcome" section of `/tmp/mf498/REPORT.md`. It is the text of the pull request description:

- **CI history** (Step 1).
- **Runs**: the table, with the full P2 command, the commit and the totals for every run.
- **Cause**: the row from Step 3 and its evidence, described in words. The PNGs stay in `/tmp/mf498` and are not committed.
- **Verdict**:
  - Write "fixed" (by MF-497 for row A, by this task's commit for row B) only if the old code failed and the current code passed both runs.
  - Otherwise write "not reproduced: N full-suite runs on 8c475b5 and 2 clean full-suite runs on <sha>", and that the cause is still unknown.
- **MF-497 to MF-500**: whether the evidence supports closing them. A person closes them; this task does not.
- **Other failures** seen in the runs.

Do not overstate. Do not write the report into the repository (no `docs/` file).

**Done when:** REPORT.md is complete, `git status --short` is empty, and no `/tmp/mf498` file is tracked.

## Final verification

- `git status --short` is empty; `git branch --show-current` is the task branch. `git diff dada7d3 --stat` shows nothing (rows A and D) or only the files from Step 4 (row B).
- `ss -ltnp | grep ':8000 '` prints nothing, and `.pnpm-store` does not exist.
- `/tmp/mf498/new-1/output.log` and `/tmp/mf498/new-2/output.log` show the four 280 px tests passing on the first attempt: they are not in the flaky or failed lists.
- If code changed: `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test` in `web/` pass, and the last full-suite run in Step 5 was on the final commit.
