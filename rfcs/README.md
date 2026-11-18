# RFCs

An RFC (request for comments) is a short design document for a major change. It lets everyone agree on
the design before the work starts, and keeps a record of why the project went one way and not another.

## When an RFC is needed

Write an RFC for:

- a new public API, or a change to an existing one,
- a change to the SDK protocol or the evaluation spec,
- a breaking change for users, deployments or SDKs,
- a new deployment topology,
- a large new UI flow.

Bug fixes, small features, docs and refactors need no RFC: open an issue or a pull request.

## Process

1. Copy [`0000-template.md`](0000-template.md) to `rfcs/0000-short-title.md` and fill it in. Set its
   Status to `Proposed`.
2. Open a pull request into `develop` titled `RFC: <short title>`.
3. Discussion happens on the pull request, for at least 7 days. Update the RFC as the discussion goes on.
4. A maintainer decides: accepted or declined. The decision, with a short reason, is recorded in the
   file's Status.
5. On merge, the maintainer gives the RFC the next free number (the pull request number is fine) and
   renames the file, for example `rfcs/0042-short-title.md`, then adds it to the index below.

Declined RFCs are merged too, so the reasoning stays on record. An accepted RFC is a design, not a
promise of a date: the work is planned on the [roadmap](../ROADMAP.md).

## Index

| Number | Title | Status |
|---|---|---|
