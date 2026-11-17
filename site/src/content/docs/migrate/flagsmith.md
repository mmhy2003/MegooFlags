---
title: Migrate from Flagsmith
description: Import a Flagsmith project with megooflags import, review the report, apply it, and switch your SDKs through OpenFeature.
---

This guide moves one Flagsmith project to MegooFlags: its environments, features, multivariate options,
segments and segment overrides. The import writes [flags-as-code](/MegooFlags/use/flags-as-code/) files and a
report; nothing reaches MegooFlags until you apply them. The options shared by every importer are in
[Overview and importers](/MegooFlags/migrate/overview/).

## Concept map

| Flagsmith                                     | MegooFlags                                                                                                                     |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Project (`--source-project`, its id)          | Project (`--project`)                                                                                                          |
| Environment                                   | Environment; the key is adjusted to MegooFlags key rules, or renamed with `--env-map`                                          |
| Feature without a value                       | Boolean flag with variations `true` and `false`                                                                                |
| Feature with a value (remote config)          | Flag typed by its values (string, number or boolean; string when they differ, with a report line)                              |
| The value in each environment (control value) | Variation `default`, the off variation; an environment with another value gets `default-<env>`                                 |
| Multivariate options and their percentages    | Variations `option-1`, `option-2`, …, and a fallthrough rollout over the percentages, the rest to the control value            |
| Feature enabled in an environment             | The flag's on/off state in that environment                                                                                    |
| Segment                                       | Segment: rules ALL become clauses of one rule, ANY several rules, NONE negated clauses                                         |
| Segment override, in priority order           | A rule with an `in_segment` clause that serves the override's value (`false` when it turns a feature without a value off)      |
| Trait                                         | Context attribute of the same name                                                                                             |
| Identity `identifier`                         | `key`, the OpenFeature targeting key                                                                                           |
| Condition operator                            | The operator of the same meaning, for example `GREATER_THAN_INCLUSIVE` to `gte`; `NOT_EQUAL` and `NOT_CONTAINS` become negated |

When a feature is off in an environment but a segment override turns it on, the flag is imported on in
that environment, and its fallthrough serves the off variation, so only the override applies.

## Steps

1. Install the [CLI](/MegooFlags/use/cli/#install) and log in to MegooFlags.
2. Create a Flagsmith **organisation API key**, find the project id in the Flagsmith project settings, and
   import. `--url` defaults to `https://api.flagsmith.com`; pass it for a self-hosted Flagsmith:

   ```bash
   export FLAGSMITH_API_KEY=…
   megooflags import flagsmith --source-project 1234 --project shop --out flags --save flagsmith.json
   # 18 flags, 4 segments; 5 items in import-report.md
   ```

   The command form is `megooflags import flagsmith (--url U | --file F) --project P --out DIR`. With the
   API, `--source-project` is required. `--save` keeps what was read (each environment's document), so you
   can import again from the file with other options and no key:

   ```bash
   megooflags import flagsmith --file flagsmith.json --project shop --out flags --env-map Production=production
   ```

   Other flags: `--token-env VAR` reads the key from another variable, `--env-map source=target`
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

- Identity overrides: one line per identity. Recreate the ones you need as individual targets.
- The `PERCENTAGE_SPLIT` operator, and segment overrides that split between options: the override serves
  its value.
- A segment override that turns a feature with a value off: a rule cannot turn a flag off, so it serves
  the override's value.
- `IS_SET` and `IS_NOT_SET`, `MODULO`, and any other unsupported operator: the segment rule is skipped.
- Segment rules nested more than two levels, or that expand to more than 20 rules.
- Feature values of different types: the flag is a string flag.
- Keys changed to fit MegooFlags key rules, for example `key "NewCheckout" was renamed to "newcheckout"`.
  Update the flag keys in your code to match.

Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently, so a user in the 20 % of a
multivariate split in Flagsmith may not be in the 20 % here. Finish a running split in Flagsmith first, or
restart it knowing the groups change.

## Switch your SDKs through OpenFeature

MegooFlags SDKs are [OpenFeature](https://openfeature.dev) providers. Move each service's flag calls to
the OpenFeature API once; after that, switching tools is a change of provider. Before, with the Flagsmith
Node.js SDK:

```js
import { Flagsmith } from "@flagsmith/nodejs";

const flagsmith = new Flagsmith({
  environmentKey: process.env.FLAGSMITH_ENVIRONMENT_KEY,
});

const flags = await flagsmith.getIdentityFlags("user-123", { plan: "pro" });
const enabled = flags.isFeatureEnabled("new_checkout");
const color = flags.getFeatureValue("button_color");
```

After, with the MegooFlags provider (`pnpm add @megooflags/node @openfeature/server-sdk`):

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
const context = { targetingKey: "user-123", plan: "pro" }; // the identifier and the traits
const enabled = await client.getBooleanValue("new_checkout", false, context);
const color = await client.getStringValue("button_color", "blue", context);
```

A feature with a value is one typed flag: read its value with `getStringValue`, `getNumberValue` or
`getBooleanValue`. When it is off, it serves its off variation, `default`. Use the flag keys from the
report where they were renamed.

### Gradual rollout

Switch one service at a time and keep Flagsmith running until the last one has moved:

1. Freeze flag changes in Flagsmith during the rollout window, or make each change in both tools. To pick
   up changes made in Flagsmith meanwhile, import again and review `megooflags diff` before `apply`.
2. Choose the tool by configuration, so a service can go back without a release. Put the switch in one
   function that the rest of the code calls:

   ```js
   const useMegooFlags = process.env.FLAGS_PROVIDER === "megooflags";

   export async function isEnabled(key, userId, traits = {}) {
     if (useMegooFlags) {
       return OpenFeature.getClient().getBooleanValue(key, false, {
         targetingKey: userId,
         ...traits,
       });
     }
     return (await flagsmith.getIdentityFlags(userId, traits)).isFeatureEnabled(
       key,
     );
   }
   ```

   Start only the client the service uses. If your code already calls OpenFeature through a Flagsmith
   provider, choose between the two providers in `setProviderAndWait` instead.

3. Turn on `megooflags` for one service in staging, then production; compare its behaviour and the
   evaluation counts on the flag pages; then the next service.
4. When no service reads Flagsmith any more, remove the old client and the variable, and retire the
   Flagsmith project.
