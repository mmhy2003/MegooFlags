# Flags as code

A MegooFlags project can live in Git as a directory of YAML files. The [CLI](cli.md) writes the
directory from the server (`export`), compares it with the server (`diff`) and makes the server match it
(`apply`). The web UI keeps working: changes made there come back to Git as a pull request (see
[Git sync](#git-sync)).

## The files

```
flags/
  megooflags.yaml        # the project and its environments
  segments/<key>.yaml    # one file per segment; the file name is the key
  flags/<key>.yaml       # one file per flag; the file name is the key
```

```yaml
# megooflags.yaml
apiVersion: megooflags/v1
project: {key: shop, name: Shop, description: ""}
environments:
  - {key: development, name: Development, description: "", approvals_required: 0}
  - {key: production, name: Production, description: "", approvals_required: 1}
```

```yaml
# segments/beta.yaml
name: Beta
description: ""
included: [u1]
excluded: []
rules:
  - description: ""
    clauses:
      - {attribute: email, operator: ends_with, values: ["@example.com"], negate: false}
```

```yaml
# flags/new-checkout.yaml
name: New checkout
description: ""
type: boolean
tags: [checkout]
owner: mona@example.com            # left out when the flag has no owner
variations:
  - {key: "true", name: "", description: "", value: true}
  - {key: "false", name: "", description: "", value: false}
off_variation: "false"
permanent: false
archived: false
environments:
  production:
    enabled: true
    targets: [{variation: "true", values: [u1]}]
    rules:
      - description: Beta testers
        clauses: [{attribute: key, operator: in_segment, values: [beta], negate: false}]
        variation: "true"
      - description: Staff
        clauses: [{attribute: email, operator: ends_with, values: ["@example.com"], negate: false}]
        rollout:
          bucket_by: key
          weights: [{variation: "true", weight: 40000}, {variation: "false", weight: 60000}]
    fallthrough: {variation: "false"}
    prerequisites: [{flag: other, variation: "true"}]
```

The files use the API's field names and units. Rollout weights are in thousandths of a percent: 40000 is
40%, and the weights of one rollout add up to 100000. A rule or the fallthrough serves either one
`variation` or a `rollout`.

`export` writes the files in a fixed form, so that a Git diff shows only real changes: keys in a fixed
order, environments sorted, tags, `included`, `excluded` and `in_segment` values sorted, and every other
list in its own order, because the order of targets, rules, clauses and variations matters.

Loading is strict: an unknown field or a wrong type is an error that starts with the file and the line,
such as `flags/new-checkout.yaml:12:` for a misspelt field on line 12.

### What is not in the files

Versions, timestamps, the flag lifecycle state (except `permanent`), scheduled changes, running
progressive rollouts, SDK keys, integrations, members, roles and tokens. Manage those in the UI or the
API.

## Export, diff and apply

```bash
export MEGOOFLAGS_URL=https://flags.example.com MEGOOFLAGS_API_TOKEN=… MEGOOFLAGS_PROJECT=shop
megooflags export --dir flags          # exported 42 flags and 3 segments to flags
megooflags diff --dir flags            # the plan; exit 3 when there are differences
megooflags apply --dir flags --dry-run # the same plan, nothing written
megooflags apply --dir flags
```

- **`export`** writes what the token can see: flags (archived ones with `archived: true`), segments and
  the environments visible to the token. It deletes `*.yaml` files in `flags/` and `segments/` that no
  longer exist on the server.
- **`diff`** checks the files, compares them with the server by meaning, not by text (`1` and `1.0`, or
  YAML and JSON forms of the same value, are equal), and prints the plan. It exits 0 with
  `No differences.`, or 3 when there are changes. Each line is a change:

  ```
  ~ flag new-checkout: name, tags
  + variation theme/high-contrast
  ~ config new-checkout [production]: rules, fallthrough
  - variation theme/sepia
  ```

  `+` creates or restores, `~` updates, `-` archives or deletes.
- **`apply`** makes those changes. It creates the project when it does not exist, so the same directory
  can seed a new project.

`--env` (repeatable) limits all three commands to the environments you name. An environment that a flag's
file does not list is not managed for that flag: `apply` leaves its config alone. Environments that exist
only on the server are ignored, and `apply` never deletes an environment.

### Checks before the first write

`apply` validates the whole directory before it sends anything, and lists every problem:

- unknown fields and wrong types;
- environments used in flags but missing from `megooflags.yaml`;
- variations, prerequisite flags and segments that do not exist (segments may exist on the server only);
- rollout weights that do not add up to 100000;
- duplicate variations, an unknown `type`, or variation values that do not match the type.

### Apply order

1. The project and its environments.
2. Segments: new segments and details first, then their configs (included, excluded, rules).
3. Flags: new flags, details, `permanent`, archive and restore.
4. New and changed variations.
5. Per-environment configs. Each is saved with the server's current version; if someone else saved in
   between, `apply` reads it again and retries once.
6. Removed variations, after no config serves them any more.
7. With `--prune`: archiving and deleting what the files no longer have.

A flag's prerequisites are created before its config, so a new flag can depend on another new flag.
A change that fails does not stop the others: `apply` lists every failure and exits 1. Changes that
depend on a failed flag, such as its configs, are skipped and listed too. A flag's `type` cannot change:
delete and recreate the flag.

### Approvals

`apply` never bypasses approvals. When an environment requires approvals (`approvals_required` above 0),
its config changes are not made:

- without `--change-requests`, `apply` prints `needs approval: <change>` for each, makes every other
  change, and exits 4;
- with `--change-requests`, it opens one config change request per flag and environment, titled
  `Apply flags-as-code: <flag> in <env>`, prints `opened change request <id>` for each, and exits 0.
  The change happens when the request is approved in MegooFlags.

### Deleting things

`apply` never deletes by default: a flag or segment missing from the files is left on the server. With
`--prune`, `apply` archives live flags and deletes segments that are missing from the files (and `diff
--prune` shows that). Archived flags can be restored; deleting a flag stays a UI or `megooflags flag
delete` action.

## Git sync

The server never talks to Git. CI keeps Git and MegooFlags in step, and Git resolves conflicts through
pull requests:

- a change made in Git is reviewed in a pull request (where `diff` shows its plan) and applied on merge;
- a change made in the UI comes back as a pull request opened by the export job.

[`docs/examples/git-sync/`](examples/git-sync/) holds three GitHub Actions workflows to copy into
`.github/workflows/` of the repository that holds `flags/`:

| Workflow | Runs on | Does |
|---|---|---|
| `megooflags-diff.yml` | pull requests that change `flags/**` | `megooflags diff --dir flags`. The plan goes to the log and the job summary; differences (exit 3) fail the check, as a reminder of what merging will change |
| `megooflags-apply.yml` | pushes to `main` that change `flags/**` | `megooflags apply --dir flags --change-requests`, one run at a time. Changes for environments that require approvals become change requests, listed in the job summary |
| `megooflags-export.yml` | every 30 minutes, and by hand (`workflow_dispatch`) | `megooflags export --dir flags` on `main`. When `flags/` changed, it force-pushes one commit to the branch `megooflags/export` and opens its pull request, or updates the one already open |

Each workflow downloads the CLI release archive, checks it against `SHA256SUMS`, and uses the repository
variables `MEGOOFLAGS_URL` and `MEGOOFLAGS_PROJECT` and the secret `MEGOOFLAGS_API_TOKEN`. The export
workflow also needs "Allow GitHub Actions to create and approve pull requests" in the repository's
Actions settings. Pin `MEGOOFLAGS_CLI_VERSION` at the top of each file to the CLI release you use.

Give the token only what it needs: `project.read` for diff and export, and the flag, segment and
environment permissions for apply.

### Why export runs on a schedule

It would be nicer to export right after each UI change, but a MegooFlags webhook cannot start a GitHub
workflow by itself. GitHub's `repository_dispatch` and `workflow_dispatch` endpoints need an
`Authorization: Bearer <GitHub token>` header, and a MegooFlags webhook sends only its own signed JSON
body ([integrations.md](integrations.md)).

To export within seconds, run a small relay of your own, for example a serverless function:

1. Add a MegooFlags webhook for the project that points at the relay, with the Flags, Targeting and
   state, and Segments event groups.
2. The relay verifies `X-MegooFlags-Signature` with the webhook's signing secret and refuses anything
   that does not verify or is older than 5 minutes.
3. It then calls `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/megooflags-export.yml/dispatches`
   with the body `{"ref": "main"}`, `Accept: application/vnd.github+json` and a fine-grained GitHub token
   with Actions write access to that repository, which only the relay holds.

The export workflow's `concurrency` group makes runs queue rather than overlap, and an export that finds
nothing new does nothing, so a burst of events costs a few short runs. Keep the schedule as a fallback for
missed webhooks.

### Open change requests

When a Git change is merged for an environment that requires approvals, `apply` opens a change request,
and MegooFlags keeps the old value until it is approved. In the meantime the server differs from Git, so
the next export proposes a pull request that would undo the Git change. Do not merge that part: once the
change request is approved, the server matches Git and the next export drops it by itself. If the change
request is rejected, revert the Git change, or merge the export pull request to bring Git back to what
the server has. The export pull request's description repeats this caveat.
