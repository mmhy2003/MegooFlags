"""SSO login state in Valkey, under `megooflags:auth:sso:<kind>:<key>` for 10 minutes.

`pop_state` takes it with GETDEL, so each state works once. When Valkey cannot be reached, SSO is
unavailable (503).
"""

import json
import logging
from typing import Any

from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

from megooflags_api.errors import unavailable

logger = logging.getLogger(__name__)

PREFIX = "megooflags:auth:sso:"
STATE_SECONDS = 600
_VALKEY_ERRORS = (ValkeyError, OSError, TimeoutError)


def _name(kind: str, key: str) -> str:
    return f"{PREFIX}{kind}:{key}"


async def put_state(client: Valkey, kind: str, key: str, value: dict[str, Any]) -> None:
    try:
        await client.set(_name(kind, key), json.dumps(value), ex=STATE_SECONDS)
    except _VALKEY_ERRORS:
        logger.warning("cannot store SSO state", exc_info=True)
        raise unavailable("single sign-on is unavailable right now; try again later") from None


async def pop_state(client: Valkey, kind: str, key: str) -> dict[str, Any] | None:
    """The stored state, removed; None when unknown, used or expired."""
    try:
        raw = await client.getdel(_name(kind, key))
    except _VALKEY_ERRORS:
        logger.warning("cannot read SSO state", exc_info=True)
        raise unavailable("single sign-on is unavailable right now; try again later") from None
    if raw is None:
        return None
    value: dict[str, Any] = json.loads(raw)
    return value
