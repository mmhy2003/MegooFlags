---
title: Migrate from LaunchDarkly
description: Import a LaunchDarkly project with megooflags import, review the report, apply it, and switch your SDKs through OpenFeature.
---

This guide moves one LaunchDarkly project to MegooFlags: its environments, flags and segments. The two
models are close, so most of a project translates one to one. The import writes
[flags-as-code](/MegooFlags/use/flags-as-code/) files and a report; nothing reaches MegooFlags until you
apply them. The options shared by every importer are in [Overview and importers](/MegooFlags/migrate/overview/).

## Concept map

| LaunchDarkly                                          | MegooFlags                                                                                                |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Project (`--source-project`, default the `--project`) | Project (`--project`)                                                                                     |
| Environment                                           | Environment; the key is adjusted to MegooFlags key rules, or renamed with `--env-map`                     |
| Flag                                                  | Flag: boolean for boolean flags, else string or number when every value is one, else JSON                 |
| Variations                                            | Variations, with their names, descriptions and values                                                     |
| Tags, maintainer, temporary                           | Tags, owner (the maintainer's email), permanent when the flag is not temporary                            |
| Targeting on or off                                   | The flag's on/off state in that environment                                                               |
| Off variation (per environment)                       | Off variation (one per flag: the first environment's)                                                     |
| Individual targets and user context targets           | Individual targets, one per variation                                                                     |
| Rules and clauses                                     | Rules and clauses, with the operator of the same meaning, for example `greaterThanOrEqual` to `gte`       |
| `segmentMatch` clause                                 | `in_segment` clause                                                                                       |
| Percentage rollout                                    | Percentage rollout, with the same weight unit (thousandths of a percent)                                  |
| Default rule (fallthrough)                            | Fallthrough                                                                                               |
| Prerequisites                                         | Prerequisites                                                                                             |
| Segment (one per environment)                         | One segment when it is the same in every environment, otherwise one segment `<key>-<env>` per environment |
| User context key and attributes                       | `key`, the OpenFeature targeting key, and attributes of the same name                                     |

## Steps

1. Install the [CLI](/MegooFlags/use/cli/#install) and log in to MegooFlags.
2. Create a LaunchDarkly **access token** with read access and import. `--url` defaults to
   `https://app.launchdarkly.com`; pass it for another LaunchDarkly host:

   ```bash
   export LAUNCHDARKLY_API_TOKEN=api-…
   megooflags import launchdarkly --source-project default --project shop --out flags --save launchdarkly.json
   # 57 flags, 6 segments; 9 items in import-report.md
   ```

   The command form is `megooflags import launchdarkly (--url U | --file F) --project P --out DIR`.
   Without `--source-project`, the LaunchDarkly project key is the `--project` value. `--save` keeps what
   was read, so you can import again from the file with other options and no token:

   ```bash
   megooflags import launchdarkly --file launchdarkly.json --project shop --out flags --env-map test=staging
   ```

   Other flags: `--token-env VAR` reads the token from another variable, `--env-map source=target`
   (repeatable) renames an environment, and `--strict` exits 1 when the report has any line.

3. Read `flags/import-report.md`. It starts with the re-bucketing notice and lists, one line each, every
   item that was skipped or changed. Fix the files by hand where you need to.
4. Compare with MegooFlags, then apply:

   ```bash
   megooflags diff --dir flags -p shop
   megooflags apply --dir flags -p shop
   ```

   `apply` creates the project when it does not exist. Environments that require approvals stop at
   `needs approval` unless you pass `--change-requests`.

5. Check a few flags with `megooflags evaluate` for contexts you know.

## Reported, not translated

These items are a line in `import-report.md` instead of a translation:

- Context kinds other than user: those targets and segment members are not imported, and rules whose
  clauses or rollout use them are skipped. A fallthrough rollout by another kind is kept and buckets user
  contexts.
- Experiments: the rollout is kept, the experiment is not.
- Big (unbounded) segments: skipped, with the rules that use them.
- Migration flags: skipped, with the prerequisites on them.
- An off variation that differs between environments: the flag uses the first environment's.
- Rules with no clauses, and prerequisites on flags or variations that do not exist: skipped.
- Keys changed to fit MegooFlags key rules, for example `key "NewCheckout" was renamed to "newcheckout"`.
  Update the flag keys in your code to match.

Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently from LaunchDarkly, so a user in
the 20 % of a rollout in LaunchDarkly may not be in the 20 % here. Finish a running rollout in LaunchDarkly
first, or restart it knowing the groups change.

## Switch your SDKs through OpenFeature

MegooFlags SDKs are [OpenFeature](https://openfeature.dev) providers, and so is LaunchDarkly's. When your
code already evaluates through OpenFeature, keep every call and replace the provider. Before, with the
LaunchDarkly provider:

```js
import { OpenFeature } from "@openfeature/server-sdk";
import { LaunchDarklyProvider } from "@launchdarkly/openfeature-node-server";

await OpenFeature.setProviderAndWait(
  new LaunchDarklyProvider(process.env.LAUNCHDARKLY_SDK_KEY),
);

const client = OpenFeature.getClient();
const enabled = await client.getBooleanValue("new-checkout", false, {
  targetingKey: "user-123",
  plan: "pro",
});
```

After, with the MegooFlags provider (`pnpm add @megooflags/node`); the evaluation code does not change:

```js
import { OpenFeature } from "@openfeature/server-sdk";
import { MegooFlagsProvider } from "@megooflags/node";

await OpenFeature.setProviderAndWait(
  new MegooFlagsProvider({
    sdkKey: process.env.MEGOOFLAGS_SDK_KEY, // a server key, mf_server_…
    baseUrl: "https://flags.example.com",
  }),
);

const client = OpenFeature.getClient();
const enabled = await client.getBooleanValue("new-checkout", false, {
  targetingKey: "user-123",
  plan: "pro",
});
```

If your code calls the LaunchDarkly SDK directly, move it to OpenFeature with the LaunchDarkly provider
first, release that, then switch the provider. Use the flag keys from the report where they were renamed.

### Gradual rollout

Switch one service at a time and keep LaunchDarkly running until the last one has moved:

1. Freeze flag changes in LaunchDarkly during the rollout window, or make each change in both tools. To
   pick up changes made in LaunchDarkly meanwhile, import again and review `megooflags diff` before `apply`.
2. Choose the provider by configuration, so a service can go back without a release:

   ```js
   const provider =
     process.env.FLAGS_PROVIDER === "megooflags"
       ? new MegooFlagsProvider({
           sdkKey: process.env.MEGOOFLAGS_SDK_KEY,
           baseUrl: "https://flags.example.com",
         })
       : new LaunchDarklyProvider(process.env.LAUNCHDARKLY_SDK_KEY);
   await OpenFeature.setProviderAndWait(provider);
   ```

3. Turn on `megooflags` for one service in staging, then production; compare its behaviour and the
   evaluation counts on the flag pages; then the next service.
4. When no service reads LaunchDarkly any more, remove the old provider and the variable, and retire the
   LaunchDarkly project.
