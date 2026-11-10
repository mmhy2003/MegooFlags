"""Client-key rate limit: a fixed one-minute window per key and client IP, shared through Valkey."""

import logging
import time
import uuid
from collections.abc import Callable

from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

from megooflags_api import metrics

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(
        self, client: Valkey, limit_per_minute: int, clock: Callable[[], float] = time.time
    ) -> None:
        self._client = client
        self._limit = limit_per_minute
        self._clock = clock

    async def hit(self, key_id: uuid.UUID, client_ip: str) -> int | None:
        """Count one request. Returns the Retry-After seconds when over the limit, else None.

        Fails open: when Valkey cannot be reached the request is allowed, logged and counted.
        """
        now = self._clock()
        key = f"megooflags:rl:{key_id}:{client_ip}:{int(now // 60)}"
        try:
            async with self._client.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, 120)
                count, _ = await pipe.execute()
        except (ValkeyError, OSError, TimeoutError):
            logger.warning("client rate limit check failed; allowing the request", exc_info=True)
            metrics.RATE_LIMIT_ERRORS.inc()
            return None
        if int(count) > self._limit:
            return max(1, 60 - int(now % 60))
        return None
