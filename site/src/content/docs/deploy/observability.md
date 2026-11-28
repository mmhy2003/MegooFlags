---
title: Observability
description: Health and readiness checks, Prometheus metrics, OpenTelemetry traces and JSON logs.
---

## Health and readiness

Caddy passes both endpoints through, so a load balancer or uptime monitor can reach them on the
public address.

| Endpoint   | Answers                                                                                                                                                                                                                                                 |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/healthz` | Liveness. Always `200` with `{"status": "ok", "version": "1.0.0", "role": "standalone"}` while the process runs. It checks no dependency. The api image's Docker health check uses it.                                                                  |
| `/readyz`  | Readiness. `200 {"status": "ready"}`, or `503` with a reason. On a single node or control plane it checks PostgreSQL and Valkey (`not_ready` when either fails). On a worker it needs a valid snapshot: `not_ready`, `revoked` or `draining` otherwise. |

Point load balancer health checks at `/readyz`, so an API process that cannot serve, or a worker
being drained, stops getting traffic.

## Prometheus metrics

Every api container serves Prometheus metrics on port 8000 at `/metrics`, for all of its worker
processes together. They are reachable only inside the compose network: Caddy answers `404` for
`/metrics`.

To scrape every API replica of a single node or control plane, attach Prometheus to the
`megooflags_default` network and find the replicas by DNS:

```yaml
scrape_configs:
  - job_name: megooflags-api
    dns_sd_configs:
      - names: [api]
        type: A
        port: 8000
```

On a worker node the network is `megooflags-worker_default`, and the target is `api:8000`.

On Kubernetes with the Prometheus Operator, set `metrics.serviceMonitor.enabled=true` in the Helm
chart: see [Kubernetes (Helm)](/MegooFlags/deploy/kubernetes/#metrics).

### HTTP

| Metric                                     | Type      | Labels                      | Meaning                                       |
| ------------------------------------------ | --------- | --------------------------- | --------------------------------------------- |
| `megooflags_http_requests_total`           | counter   | `method`, `route`, `status` | Requests handled.                             |
| `megooflags_http_request_duration_seconds` | histogram | `method`, `route`           | Request duration.                             |
| `megooflags_http_requests_in_progress`     | gauge     |                             | Requests being handled now.                   |
| `megooflags_build_info`                    | gauge     | `version`                   | Always 1; the `version` label is the release. |

`route` is the route template, such as `/api/projects/{project}`, never the raw path, so the
number of series stays bounded.

### SDK delivery

| Metric                                   | Type    | Meaning                                                                                                 |
| ---------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------- |
| `megooflags_rate_limit_errors_total`     | counter | Client rate-limit checks that failed open because Valkey returned an error.                             |
| `megooflags_change_publish_errors_total` | counter | Flag change messages that could not be published to Valkey. The periodic stream re-check delivers them. |

### Scheduler and integrations

| Metric                                    | Type    | Labels            | Meaning                                                                                          |
| ----------------------------------------- | ------- | ----------------- | ------------------------------------------------------------------------------------------------ |
| `megooflags_scheduler_leader`             | gauge   |                   | 1 in the process that runs the background jobs. Summed over all replicas it should be exactly 1. |
| `megooflags_scheduler_jobs_total`         | counter | `job`, `outcome`  | Rows each job handled, by outcome: `ok`, `failed`, or `error` for a job that raised.             |
| `megooflags_integration_deliveries_total` | counter | `kind`, `outcome` | Integration delivery attempts.                                                                   |

See [Release workflows](/MegooFlags/use/release-workflows/) and
[Integrations](/MegooFlags/use/integrations/) for what each job does.

### Multi-node

| Metric                                      | Node          | Meaning                                                                   |
| ------------------------------------------- | ------------- | ------------------------------------------------------------------------- |
| `megooflags_node_streams`                   | control plane | Open worker streams.                                                      |
| `megooflags_worker_snapshot_age_seconds`    | worker        | Seconds since the worker last heard from the control plane while in sync. |
| `megooflags_worker_snapshot_version`        | worker        | Highest snapshot version the worker has applied.                          |
| `megooflags_worker_control_plane_connected` | worker        | 1 while the worker's stream to the control plane is open.                 |
| `megooflags_worker_buffered_events`         | worker        | Exposures and counts waiting to be forwarded.                             |
| `megooflags_worker_events_dropped_total`    | worker        | Exposures dropped because the buffer was full or older than 7 days.       |

[Multi-node deployment](/MegooFlags/deploy/multi-node/#metrics) has an alert rule for a stale
worker.

## Traces

The API sends OpenTelemetry traces over OTLP/HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set, for
example `http://otel-collector:4318`. It traces HTTP requests and SQL queries. The health, metrics
and SDK stream endpoints, invitation links and SSO callbacks are not traced, so no key or token in
their URLs reaches your collector.

| Variable                      | Meaning                                                                      |
| ----------------------------- | ---------------------------------------------------------------------------- |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Collector endpoint. Empty: tracing is off.                                   |
| `OTEL_SERVICE_NAME`           | Service name, `megooflags-api` by default (`megooflags-worker` on a worker). |
| `OTEL_TRACES_SAMPLER_ARG`     | Fraction of new traces to sample, `0.0` to `1.0`. A parent's decision wins.  |

The exporter also reads the other standard `OTEL_EXPORTER_OTLP_*` variables, such as
`OTEL_EXPORTER_OTLP_HEADERS`; add them with a compose override (see
[Configuration](/MegooFlags/deploy/configuration/#settings-the-compose-files-do-not-pass)). Export
failures are logged and never fail a request.

## Logs

The API writes one JSON object per line to stdout:

```json
{
  "ts": "2026-11-17T10:00:00.123+00:00",
  "level": "INFO",
  "logger": "megooflags_api.access",
  "msg": "GET /api/projects 200",
  "method": "GET",
  "route": "/api/projects",
  "status": 200,
  "duration_ms": 4.21,
  "client_ip": "172.18.0.5",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7"
}
```

- Every request is logged by `megooflags_api.access`. Health and metrics requests are logged at
  `DEBUG`, so they stay out of the default `INFO` output.
- `trace_id` and `span_id` appear when tracing is on, to join logs with traces.
- Errors carry the traceback in `exc`.

Set the level with `MEGOOFLAGS_LOG_LEVEL`. For a human-readable format while debugging, set
`MEGOOFLAGS_LOG_FORMAT=text`. Read the logs with `docker compose logs -f api`.
