"""Sign-in and MFA throttles: failure counters in Valkey with a 15-minute window.

Sign-in allows 10 failures per account and 100 per client IP; MFA allows 10 failures per user.
Like the client rate limit, the throttle fails open: when Valkey cannot be reached the attempt is
allowed and a warning is logged.
"""

import hashlib
import logging
import uuid

from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

logger = logging.getLogger(__name__)

PREFIX = "megooflags:auth:throttle:"
WINDOW_SECONDS = 900
ACCOUNT_LIMIT = 10
IP_LIMIT = 100
MFA_LIMIT = 10

_ERRORS = (ValkeyError, OSError, TimeoutError)


def _account_key(email: str) -> str:
    return f"{PREFIX}acct:{hashlib.sha256(email.lower().encode()).hexdigest()}"


def _ip_key(ip: str) -> str:
    return f"{PREFIX}ip:{ip}"


def _mfa_key(user_id: uuid.UUID) -> str:
    return f"{PREFIX}mfa:{user_id}"


class AuthThrottle:
    def __init__(self, client: Valkey) -> None:
        self._client = client

    async def _blocked(self, limits: dict[str, int]) -> int | None:
        """Retry-After seconds when any key has reached its limit, else None."""
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                for key in limits:
                    pipe.get(key)
                    pipe.ttl(key)
                results = await pipe.execute()
        except _ERRORS:
            logger.warning("sign-in throttle check failed; allowing the attempt", exc_info=True)
            return None
        retry_after = None
        for i, limit in enumerate(limits.values()):
            count, ttl = results[2 * i], results[2 * i + 1]
            if count is not None and int(count) >= limit:
                wait = max(1, int(ttl))
                retry_after = wait if retry_after is None else max(retry_after, wait)
        return retry_after

    async def _count(self, keys: list[str]) -> None:
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                for key in keys:
                    pipe.incr(key)
                    pipe.expire(key, WINDOW_SECONDS)
                await pipe.execute()
        except _ERRORS:
            logger.warning("cannot record a failed sign-in attempt", exc_info=True)

    async def blocked(self, email: str, ip: str | None) -> int | None:
        """Retry-After seconds when the account or the IP has too many recent failures."""
        limits = {_account_key(email): ACCOUNT_LIMIT}
        if ip is not None:
            limits[_ip_key(ip)] = IP_LIMIT
        return await self._blocked(limits)

    async def failed(self, email: str, ip: str | None) -> None:
        keys = [_account_key(email)]
        if ip is not None:
            keys.append(_ip_key(ip))
        await self._count(keys)

    async def cleared(self, email: str) -> None:
        """Reset the account counter after a successful sign-in (the IP counter stays)."""
        try:
            await self._client.delete(_account_key(email))
        except _ERRORS:
            logger.warning("cannot clear the sign-in throttle", exc_info=True)

    async def mfa_blocked(self, user_id: uuid.UUID) -> int | None:
        return await self._blocked({_mfa_key(user_id): MFA_LIMIT})

    async def mfa_failed(self, user_id: uuid.UUID) -> None:
        await self._count([_mfa_key(user_id)])
