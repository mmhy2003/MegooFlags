# MF-103 plan: `develop` is for development, `main` is for production

## Goal
Make the repository document and enforce one branching model: all work lands in `develop` through merge-commit PRs, `main` changes only through a `develop` → `main` release PR, and final `vX.Y.Z` release tags are refused unless they point to a commit on `main`.

## Architecture
Two workflow edits enforce the model: `ci.yml` also runs on pushes to `develop`, and the `meta` job of `release.yml` gets a "Tag is on main" step (with a full-history checkout) that fails before anything is built if a final tag is not an ancestor of `origin/main`. rc tags skip the check. Three docs describe the model: a new AGENTS.md section 2 (later sections renumbered, cross-references and the README anchor fixed), a release procedure in AGENTS.md section 10, one Claude note in CLAUDE.md, and one paragraph in the README.

## File map
| File | Change |
|---|---|
| `.github/workflows/ci.yml` | `on.push.branches: [main]` → `[main, develop]` |
| `.github/workflows/release.yml` | `meta` job: checkout `fetch-depth: 0`; new first step "Tag is on main" before "Tag matches VERSION" |
| `AGENTS.md` | New `## 2. Branches and pull requests`; sections 2–10 become 3–11; internal "section N" references updated; `## 10. Releases` gets the ordered release procedure |
| `README.md` | Anchor `#8-local-checks-the-same-as-ciyml` → `#9-local-checks-the-same-as-ciyml`; new branching paragraph in "Versioning and releases" |
| `CLAUDE.md` | One extra bullet in "Claude Code notes" |

No CHANGELOG entry (process-only change). Do not touch `docs/` (existing MF-74 files stay as they are).

Section renumbering map for AGENTS.md (old → new): 2 Versioning → 3, 3 Before 1.0.0 → 4, 4 Bumping the version → 5, 5 CHANGELOG → 6, 6 Database migrations → 7, 7 API client → 8, 8 Local checks → 9, 9 Releases → 10, 10 SDKs → 11. Existing internal references to update: "(see section 8)" in Database migrations → "(see section 9)"; "(the version bump in section 4)" in Releases → section 5; "The rules in section 9" in SDKs → "section 10".

---

## Step 1: Run CI on pushes to `develop`

**Check first (fails today):** `grep -n 'branches: \[main, develop\]' .github/workflows/ci.yml` prints nothing.

**Change** in `.github/workflows/ci.yml`, the `on:` block becomes:
```yaml
on:
  pull_request:
  push:
    branches: [main, develop]
  workflow_call:
```
Leave `pull_request` unfiltered and everything else unchanged.

**Verify:**
- `grep -n 'branches: \[main, develop\]' .github/workflows/ci.yml` prints line 6.
- `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest` prints nothing and exits 0.

**Commit:** `ci: run CI on pushes to develop as well as main`

---

## Step 2: Refuse final release tags that are not on `main`

**Check first (fails today):** `grep -n 'Tag is on main' .github/workflows/release.yml` prints nothing.

**Change** in `.github/workflows/release.yml`, the start of the `meta` job's `steps:` becomes exactly:
```yaml
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Tag is on main
        run: |
          case "$GITHUB_REF_NAME" in
            *-rc.*)
              echo "$GITHUB_REF_NAME is a release candidate; the main-branch check is skipped."
              ;;
            *)
              if ! git merge-base --is-ancestor "$GITHUB_SHA" origin/main; then
                echo "::error::$GITHUB_REF_NAME must point to a commit on main"
                exit 1
              fi
              echo "$GITHUB_REF_NAME is on main."
              ;;
          esac
      - name: Tag matches VERSION
        run: python3 scripts/check_version.py --tag "$GITHUB_REF_NAME"
```
Everything after "Tag matches VERSION" stays as is. `fetch-depth: 0` makes checkout fetch full history and all branches, so `origin/main` exists; `ci` and `build` already `need: meta`, so a failure stops the whole release. Do not change the other jobs' checkouts.

**Test the guard locally** in a scratch directory outside the repo (GitHub runs `run:` with `bash -e`). Put the step body, copied verbatim (the lines between `run: |` and `- name: Tag matches VERSION`, de-indented), into `$S/guard.sh`:
```bash
S=$(mktemp -d)
git init -q -b main "$S/origin"
git -C "$S/origin" -c user.name=t -c user.email=t@t commit -q --allow-empty -m base
git -C "$S/origin" switch -q -c develop
git -C "$S/origin" -c user.name=t -c user.email=t@t commit -q --allow-empty -m feature
git clone -q "$S/origin" "$S/work"
MAIN=$(git -C "$S/work" rev-parse origin/main); DEV=$(git -C "$S/work" rev-parse origin/develop)
# write the step body to $S/guard.sh, then:
cd "$S/work"
GITHUB_REF_NAME=v0.1.0      GITHUB_SHA=$MAIN bash -e "$S/guard.sh"; echo "exit=$?"   # "v0.1.0 is on main." exit=0
GITHUB_REF_NAME=v0.1.0      GITHUB_SHA=$DEV  bash -e "$S/guard.sh"; echo "exit=$?"   # "::error::v0.1.0 must point to a commit on main" exit=1
GITHUB_REF_NAME=v0.1.0-rc.1 GITHUB_SHA=$DEV  bash -e "$S/guard.sh"; echo "exit=$?"   # "...release candidate; ... skipped." exit=0
cd - ; rm -rf "$S"
```
All three must give the shown output and exit code.

**Verify:** `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest` prints nothing (actionlint also runs shellcheck on the new script). `grep -n -A2 'actions/checkout@v4' .github/workflows/release.yml` shows `fetch-depth: 0` only under the first (meta) checkout.

**Commit:** `ci: refuse vX.Y.Z release tags that are not on main`

---

## Step 3: AGENTS.md branching section, renumbering and release procedure

**Check first (fails today):** `grep -n '^## 2. Branches and pull requests' AGENTS.md` prints nothing.

**3a. Insert** right after section 1 (after the "Always commit lockfile changes…" bullet, before `## 2. Versioning (X.Y.Z)`):
````markdown
## 2. Branches and pull requests

- `develop` is for new features and upcoming changes. Every task branch starts from an up-to-date `origin/develop`:

  ```bash
  git fetch origin
  git switch -c <branch> origin/develop
  ```

- `main` is production. It changes only through a release pull request from `develop` (section 10).
- Nobody commits or pushes directly to `develop` or `main`. Every change goes through a pull request.
- Task pull requests target `develop`: `gh pr create --base develop`. Never open a task pull request against `main`.
- Merge with a merge commit (`gh pr merge --merge`), never squash or rebase. This applies to pull requests into
  `develop` and to the `develop` → `main` release pull request. Squash or rebase makes `main` and `develop` drift
  apart, and every later release pull request shows old commits again.
- Hotfixes take the same path: a fix pull request into `develop`, then a patch release (section 10).
````

**3b. Renumber** the following headings per the map in the File map section (`## 3. Versioning (X.Y.Z)`, `## 4. Before 1.0.0 (0.Y.Z)`, `## 5. Bumping the version`, `## 6. CHANGELOG`, `## 7. Database migrations`, `## 8. API client`, `## 9. Local checks (the same as \`ci.yml\`)`, `## 10. Releases`, `## 11. SDKs`) and fix the three internal references: "(see section 8)" → "(see section 9)", "The rules in section 9" → "The rules in section 10". (The "section 4" reference disappears in 3c.)

**3c. Replace** the first part of `## 10. Releases`, from its first line down to and including the `vX.Y.Z-rc.N` bullet, with:
```markdown
## 10. Releases

Agents prepare release commits and pull requests. Agents **never**:

- create or push `v*` or `sdk-*` tags,
- create GitHub releases,
- push images to any registry.

A release follows these steps, in order:

1. **Bump the version in `develop`.** Open an ordinary pull request into `develop` with the version bump from section 5, and merge it once CI is green.
2. **Open the release pull request** from `develop` into `main`, titled `Release X.Y.Z`:
   `gh pr create --base main --head develop --title "Release X.Y.Z"`.
3. **Merge and tag.** A maintainer merges it with a merge commit and pushes the `vX.Y.Z` tag on that merge commit on `main`.
   A test release `vX.Y.Z-rc.N` may be tagged on `develop` or on `main`.
4. **`release.yml` does the rest.** It checks the tag before anything is built:
   - `vX.Y.Z` is a release. Its commit must be on `main`, and the tag must match `VERSION`, or the workflow fails.
     A tag pushed before the release pull request was merged fails: delete the tag, merge the pull request, then tag `main` again.
   - `vX.Y.Z-rc.N` is a test release. It may come from `develop`, publishes only the `X.Y.Z-rc.N` image tag and is always a pre-release.
```
Keep the rest of the section (the "Images go to…" paragraph, tag table and rc paragraph) unchanged.

**3d. README anchor** (same commit, so the link never breaks): in `README.md` change `AGENTS.md#8-local-checks-the-same-as-ciyml` to `AGENTS.md#9-local-checks-the-same-as-ciyml`.

**Verify:**
- `grep -n '^## ' AGENTS.md` shows sections 1 to 11 in order: Project layout, Branches and pull requests, Versioning, Before 1.0.0, Bumping the version, CHANGELOG, Database migrations, API client, Local checks, Releases, SDKs.
- `grep -n 'section [0-9]*' AGENTS.md` shows only: section 10 (twice in section 2), section 9 (migrations), section 5 (releases step 1), section 10 (SDKs).
- `grep -n 'base develop' AGENTS.md` matches; `grep -n 'Tag is on main\|must be on .main' AGENTS.md` matches the step-4 bullet.
- `grep -n '9-local-checks-the-same-as-ciyml' README.md` matches line 54 and `grep -n '^## 9. Local checks (the same as .ci.yml.)' AGENTS.md` matches (GitHub turns that heading into `9-local-checks-the-same-as-ciyml`).
- `grep -n 'never' AGENTS.md` still lists the three agent prohibitions.

**Commit:** `docs: branching model and release procedure in AGENTS.md`

---

## Step 4: CLAUDE.md note and README branching paragraph

**Check first (fails today):** `grep -n develop CLAUDE.md README.md` prints nothing.

**CLAUDE.md:** append to "Claude Code notes" (after the last bullet):
```markdown
- Branch from `origin/develop` and open PRs against `develop`, never `main`. `main` only receives release PRs from `develop`.
```

**README.md:** in "## Versioning and releases", after the existing paragraph (which ends "…`ghcr.io/mmhy2003/megooflags-web`."), add a blank line and:
```markdown
Day-to-day work is merged into `develop`, and `main` holds the released code. A release is a pull
request from `develop` into `main`; once it is merged, the maintainer tags the merge commit on `main`.
The branching rules and release steps are in [AGENTS.md](AGENTS.md#2-branches-and-pull-requests).
```

**Verify:**
- `grep -n develop CLAUDE.md README.md` matches the new CLAUDE.md bullet and the README paragraph.
- `grep -n '^## 2. Branches and pull requests' AGENTS.md` matches (the README anchor `#2-branches-and-pull-requests` resolves).
- `cat CLAUDE.md` still starts with `@AGENTS.md`.

**Commit:** `docs: point CLAUDE.md and the README at the develop/main model`

---

## Final verification
1. `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:latest` prints nothing.
2. The scratch-repo guard test from step 2 gives exit 0 / 1 / 0 for main-final, develop-final, develop-rc.
3. `grep -n 'develop' .github/workflows/ci.yml AGENTS.md CLAUDE.md README.md` shows the CI trigger and `develop` as the PR base in all three docs.
4. `grep -n '^## ' AGENTS.md` shows sections 1–11; both README anchors (`#2-branches-and-pull-requests`, `#9-local-checks-the-same-as-ciyml`) match AGENTS.md headings.
5. `python3 scripts/check_version.py` still prints `version OK: …` (nothing version-related changed).
6. Maintainer, on GitHub: a merge into `develop` triggers CI; the next `vX.Y.Z` tag on `main` passes "Tag is on main". Branch protection on `develop`/`main` is set by hand (out of scope).
