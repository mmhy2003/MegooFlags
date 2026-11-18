---
title: The megooflags CLI
description: "Install and configure the megooflags CLI: every command, output formats and exit codes."
---

`megooflags` manages MegooFlags from a terminal or from CI: projects, environments, segments and flags,
promotion between environments, local development with an SDK bootstrap file, flags as code
([Flags as code](/MegooFlags/use/flags-as-code/)) and imports from other tools ([Importers](/MegooFlags/migrate/overview/)).
It uses only the management REST API and an API token.

The CLI has its own version (0.1.0), independent of the server.

## Install

Each release is tagged `cli/vX.Y.Z` on the [GitHub releases page](https://github.com/mmhy2003/MegooFlags/releases).
It is a pre-release, so the page's "latest release" stays the server. A release has one archive per
system and a `SHA256SUMS` file:

| System  | Archive                                                                        |
| ------- | ------------------------------------------------------------------------------ |
| Linux   | `megooflags_X.Y.Z_linux_amd64.tar.gz`, `megooflags_X.Y.Z_linux_arm64.tar.gz`   |
| macOS   | `megooflags_X.Y.Z_darwin_amd64.tar.gz`, `megooflags_X.Y.Z_darwin_arm64.tar.gz` |
| Windows | `megooflags_X.Y.Z_windows_amd64.zip`, `megooflags_X.Y.Z_windows_arm64.zip`     |

Download the archive and `SHA256SUMS`, check the archive, and put the binary on your `PATH`:

```bash
v=0.1.0
gh release download "cli/v$v" --repo mmhy2003/MegooFlags \
  --pattern "megooflags_${v}_linux_amd64.tar.gz" --pattern SHA256SUMS
sha256sum --check --ignore-missing SHA256SUMS      # macOS: shasum -a 256 --check --ignore-missing SHA256SUMS
tar -xzf "megooflags_${v}_linux_amd64.tar.gz" megooflags
sudo install megooflags /usr/local/bin/
megooflags version                                 # megooflags 0.1.0
```

Without `gh`, download the same two files from the release page in a browser. On Windows, check the
archive with `Get-FileHash megooflags_0.1.0_windows_amd64.zip` against its line in `SHA256SUMS`.

To build from source, with Go 1.26 or later: `cd cli && go build -o bin/megooflags ./cmd/megooflags`.

## Log in

Create an API token in MegooFlags (a service token under `/api/tokens`, or a personal token from your
account), then:

```bash
megooflags login --url https://flags.example.com
# API token: (typed, not echoed)
# Logged in to https://flags.example.com as ci-bot
megooflags whoami
megooflags logout          # removes the token from the config file
```

`login` reads the token from `MEGOOFLAGS_API_TOKEN` when it is set, otherwise from a prompt on a terminal,
otherwise from one line on standard input (`echo "$TOKEN" | megooflags login --url …`). It checks the
token with the API and saves the URL and token to the config file with mode 0600.

There is no `--token` flag: a token in an argument would end up in the shell history and the process
list. The CLI never prints the token.

## Configuration

Each setting is taken from the first of these that has it:

1. the command-line flag;
2. the environment variable;
3. the config file, `~/.config/megooflags/config.yaml` (or `$XDG_CONFIG_HOME/megooflags/config.yaml`, or
   the file given with `--config`).

| Setting       | Flag              | Environment variable   | Config file key |
| ------------- | ----------------- | ---------------------- | --------------- |
| API URL       | `--url`           | `MEGOOFLAGS_URL`       | `url`           |
| API token     | none              | `MEGOOFLAGS_API_TOKEN` | `token`         |
| Project       | `-p`, `--project` | `MEGOOFLAGS_PROJECT`   | `project`       |
| Output format | `-o`, `--output`  | `MEGOOFLAGS_OUTPUT`    | `output`        |

```yaml
# ~/.config/megooflags/config.yaml
url: https://flags.example.com
token: mf_api_…
project: shop
output: table
```

In CI, set `MEGOOFLAGS_URL`, `MEGOOFLAGS_API_TOKEN` and `MEGOOFLAGS_PROJECT` and skip `login`.

## Output formats

`-o table` (the default) prints aligned columns. `-o json` prints indented JSON and `-o yaml` prints YAML,
both with the API's field names, for scripts:

```bash
megooflags flag list -p shop -o json | jq -r '.[].key'
```

`ruleset pull` is the exception: there `-o` is the file to write (see below).

## Exit codes

| Code | Meaning                                                                                                                                      |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | Done                                                                                                                                         |
| 1    | Error: the API refused the request, a file is invalid, or something failed. The message is `error: <code>: <message>`                        |
| 2    | Usage: an unknown command, a bad flag or argument, a missing `--project`, or a delete without `--yes`                                        |
| 3    | `diff` found differences between the files and the server                                                                                    |
| 4    | A change needs approval: the environment requires a change request (`apply`, `promote`, `flag on`, `flag config set` and other writes there) |

API errors print the error `code` and message. A `forbidden` error names the missing permission.

## Commands

Every command accepts `--help`. Commands that work inside a project need `-p`, `MEGOOFLAGS_PROJECT` or
`project` in the config file.

### Session

| Command                                  | Example                                                          |
| ---------------------------------------- | ---------------------------------------------------------------- |
| `login --url URL`                        | `megooflags login --url https://flags.example.com`               |
| `logout`                                 | `megooflags logout`                                              |
| `whoami`                                 | `megooflags whoami -o json`                                      |
| `version`                                | `megooflags version`                                             |
| `completion bash\|zsh\|fish\|powershell` | `megooflags completion bash > /etc/bash_completion.d/megooflags` |

### Projects

```bash
megooflags project list
megooflags project get shop
megooflags project create shop --name Shop --description "The web shop" \
  --env development:Development --env staging:Staging --env production:Production
megooflags project update shop --name "Web shop" --stale-after-days 60
megooflags project delete shop --yes
```

### Environments

```bash
megooflags env list -p shop
megooflags env get production -p shop
megooflags env create qa -p shop --name QA --description "Manual testing" --sort-order 1
megooflags env update production -p shop --approvals-required 1
megooflags env delete qa -p shop --yes
```

### Segments

```bash
megooflags segment list -p shop
megooflags segment get beta -p shop                  # details and config
megooflags segment create beta -p shop --name "Beta testers" --description "Opted in"
megooflags segment update beta -p shop --name Beta -f beta.yaml
megooflags segment delete beta -p shop --yes
```

The `-f` file of `segment update` holds the segment config, as YAML or JSON:

```yaml
included: [u1, u2]
excluded: []
rules:
  - description: Staff
    clauses:
      - {
          attribute: email,
          operator: ends_with,
          values: ["@example.com"],
          negate: false,
        }
```

### Flags

```bash
megooflags flag list -p shop                         # KEY, TYPE, NAME and on/off per environment
megooflags flag list -p shop --archived
megooflags flag list -p shop --tag checkout -q new
megooflags flag get new-checkout -p shop --env production
megooflags flag create new-checkout -p shop --name "New checkout" --type boolean \
  --description "Replaces the old checkout" --tag checkout --owner mona@example.com
megooflags flag create theme -p shop --name Theme --type string \
  --variation 'light="light"' --variation 'dark="dark"' --off-variation light
megooflags flag update new-checkout -p shop --tag checkout --tag web   # --tag replaces the tags
megooflags flag update new-checkout -p shop --clear-owner
megooflags flag archive new-checkout -p shop
megooflags flag restore new-checkout -p shop
megooflags flag delete new-checkout -p shop --yes    # the flag must be archived first
```

Variations take a JSON value:

```bash
megooflags flag variation set theme high-contrast -p shop --value '"high-contrast"' --name "High contrast"
megooflags flag variation remove theme high-contrast -p shop
```

`variation set` creates the variation, or updates it when the flag already has one with that key.

### Targeting and state

```bash
megooflags flag config get new-checkout -p shop --env staging            # YAML unless -o json
megooflags flag config set new-checkout -p shop --env staging -f staging.yaml
megooflags flag on new-checkout -p shop --env staging
megooflags flag off new-checkout -p shop --env staging
megooflags flag kill new-checkout -p shop --env production
```

The file of `config set` has the same shape as one environment in a flags-as-code flag file
([Flags as code](/MegooFlags/use/flags-as-code/#the-files)), as YAML or JSON:

```yaml
enabled: true
targets: [{ variation: "true", values: [alice, bob] }]
rules:
  - description: Beta testers
    clauses:
      [{ attribute: key, operator: in_segment, values: [beta], negate: false }]
    variation: "true"
fallthrough:
  rollout:
    bucket_by: key
    weights:
      [
        { variation: "true", weight: 25000 },
        { variation: "false", weight: 75000 },
      ]
prerequisites: []
```

Weights are in thousandths of a percent: 25000 is 25%, and the weights of a rollout add up to 100000.
`config set` reads the current version first and retries once if someone else saved in between.

In an environment that requires approvals, `flag on`, `off`, `config set` and the other targeting writes
exit 4 with `error: approval_required: …`: open a change request in the UI, or use
`megooflags apply --change-requests`. The kill switch stays direct.

### Promote

`promote` copies flag configs from one environment to others, with the copy endpoint:

```bash
megooflags promote new-checkout theme -p shop --from staging --to production
megooflags promote --all -p shop --from staging --to qa,production --include-state
megooflags promote new-checkout -p shop --from staging --to production --change-requests
```

- It copies the targets, rules, fallthrough and prerequisites. `--include-state` also copies whether the
  flag is on; without it each target keeps its own on or off state.
- `--all` promotes every flag that is not archived.
- A target environment that requires approvals is not changed: the CLI prints
  `<flag> -> <env>: needs approval` and exits 4. With `--change-requests` it opens a config change request
  for that target instead, and prints its id.
- A flag that fails does not stop the others; the exit code is then 1.

### Evaluate

`evaluate` asks the server what flags return for a context, with the value, the variant and the reason:

```bash
megooflags evaluate new-checkout theme -p shop --env staging -c key=u1 -c plan=pro -c beta=true
megooflags evaluate -p shop --env staging --context ctx.json           # every flag
```

- `--context` reads a JSON or YAML context object; `-c key=value` pairs are merged into it.
- A value is parsed as JSON when it is valid JSON, so `-c beta=true` is a boolean and `-c age=42` a
  number; otherwise it is a string.
- The context `key` defaults to `cli`.

### Ruleset

`ruleset pull` downloads an environment's ruleset, the same file the server SDKs download, for offline
use and tests:

```bash
megooflags ruleset pull -p shop --env development -o ruleset.json    # wrote ruleset.json (12 flags)
megooflags ruleset pull -p shop --env development -o -               # to stdout
```

Here `-o` is the file to write, and it replaces the global output format flag.

### Flags as code and imports

| Command                                                                 | Example                                                  |
| ----------------------------------------------------------------------- | -------------------------------------------------------- |
| `export --dir DIR [--env e …]`                                          | `megooflags export -p shop --dir flags`                  |
| `diff --dir DIR [--env e …] [--prune]`                                  | `megooflags diff -p shop --dir flags`                    |
| `apply --dir DIR [--env e …] [--prune] [--dry-run] [--change-requests]` | `megooflags apply -p shop --dir flags --change-requests` |
| `import unleash\|flagsmith\|launchdarkly …`                             | `megooflags import launchdarkly -p shop --out flags`     |

See [Flags as code](/MegooFlags/use/flags-as-code/) and [Importers](/MegooFlags/migrate/overview/).

## Local development

Develop and test against a fixed copy of the flags, with no MegooFlags server running.

1. Pull the ruleset of an environment:

   ```bash
   megooflags ruleset pull -p shop --env development -o ruleset.json
   ```

2. Start the SDK from that file, offline. With the Go SDK:

   ```go
   provider := megooflags.NewProvider(megooflags.Config{
       SDKKey:        os.Getenv("MEGOOFLAGS_SDK_KEY"), // still a server key, mf_server_…
       BootstrapFile: "ruleset.json",
       Offline:       true, // no network: evaluate from the file only
   })
   if err := openfeature.SetProviderAndWait(provider); err != nil {
       log.Fatal(err)
   }
   ```

   The provider evaluates every flag locally from the file and makes no request. The other server SDKs
   have the same bootstrap file and offline options; see their READMEs under `sdks/`.

3. Check what the server returns for a context, to compare with your code:

   ```bash
   megooflags evaluate new-checkout -p shop --env development -c key=u1 -c plan=pro
   ```

Pull the file again when the flags change, or commit it as a test fixture.
