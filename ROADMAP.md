# Roadmap

MegooFlags stays on `0.Y.Z` releases until `v1.0.0`, the GA release. This roadmap follows the release
plan of the product requirements; the dates depend on the GA checklist, not on a calendar.

## Done for GA (`v1.0.0`)

- Flags: boolean, string, number and JSON flags with variations, per-environment state, projects, the
  kill switch, lifecycle states and stale-flag detection, prerequisites, copying between environments
  and scheduled changes.
- Targeting: evaluation context, individual targeting, rules with string, regex, numeric, semver and date
  operators, reusable segments, sticky percentage rollouts and progressive rollouts.
- SDKs: server SDKs for Go, Node.js, Python, Java, .NET, Ruby and PHP, and client SDKs for JavaScript,
  React, React Native, iOS, Android and Flutter. Every SDK is an OpenFeature provider, passes the shared
  conformance suite and gets updates over server-sent events with a polling fallback.
- An OFREP endpoint for remote evaluation.
- Governance: users, teams and custom roles at org, project and environment level, SSO with OIDC and
  SAML 2.0, the audit log, change requests with approvals, scoped API tokens and SDK key rotation, all
  in the open source edition.
- Developer tools: a REST API with an OpenAPI spec, the `megooflags` CLI, flags as code, the Terraform
  provider, webhooks with Slack and Microsoft Teams notifications, and importers from Unleash, Flagsmith
  and LaunchDarkly.
- Deployment: the single-node Docker Compose bundle with automatic HTTPS or plain HTTP, and multi-node
  deployments (one control plane and any number of workers) from the same images, replacing a separate
  relay, and a Helm chart for Kubernetes.
- Update notices in the UI.

## Phase 2 (`1.Y.Z` releases)

- Experimentation: experiments on multivariate flags, warehouse-native analysis and statistical results.
- Guarded rollouts that roll back when a linked metric or error rate crosses a threshold.
- Code references: find where each flag is used, to help clean up old flags.
- Observability integrations (Datadog, Grafana, Sentry and analytics tools).
- Control-plane redundancy: a standby control plane with database replication and failover.

## Not planned

Telemetry is not on the roadmap: MegooFlags collects no usage data. The only outbound request is the
daily update check, which you can turn off with `MEGOOFLAGS_UPDATE_CHECK=false`.

## How to propose items

- For a small change (a bug, a missing option, a docs gap), open an issue.
- For a big change (a new public API, an SDK protocol change, a breaking change, a new deployment
  topology or a large UI flow), write an RFC as described in [rfcs/README.md](rfcs/README.md).
