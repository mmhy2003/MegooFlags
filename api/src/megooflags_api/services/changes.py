"""Change fan-out: every write publishes the project id on a Valkey channel after its commit, and
every replica dispatches the messages it receives to the caches and streams."""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

from megooflags_api import metrics

CHANNEL = "megooflags:changes"
PUBLISH_TIMEOUT = 2.0
BACKOFF_MIN = 0.5
BACKOFF_MAX = 5.0

logger = logging.getLogger(__name__)


class ChangeBus:
    def __init__(self, client: Valkey, url: str) -> None:
        self._client = client
        self._url = url
        self.on_project: list[Callable[[uuid.UUID], None]] = []
        self.on_key_revoked: list[Callable[[uuid.UUID], None]] = []
        self.on_resubscribe: list[Callable[[], Awaitable[None]]] = []

    async def publish(self, project_id: uuid.UUID) -> None:
        """Dispatch locally first, so this replica never waits for the Valkey round trip."""
        message = {"kind": "project", "id": str(project_id)}
        self.dispatch(json.dumps(message))
        await self._send(message)

    async def publish_key_revoked(self, key_id: uuid.UUID) -> None:
        message = {"kind": "key", "id": str(key_id)}
        self.dispatch(json.dumps(message))
        await self._send(message)

    async def _send(self, message: dict[str, str]) -> None:
        try:
            async with asyncio.timeout(PUBLISH_TIMEOUT):
                await self._client.publish(CHANNEL, json.dumps(message))
        except Exception:
            logger.warning(
                "change publish failed; the periodic re-check delivers it", exc_info=True
            )
            metrics.CHANGE_PUBLISH_ERRORS.inc()

    def dispatch(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
            kind, id = message["kind"], uuid.UUID(message["id"])
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.warning("ignoring malformed change message %r", raw)
            return
        if kind == "project":
            callbacks = self.on_project
        elif kind == "key":
            callbacks = self.on_key_revoked
        else:
            logger.warning("ignoring change message of unknown kind %r", kind)
            return
        for callback in callbacks:
            callback(id)

    async def run(self) -> None:
        """Listen forever, reconnecting with backoff; each (re)subscribe runs `on_resubscribe`."""
        backoff = BACKOFF_MIN
        while True:
            sub: Valkey = Valkey.from_url(
                self._url, socket_connect_timeout=2, socket_timeout=None, health_check_interval=15
            )
            try:
                async with sub.pubsub() as pubsub:
                    await pubsub.subscribe(CHANNEL)
                    backoff = BACKOFF_MIN
                    for callback in self.on_resubscribe:
                        try:
                            await callback()
                        except Exception:
                            logger.exception("resubscribe callback failed")
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            self.dispatch(message["data"])
            except (ValkeyError, OSError, TimeoutError):
                logger.warning("change subscription lost; reconnecting", exc_info=True)
            finally:
                await sub.aclose()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)


def get_change_bus(request: Request) -> ChangeBus:
    bus: ChangeBus = request.app.state.change_bus
    return bus


ChangesDep = Annotated[ChangeBus, Depends(get_change_bus)]
