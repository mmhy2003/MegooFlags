# MF-498: reproduce the 280 px matrix failure on the old code, then prove it is gone

## Goal

We learn why `dark › change-requests at 280 px` failed at the finish of MF-457, with the diff image as evidence, and we show on the current code that it no longer fails. Code changes only if the evidence asks for one, so this task may end as a report with no code change.

## What is known

- **The fix that may cover this is already merged.** MF-497 (`b91ebed`) made the matrix mask only visible elements and regenerated 40 baselines. The current 280 px baselines for change-requests and keys have no pink box under "More".
- **That fix was never proven.** MF-497 could not reproduce the failure. Its explanation (a timestamp that wraps at 280 px) was disproved: the hidden boxes changed by about 5×18 px, far below the 1% limit.
- **MF-497 only tried matrix-only runs.** The failure was seen in a full-suite run. The full suite was never run on the old code.
- **The four failures look like one cause.** MF-497 to MF-500 are change-requests and keys at 280 px, in both themes, and nothing else failed. This pattern suggests something in that run's data and not random timing. It is an inference, not a fact.
- **Old and new code differ only in the matrix.** Between `8c475b5` (before MF-497) and this branch, only `web/e2e/matrix.spec.ts` and 40 PNGs changed, apart from two docs files. The application is the same.

## Decisions

- **Reproduce on the old code first, then prove on the new** (chosen by the human). Rejected: confirming on the current code only (the cause stays unknown), hardening on a guess, and closing as covered by MF-497.
- **Use the exact conditions of the original failure.** Full suite, pinned image, fresh database, `pnpm e2e:docker` with no extra arguments. The image sets `CI=1`, so `retries: 1` stays on. Playwright then tells us whether a test failed twice or failed and passed on its retry, which is not known today.
- **At most 4 full-suite runs:** up to 2 on the old code (stop after the first one that reproduces), then 2 on the current code.
- **Keep the evidence outside the repository.** Playwright empties `web/test-results` at the start of every run, so each run's `test-results` and `playwright-report` are copied to a scratch directory before the next run. Nothing from them is committed.
- **Fix only what the evidence shows, and only on the test side.** A defect in the application is reported to the human with the evidence and is not fixed under this design.

## Architecture

No new code is planned. The work is an experiment in three parts.

### Part 0: look at CI history (cheap, no run)

- With `gh`, read the `web-e2e` job of the CI runs for pull requests #18 (MF-457) and #19 (MF-497) and for the pushes to `develop`.
- Note whether any of the four tests failed or was reported as flaky there. The job uploads the `web-e2e` artifact (report, `test-results`, API log) only when it fails; download it if one exists.

### Part A: the old code

- With a clean tree, check out `8c475b5` detached in the task worktree.
- Run the full suite as in "Decisions". After the run, save the evidence, restore file ownership, and check that port 8000 is free.
- For each of the four tests (`change-requests` and `keys` at 280 px, light and dark), record:
  - the result: passed, flaky (failed, then passed on the retry) or failed;
  - which check failed: screenshot, axe, horizontal scroll, small targets or a timeout;
  - for a screenshot: the diff ratio, and what the diff image shows and where.
- If none of the four fails, run once more on a fresh database. Then go back to the task branch and confirm `git status` is clean.

### Part B: the current code

- On the task branch, run the full suite twice, each on a fresh database, with the same command.
- All four tests must pass on their first attempt in both runs. "Flaky" counts as a failure here.

### What each outcome leads to

| Old code | Meaning | Action |
|---|---|---|
| Fails, and the diff is a mask box over content that is not visible | MF-497 removed the cause | No code change. Part B is the proof. |
| Fails for a test-side reason (the matrix waits too little, a value changes per run, a baseline was taken with other data) | A cause that is still present | Fix it in `web/e2e/matrix.spec.ts`, `web/e2e/global-setup.ts` or the baselines, with the patterns already there (`ready`, a `fixTimes`-style route, `maskText`). Then Part B on the fixed code. |
| Fails because the page really renders differently | An application defect | Stop and ask the human, with the evidence. |
| Passes in both runs | Not reproduced | No code change. Run Part B anyway and report plainly that the cause is still unknown. |

A failure of one of the four tests in Part B is handled the same way: keep its evidence and sort it into one of the rows above.

## Data and interfaces

None. If a test-side fix is needed, it touches only the files named above. Baselines are regenerated only in the pinned image, in a full-suite run, and only the PNGs with the intended change are committed.

## Error handling and edge cases

- **Side effects of `pnpm e2e:docker`.** It leaves root-owned files in `web/` and a `.pnpm-store` at the repository root, and the API can stay on port 8000. Each run ends with the ownership fix, the store removed and the API stopped.
- **The old commit must not leak into the branch.** After Part A, `git status` is clean and `HEAD` is the task branch again. Nothing is committed while detached.
- **Other tests may fail in these runs.** Known flaky tests (the shell drawer, settings-audit at 390 and 1024 px) are recorded and left alone.
- **The cause may depend on the clock.** If the failure needs a certain seed time, runs made now can miss it. This design does not fake the clock; the report says so if the failure is not reproduced.
- **The report does not overstate.** "Fixed" is used only when the old code failed and the current code passed. Otherwise the pull request says "not reproduced, N clean full-suite runs".

## Testing

- The runs of Part A and Part B are the tests. Each is recorded with its command, the commit, the totals and the result of the four tests.
- If code changed: `pnpm lint`, `pnpm format:check`, `pnpm typecheck` and `pnpm test` in `web/`, plus one more full-suite run on the final code.
- The pull request description holds the findings: CI history, the result per run, the cause (or that none was found), and whether MF-497 to MF-500 can be closed. A person closes them.

## Out of scope

- Changes to application code, to `retries` or to `maxDiffPixelRatio`.
- Regenerating the 1024 and 1440 px baselines that still hold a few old mask pixels (the follow-up from the MF-497 review).
- The other known flaky tests.
- Closing MF-497, MF-499 and MF-500.
