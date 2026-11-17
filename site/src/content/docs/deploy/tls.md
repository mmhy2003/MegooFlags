---
title: TLS modes
description: Let Caddy get certificates on its own, or serve plain HTTP behind your own proxy.
---

The bundled Caddy runs in one of two modes. One value in `.env` picks the mode:
`MEGOOFLAGS_SITE_ADDRESS`.

| `MEGOOFLAGS_SITE_ADDRESS` | Mode            | Ports on the host        | Who terminates TLS                      |
| ------------------------- | --------------- | ------------------------ | --------------------------------------- |
| empty (the default)       | Plain HTTP      | 80 only                  | Your reverse proxy or load balancer     |
| a domain                  | Automatic HTTPS | 80 and 443 (TCP and UDP) | Caddy, with a Let's Encrypt certificate |

Worker nodes use the same setting, in the same way. See
[Multi-node deployment](/MegooFlags/deploy/multi-node/#tls).

## Automatic HTTPS

Set the domain the instance is reached at:

```bash
MEGOOFLAGS_SITE_ADDRESS=flags.example.com
MEGOOFLAGS_PUBLIC_URL=https://flags.example.com
```

Then run `docker compose up -d`. Caddy gets a certificate from Let's Encrypt, renews it before it
expires, redirects HTTP to HTTPS and serves HTTP/3 on UDP 443. For this to work:

- The domain's DNS record points at this host.
- Ports 80 and 443 are reachable from the internet. The ACME HTTP-01 challenge uses port 80, so keep
  `MEGOOFLAGS_HTTP_PORT=80`; with another port only the TLS-ALPN challenge on 443 is left.
- The `caddy_data` volume persists. It holds the certificates and the ACME account; deleting it
  makes Caddy request new certificates, and Let's Encrypt rate-limits repeated requests.

Caddy adds `Strict-Transport-Security: max-age=31536000; includeSubDomains` to every HTTPS response.

## Plain HTTP behind your proxy

Leave `MEGOOFLAGS_SITE_ADDRESS` empty when something in front of MegooFlags already terminates TLS:
Nginx, Traefik, HAProxy, Coolify, a cloud load balancer or Cloudflare. Caddy then listens on port 80
only, never asks for a certificate, and port 443 is not published, so it cannot clash with your
proxy.

- Set `MEGOOFLAGS_HTTP_PORT` when port 80 on the host is taken, for example by the proxy itself.
- Set `MEGOOFLAGS_PUBLIC_URL` to the `https://` address users reach (see
  [below](#megooflags_public_url)).
- Caddy only trusts `X-Forwarded-*` headers from private IP ranges. If your proxy connects from a
  public address, add it to `MEGOOFLAGS_TRUSTED_PROXIES` (Caddy syntax, for example
  `private_ranges 203.0.113.10`).
- HSTS is not sent in this mode, because Caddy does not see HTTPS. Let your proxy send it.

Leave the value empty rather than setting `:80`: the compose file publishes port 443 for any
non-empty value.

Your proxy must not buffer the SDK streams (server-sent events) and must keep idle connections open
longer than the heartbeat (`MEGOOFLAGS_SSE_HEARTBEAT_SECONDS`). See
[Behind a reverse proxy](/MegooFlags/deploy/reverse-proxy/) for the rules and tested samples for
nginx, Caddy, Traefik and HAProxy.

## `MEGOOFLAGS_PUBLIC_URL`

The external URL of the instance, for example `https://flags.example.com`. Set it in either mode.

- **Passkeys and SSO** refuse to start without it: they are bound to this origin. Set it before you
  register passkeys or configure an identity provider, and do not change it afterwards.
- **Integrations** (webhooks, Slack, Teams) link each event to the flag in the UI only when it is
  set: the scheduler that sends them has no request to take the address from.
- **Everything else** that shows a URL uses it when set, and otherwise the host and scheme of the
  request, corrected from the `X-Forwarded-*` headers of a trusted proxy.
