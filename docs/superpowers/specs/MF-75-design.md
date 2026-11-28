# MF-75 — Single-node Compose bundle: TLS modes, scaling and observability

## Goal
`docker compose up` with the bundle in `deploy/` gives a working MegooFlags stack in either TLS mode, chosen by one variable. If `MEGOOFLAGS_SITE_ADDRESS` is empty, Caddy serves plain HTTP on port 80 only. If it holds a domain, Caddy gets a certificate for it and serves HTTPS. API replicas can be scaled and are load-balanced. The API exposes Prometheus metrics, OpenTelemetry traces and JSON logs. CI smoke-tests both TLS modes.

## Starting point (already in the repo from MF-74)
`deploy/docker-compose.yml` already has caddy/web/migrate/api/db, `${MEGOOFLAGS_VERSION:-latest}`, migrate → api ordering, healthchecks, `caddy_data` and `db_data`. The repo also has the PRD Caddyfile (with `/healthz` and `/readyz` added), `.env.example` (already recommends pinning the version), `compose.ci.yml`, `scripts/smoke.sh` (plain HTTP only), and real `/healthz` and `/readyz` endpoints (the latter checks the DB). MF-75 fills these gaps:

1. Caddy always publishes 443, even in plain-HTTP mode. The PRD says port 80 only, and the current setup breaks on hosts where another proxy already uses :443.
2. `caddy → api:8000` uses one static upstream, so `--scale api=N` is not load-balanced properly.
3. The API ignores `MEGOOFLAGS_PUBLIC_URL` and trusts forwarded headers from anyone (`--forwarded-allow-ips *`).
4. There are no metrics, traces or structured logs.
5. Nothing checks the footprint, and the HTTPS mode is not smoke-tested.

## Decisions
- **The TLS mode is set by `MEGOOFLAGS_SITE_ADDRESS` alone** (your answer). Empty or unset means plain HTTP. A domain means automatic HTTPS. No second file, profile or extra variable. `.env.example` changes from `MEGOOFLAGS_SITE_ADDRESS=:80` to an empty value. Rejected: an HTTPS override file enabled through `COMPOSE_FILE`, because it needs a second setting.
- **How 443 is published:** a long-syntax port with `published: "${MEGOOFLAGS_SITE_ADDRESS:+443}"`, for both tcp and udp. With a domain, the host's 443 is published. Without one, Docker maps container port 443 to a random free host port, where nothing listens because Caddy only binds :80. So it never clashes with another proxy on the host. *Fallback if Compose rejects an empty `published`:* always publish `${MEGOOFLAGS_HTTPS_PORT:-443}`, document it, and note the change in the commit.
- **`/metrics` is internal only** (your answer). Caddy does not route it: the Caddyfile answers `/metrics` with 404. Each `api` container serves it on :8000 inside the compose network. The docs comment in `.env.example` shows a Prometheus `dns_sd_configs` snippet (type A, name `api`, port 8000) for scraping every replica.
- **1 Uvicorn worker per container; scale with `--scale api=N`** (your answer). Metrics stay plain per-process, with no multiprocess mode.
- **Load balancing:** Caddy uses dynamic A-record upstreams for `api`, so new or removed replicas are picked up without a restart.
- **Forwarded headers:** Caddy is the only hop in front of the API. Caddy decides whether to pass a client's `X-Forwarded-*` headers on, based on `MEGOOFLAGS_TRUSTED_PROXIES` (DEP-3). The API then trusts forwarded headers only from private ranges (the Docker network), not from `*`.
- **Traces are off unless configured:** they use the standard `OTEL_*` variables, so no collector ships in the bundle.

## Architecture and files
**`deploy/docker-compose.yml`**
- caddy ports: `${MEGOOFLAGS_HTTP_PORT:-80}:80`, plus 443/tcp and 443/udp via `${MEGOOFLAGS_SITE_ADDRESS:+443}` as above. Caddy keeps getting `MEGOOFLAGS_SITE_ADDRESS: ${MEGOOFLAGS_SITE_ADDRESS:-:80}`.
- api env adds `MEGOOFLAGS_PUBLIC_URL`, `MEGOOFLAGS_LOG_LEVEL`, `FORWARDED_ALLOW_IPS` (default `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1`), and pass-through for `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME` and `OTEL_TRACES_SAMPLER_ARG`. Empty values mean the feature is off.
- No hard memory limits. Footprint is checked in CI (see Testing).

**`deploy/Caddyfile`**: the PRD layout, plus:
```caddyfile
respond /metrics 404
reverse_proxy @api {
    dynamic a api 8000 { refresh 5s }
    lb_policy round_robin
    lb_try_duration 5s
    flush_interval -1
}
```
`lb_try_duration` retries on another replica while one restarts. `/healthz` and `/readyz` stay routed to the API.

**`deploy/.env.example`**
- `MEGOOFLAGS_SITE_ADDRESS=` (empty), with comments explaining both modes.
- With a domain: DNS must point at the host, and ports 80 and 443 must be reachable for ACME.
- Comments for `MEGOOFLAGS_PUBLIC_URL`, `MEGOOFLAGS_LOG_LEVEL` and the `OTEL_*` variables.
- The Prometheus scrape snippet.

**`deploy/compose.ci.yml`**: caddy ports override to `${MEGOOFLAGS_HTTP_PORT:-8080}:80` and `${MEGOOFLAGS_HTTPS_PORT:-8443}:443`.

**`api/Dockerfile`**: the CMD drops `--forwarded-allow-ips *` and `--proxy-headers` reads `FORWARDED_ALLOW_IPS` from the env, with the private-ranges default set by `ENV`. It also adds `--no-access-log` (access logging moves to the app) and `--log-config` pointing at the JSON config.

**API package (`api/src/megooflags_api/`)**: small modules, one job each.
- `settings.py`: adds `public_url: AnyHttpUrl | None`, `log_level: str = "INFO"` and `log_format: Literal["json", "text"] = "json"`.
- `urls.py`: `public_base_url(request)` returns `settings.public_url` if set. Otherwise it builds the URL from the request's scheme and host, which are already proxy-corrected (DEP-2). Later features (links, webhooks, SDK snippets) use this.
- `logging.py`: a stdlib `logging` JSON formatter with fields `ts, level, logger, msg`, plus `trace_id`/`span_id` when a span is active. `configure_logging(settings)` applies it to the app and to Uvicorn's loggers. An access-log middleware logs `method, route, status, duration_ms, client_ip` (`/healthz`, `/readyz` and `/metrics` are logged at DEBUG).
- `metrics.py`: uses `prometheus-client` and defines:
  - `megooflags_http_requests_total{method,route,status}`
  - `megooflags_http_request_duration_seconds{method,route}` (histogram)
  - `megooflags_http_requests_in_progress`
  - `megooflags_build_info{version}`

  The `route` label is the route template, not the raw path, to keep cardinality low. Unmatched paths are labeled `other`. The module also provides a middleware and a `GET /metrics` route with `include_in_schema=False`, so `openapi.json` doesn't change.
- `tracing.py`: if `OTEL_EXPORTER_OTLP_ENDPOINT` is set, it sets up the OTel SDK with an OTLP/HTTP exporter, the FastAPI instrumentation (health and metrics routes excluded) and the SQLAlchemy instrumentation on the async engine. Otherwise it does nothing.
- `main.py`: `create_app` wires these in. Health routes are unchanged.
- New dependencies in `pyproject.toml` and `uv.lock`: `prometheus-client`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-fastapi` and `opentelemetry-instrumentation-sqlalchemy`.

**`scripts/smoke.sh`** takes a mode, `http` (default) or `https`.
- **Both modes:**
  - `/healthz`, `/readyz` and `/` work.
  - `/metrics` through Caddy returns 404.
  - Inside the network, `docker compose exec api` fetching `127.0.0.1:8000/metrics` returns `megooflags_http_requests_total`.
  - The api logs contain a JSON line with a `route` field.
  - Total idle memory from `docker stats --no-stream` is under 1 GiB, to keep headroom for the 2 GB target.
- **http:**
  - Started with `--scale api=2`. Several `/healthz` requests succeed, and both replicas show access-log lines.
  - Nothing listens on host 8443.
- **https:** `MEGOOFLAGS_SITE_ADDRESS=localhost`. Caddy then issues a certificate from its internal CA, so no Let's Encrypt is needed in CI.
  - The CA root is copied out of the caddy container, and `curl --cacert … https://localhost:8443/healthz` succeeds.
  - `http://localhost:8080/` returns a redirect to https.

**`.github/workflows/ci.yml`**: the `smoke` job gets `strategy.matrix.mode: [http, https]` and runs `bash scripts/smoke.sh ${{ matrix.mode }}`.

**`CHANGELOG.md`**: `[Unreleased]` entries. Under Changed, note that the default `MEGOOFLAGS_SITE_ADDRESS` is now empty and 443 is published only in HTTPS mode. Under Added, list metrics, traces, JSON logs and API load balancing.

## Error handling and edge cases
- **A domain is set but DNS or the ports are wrong:** Caddy keeps retrying ACME and logs the error. The stack stays up, and `.env.example` says what is needed.
- **A domain is set and `MEGOOFLAGS_HTTP_PORT` is not 80:** the HTTP-01 challenge fails, but TLS-ALPN on 443 still works. This is documented in the comment.
- **Someone sets `MEGOOFLAGS_SITE_ADDRESS=:80` explicitly:** this counts as non-empty, so host 443 gets published even though nothing listens there. `.env.example` tells operators to leave the value empty for plain HTTP. This is noted in the CHANGELOG as an upgrade step.
- **A replica dies or restarts:** Caddy retries on another replica within 5 s, and DNS refresh drops the dead one.
- **The OTel endpoint can't be reached:** the exporter drops spans and logs a warning. Requests never fail because of it.
- **`/readyz` without the DB:** it still returns 503. `/healthz` stays a liveness check only, so Docker doesn't restart containers during a DB outage.

## Testing
- **pytest (api):**
  - The JSON formatter outputs valid JSON with the expected fields and trace ids when a span is active.
  - `/metrics` returns the counters, uses route-template labels and is not in the OpenAPI spec.
  - `public_base_url` uses the setting when set, otherwise it uses the `X-Forwarded-Proto`/`Host` headers.
  - Forwarded headers from an IP that isn't trusted are ignored (Uvicorn `ProxyHeadersMiddleware` with the configured IPs).
  - Tracing is a no-op without the env var and is set up when it is set (in-memory exporter).
- **The OpenAPI drift check stays green** (no schema change).
- **Smoke tests:** `scripts/smoke.sh http|https` locally and in the CI matrix, as described above.
- **`docker compose config`** with the domain empty and set shows the expected port publishing.

## Out of scope
- DEP-5 (proxy sample docs) → the docs task.
- DEP-6 (the plain-HTTP warning in the UI) → the UI task.
- Bundled Prometheus, Grafana or an OTel collector.
- Web (Next.js) metrics and tracing.
- Multiple workers per container and Prometheus multiprocess mode.
- Multi-node compose files.
- Hard container memory limits.
- Consumers of `public_base_url` (links, webhooks and SDK snippets come with their features).
