---
title: Introduction
description: What MegooFlags is, what comes in the box, and where to go next.
---

MegooFlags is an open-source feature flag platform you host yourself. You turn features on and off at
runtime, target them at users and segments, roll them out by percentage, and protect production with
change requests and approvals. Your applications evaluate flags through
[OpenFeature](https://openfeature.dev) SDKs, and your flags and your users' data stay on your own
servers. It is licensed under Apache-2.0.

## What is in the box

- **A single-node bundle.** One Docker Compose file starts Caddy (automatic HTTPS, or plain HTTP
  behind your own proxy), the web UI, the API, PostgreSQL and Valkey. Database migrations run before
  the API starts. The prebuilt, versioned images are on GHCR.
- **Multi-node.** For more SDK capacity, or to keep serving flags while the management side is down,
  run a control plane and any number of worker nodes from the same images. See
  [Multi-node](/MegooFlags/deploy/multi-node/).
- **Thirteen OpenFeature SDKs.** Server SDKs for [Go](/MegooFlags/sdks/go/),
  [Node.js](/MegooFlags/sdks/node/), [Python](/MegooFlags/sdks/python/),
  [Java](/MegooFlags/sdks/java/), [.NET](/MegooFlags/sdks/dotnet/), [Ruby](/MegooFlags/sdks/ruby/)
  and [PHP](/MegooFlags/sdks/php/) evaluate locally, with no network call per evaluation. Client SDKs
  for [JavaScript](/MegooFlags/sdks/js/), [React](/MegooFlags/sdks/react/),
  [React Native](/MegooFlags/sdks/react-native/), [iOS](/MegooFlags/sdks/ios/),
  [Android](/MegooFlags/sdks/android/) and [Flutter](/MegooFlags/sdks/flutter/) get evaluated
  results. Any OFREP provider works too.
- **Release workflows.** Targeting rules, segments, percentage and progressive rollouts,
  prerequisites, scheduled changes, change requests with approvals, a kill switch and an audit log.
  See [Release workflows](/MegooFlags/use/release-workflows/).
- **Flags as code and automation.** The [CLI](/MegooFlags/use/cli/), the
  [Terraform provider](/MegooFlags/use/terraform/), [flags as code](/MegooFlags/use/flags-as-code/),
  [integrations](/MegooFlags/use/integrations/) (webhooks, Slack and Teams) and a REST API, with
  its reference under Reference in the sidebar.
- **Importers** from Unleash, Flagsmith and LaunchDarkly. See
  [Migrate](/MegooFlags/migrate/overview/).

MegooFlags collects no usage data: no telemetry, no instance ID and no pings. The only outgoing
request is an optional daily update check, described on the [Privacy](/MegooFlags/project/privacy/)
page with how to turn it off.

## Next steps

- [Quick start](/MegooFlags/start/quick-start/): run MegooFlags on your machine and evaluate a flag
  in under five minutes.
- [Concepts](/MegooFlags/start/concepts/): projects, environments, flags, targeting and keys, one
  paragraph each.
- [Security](/MegooFlags/deploy/security/): what an operator needs to know before going to
  production.
