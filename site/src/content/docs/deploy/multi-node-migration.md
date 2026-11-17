---
title: Migrating from a single node to multi-node
description: Turn a running single node into a control plane with workers, without downtime.
---

This guide turns a running single node (`docker-compose.yml`, `MEGOOFLAGS_ROLE=standalone`) into
a control plane with workers, without downtime for your applications. The database, the volumes
and the URL of the web UI stay as they are. [Multi-node deployment](/MegooFlags/deploy/multi-node/) explains the roles,
settings and failure behaviour.

The examples use `flags.example.com` for the existing node, which becomes the control plane, and
`sdk.example.com` for the load balancer in front of the workers. Run the commands in the directory
that holds the node's `docker-compose.yml` and `.env`.

## 1. Upgrade the single node

Multi-node needs a release that ships `docker-compose.control-plane.yml`. Download it, the
`Caddyfile` and `.env.example` of that release next to your `docker-compose.yml`, compare
`.env.example` with your `.env`, then set `MEGOOFLAGS_VERSION` in `.env` to that release (for
example `MEGOOFLAGS_VERSION=0.1.0`) and upgrade as usual:

```bash
docker compose pull
docker compose up -d
curl -fsS https://flags.example.com/healthz    # {"status":"ok","version":"0.1.0","role":"standalone"}
```

Check that `.env` does not set `MEGOOFLAGS_ROLE`: each compose file brings its own default.

## 2. Make it the control plane

`docker-compose.control-plane.yml` is `docker-compose.yml` with `MEGOOFLAGS_ROLE` defaulting to
`control-plane`. Both use the compose project `megooflags`, so the same containers and volumes are
kept, and only the `api` container is recreated:

```bash
docker compose -f docker-compose.control-plane.yml up -d
curl -fsS https://flags.example.com/healthz    # {"status":"ok","version":"0.1.0","role":"control-plane"}
```

While the API restarts (a few seconds), SDKs evaluate from their cache and then reconnect their
streams. The control plane still serves SDK traffic, so nothing else changes for your applications.
From now on, use `-f docker-compose.control-plane.yml` for every `docker compose` command on this
host. **Nodes** now appears in the sidebar for users with `org.manage`.

## 3. Add workers and wait until they are Healthy

For each worker (for example `worker-1` to `worker-3`):

1. On the Nodes page, click **Add worker**, enter the name and copy the `.env` lines. The token is
   shown once.
2. On the worker's host, download `docker-compose.worker.yml`, `Caddyfile.worker` and
   `.env.worker.example` from the **same release** as the control plane, then:

   ```bash
   cp .env.worker.example .env
   # Set MEGOOFLAGS_CONTROL_PLANE_URL=https://flags.example.com, MEGOOFLAGS_NODE_TOKEN=mf_node_...
   # and MEGOOFLAGS_VERSION to the control plane's release.
   docker compose -f docker-compose.worker.yml up -d
   curl -fsS http://localhost/readyz           # {"status":"ready"}
   ```

3. Wait until the worker shows **Healthy** on the Nodes page.

Then set up the load balancer from [`docs/examples/multi-node/`](https://github.com/mmhy2003/MegooFlags/tree/main/docs/examples/multi-node) with your
workers' addresses, and check it before any application uses it:

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $SERVER_SDK_KEY" https://sdk.example.com/sdk/v1/ruleset   # 200
```

## 4. Point the SDK hostname at the load balancer

Point the `sdk.example.com` DNS record at the load balancer and change your applications' SDK base
URL from `https://flags.example.com` to `https://sdk.example.com`, one application at a time, with
their normal deploys. The control plane and the workers serve the same rulesets with the same ETags,
so an SDK that moves keeps its cache and only reconnects its stream.

If you would rather keep the SDKs on `flags.example.com`, put the load balancer in front of that
hostname instead: send `/sdk/` and `/ofrep/` to the workers and everything else to the control plane
(the samples answer 404 for those other paths; route them to the control plane instead), then move
the DNS record to the load balancer.

Applications that still use the control plane's URL keep working: the control plane serves SDK
traffic as before.

## Rolling back

Rolling back is the same steps in reverse, also without downtime:

1. Point the SDKs back at `https://flags.example.com` (or the DNS record back at the control plane),
   and wait until the workers' SDK streams have moved: on the Nodes page, each worker's SDK streams
   drop to 0.
2. Stop the workers. On each worker's host:

   ```bash
   docker compose -f docker-compose.worker.yml down -v
   ```

   Then revoke and remove each worker on the Nodes page. Events a worker still had buffered are
   lost with `-v`; leave it out and start the worker again later if you need them forwarded.

3. Make the control plane a single node again (only `api` is recreated):

   ```bash
   docker compose up -d
   curl -fsS https://flags.example.com/healthz    # {"status":"ok","version":"0.1.0","role":"standalone"}
   ```

The node registry stays in the database and the Nodes page is hidden while the node is
`standalone`. Going back to an older release is a separate step: follow that release's upgrade
notes in `CHANGELOG.md`.
