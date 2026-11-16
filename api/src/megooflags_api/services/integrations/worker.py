"""The delivery worker: sends due deliveries and schedules their retries.

It works only while this process is the scheduler leader. A claimed delivery is leased until
the timeout plus 30 s, so a send cut short by a leader change is tried again: delivery is at
least once, and receivers dedupe on the event id.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api import __version__, metrics
from megooflags_api.crypto import SecretBox
from megooflags_api.models import Integration, IntegrationDelivery
from megooflags_api.models.enums import DeliveryStatus, IntegrationKind
from megooflags_api.services.integrations.format import render
from megooflags_api.services.integrations.net import PostResult, post_json
from megooflags_api.services.scheduler import JobResult, now_utc
from megooflags_api.settings import Settings

logger = logging.getLogger(__name__)

RETRY_DELAYS = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(hours=1),
    timedelta(hours=6),
)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
BATCH = 10
LEASE_MARGIN = timedelta(seconds=30)
# Errors that another attempt cannot fix.
PERMANENT_ERRORS = ("unsafe address", "cannot decrypt")
PAUSED = "the integration is paused"


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def retryable(result: PostResult) -> bool:
    """A network error, a timeout, 408, 429 or 5xx. A refused address is not retried."""
    if result.status is None:
        return not (result.error or "").startswith(PERMANENT_ERRORS)
    return result.status in (408, 429) or result.status >= 500


class DeliveryWorker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        secret_box: SecretBox | None,
        *,
        is_leader: Callable[[], bool],
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = now_utc,
        poll_seconds: float = 1.0,
        concurrency: int = 10,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.secret_box = secret_box
        self.is_leader = is_leader
        self.transport = transport
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.concurrency = concurrency

    @property
    def lease(self) -> timedelta:
        """How long a claimed delivery is left to the send in progress."""
        return timedelta(seconds=self.settings.webhook_timeout_seconds) + LEASE_MARGIN

    async def run(self) -> None:
        while True:
            try:
                if self.is_leader():
                    await self.send_due()
            except Exception:
                logger.exception("sending integration deliveries failed")
            await asyncio.sleep(self.poll_seconds)

    async def send_due(self) -> int:
        """Claims up to BATCH due deliveries, leases them, and sends them concurrently."""
        now = self.clock()
        async with self.sessionmaker() as session:
            rows = list(
                await session.scalars(
                    select(IntegrationDelivery)
                    .where(
                        IntegrationDelivery.status == DeliveryStatus.PENDING,
                        IntegrationDelivery.next_attempt_at <= now,
                    )
                    .order_by(IntegrationDelivery.next_attempt_at, IntegrationDelivery.id)
                    .limit(BATCH)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.next_attempt_at = now + self.lease
            await session.commit()
        ids = [row.id for row in rows]
        semaphore = asyncio.Semaphore(self.concurrency)

        async def send(delivery_id: uuid.UUID) -> None:
            async with semaphore:
                try:
                    await self.deliver(delivery_id)
                except Exception:
                    logger.exception("integration delivery %s failed", delivery_id)

        await asyncio.gather(*(send(delivery_id) for delivery_id in ids))
        return len(ids)

    async def deliver(
        self, delivery_id: uuid.UUID, *, even_if_paused: bool = False
    ) -> IntegrationDelivery:
        """One attempt now; the delivery afterwards. LookupError when it no longer exists.

        A delivery of a paused integration is not sent: it ends failed, and can be sent again
        once the integration is resumed. `even_if_paused` is for the test send."""
        if self.secret_box is None:
            raise RuntimeError("MEGOOFLAGS_SECRET_KEY is not set")
        async with self.sessionmaker() as session:
            found = (
                await session.execute(
                    select(IntegrationDelivery, Integration)
                    .join(Integration, Integration.id == IntegrationDelivery.integration_id)
                    .where(IntegrationDelivery.id == delivery_id)
                )
            ).first()
            if found is None:
                raise LookupError(f"delivery {delivery_id} not found")
            delivery, integration = found
            if not integration.enabled and not even_if_paused:
                delivery.status = DeliveryStatus.FAILED
                delivery.next_attempt_at = None
                delivery.last_error = PAUSED
                await session.commit()
                return delivery
            # End the read transaction: no locks or open transaction during the request.
            await session.commit()

            result = await self._post(delivery, integration)
            now = self.clock()
            delivery.attempts += 1
            delivery.last_status_code = result.status
            delivery.last_error = result.error
            delivery.response_body = result.body if result.status is not None else None
            delivery.duration_ms = result.duration_ms
            ok = result.status is not None and 200 <= result.status < 300
            if ok:
                delivery.status = DeliveryStatus.DELIVERED
                delivery.delivered_at = now
                delivery.next_attempt_at = None
                outcome = "delivered"
            elif retryable(result) and delivery.attempts < MAX_ATTEMPTS:
                delivery.next_attempt_at = now + RETRY_DELAYS[delivery.attempts - 1]
                outcome = "retry"
            else:
                delivery.status = DeliveryStatus.FAILED
                delivery.next_attempt_at = None
                outcome = "failed"
            integration.last_delivery_at = now
            integration.last_delivery_ok = ok
            await session.commit()
        metrics.INTEGRATION_DELIVERIES.labels(kind=integration.kind.value, outcome=outcome).inc()
        return delivery

    async def _post(self, delivery: IntegrationDelivery, integration: Integration) -> PostResult:
        assert self.secret_box is not None
        try:
            url = self.secret_box.decrypt(integration.url_encrypted)
            secret = (
                None
                if integration.secret_encrypted is None
                else self.secret_box.decrypt(integration.secret_encrypted)
            )
        except ValueError as exc:
            return PostResult(None, "", 0, str(exc))  # "cannot decrypt the stored secret"
        body = json.dumps(
            render(integration.kind, delivery.payload), separators=(",", ":")
        ).encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"MegooFlags-Webhook/{__version__}",
            "X-MegooFlags-Event": delivery.event_type,
            "X-MegooFlags-Delivery": str(delivery.id),
        }
        if integration.kind is IntegrationKind.WEBHOOK and secret is not None:
            headers["X-MegooFlags-Signature"] = sign(secret, int(self.clock().timestamp()), body)
        return await post_json(
            url,
            body,
            headers,
            allow_private=self.settings.webhook_allow_private,
            timeout=self.settings.webhook_timeout_seconds,
            transport=self.transport,
        )


async def run_delivery_retention(
    sessionmaker: async_sessionmaker[AsyncSession], now: datetime, *, days: int
) -> JobResult:
    """Deletes delivered and failed deliveries created more than `days` ago."""
    async with sessionmaker() as session:
        result = await session.execute(
            delete(IntegrationDelivery).where(
                IntegrationDelivery.status != DeliveryStatus.PENDING,
                IntegrationDelivery.created_at < now - timedelta(days=days),
            )
        )
        await session.commit()
    return JobResult(ok=int(result.rowcount))  # type: ignore[attr-defined]
