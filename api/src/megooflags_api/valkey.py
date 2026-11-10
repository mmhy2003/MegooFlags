import asyncio
import logging
from urllib.parse import urlsplit, urlunsplit

from valkey.asyncio import Valkey

VALKEY_TIMEOUT = 2.0

logger = logging.getLogger(__name__)


def create_valkey(url: str) -> Valkey:
    # No I/O: from_url connects lazily on the first command.
    client: Valkey = Valkey.from_url(
        url,
        socket_connect_timeout=VALKEY_TIMEOUT,
        socket_timeout=VALKEY_TIMEOUT,
        health_check_interval=15,
    )
    return client


def redacted_url(url: str) -> str:
    """The URL without user or password, safe to log."""
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


async def check_valkey(client: Valkey, url: str) -> None:
    try:
        async with asyncio.timeout(VALKEY_TIMEOUT + 1):
            await client.ping()
    except Exception as exc:
        logger.error(
            "cannot reach Valkey at %s: set MEGOOFLAGS_VALKEY_URL to a running Valkey",
            redacted_url(url),
        )
        raise RuntimeError(f"cannot reach Valkey at {redacted_url(url)}") from exc


async def valkey_ready(client: Valkey) -> bool:
    try:
        async with asyncio.timeout(VALKEY_TIMEOUT + 1):
            await client.ping()
    except Exception:
        return False
    return True
