# MegooFlags delivery spec

Version 1 (`/sdk/v1`, `/ofrep/v1`).

This document defines how an SDK talks to the MegooFlags API: how it authenticates, downloads the
ruleset or evaluates remotely, follows changes, and sends exposure events and evaluation counts. How a
flag is evaluated is defined in [`evaluation.md`](evaluation.md).

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. SDK keys

An SDK key belongs to one environment of one project. Every request made with it reads or writes that
environment only.

| Kind | Prefix | For | Can |
|---|---|---|---|
| Server | `mf_server_` | Back-end SDKs, which evaluate locally | Download the ruleset, evaluate, stream full rulesets, call OFREP, send events |
| Client | `mf_client_` | Browser and mobile SDKs, which evaluate remotely | Evaluate, stream change notices, call OFREP, send events. Never download the ruleset |

- A key is the prefix followed by 32 base62 characters (`0-9A-Za-z`), 42 characters in all.
- Keys are created, listed and revoked with the management API
  (`/api/projects/{project}/environments/{env}/sdk-keys`). The secret is returned only once, when the key
  is created; the API stores only its hash.
- A server key is a secret. It MUST NOT be shipped in a browser or mobile app. A client key is public:
  it reveals evaluated values only, never rules, and it is rate-limited (section 4).

### 1.1 Sending the key

- An SDK MUST send the key as `Authorization: Bearer <key>`. The scheme is case-insensitive.
- OFREP routes also accept `X-API-Key: <key>`, which OFREP providers send. `Authorization` wins when
  both are present.
- Browsers' `EventSource` cannot set headers. A client key MAY therefore be sent as the query parameter
  `?key=<key>`, on `GET /sdk/v1/stream` only. `Authorization` wins when both are present.
- A server key MUST NOT be sent in a URL. A server key in `?key=` is refused with 401, even when it is
  valid, so a leaked URL never exposes a server key.

## 2. Endpoints

All bodies are JSON (`Content-Type: application/json`) unless stated otherwise. `/sdk/v1` fields are
snake_case; `/ofrep/v1` fields are camelCase, as the OFREP protocol requires.

| Route | Keys | Request | Response |
|---|---|---|---|
| `GET /sdk/v1/ruleset` | server | `If-None-Match` (optional) | 200: the ruleset ([`ruleset.schema.json`](ruleset.schema.json), [evaluation.md §10](evaluation.md)) with `ETag`. 304 when `If-None-Match` matches |
| `POST /sdk/v1/evaluate` | server, client | `{"context": {...}, "flags": ["a", "b"]}`; `flags` is optional and defaults to every flag. `If-None-Match` (optional) | 200: `{"results": [{"key", "value", "variant", "reason", "error_code", "error_message", "metadata"}]}`, sorted by key, with `ETag`. `metadata` is present for server keys only. 304 when `If-None-Match` matches |
| `GET /sdk/v1/stream` | server; client also via `?key=` | `Last-Event-ID` (optional) | 200: `text/event-stream` (section 6) |
| `POST /sdk/v1/events` | server, client | `{"exposures": [...], "counts": [...]}` (section 7) | 202, empty body |
| `POST /ofrep/v1/evaluate/flags/{key}` | server, client | `{"context": {...}}` | 200: `{"key", "value", "reason", "variant", "metadata"}`. 404 or 400: `{"key", "errorCode", "errorDetails"}` (section 9) |
| `POST /ofrep/v1/evaluate/flags` | server, client | `{"context": {...}}`. `If-None-Match` (optional) | 200: `{"flags": [...]}`, one success or error item per flag, sorted by key, with `ETag`. 304 when `If-None-Match` matches |

- Archived flags are not in the ruleset. Evaluating one, or an unknown key, gives `FLAG_NOT_FOUND`.
- Client keys never receive `metadata` from `/sdk/v1/evaluate`, and receive `"metadata": {}` from OFREP,
  so nothing about the rules leaks.

## 3. Status codes

Errors other than OFREP evaluation errors (section 9) have the body
`{"code": "...", "message": "...", "details": ...}`. `message` is not normative.

| Status | `code` | When | The SDK MUST |
|---|---|---|---|
| 401 | `unauthorized` | The key is missing, malformed or unknown, its environment was deleted, or a server key was sent in `?key=`. The response carries `WWW-Authenticate: Bearer` | Stop and report a configuration error. It MUST NOT retry with the same key |
| 403 | `forbidden` | The key is revoked, or a client key called `GET /sdk/v1/ruleset` | Stop and report a configuration error. It MUST NOT retry with the same key |
| 413 | `payload_too_large` | An events body is larger than 1 MB | Split the batch and send the parts |
| 422 | `validation_error` | The body is not valid: wrong types, a missing field, or more than 1000 items in a list. `details` lists the problems | Drop the batch: sending it again fails the same way |
| 429 | `rate_limited` | A client key is over its rate limit (section 4) | Wait at least the number of seconds in `Retry-After`, then retry |

- On 5xx or a network error, an SDK SHOULD keep serving its last known values and retry with backoff
  (section 6.3).
- Nothing is stored from a request that fails: an events batch is stored whole or not at all.

## 4. Rate limit

- Client keys are limited per key and client IP address, in fixed one-minute windows. The default limit
  is 600 requests per minute (`MEGOOFLAGS_CLIENT_RATE_LIMIT_PER_MINUTE`). The limit is shared by all API
  replicas.
- Over the limit, the API returns 429 with `Retry-After` in seconds, until the window ends.
- Server keys are not rate-limited.
- Opening a stream counts as one request, however long the stream stays open.

## 5. ETags and polling

### 5.1 ETags

- `GET /sdk/v1/ruleset` returns a strong `ETag` derived from the ruleset content. It changes when, and
  only when, the ruleset changes, so a change that leaves the ruleset identical (a flag renamed, for
  example) produces no new ETag.
- `POST /sdk/v1/evaluate` returns an `ETag` derived from the ruleset ETag, the key kind, the context and
  the requested `flags`. It changes when the ruleset changes or when the request does. The order of
  `flags` and of context attributes does not matter.
- `POST /ofrep/v1/evaluate/flags` returns an `ETag` derived the same way from the ruleset ETag, the key
  kind and the context. The single-flag OFREP route has no ETag.
- An ETag is opaque. An SDK MUST NOT parse it, and MUST NOT reuse an evaluate ETag for a different
  context or flag list.

### 5.2 If-None-Match

- An SDK SHOULD send the last ETag it received for the same request in `If-None-Match`. When it matches
  the current ETag, the response is 304 with the `ETag` header and no body, and the SDK keeps the values
  it has.
- `If-None-Match` MAY list several ETags separated by commas, MAY use the weak form `W/"..."`, and MAY be
  `*`, which always matches.
- A 304 from `/sdk/v1/evaluate` or OFREP bulk is not counted as an evaluation.

### 5.3 Polling fallback

When it cannot use the stream (section 6), an SDK MUST poll instead:

- Server SDKs poll `GET /sdk/v1/ruleset`.
- Client SDKs poll `POST /sdk/v1/evaluate` with the same context and flags.
- The SDK SHOULD poll every 30 s, MUST NOT poll more often than every 15 s, and MUST send `If-None-Match`.
- While the stream is connected, the SDK SHOULD NOT poll. When the stream has failed several times in a
  row, it SHOULD poll while it keeps trying to reconnect.

## 6. Streaming (server-sent events)

`GET /sdk/v1/stream` returns `text/event-stream` with `Cache-Control: no-cache` and
`X-Accel-Buffering: no`. A change to any flag or segment of the environment, the kill switch included,
reaches every open stream on every API replica within seconds.

### 6.1 Messages

| Message | Data | When |
|---|---|---|
| `retry: 3000` | | First line of the response. The SDK's reconnect delay is 3 s |
| `: ping` | | A comment every 15 s (`MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`) while nothing else is sent |
| `event: put` | The full ruleset, as from `GET /sdk/v1/ruleset`. `id` is its ETag | Server keys: on connect, and after every change |
| `event: changed` | `{"etag": "<ruleset ETag>"}`. `id` is the same ETag | Client keys: on connect, and after every change |
| `event: revoked` | `{}` | The key was revoked or its environment deleted. The server closes the stream after it |

- After a `put`, a server SDK MUST validate the ruleset ([evaluation.md §11](evaluation.md)) and use it
  from then on. If it is invalid, the SDK keeps its previous ruleset.
- After a `changed`, a client SDK MUST call `POST /sdk/v1/evaluate` again to get the new values.
- Changes may be merged: an SDK MAY skip intermediate rulesets and receive only the latest one, and MUST
  NOT rely on seeing every change.
- An SDK MUST ignore unknown event names, so later versions can add events.

### 6.2 Last-Event-ID

- On reconnect, an SDK SHOULD send the `id` of the last `put` or `changed` it processed in
  `Last-Event-ID`. `EventSource` does this by itself.
- If `Last-Event-ID` equals the current ruleset ETag, the server skips the first `put` or `changed`
  message, because the SDK is already up to date. Otherwise the first message is sent as usual.

### 6.3 Reconnecting

- When the stream ends or fails without a `revoked` event, the SDK MUST reconnect. It SHOULD wait the
  `retry` delay (3 s) first, and SHOULD back off exponentially with jitter, up to 60 s, while
  connections keep failing. It resets the backoff after a stream has stayed open for 60 s.
- If no data and no `: ping` arrive for 45 s (three heartbeats), the SDK SHOULD treat the connection as
  dead and reconnect.
- On `revoked`, or a 401 or 403 response, the SDK MUST stop and MUST NOT reconnect or poll with that key.
  It keeps serving its last known values and reports a configuration error.
- On 429, the SDK MUST wait `Retry-After` before it reconnects.
- While disconnected, the SDK keeps serving its last known values and falls back to polling (section
  5.3).

## 7. Exposure events and evaluation counts

`POST /sdk/v1/events` takes a batch:

```json
{
  "exposures": [
    {"flag_key": "new-checkout", "variant": "on", "reason": "SPLIT",
     "timestamp": "2026-11-10T09:15:02Z", "context": {"key": "user-1"}}
  ],
  "counts": [
    {"flag_key": "new-checkout", "variant": "on", "hour": "2026-11-10T09:00:00Z", "count": 1250}
  ]
}
```

- An **exposure** records that a user saw a variant. `variant` MAY be `null`. `timestamp` MUST carry a
  time zone; a timestamp in the future is clamped to the time the batch is received.
- A **count** is the number of evaluations of one flag and variant in one hour. `hour` MUST carry a time
  zone and is truncated to the hour (UTC). `count` is from 1 to 10^9. Counts for the same flag, variant
  and hour add up.
- Both lists are optional. Each holds at most 1000 items, and the body MUST NOT exceed 1 MB. A larger
  batch is refused whole (422 or 413).
- `flag_key` is not checked against the ruleset, so events for a deleted flag are still stored.
- **Privacy.** `context` is dropped unless the server enables `MEGOOFLAGS_PERSIST_CONTEXT`. An SDK SHOULD
  send no context unless its user opts in. Counts never carry a context.

An SDK SHOULD:

- buffer exposures and counts in memory, bounded, and send them in the background, never on the
  evaluation path;
- send a batch every 10 to 60 s, when a list reaches 1000 items, and on shutdown;
- aggregate counts locally per flag, variant and UTC hour, and send each bucket once per batch;
- on 429 or 5xx, keep the batch and retry later with backoff; on 413 or 422, drop it.

Remote evaluations (`/sdk/v1/evaluate` and OFREP) are counted by the server. Client SDKs that evaluate
remotely MUST NOT send counts for them; they MAY send exposures.

## 8. CORS

- `POST /sdk/v1/evaluate`, `GET /sdk/v1/stream`, `POST /sdk/v1/events` and every `/ofrep/` route allow
  any origin: `Access-Control-Allow-Origin: *`, without credentials. `ETag` and `Retry-After` are
  exposed to scripts.
- Preflight requests are answered for `GET`, `POST` and `OPTIONS` with the headers `Authorization`,
  `Content-Type`, `X-API-Key`, `If-None-Match` and `Last-Event-ID`.
- `GET /sdk/v1/ruleset` has no CORS headers: browsers MUST NOT download the ruleset.
- Browser SDKs MUST NOT send cookies; the key is the only credential.

## 9. OFREP

MegooFlags implements the
[OpenFeature Remote Evaluation Protocol](https://github.com/open-feature/protocol) under the base path
`/ofrep/v1`. An OFREP provider is configured with the API's base URL and a server or client key, sent as
`Authorization: Bearer` or `X-API-Key`.

- The context's `targetingKey` is copied to the attribute `key` before evaluation
  ([evaluation.md §1](evaluation.md)), replacing any `key` attribute.
- `PREREQUISITE_FAILED` is passed through as the reason.
- `variant` is omitted when no variation was served, and `errorDetails` when there is no message.
- `metadata` is `{}` for client keys.

Single-flag evaluation errors:

| `errorCode` | Status |
|---|---|
| `FLAG_NOT_FOUND` | 404 |
| `PARSE_ERROR`, `TYPE_MISMATCH`, `INVALID_CONTEXT`, `GENERAL` | 400 |

The bulk route always returns 200: each item is either a success or `{"key", "errorCode",
"errorDetails"}`. Authentication and rate-limit errors use the status codes of section 3.

## 10. Spec details (normative summary)

1. **Key transport.** `Authorization: Bearer` everywhere; `X-API-Key` on OFREP only; `?key=` with a
   client key on `GET /sdk/v1/stream` only. A server key in a URL is a 401.
2. **Key scope.** A key reads and writes its own environment only. Client keys never download the
   ruleset (403) and never see `metadata`.
3. **Stop conditions.** 401, 403 and `event: revoked` stop the SDK: no retries, no reconnects, no polling
   with that key.
4. **Retry conditions.** 429 waits `Retry-After`. 5xx and network errors back off. 413 and 422 drop the
   batch.
5. **ETags.** Opaque and strong. Send `If-None-Match` when polling; 304 means "keep what you have".
6. **Polling.** Every 30 s, never under 15 s, only while the stream is unavailable.
7. **Stream.** `retry: 3000`, `: ping` every 15 s, `put` (server) or `changed` (client) with `id` = the
   ruleset ETag, `revoked` before close. `Last-Event-ID` equal to the current ETag skips the first message.
8. **Events.** At most 1000 exposures and 1000 counts per batch and 1 MB per body. Counts are bucketed by
   UTC hour. The context is stored only when the server enables it.
