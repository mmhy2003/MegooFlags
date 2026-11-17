---
title: Migrate from Unleash
description: Import an Unleash project with megooflags import, review the report, apply it, and switch your SDKs through OpenFeature.
---

This guide moves one Unleash project to MegooFlags: its environments, features, strategies, segments and
dependencies. The import writes [flags-as-code](/MegooFlags/use/flags-as-code/) files and a report; nothing
reaches MegooFlags until you apply them. The options shared by every importer are in
[Overview and importers](/MegooFlags/migrate/overview/).

## Concept map

| Unleash                                         | MegooFlags                                                                                               |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Project (`--source-project`, default `default`) | Project (`--project`)                                                                                    |
| Environment (the enabled ones)                  | Environment; the key is adjusted to MegooFlags key rules, or renamed with `--env-map`                    |
| Feature toggle                                  | Flag: boolean, or string (or JSON) when it has variants                                                  |
| Feature enabled in an environment               | The flag's on/off state in that environment                                                              |
| Feature type `kill-switch` or `permission`      | Permanent flag                                                                                           |
| Archived feature                                | Archived flag                                                                                            |
| Variants and their payloads                     | Variations; the payload is the value, else the variant name. A `disabled` variation (value `""`) is off  |
| Strategy, in sort order                         | One targeting rule                                                                                       |
| `default` strategy with no constraints          | The fallthrough                                                                                          |
| `flexibleRollout` (`rollout`, `stickiness`)     | A percentage rollout; stickiness `default` or `userId` buckets by `key`, another field by that attribute |
| `userWithId` (`userIds`)                        | Individual targets of `true`, or a rule `key in [...]` on a flag with variants or constraints            |
| Constraint                                      | Clause, with the operator of the same meaning (`NOT_IN` becomes a negated `in`)                          |
| Context field `userId`                          | `key`, the OpenFeature targeting key                                                                     |
| Other context fields and `properties`           | Attributes of the same name                                                                              |
| Segment                                         | Segment with one rule made of all its constraints; a strategy's segment becomes an `in_segment` clause   |
| Dependency on a parent feature                  | Prerequisite: the parent must serve `true`, or the first variant listed                                  |

## Steps

1. Install the [CLI](/MegooFlags/use/cli/#install) and log in to MegooFlags.
2. Create an Unleash **admin** API token and import. Unleash has no cloud default, so `--url` is required
   with the API:

   ```bash
   export UNLEASH_API_TOKEN=…
   megooflags import unleash --url https://unleash.example.com --source-project default \
     --project shop --out flags --save unleash.json
   # 42 flags, 3 segments; 7 items in import-report.md
   ```

   The command form is `megooflags import unleash (--url U | --file F) --project P --out DIR`. `--save`
   keeps what was read, so you can import again from the file with other options and no token:

   ```bash
   megooflags import unleash --file unleash.json --project shop --out flags --env-map development=dev
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

- Case-insensitive constraints: imported case-sensitive.
- IP ranges: `IN_CIDR` constraints and the `remoteAddress` strategy. A strategy with such a constraint is
  skipped, since dropping the constraint would widen who matches.
- `SEMVER_GTE` and `SEMVER_LTE`, and any other unsupported operator: the strategy is skipped.
- `random` stickiness: the rollout buckets by `key`.
- Custom and other strategies, and disabled strategies: skipped.
- Variant overrides, and payloads other than string and JSON.
- Dependencies on a disabled parent or on several variants.
- Constraints on `currentTime`: Unleash SDKs set it, MegooFlags SDKs do not, so your app must send it.
- A segment with no constraints matched everyone in Unleash; here it matches no one.
- A `flexibleRollout` under 100 % with constraints: contexts that match the constraints but fall outside
  the percentage get the off variation instead of trying later strategies.
- Keys changed to fit MegooFlags key rules, for example `key "NewCheckout" was renamed to "newcheckout"`.
  Update the flag keys in your code to match.

Percentage rollouts are re-bucketed: MegooFlags hashes contexts differently, so a user in the 20 % of a
rollout in Unleash may not be in the 20 % here. Finish a running rollout in Unleash first, or restart it
knowing the groups change.

## Switch your SDKs through OpenFeature

MegooFlags SDKs are [OpenFeature](https://openfeature.dev) providers. Move each service's flag calls to
the OpenFeature API once; after that, switching tools is a change of provider. Before, with the Unleash
Node.js SDK:

```js
import { startUnleash } from "unleash-client";

const unleash = await startUnleash({
  url: "https://unleash.example.com/api/",
  appName: "checkout",
  customHeaders: { Authorization: process.env.UNLEASH_CLIENT_TOKEN },
});

const enabled = unleash.isEnabled("new-checkout", {
  userId: "user-123",
  properties: { plan: "pro" },
});
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
const enabled = await client.getBooleanValue("new-checkout", false, {
  targetingKey: "user-123", // was userId
  plan: "pro", // was properties.plan
});
```

A feature with variants is a string flag: read it with `client.getStringValue(key, "disabled", context)`.
Use the flag keys from the report where they were renamed.

### Gradual rollout

Switch one service at a time and keep Unleash running until the last one has moved:

1. Freeze flag changes in Unleash during the rollout window, or make each change in both tools. To pick up
   changes made in Unleash meanwhile, import again and review `megooflags diff` before `apply`.
2. Choose the tool by configuration, so a service can go back without a release. Put the switch in one
   function that the rest of the code calls:

   ```js
   const useMegooFlags = process.env.FLAGS_PROVIDER === "megooflags";

   export async function isEnabled(key, userId, attributes = {}) {
     if (useMegooFlags) {
       return OpenFeature.getClient().getBooleanValue(key, false, {
         targetingKey: userId,
         ...attributes,
       });
     }
     return unleash.isEnabled(key, { userId, properties: attributes });
   }
   ```

   Start only the client the service uses. If your code already calls OpenFeature through an Unleash
   provider, choose between the two providers in `setProviderAndWait` instead.

3. Turn on `megooflags` for one service in staging, then production; compare its behaviour and the
   evaluation counts on the flag pages; then the next service.
4. When no service reads Unleash any more, remove the old provider and the variable, and retire the Unleash
   project.
