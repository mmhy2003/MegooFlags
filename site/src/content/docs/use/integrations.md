---
title: Webhooks, Slack and Microsoft Teams
description: Notify other systems when flags, targeting, segments and change requests change.
---

MegooFlags can tell other systems when flags, targeting, segments and change requests change. Each
project has its own integrations: webhooks (a signed JSON request to your URL), Slack and Microsoft Teams
(a one-sentence message with a link).

## Adding an integration

Open the project's **Integrations** page and choose **Add integration**. You need the `project.manage`
permission, which the built-in Admin role has.

| Type            | URL                                                                                                                                                       |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Webhook         | Any `https` URL that accepts a `POST` with a JSON body                                                                                                    |
| Slack           | An [incoming webhook](https://api.slack.com/messaging/webhooks) URL, on `hooks.slack.com`                                                                 |
| Microsoft Teams | The URL of a Teams workflow started by "When a Teams webhook request is received" (Workflows app, "Post to a channel when a webhook request is received") |

Then pick the event groups, and either all environments or the ones you want.

- A webhook's **signing secret** (`mf_whsec_…`) is shown once, right after you create the webhook. Copy it
  then. To replace it, call `POST /api/projects/{project}/integrations/{id}/rotate-secret`. Rotation is in the API only;
  the Integrations page has no rotate action.
- The URL is stored encrypted and never shown again; the list shows a masked hint such as
  `https://hooks.example.com/…ook1`. To change it, enter a new URL when you edit the integration.
- **Send test** sends a `ping` event now and shows the result.
- **Deliveries** lists recent deliveries with their request body, the response status and body, and the
  duration. **Send again** queues a failed delivery once more, with the same event `id`.
- **Pause** stops deliveries until you resume: events are not queued while paused, and deliveries still
  waiting to be sent or retried end failed with "the integration is paused" (use **Send again** after
  you resume). **Send test** still sends. An integration whose last delivery failed shows
  **Failing**; it keeps retrying each event (see [Retries](#retries)).

The same actions are in the API under `/api/projects/{project}/integrations` (see `api/openapi.json`):
`GET` and `POST` on the list, `GET`, `PATCH` and `DELETE` on `/{id}`, and `POST` on `/{id}/rotate-secret`,
`/{id}/test`, `/{id}/deliveries/{delivery}/redeliver`, plus `GET /{id}/deliveries`. Every change to an
integration is in the audit log; the URL and the secret never are.

## Events

The event `type` is the audit log action. Each integration listens to one or more groups:

| Group               | Event types                                                                                                                                                                                |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Flags               | `flag.create`, `flag.update`, `flag.archive`, `flag.restore`, `flag.delete`, `flag.variation.*` (create, update, delete), `flag.lifecycle.update`                                          |
| Targeting and state | `flag.config.update`, `flag.toggle`, `flag.kill`, `flag.copy`, `flag.schedule.*` (create, run, cancel, fail), `flag.rollout.*` (start, step, pause, resume, complete, abort, fail, cancel) |
| Segments            | `segment.create`, `segment.update`, `segment.config.update`, `segment.delete`                                                                                                              |
| Change requests     | `change_request.create`, `change_request.approve`, `change_request.reject`, `change_request.apply`, `change_request.fail`, `change_request.cancel`                                         |

Comments on change requests and changes to integrations, SDK keys, members and settings send nothing.

An event with an environment, such as `flag.toggle` in `production`, goes only to integrations that
include that environment. An event without one, such as `flag.create`, goes to every integration of the
project that listens to its group. A progressive rollout step sends two events, `flag.rollout.step` and
`flag.config.update`, as the audit log records two entries.

## Webhook requests

MegooFlags sends `POST <your URL>` with this JSON body:

```json
{
  "id": "evt_81234",
  "type": "flag.toggle",
  "occurred_at": "2026-11-15T14:02:10Z",
  "project": "shop",
  "environment": "production",
  "resource": { "type": "flag", "key": "new-checkout" },
  "actor": { "kind": "user", "label": "mona@example.com" },
  "before": { "enabled": false },
  "after": { "enabled": true },
  "url": "https://flags.example.com/projects/shop/flags/new-checkout?env=production"
}
```

| Field                    | Notes                                                                                                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `id`                     | Unique per event; the same in every delivery and redelivery of it. Test events use `evt_ping_<hex>`                                                          |
| `type`                   | The audit action (see [Events](#events)), or `ping` for a test                                                                                               |
| `occurred_at`            | When the change happened, ISO 8601 in UTC                                                                                                                    |
| `project`, `environment` | Keys; `environment` is null for events without one                                                                                                           |
| `resource`               | `type` (`flag`, `segment`, `change_request`, or `integration` for a ping) and `key`                                                                          |
| `actor`                  | Who made the change: `kind` (`user`, `token`, `system`…) and `label`                                                                                         |
| `before`, `after`        | The audit log snapshots, with secrets already removed; null where there is none                                                                              |
| `url`                    | A link to the flag (with `?env=`), the flag list after a delete, the segments page or the change requests page. Null when `MEGOOFLAGS_PUBLIC_URL` is not set |

Headers:

| Header                   | Value                                               |
| ------------------------ | --------------------------------------------------- |
| `Content-Type`           | `application/json`                                  |
| `User-Agent`             | `MegooFlags-Webhook/<server version>`               |
| `X-MegooFlags-Event`     | The event `type`                                    |
| `X-MegooFlags-Delivery`  | The delivery id (a new one for each redelivery)     |
| `X-MegooFlags-Signature` | `t=<unix time>,v1=<hex HMAC-SHA256>`, webhooks only |

Slack and Teams receive the same event as a message instead: Slack gets `{text, blocks}`, Teams an
Adaptive Card. Both say who did what to which resource, where, such as "mona@example.com turned on flag
new-checkout in production (shop)", with an "Open in MegooFlags" link when `url` is set. They are not
signed; Slack and Teams URLs are secrets in themselves.

## Verifying the signature

`v1` is the HMAC-SHA256, keyed with the signing secret, of the timestamp `t`, a dot, and the raw request
body: `"<t>.<body>"`. To verify a request:

1. Read the raw body bytes before parsing the JSON; a parsed and re-encoded body does not match.
2. Split the header into `t` and `v1`.
3. Compute the HMAC of `t + "." + body` and compare it with `v1` in constant time.
4. Refuse the request when `t` is more than 5 minutes from your clock, so that a captured request cannot
   be replayed later.

Python:

```python
import hashlib
import hmac
import time

TOLERANCE = 300  # seconds


def verify(secret: str, header: str, body: bytes) -> bool:
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    t, v1 = parts.get("t", ""), parts.get("v1", "")
    if not t.isdigit() or abs(time.time() - int(t)) > TOLERANCE:
        return False
    expected = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


# Flask: verify(SECRET, request.headers["X-MegooFlags-Signature"], request.get_data())
```

Node.js:

```js
import { createHmac, timingSafeEqual } from "node:crypto";

const TOLERANCE = 300; // seconds

export function verify(secret, header, body /* Buffer */) {
  const parts = Object.fromEntries(
    header.split(",").map((p) => p.split("=", 2)),
  );
  const t = Number(parts.t);
  if (!Number.isInteger(t) || Math.abs(Date.now() / 1000 - t) > TOLERANCE)
    return false;
  const expected = createHmac("sha256", secret)
    .update(`${parts.t}.`)
    .update(body)
    .digest();
  const given = Buffer.from(parts.v1 ?? "", "hex");
  return given.length === expected.length && timingSafeEqual(given, expected);
}

// Express: app.post("/hook", express.raw({ type: "application/json" }), (req, res) => {
//   if (!verify(SECRET, req.get("X-MegooFlags-Signature") ?? "", req.body)) return res.sendStatus(401);
//   ...
// });
```

Go:

```go
package webhook

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"math"
	"strconv"
	"strings"
	"time"
)

const tolerance = 5 * time.Minute

func Verify(secret, header string, body []byte, now time.Time) bool {
	var t, v1 string
	for _, part := range strings.Split(header, ",") {
		k, v, _ := strings.Cut(part, "=")
		switch k {
		case "t":
			t = v
		case "v1":
			v1 = v
		}
	}
	ts, err := strconv.ParseInt(t, 10, 64)
	if err != nil || math.Abs(now.Sub(time.Unix(ts, 0)).Seconds()) > tolerance.Seconds() {
		return false
	}
	given, err := hex.DecodeString(v1)
	if err != nil {
		return false
	}
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(t + "."))
	mac.Write(body)
	return hmac.Equal(given, mac.Sum(nil))
}

// In a handler: body, _ := io.ReadAll(r.Body)
// Verify(secret, r.Header.Get("X-MegooFlags-Signature"), body, time.Now())
```

After rotating the secret, requests are signed with the new secret only, so update your receiver right
away.

## Delivery

Notifications leave within about 10 seconds of the change. Any 2xx response means delivered; the response
body is kept (its first 2 KB) for the delivery log and otherwise ignored.

### Retries

Network errors, timeouts, and the statuses 408, 429 and 5xx are retried. Every other status, redirects
included, fails the delivery at once. A delivery gets 6 attempts in all:

| Attempt | When                       |
| ------- | -------------------------- |
| 1       | Right away                 |
| 2       | 30 seconds after attempt 1 |
| 3       | 2 minutes after attempt 2  |
| 4       | 10 minutes after attempt 3 |
| 5       | 1 hour after attempt 4     |
| 6       | 6 hours after attempt 5    |

After the sixth failure the delivery is **Failed**; **Send again** in the delivery log queues it once more.
Failing integrations are never paused automatically.

### At least once

A delivery can arrive more than once, for example when the API process that was sending it stops in the
middle and another one takes over. Deliveries can also arrive out of order after retries. Make your
receiver idempotent: remember the event `id`s you have handled, ignore repeats, and use `occurred_at` to
order events.

### Addresses

By default MegooFlags sends only to public addresses over `https`:

- the URL must use `https`, have a host, and no user name or password;
- every address the host resolves to must be public: private networks (10/8, 172.16/12, 192.168/16),
  loopback, link-local (including cloud metadata at 169.254.169.254), carrier-grade NAT (100.64/10),
  unique-local IPv6, multicast and unspecified addresses are refused, also when they are wrapped in IPv6
  (IPv4-mapped, 6to4, NAT64);
- the connection goes to the address that was checked, so a DNS answer that changes between the check
  and the connection cannot redirect it;
- redirects are not followed, proxy settings from the environment are ignored, and at most 64 KB of the
  response is read;
- a Slack URL must be on `hooks.slack.com`.

The URL is checked when you save the integration and again on every send.

To send to a receiver on your own network, set `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE=true`. It then allows
`http` too, and every address except unspecified and multicast ones. Only set it when everyone who can
manage a project may reach your internal network through it.

## Settings

Set these on the `api` service (see `deploy/.env.example`):

| Variable                             | Default | Meaning                                                                                     |
| ------------------------------------ | ------- | ------------------------------------------------------------------------------------------- |
| `MEGOOFLAGS_WEBHOOK_ALLOW_PRIVATE`   | `false` | Allow `http` and private, loopback and link-local addresses                                 |
| `MEGOOFLAGS_WEBHOOK_TIMEOUT_SECONDS` | `10`    | The deadline for a whole request, from connecting to reading the answer, at most 60         |
| `MEGOOFLAGS_WEBHOOK_RETENTION_DAYS`  | `7`     | Days to keep the delivery log; older delivered and failed deliveries are deleted every hour |
| `MEGOOFLAGS_PUBLIC_URL`              | empty   | The external URL, used for the `url` field and the links in Slack and Teams                 |

Deliveries are sent by the API process that runs the scheduler (`MEGOOFLAGS_SCHEDULER_ENABLED`, see
[Release workflows](/MegooFlags/use/release-workflows/)), at most 10 at a time.

## Metrics

`/metrics` has the counter `megooflags_integration_deliveries_total{kind, outcome}`, one increment per
attempt:

- `kind` is `webhook`, `slack` or `teams`;
- `outcome` is `delivered`, `retry` (failed, another attempt is planned) or `failed` (failed for good).

For example, alert on `increase(megooflags_integration_deliveries_total{outcome="failed"}[1h]) > 0`.
