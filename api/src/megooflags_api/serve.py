"""Container entry point: `python -m megooflags_api.serve`.

Runs uvicorn with `MEGOOFLAGS_API_WORKERS` worker processes (0, the default, means one per CPU, at
most `MAX_AUTO_WORKERS`). One process serves a few hundred SDK requests per second, so several
workers are what lets one container take thousands. Workers keep their own caches and streams and
stay in sync through Valkey, exactly like API replicas.

Valkey is checked here, before any worker starts, so the API still refuses to start without it
instead of uvicorn restarting failing workers forever. With several workers, Prometheus metrics go
to a shared directory so `/metrics` reports the whole container.
"""

import asyncio
import os
import sys
import tempfile
from collections.abc import MutableMapping
from pathlib import Path

import uvicorn

from megooflags_api.settings import Settings
from megooflags_api.valkey import check_valkey, create_valkey

MAX_AUTO_WORKERS = 8
LOG_CONFIG = Path("/app/log_config.json")
ENV_PREFIX = "MEGOOFLAGS_"


def worker_count(configured: int, cpus: int | None) -> int:
    """The configured count, or one worker per CPU (at most MAX_AUTO_WORKERS) when it is 0."""
    if configured > 0:
        return configured
    return max(1, min(cpus or 1, MAX_AUTO_WORKERS))


def available_cpus() -> int | None:
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count()


async def _check_valkey(url: str) -> None:
    client = create_valkey(url)
    try:
        await check_valkey(client, url)
    finally:
        await client.aclose()


def main(environ: MutableMapping[str, str] = os.environ) -> None:
    settings = Settings.model_validate(
        {
            k.removeprefix(ENV_PREFIX).lower(): v
            for k, v in environ.items()
            if k.startswith(ENV_PREFIX)
        }
    )
    try:
        asyncio.run(_check_valkey(settings.valkey_url))
    except RuntimeError:
        sys.exit(1)  # check_valkey logged why
    workers = worker_count(settings.api_workers, available_cpus())
    if workers > 1 and not environ.get("PROMETHEUS_MULTIPROC_DIR"):
        environ["PROMETHEUS_MULTIPROC_DIR"] = tempfile.mkdtemp(prefix="megooflags-metrics-")
    uvicorn.run(
        "megooflags_api.main:app",
        host="0.0.0.0",
        port=8000,
        workers=workers,
        proxy_headers=True,
        access_log=False,
        timeout_graceful_shutdown=5,
        log_config=str(LOG_CONFIG) if LOG_CONFIG.exists() else None,
    )


if __name__ == "__main__":
    main()
