# MF-497: the visual matrix masks only what is visible

## Goal

`matrix.spec.ts` stops failing at 280 px on the change-requests and keys pages depending on when the database was seeded. Hidden text can no longer change any matrix screenshot. The same change resolves MF-498, MF-499 and MF-500, which have the same cause.

## Cause

This comes from reading the spec, the baselines and the Playwright documentation. Nothing was run in this read-only stage, so the build confirms it first (see Testing).

- The matrix masks `page.locator("time")`. Playwright's `mask` "is also applied to invisible elements".
- In `DataList` (`web/components/patterns/data-list.tsx`), `extra` columns such as "Created" sit inside a closed `<details>` ("More") below 1024 px. The `<time>` there is not shown, but it still gets a pink box.
- The box takes its shape from the timestamp text, which is the seed time of that run's database. At 280 px the hidden text has about 6 px of room. "Nov 22, 2026, 9:28 PM" (21 characters) fits on one line; a 22-character time (hours 10 to 12, AM or PM, UTC) wraps to two. The box changes shape and the diff passes the 1 % limit.
- The baselines show it: pink boxes under "More" in `change-requests-light-280` and `keys-light-280`, a strip down the right edge of `change-requests-light-880`, and a box over part of the visible flag keys in `flags-light-280`.

## Decisions

- **Mask only visible elements** (chosen by the human). Every mask locator gets `.filter({ visible: true })`. Playwright's visibility check uses `checkVisibility()`, which treats the content of a closed `<details>` as hidden.
- **Applied to all three kinds of mask** (the app version, `time`, and the `maskText` patterns), not only `time`. The rule is then one sentence, and a masked prefix or id inside "More" cannot cause the same problem later.
- **Regenerate every matrix baseline in one run** with `--update-snapshots=all`, and commit the PNGs that git reports as changed. The default mode (`changed`) rewrites only baselines that fail the 1 % limit. It would leave smaller hidden boxes in place, for example the 880 px strip, and they would keep using part of the tolerance on every run.
  - Consequence: a baseline with a *visible* masked time can also change by a few pixels, because its box follows the new seed time. That is expected.
- **Rejected: pin `created_at` on these two pages** (like `fixAuditTimes`). The hidden boxes would stay, and flags, integrations, nodes, tokens and members would keep the weakness.
- **Rejected: also pin every visible time.** Larger change; no such failure has been reported since the audit fix.
- **Rejected: more retries or a higher `maxDiffPixelRatio`.** This hides the failure and weakens every other screenshot.
- **No permanent regression test.** Once hidden elements are not masked, they are not in the screenshot at all. A comment at the mask explains why the filter is there.

## Architecture

One file of code and its baselines:

- `web/e2e/matrix.spec.ts`
  - The `mask` list in `toHaveScreenshot` is built as today, then each locator is filtered to visible elements.
  - The comment above `PAGES` says that only visible elements are masked, and why (Playwright masks hidden ones too, and their boxes follow text that is not on the page).
  - `fixTimes` and `fixAuditTimes` stay as they are: audit times are visible in the cards, so their width still matters.
- `web/e2e/matrix.spec.ts-snapshots/*.png`: regenerated in the pinned image, on a fresh database, with `pnpm e2e:docker e2e/matrix.spec.ts --update-snapshots=all` (API started as AGENTS.md §9 says).

No application code, API, configuration or documentation changes.

## Error handling and edge cases

- **Elements hidden with `display: none`** (the table below 768 px) have no box today, so nothing changes for them.
- **Visible times keep the width of their text.** A page where that width changes the wrapping still needs `fixTimes`, as the audit pages do.
- **Pinned image side effects.** `pnpm e2e:docker` writes the PNGs as root and can leave an API on port 8000. After the run, `git status` must show only `matrix.spec.ts` and PNGs under `matrix.spec.ts-snapshots`.
- **If the build cannot reproduce the failure**, it still delivers the change, because the hidden boxes are wrong either way. The pull request then says plainly that the flake was not reproduced.

## Testing

All in the pinned image (`pnpm e2e:docker`), against a fresh database.

1. **Reproduce before the fix.** With the spec unchanged, run `-g "change-requests at 280"` with a temporary, uncommitted `page.route` that sets the list's `created_at` to `2026-12-28T12:28:28Z` ("Dec 28, 2026, 12:28 PM", 22 characters). Expected: the test fails and the diff is the pink boxes. Also run the unchanged test with `--repeat-each 10` to see whether it is unstable on the same data. Record which of the two reproduced it.
2. **Fix and regenerate**, as in Architecture.
3. **Prove the fix.** The same temporary route now passes at 280 px for change-requests, and the equivalent one for the SDK keys list passes for keys, in both themes. Remove the temporary routes.
4. **Check the changed PNGs.** Each differs only by pink boxes that are gone, or by a few pixels in a visible masked box. Spot checks:
   - `change-requests-*-280` and `keys-*-280` have no box under "More";
   - `change-requests-*-880` has no strip;
   - `flags-*-280` shows the full flag keys.
5. **Gates.** The matrix without `--update-snapshots`, then the full `pnpm e2e:docker` suite, then `pnpm lint`, `pnpm format:check`, `pnpm typecheck` and `pnpm test` in `web/`.

## Out of scope

- Pinning visible times on other pages, and any change to `DataList` or other application code.
- Changing `retries` or `maxDiffPixelRatio`.
- Closing MF-498, MF-499 and MF-500. The pull request names them as fixed by the same change; a person closes them.
