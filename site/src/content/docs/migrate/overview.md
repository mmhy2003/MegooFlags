---
title: "Migrate: overview and importers"
description: Import projects from Unleash, Flagsmith and LaunchDarkly as flags-as-code files.
---

`megooflags import` reads a project from another feature flag tool and writes it as a
[flags-as-code](/MegooFlags/use/flags-as-code/) directory plus a report, `import-report.md`, of everything that could
not be translated as it was. It never writes to MegooFlags: you review the files, then `apply` them.

## Commands

```bash
megooflags import <tool> (--url URL | --file FILE) --project P --out DIR \
  [--source-project X] [--token-env VAR] [--env-map source=target …] [--strict] [--save FILE]
```

| Tool         | Command                                                                                  | Token variable (default `--token-env`)                     | `--url`                                | `--source-project`                                          |
| ------------ | ---------------------------------------------------------------------------------------- | ---------------------------------------------------------- | -------------------------------------- | ----------------------------------------------------------- |
| Unleash      | `megooflags import unleash --url https://unleash.example.com --project shop --out flags` | `UNLEASH_API_TOKEN`, an admin API token                    | Required with the API                  | The Unleash project id; default `default`                   |
| Flagsmith    | `megooflags import flagsmith --source-project 1234 --project shop --out flags`           | `FLAGSMITH_API_KEY`, an organisation API key               | Default `https://api.flagsmith.com`    | The Flagsmith project id; required with the API             |
| LaunchDarkly | `megooflags import launchdarkly --project shop --out flags`                              | `LAUNCHDARKLY_API_TOKEN`, an access token with read access | Default `https://app.launchdarkly.com` | The LaunchDarkly project key; default the `--project` value |

- `--project` (or `-p`) is the MegooFlags project key written to `megooflags.yaml`.
- The token is read from the environment variable named by `--token-env`, never from an argument:

  ```bash
  export LAUNCHDARKLY_API_TOKEN=api-…
  megooflags import launchdarkly -p shop --out flags
  ```

- For a self-hosted Flagsmith or another LaunchDarkly host, pass `--url`.
- `--env-map prod=production` renames an environment of the other tool (repeatable). Other environment
  keys are kept, adjusted to MegooFlags' key rules.

The command prints `<n> flags, <m> segments; <k> items in import-report.md` and the `apply` command to run
next.

## Saved files: `--save` and `--file`

`--save FILE` also writes everything read from the tool's API to `FILE` as JSON. `--file FILE` reads such a
file instead of the API, with no token and no network:

```bash
megooflags import unleash --url https://unleash.example.com -p shop --out flags --save unleash.json
megooflags import unleash --file unleash.json -p shop --out flags --env-map development=dev
```

Use it to re-run an import with other options, to share an export without sharing the token, or as a test
fixture. The file holds, per tool:

| Tool         | Contents                                                                                                                  |
| ------------ | ------------------------------------------------------------------------------------------------------------------------- |
| Unleash      | The enabled environments, the response of `POST /api/admin/features-batch/export` for each, and `GET /api/admin/segments` |
| Flagsmith    | Each environment's document (`GET /api/v1/environments/{api_key}/document/`), keyed by environment name                   |
| LaunchDarkly | The environments, and the flags and segments of each environment                                                          |

## What is translated

| Tool         | Becomes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Reported, not translated                                                                                                                                                                                                                                 |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unleash      | A feature becomes a boolean flag, or a string (or JSON) flag when it has variants, with a `disabled` off variation. Each strategy becomes one rule: constraints become clauses, `flexibleRollout` a rollout, `userWithId` targets, segments an `in_segment` clause; a `default` strategy with no constraints becomes the fallthrough. Segments become segments. Dependencies become prerequisites                                                                                                                  | Case-insensitive constraints (kept case-sensitive), IP ranges (`IN_CIDR`, `remoteAddress`), random stickiness, `SEMVER_GTE` and `SEMVER_LTE`, custom and other strategies, disabled strategies, dependencies on a disabled parent or on several variants |
| Flagsmith    | A feature becomes a boolean flag, or a flag typed by its value (string, number, boolean) with variations `default` and `option-<n>` for the multivariate options and a rollout over their percentages. Segment overrides become rules with an `in_segment` clause, in priority order; when the feature is off in an environment but an override turns it on, the flag is on there and its fallthrough serves the off variation. Segment rules ALL, ANY and NONE become clauses, several rules, and negated clauses | Identity overrides (one line each), percentage split, `IS_SET` and `IS_NOT_SET`, modulo, rules nested more than two levels or expanding to more than 20 rules                                                                                            |
| LaunchDarkly | Almost one to one: variations, targets, rules and clauses, rollouts (the same weight unit), fallthrough, off variation, prerequisites. Segments are per environment in LaunchDarkly: a segment that is the same in every environment becomes one segment, otherwise one segment `<key>-<env>` per environment                                                                                                                                                                                                      | Context kinds other than user (those targets, clauses and rules are skipped), experiments (the rollout is kept), big segments (skipped, with the rules that use them), migration flags (skipped)                                                         |

Constraint and condition operators map to MegooFlags operators of the same meaning, for example Unleash
`STR_ENDS_WITH` to `ends_with` and `NOT_IN` to a negated `in`, and Flagsmith `GREATER_THAN_INCLUSIVE` to
`gte`. The attribute that names the user (Unleash `userId`, Flagsmith `identifier`) becomes `key`.

Nothing untranslatable is dropped silently: every skipped or changed item is a line in the report.

### Keys

MegooFlags keys match `^[a-z0-9][a-z0-9._-]{0,99}$`. A flag, segment or environment key that does not
fit is lowercased, other characters become `-`, leading characters that are not letters or digits are
removed, and it is cut at 100 characters. When two keys end up the same, the later ones get `-2`, `-3`
and so on. Each changed key is a report line, for example `key "NewCheckout" was renamed to
"newcheckout"`. Update the flag keys in your code to match.

## The report

`DIR/import-report.md` starts with this notice, always, with the tool's name:

> Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently from LaunchDarkly, so users
> can land in a different bucket than before.

A user who was in the 20% of a rollout in the old tool may not be in the 20% in MegooFlags. If that
matters for a running rollout, finish it in the old tool first, or start it again knowing the groups
change.

Then comes a table with one line per item, sorted by resource, key and environment:

```
| Resource | Key | Environment | Message |
|---|---|---|---|
| flag | newcheckout |  | key "NewCheckout" was renamed to "newcheckout" |
| flag | newcheckout | production | targets of context kind organization are not imported |
| segment | big-spenders | production | big segments (unbounded) are not imported; rules that use it are skipped |
```

`--strict` exits 1 when the report has any line, for scripts that must stop on a lossy import.

## Review, then apply

1. Import into a directory and read `import-report.md`.
2. Fix what the report lists in the files, by hand, where you need it.
3. Check the plan against MegooFlags, then apply:

   ```bash
   megooflags diff --dir flags -p shop
   megooflags apply --dir flags -p shop
   ```

   `apply` creates the project when it does not exist. In environments that require approvals it stops
   at `needs approval` unless you pass `--change-requests` (see [Flags as code](/MegooFlags/use/flags-as-code/#approvals)).

4. Check a few flags with `megooflags evaluate` for contexts you know, and commit the directory if you
   use [Git sync](/MegooFlags/use/flags-as-code/#git-sync).
