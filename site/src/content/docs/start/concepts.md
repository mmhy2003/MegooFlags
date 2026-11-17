---
title: Concepts
description: Projects, environments, flags, targeting, keys, evaluation and roles, one paragraph each.
---

## Projects

A project groups the flags, segments, environments and integrations of one product or service. It has
a unique key, such as `shop`, that appears in API paths and SDK configuration. A project also decides
when a flag counts as stale: when no environment has evaluated it for `stale_after_days` days (30 by
default).

## Environments

Each project has its own environments. A new project gets Development, Staging and Production unless
you list others. Every flag exists in every environment of its project, but its state (on or off,
targeting, rollout) is kept per environment, so turning a flag on in Staging does not touch
Production. SDK keys belong to one environment.

## Flags and variations

A flag has a key, a type (boolean, string, number or JSON) and a list of variations, the values it can
serve. A boolean flag always has the variations `true` and `false`; the others have the variations you
define. The off variation is served wherever the flag is turned off. Flags carry tags and an owner, move
through the lifecycle states new, active, permanent and stale, and are archived before they are
deleted. An archived flag is no longer served.

## Targeting rules

When a flag is on in an environment, it is evaluated in a fixed order: prerequisites, then individual
targets (context keys that get a given variation), then rules, then the fallthrough. A rule is a list
of clauses that must all match, each comparing a context attribute with an operator such as `in`,
`contains`, `matches`, `gt`, `semver_lt`, `before` or `in_segment`. The first matching rule decides the
variation. The [evaluation spec](/MegooFlags/reference/evaluation/) defines every step.

## Segments

A segment is a reusable group of contexts in a project, for example "Beta testers". It has an included
list and an excluded list of context keys, and rules built from the same clauses as flag rules. A flag
rule targets it with the `in_segment` operator, so changing the segment changes every flag that uses
it.

## Rollouts

Any rule, or the fallthrough, can serve a percentage rollout instead of a fixed variation: weights per
variation that add up to 100%. Each context is put in a bucket by hashing the flag key with its
`key` attribute (or another attribute you choose), so the same user always gets the same variation. A
progressive rollout moves traffic from one variation to another in timed steps; scheduled changes turn
a flag on or off, or change its rollout, at a set time. See
[Release workflows](/MegooFlags/use/release-workflows/).

## Prerequisites

A flag can depend on other flags: each prerequisite names a flag and the variation it must serve. When
any prerequisite is off or serves another variation, the flag serves its off variation with the reason
`PREREQUISITE_FAILED`. Cycles are refused when you save.

## SDK keys

SDKs authenticate with a key that belongs to one environment. A **server key** (`mf_server_…`) is a
secret for your backend services: it can download the environment's full ruleset. A **client key**
(`mf_client_…`) is meant to be shipped in browser and mobile apps: it only gets evaluated results for
one context, never the rules, and it is rate-limited per key and client IP. Keys are stored as hashes,
can be rotated with an overlap and revoked. Management tools (the CLI, Terraform, scripts) use API
tokens instead.

## Local and remote evaluation

The server SDKs evaluate **locally**: they download the ruleset once, evaluate every flag in-process
with no network call, and keep serving the last known ruleset when the server is unreachable. The
client SDKs, and any OFREP provider, evaluate **remotely**: they send the context to the API and get
the results back. Both follow the same [evaluation spec](/MegooFlags/reference/evaluation/), checked by
a shared conformance suite.

## Streaming

SDKs keep a server-sent events stream open, so a flag change reaches them within seconds without
polling. Server SDKs receive the new ruleset on the stream itself; client SDKs get a change notice
and fetch new results. If the stream breaks, they fall back to polling until it reconnects. The [delivery protocol](/MegooFlags/reference/delivery/)
has the details, including the heartbeat that keeps proxies from closing idle streams.

## Change requests and approvals

An environment can require approvals: set the number of approvals (up to 10) and it becomes
protected. Changes to a flag's targeting, state, schedules and rollouts there are then proposed as
change requests, which reviewers with the approve permission accept or reject. The change is applied
once enough approvals are in. The kill switch always works directly, so an incident never waits for a
review.

## Roles

Access is granted by binding a role to a user, a team or a service token at the scope of the whole
organisation, one project or one environment. The built-in roles are admin (everything), editor
(everything except organisation settings, members, roles, tokens and the audit log) and viewer (read
flags, evaluate, read SDK keys and the audit log). Admins can define custom roles from the same
permissions. Projects and environments you can't see are hidden. Every change, and every sign-in, is
written to the audit log.
