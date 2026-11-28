# MF-103: `develop` is for development, `main` is for production

## Goal
Make the repository say and enforce one branching model: all feature and fix work lands in `develop`, and `main` changes only through a release PR from `develop`. Production releases (`vX.Y.Z` tags) can only be cut from commits on `main`.

## Decisions
- **Releases stay tag-driven, but only from `main`** (your answer). A maintainer still pushes `vX.Y.Z` after merging `develop` into `main`. `release.yml` gets a guard that fails before anything is built if the tagged commit is not on `main`. Rejected: releasing automatically when `main` gets a new `VERSION`, and changing only the docs with no check.
- **rc tags (`vX.Y.Z-rc.N`) may come from `develop` or `main`.** They are pre-releases used to test a release before it reaches production, so tagging the `develop` tip is the normal case. Only final `vX.Y.Z` tags must be on `main`. Say so if you want rc tags restricted to `main` too.
- **CI also runs on pushes to `develop`.** Today it runs only on `main`, so a merged result in `develop` gets no CI.
- **The version bump goes into `develop`** through an ordinary agent PR (AGENTS.md section 4). The release PR `develop` → `main` then carries it. This keeps `main` an exact copy of a tested `develop` state.
- **Use merge commits** (no squash or rebase) for PRs into `develop` and for `develop` → `main`. With squash, `main` and `develop` drift apart and every release PR shows old commits again. PR #1 already used a merge commit.
- **Hotfixes also go through `develop`**, followed by a patch release PR. There is no separate hotfix-branch flow (YAGNI).

## Changes by file
| File | Change |
|---|---|
| `.github/workflows/ci.yml` | `push.branches: [main]` → `[main, develop]`. `pull_request` stays unfiltered, so CI covers PRs into both branches. |
| `.github/workflows/release.yml` | In the `meta` job, `actions/checkout@v4` gets `fetch-depth: 0`. A new first step, "Tag is on main", runs before "Tag matches VERSION". For a final tag (no `-rc.`), it runs `git merge-base --is-ancestor "$GITHUB_SHA" origin/main` and, if that fails, prints `::error::vX.Y.Z must point to a commit on main` and exits 1. For an rc tag, the step logs that it is skipped. Since `ci` and `build` need `meta`, nothing is built or published when the check fails. |
| `AGENTS.md` | New section **"Branches and pull requests"**, placed right after "1. Project layout" (later sections are renumbered, and the `#8-local-checks...` anchor in the README is updated to match). The section says: `develop` is for new features and upcoming changes, and every task branch starts from an up-to-date `origin/develop` (`git fetch origin` then `git switch -c <branch> origin/develop`). `main` is production and changes only through a release PR from `develop`. Nobody commits or pushes directly to either branch. Task PRs target `develop` (`gh pr create --base develop`). Merge commits only. Section 9 "Releases" becomes an ordered procedure: (1) a version-bump PR into `develop` (section 4), (2) a PR from `develop` into `main` titled `Release X.Y.Z`, (3) the maintainer merges it and tags the merge commit on `main` with `vX.Y.Z` (rc tags may be cut from `develop`), (4) `release.yml` refuses a final tag that is not on `main`. The section keeps all current rules that agents never tag, release or push images. |
| `CLAUDE.md` | Add to "Claude Code notes": "Branch from `origin/develop` and open PRs against `develop`, never `main`. `main` only receives release PRs from `develop`." |
| `README.md` | "Versioning and releases": one short paragraph saying day-to-day work is merged into `develop`, `main` holds the released code, and releases are tagged on `main` after a `develop` → `main` release PR. It links to AGENTS.md for details. |

No `CHANGELOG.md` entry, because this changes only contributor and CI process, not anything users see.

## Edge cases
- A tag pushed before its `develop` → `main` PR is merged fails the guard. The fix is to delete the tag, merge the PR, then tag again on `main`.
- A tag on an old commit of `main` passes the guard. `check_version.py` still requires the tag to match `VERSION` at that commit.
- A shallow checkout would make `merge-base` fail even for a valid tag. That is why `fetch-depth: 0` is set, which also fetches `origin/main`.

## Testing
- Run `actionlint` (the docker command in AGENTS.md) on both workflows.
- Test the guard logic locally in a scratch clone: a commit on `main` passes, and a commit that is only on `develop` fails.
- `grep` checks: AGENTS.md, CLAUDE.md and README all name `develop` as the PR base, and the README anchor resolves to the renumbered section.
- Agents can't push, so a maintainer confirms on GitHub that a push to `develop` triggers CI and that the next real release tag on `main` passes the guard.

## Out of scope
- Branch protection rules for `develop` and `main`. A maintainer sets these by hand in GitHub settings; requiring PRs and the CI checks on both branches is recommended.
- Changing the default branch on GitHub.
- Automatic releases from `main` and changes to how `sdk-*` tags are released.
