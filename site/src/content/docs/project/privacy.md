---
title: Privacy
description: MegooFlags collects no usage data. What the update check sends, and what your server stores.
---

MegooFlags collects no usage data. There is no telemetry, no instance ID and no ping: the project
does not know how many instances run, or who runs them. Everything MegooFlags stores stays in your
own PostgreSQL database.

## The update check

The only request MegooFlags makes on its own is a check for a newer release, so that organisation
admins see an update notice next to the version in the sidebar.

- **What it requests:** `GET https://api.github.com/repos/mmhy2003/MegooFlags/releases/latest`, the
  public GitHub Releases API.
- **What it sends:** only the headers `User-Agent: MegooFlags/<running version>` and
  `Accept: application/vnd.github+json`. No cookie, token, instance ID, hostname, user, flag or usage
  figure is sent.
- **What GitHub sees:** the request itself, the public IP address it comes from, the time and the
  running version in the `User-Agent`. GitHub's own
  [privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement)
  applies to that.
- **How often:** at most once every 24 hours, and again after one hour if the last attempt failed. One
  API replica (the scheduler's leader) makes the request, and the times are kept in the database, so
  more replicas or a restart do not add requests. Worker nodes never make it.
- **What is kept:** the newest version number. The answer is accepted only when the release tag looks
  like `vX.Y.Z`, and the link in the notice is built from that version, never taken from the
  response. Only users who can manage the organisation see the notice.

To turn it off, set this in `.env` and run `docker compose up -d`:

```bash
MEGOOFLAGS_UPDATE_CHECK=false
```

With it off, no request is made, and no notice is shown, even if a newer version was stored before.
The running version is still shown.

## Evaluation context

The evaluation context is the user key and attributes you pass to an SDK. Client SDKs and OFREP
providers send it to the API to evaluate remotely; server SDKs evaluate locally and send it only in
exposure events, and only when you turn that on.

- **Evaluation** uses the context in memory only. It is never written to the database or the logs.
- **Evaluation counts** are totals per environment, flag, variation and hour. They never contain a
  context.
- **Exposure events** record the flag, variation, reason and time. The context is dropped unless you
  set `MEGOOFLAGS_PERSIST_CONTEXT=true` (default `false`), since it may contain personal data. The
  server SDKs send no context with exposures unless their `sendContext` option is on.
- **Retention:** exposure events are deleted after `MEGOOFLAGS_EXPOSURE_RETENTION_DAYS` days (default
  30).

## Other data in your database

- **Users:** email, name and an argon2id hash of the password. API tokens, SDK keys, session tokens,
  invitation links and recovery codes are stored only as SHA-256 hashes.
- **Sessions** record the IP address and user agent of the sign-in, and end after 12 hours idle or 7
  days in total.
- **Audit log:** every change and security event, with the actor, IP address, user agent and the
  before and after values. Entries are never changed or deleted by MegooFlags; keep or prune them as
  your own policy requires.
- **Integration deliveries** (webhook, Slack and Teams) are kept for
  `MEGOOFLAGS_WEBHOOK_RETENTION_DAYS` days (default 7).
- **Logs** are JSON lines on standard output, including each request's client IP address. Their
  retention is up to your log collector.
