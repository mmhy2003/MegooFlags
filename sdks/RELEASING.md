# Releasing an SDK

This guide is for maintainers. Agents prepare the version bump and the pull requests, but they never
push tags, create registry accounts or set secrets (see [AGENTS.md](../AGENTS.md), sections 10 and 11).

## 1. How an SDK release works

Each SDK has its own version and its own tag, `sdk-<lang>/vX.Y.Z`, where `<lang>` is `node`, `python`,
`java`, `dotnet`, `ruby` or `php`. The version follows the X.Y.Z and 0.Y.Z rules in `AGENTS.md`.

1. Open a pull request into `develop` that bumps the version in the SDK's manifest and in its version
   constant, adds the SDK's changes to `CHANGELOG.md`, and updates the lockfile where it records the
   version. The files are:

   | SDK | Manifest | Version constant | Lockfile |
   |---|---|---|---|
   | Node.js | `sdks/node/package.json` | `sdks/node/src/version.ts` | none |
   | Python | `sdks/python/pyproject.toml` | `sdks/python/src/megooflags/_version.py` | `sdks/python/uv.lock` (`uv lock`) |
   | Java | `sdks/java/pom.xml` | `sdks/java/src/main/java/io/github/mmhy2003/megooflags/internal/BuildInfo.java` | none |
   | .NET | `sdks/dotnet/src/MegooFlags/MegooFlags.csproj` | `sdks/dotnet/src/MegooFlags/Internal/BuildInfo.cs` | none |
   | Ruby | `sdks/ruby/lib/megooflags/version.rb` (the gemspec reads it) | the same file | `sdks/ruby/Gemfile.lock` (`bundle install`) |
   | PHP | `sdks/php/src/Version.php` (`composer.json` has no version) | the same file | none |

2. In the same pull request, update the tests that assert the version (they pin it on purpose):
   `git grep -n -F '<old version>' -- sdks/<lang>` finds them.
3. Check the version: `python3 scripts/check_sdk_version.py --sdk <lang>` prints
   `sdk <lang> version OK: X.Y.Z`.
4. Merge the pull request once CI is green.
5. Open the release pull request from `develop` into `main` and merge it with a merge commit.
6. Push the tag `sdk-<lang>/vX.Y.Z` on that merge commit on `main`.
7. The `sdk-tag` job in `.github/workflows/release.yml` checks that the tag points to a commit on
   `main`. The `sdk-<lang>-release` job then checks that the tag matches the manifest, runs the
   checks, builds the package and uploads it as the workflow artifact `sdk-<lang>`.
8. The package is published only when the repository variable `SDK_PUBLISH_<LANG>` is `true`
   (section 3). Otherwise, download the artifact from the workflow run if you want to inspect it.
   npm, PyPI, NuGet and RubyGems are published by a separate `sdk-<lang>-publish` job: it is the only
   job with `id-token: write`, and it only downloads the artifact and uploads it, so no dependency
   install or build runs with that permission.

A test release `sdk-<lang>/vX.Y.Z-rc.N` runs the same checks (except the `main` check) and builds the
artifact, but never publishes, whatever the variable says.

## 2. One-time setup per registry

Do this once per SDK, before you set its `SDK_PUBLISH_<LANG>` variable to `true`. Secrets and variables
are set in the repository settings under **Secrets and variables → Actions**.

### npm (`@megooflags/node`)

1. Create the npm organization `megooflags`, so that the `@megooflags` scope exists.
2. Publish the first version by hand from a release artifact: `npm publish megooflags-node-X.Y.Z.tgz --access public`.
   npm allows a trusted publisher only on a package that exists.
3. In the package settings on npmjs.com, add a trusted publisher: GitHub Actions, repository
   `mmhy2003/MegooFlags`, workflow `release.yml`.

The job publishes with OIDC; no npm token is stored.

### PyPI (`megooflags`)

1. On PyPI, under **Your projects → Publishing**, add a pending trusted publisher: project name
   `megooflags`, owner `mmhy2003`, repository `MegooFlags`, workflow `release.yml`, no environment.

The first release creates the project. No API token is stored.

### Maven Central (`io.github.mmhy2003:megooflags`)

1. In the Central Publisher Portal (central.sonatype.com), verify the namespace `io.github.mmhy2003`.
2. Generate a user token in the portal.
3. Set the secrets `CENTRAL_USERNAME` and `CENTRAL_PASSWORD` to the two parts of the token.
4. Create a GPG key for signing and publish its public key to a key server (for example
   `keys.openpgp.org`).
5. Set the secret `GPG_SIGNING_KEY` to the ASCII-armored private key
   (`gpg --armor --export-secret-keys <key id>`).
6. Set the secret `GPG_PASSPHRASE` to the key's passphrase.

### NuGet (`MegooFlags`)

1. On nuget.org, under **Trusted Publishing**, add a policy: repository owner `mmhy2003`, repository
   `MegooFlags`, workflow `release.yml`.
2. Set the repository variable `NUGET_USER` to the nuget.org user name (not the email address) that
   owns the policy.

The job exchanges the OIDC token for a short-lived API key with `NuGet/login`; no API key is stored.

### RubyGems (`megooflags`)

1. On rubygems.org, under **Trusted Publishers**, add a pending trusted publisher: gem name
   `megooflags`, repository `mmhy2003/MegooFlags`, workflow `release.yml`.

The first release creates the gem. No API key is stored.

### Packagist (`megooflags/megooflags`)

Packagist reads a package from the root of a repository, so the job publishes `sdks/php` to a
separate read-only repository.

1. Create the empty repository `mmhy2003/megooflags-php` on GitHub. Mark it as read-only in its
   description: changes go to `sdks/php` in this repository.
2. Create a fine-grained personal access token with **Contents: read and write** on that repository only.
3. Set the secret `SDK_PHP_SPLIT_TOKEN` to that token.
4. If you use another repository name, set the repository variable `SDK_PHP_SPLIT_REPO` to
   `<owner>/<name>`. Without it the job pushes to `mmhy2003/megooflags-php`.
5. After the first release has pushed to the split repository, submit it on packagist.org and turn on
   the GitHub hook, so that Packagist picks up new tags.

The job pushes the split as the branch `main` and the tag `vX.Y.Z` of the split repository.

## 3. Switching publishing on

Each SDK has its own repository variable. Set it to `true` once the registry setup of section 2 is
done. Any other value, or no variable, means build only.

| SDK | Variable |
|---|---|
| Node.js | `SDK_PUBLISH_NODE` |
| Python | `SDK_PUBLISH_PYTHON` |
| Java | `SDK_PUBLISH_JAVA` |
| .NET | `SDK_PUBLISH_DOTNET` |
| Ruby | `SDK_PUBLISH_RUBY` |
| PHP | `SDK_PUBLISH_PHP` |

## 4. Go

The Go SDK keeps its own flow, because Go modules in a repository subdirectory are tagged with the
subdirectory path and are served by the Go module proxy, not by a registry.

1. Bump `Version` in `sdks/go/version.go` in a pull request into `develop`, and release it to `main`
   as above.
2. Push the tag `sdks/go/vX.Y.Z` on the merge commit on `main`.
3. The `sdk-go-release` job checks that the tag matches `version.go`, runs the tests and the
   conformance suite, and asks `proxy.golang.org` for the new version so that it shows up on
   pkg.go.dev.

There is nothing to set up and no variable to switch on.
