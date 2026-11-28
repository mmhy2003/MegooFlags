# Load balancer samples for worker nodes

In a multi-node deployment, SDKs and OpenFeature clients talk to the workers through a load
balancer. These samples send `/sdk/` and `/ofrep/` to three workers and answer everything else
with 404: the web UI, `/api/*` and `/metrics` stay on the control plane.

`worker-1`, `worker-2` and `worker-3` are placeholders. Replace them with your workers'
hostnames or addresses. Each worker runs `deploy/docker-compose.worker.yml`, whose Caddy listens
on port 80 (plain HTTP behind the load balancer). The load balancer terminates TLS.

| File | Load balancer | TLS | Health check |
|---|---|---|---|
| `nginx.conf` | nginx 1.25 or later | `/etc/nginx/tls/fullchain.pem` and `privkey.pem` | Passive (`max_fails`) |
| `haproxy.cfg` | HAProxy 2.2 or later | `/usr/local/etc/haproxy/tls/site.pem` (chain then key) | Active, `GET /readyz` |
| `Caddyfile` | Caddy 2 | Automatic for `flags.example.com`, or your own files | Active, `GET /readyz` |
| `traefik.yml` | Traefik 3 | `/etc/traefik/tls/fullchain.pem` and `privkey.pem` | Active, `GET /readyz` |

## What every sample sets

- **Health check on `/readyz`.** A worker answers 200 only when it holds a valid snapshot and is
  neither draining nor revoked; otherwise 503. Checking it every few seconds takes a worker out
  before you drain or upgrade it, and keeps a new worker out until its first sync is done.
- **No response buffering.** SDK streams (`/sdk/v1/stream`) are server-sent events. Buffering
  holds back flag changes, so each sample flushes every write.
- **A read or idle timeout above 60 s.** A stream stays open for hours and a worker sends a
  heartbeat every 15 s (`MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`). A timeout under that closes streams;
  the SDKs reconnect, but needlessly.
- **`X-Forwarded-For`.** Workers trust forwarded headers from private ranges by default
  (`MEGOOFLAGS_TRUSTED_PROXIES`), which keeps per-client rate limits per client.

Streams need no sticky sessions: any worker serves any environment, and an SDK that reconnects
to another worker resyncs from its ETag.

## nginx

Open source nginx has no active health checks. It marks a worker down after `max_fails` failed
requests for `fail_timeout`, and `proxy_next_upstream` retries a failed or 503 `GET` on another
worker. A draining worker keeps answering until its grace time ends, so passive checks are
enough for a rolling upgrade; add an active check if you have one:

- NGINX Plus: `health_check uri=/readyz interval=5s;` in both `location` blocks (the upstream
  also needs `zone megooflags_workers 64k;`).
- Otherwise, take the worker out of the `upstream` block (or mark it `down`) and reload nginx
  before you drain it.

Check the file with `nginx -t` before you load it.

## HAProxy

`timeout tunnel` covers upgraded connections; `timeout server` and `timeout client` (75 s) cover
idle streams between heartbeats. `http-check` takes a worker out after two failed checks and
back after two good ones.

## Caddy

Caddy gets a certificate for the site address on its own. `flush_interval -1` sends every
event at once and `read_timeout 1h` keeps idle streams open. Check the file with
`caddy validate --config Caddyfile --adapter caddyfile`.

## Traefik

`traefik.yml` holds both the static configuration (entry points, timeouts) and the dynamic one
(router, service, health check), and names itself as its file provider. Start Traefik without
command-line flags: flags replace the configuration file.

## Cloud load balancers

Point the load balancer at port 80 of each worker, route `/sdk/*` and `/ofrep/*` to them, and
use `GET /readyz` with an expected status of 200 as the health check (every 5 to 10 s, two
failures to mark a worker unhealthy). Then raise the timeout that would close idle streams:

- **AWS Application Load Balancer:** the default idle timeout of 60 s is close to the limit. Set
  the load balancer attribute `idle_timeout.timeout_seconds` to 3600 or so. The target group
  health check path is `/readyz`, with success code 200.
- **Google Cloud Application Load Balancer:** the backend service timeout (30 s by default)
  is the longest a response may take, streams included. Set `--timeout=3600s` on the backend
  service, and use an HTTP health check with `--request-path=/readyz`.
- **Azure Application Gateway:** the backend setting's request timeout (20 s by default) closes
  streams. Set it to 3600 s and add a custom health probe on `/readyz` that expects 200. On v2
  gateways, also turn response buffering off so stream events are not held back.
