# Reverse proxy samples

Each file puts a TLS-terminating proxy in front of the bundle running on the same host with
`MEGOOFLAGS_SITE_ADDRESS=` (empty) and `MEGOOFLAGS_HTTP_PORT=8080`, with the settings the SDK streams
need. The guide is [Behind a reverse proxy](https://mmhy2003.github.io/MegooFlags/deploy/reverse-proxy/),
and `scripts/proxy_samples_test.sh` tests every sample in CI.

- [`nginx.conf`](nginx.conf): nginx server block; certificate in `/etc/megooflags-proxy/{fullchain,privkey}.pem`.
- [`Caddyfile`](Caddyfile): Caddy with automatic HTTPS for `MF_DOMAIN`.
- [`traefik.yml`](traefik.yml): Traefik static configuration, the `websecure` entry point and the file provider.
- [`traefik-dynamic.yml`](traefik-dynamic.yml): Traefik router, service and certificate for `MF_DOMAIN`.
- [`haproxy.cfg`](haproxy.cfg): HAProxy 3.0 frontend and backend; certificate in `/etc/megooflags-proxy/{fullchain,privkey}.pem`.
