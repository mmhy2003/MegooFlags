@AGENTS.md

## Claude Code notes
- Use `uv run` and `pnpm` from the package directory; do not install tools globally.
- Run the local checks from AGENTS.md before declaring a task done.
- Never run `git tag`, `git push --tags` or `gh release` for this repository.
- Branch from `origin/develop` and open PRs against `develop`, never `main`. `main` only receives release PRs from `develop`.
