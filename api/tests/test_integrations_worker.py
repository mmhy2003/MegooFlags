"""The delivery worker: signed requests, the retry schedule and the leader-only loop."""

import asyncio
import hashlib
import hmac
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import FakeClock, create_project
from prometheus_client import REGISTRY
from sqlalchemy import select
from test_integrations_api import SLACK_URL, create_webhook
from test_integrations_api import fake_dns as fake_dns  # autouse: resolves the test hosts

from megooflags_api import __version__
from megooflags_api.models import Integration, IntegrationDelivery
from megooflags_api.models.enums import DeliveryStatus
from megooflags_api.services.integrations.format import slack_body
from megooflags_api.services.integrations.worker import (
    MAX_ATTEMPTS,
    RETRY_DELAYS,
    DeliveryWorker,
    run_delivery_retention,
    sign,
)
from megooflags_api.services.scheduler import default_jobs
from megooflags_api.settings import Settings

EVENT: dict[str, Any] = {
    "id": "evt_42",
    "type": "flag.toggle",
    "occurred_at": "2026-01-01T12:00:00Z",
    "project": "demo",
    "environment": "production",
    "resource": {"type": "flag", "key": "new-ui"},
    "actor": {"kind": "user", "label": "mona@example.com"},
    "before": {"enabled": False},
    "after": {"enabled": True},
    "url": None,
}

Handler = Callable[[httpx.Request], httpx.Response]


class Receiver:
    """A MockTransport handler that records requests and answers from a list of statuses."""

    def __init__(self, *responses: int | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(answer, text=f"answer {answer}")


def make_worker(
    app: FastAPI,
    handler: Handler,
    clock: FakeClock,
    *,
    is_leader: Callable[[], bool] = lambda: True,
    poll_seconds: float = 1.0,
) -> DeliveryWorker:
    return DeliveryWorker(
        app.state.sessionmaker,
        Settings(webhook_allow_private=True),
        app.state.secret_box,
        is_leader=is_leader,
        transport=httpx.MockTransport(handler),
        clock=clock,
        poll_seconds=poll_seconds,
    )


async def add_delivery(app: FastAPI, integration_id: str, clock: FakeClock) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        row = IntegrationDelivery(
            integration_id=uuid.UUID(integration_id),
            event_id=EVENT["id"],
            event_type=EVENT["type"],
            payload=EVENT,
            status=DeliveryStatus.PENDING,
            next_attempt_at=clock.now,
        )
        session.add(row)
        await session.commit()
        return row.id


async def load(app: FastAPI, delivery_id: uuid.UUID) -> IntegrationDelivery:
    async with app.state.sessionmaker() as session:
        row = await session.get(IntegrationDelivery, delivery_id)
        assert row is not None
        return row


async def load_integration(app: FastAPI, integration_id: str) -> Integration:
    async with app.state.sessionmaker() as session:
        row = await session.get(Integration, uuid.UUID(integration_id))
        assert row is not None
        return row


def count(kind: str, outcome: str) -> float:
    labels = {"kind": kind, "outcome": outcome}
    return REGISTRY.get_sample_value("megooflags_integration_deliveries_total", labels) or 0.0


async def test_webhook_request_is_signed(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    hook = await create_webhook(api)
    clock = FakeClock()
    receiver = Receiver(200)
    delivery_id = await add_delivery(app, hook["id"], clock)

    await make_worker(app, receiver, clock).deliver(delivery_id)

    [request] = receiver.requests
    body = request.content
    assert json.loads(body) == EVENT
    assert body == json.dumps(json.loads(body), separators=(",", ":")).encode()  # compact
    assert request.headers["content-type"] == "application/json"
    assert request.headers["user-agent"] == f"MegooFlags-Webhook/{__version__}"
    assert request.headers["x-megooflags-event"] == "flag.toggle"
    assert request.headers["x-megooflags-delivery"] == str(delivery_id)
    timestamp = int(clock.now.timestamp())
    expected = hmac.new(
        hook["secret"].encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    assert request.headers["x-megooflags-signature"] == f"t={timestamp},v1={expected}"
    assert str(request.url) == "https://hooks.example.com/services/abc/secret-hook"


def test_sign_format() -> None:
    mac = hmac.new(b"s", b"7.{}", hashlib.sha256).hexdigest()
    assert sign("s", 7, b"{}") == f"t=7,v1={mac}"


async def test_slack_gets_the_slack_body(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    hook = await create_webhook(api, kind="slack", url=SLACK_URL)
    clock = FakeClock()
    receiver = Receiver(200)
    delivery_id = await add_delivery(app, hook["id"], clock)

    await make_worker(app, receiver, clock).deliver(delivery_id)

    [request] = receiver.requests
    assert json.loads(request.content) == slack_body(EVENT)
    assert "x-megooflags-signature" not in request.headers
    assert request.headers["x-megooflags-event"] == "flag.toggle"


async def test_retry_then_delivered(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    hook = await create_webhook(api)
    clock = FakeClock()
    receiver = Receiver(500, 200)
    delivery_id = await add_delivery(app, hook["id"], clock)
    worker = make_worker(app, receiver, clock)

    first = await worker.deliver(delivery_id)
    assert first.status == DeliveryStatus.PENDING
    assert first.attempts == 1
    assert first.last_status_code == 500
    assert first.response_body == "answer 500"
    assert first.next_attempt_at == clock.now + timedelta(seconds=30)

    clock.advance(seconds=30)
    second = await worker.deliver(delivery_id)
    assert second.status == DeliveryStatus.DELIVERED
    assert second.attempts == 2
    assert second.delivered_at == clock.now
    assert second.last_status_code == 200
    assert second.duration_ms is not None

    stored = await load(app, delivery_id)
    assert stored.status == DeliveryStatus.DELIVERED
    assert stored.attempts == 2


async def test_six_failures_end_failed(app: FastAPI, api: httpx.AsyncClient) -> None:
    assert RETRY_DELAYS == (
        timedelta(seconds=30),
        timedelta(minutes=2),
        timedelta(minutes=10),
        timedelta(hours=1),
        timedelta(hours=6),
    )
    await create_project(api)
    hook = await create_webhook(api)
    clock = FakeClock()
    receiver = Receiver(500)
    delivery_id = await add_delivery(app, hook["id"], clock)
    worker = make_worker(app, receiver, clock)

    gaps = []
    for attempt in range(1, MAX_ATTEMPTS):
        delivery = await worker.deliver(delivery_id)
        assert delivery.status == DeliveryStatus.PENDING
        assert delivery.attempts == attempt
        assert delivery.next_attempt_at is not None
        gaps.append(delivery.next_attempt_at - clock.now)
        clock.now = delivery.next_attempt_at
    last = await worker.deliver(delivery_id)

    assert gaps == list(RETRY_DELAYS)
    assert last.status == DeliveryStatus.FAILED
    assert last.attempts == MAX_ATTEMPTS
    assert last.next_attempt_at is None
    assert last.delivered_at is None
    assert len(receiver.requests) == MAX_ATTEMPTS


@pytest.mark.parametrize("status", [404, 400, 301, 302])
async def test_other_statuses_fail_at_once(
    app: FastAPI, api: httpx.AsyncClient, status: int
) -> None:
    await create_project(api)
    hook = await create_webhook(api)
    clock = FakeClock()
    delivery_id = await add_delivery(app, hook["id"], clock)

    delivery = await make_worker(app, Receiver(status), clock).deliver(delivery_id)

    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.attempts == 1
    assert delivery.last_status_code == status
    assert delivery.next_attempt_at is None


@pytest.mark.parametrize(
    "answer", [408, 429, 503, httpx.ReadTimeout("slow"), httpx.ConnectError("refused")]
)
async def test_retryable_answers(
    app: FastAPI, api: httpx.AsyncClient, answer: int | Exception
) -> None:
    await create_project(api)
    hook = await create_webhook(api)
    clock = FakeClock()
    delivery_id = await add_delivery(app, hook["id"], clock)

    delivery = await make_worker(app, Receiver(answer), clock).deliver(delivery_id)

    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.next_attempt_at == clock.now + RETRY_DELAYS[0]
    if isinstance(answer, int):
        assert delivery.last_status_code == answer
        assert delivery.last_error is None
    else:
        assert delivery.last_status_code is None
        expected = "timeout" if isinstance(answer, httpx.ReadTimeout) else "refused"
        assert delivery.last_error == expected


async def test_integration_last_delivery_and_metric(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    hook = await create_webhook(api)
    clock = FakeClock()
    worker = make_worker(app, Receiver(500, 404, 200), clock)
    before = {o: count("webhook", o) for o in ("delivered", "retry", "failed")}

    retried = await add_delivery(app, hook["id"], clock)
    await worker.deliver(retried)
    integration = await load_integration(app, hook["id"])
    assert integration.last_delivery_ok is False
    assert integration.last_delivery_at == clock.now

    clock.advance(minutes=1)
    await worker.deliver(await add_delivery(app, hook["id"], clock))
    assert (await load_integration(app, hook["id"])).last_delivery_ok is False

    clock.advance(minutes=1)
    await worker.deliver(retried)
    integration = await load_integration(app, hook["id"])
    assert integration.last_delivery_ok is True
    assert integration.last_delivery_at == clock.now

    assert count("webhook", "retry") == before["retry"] + 1
    assert count("webhook", "failed") == before["failed"] + 1
    assert count("webhook", "delivered") == before["delivered"] + 1


async def test_send_due_claims_at_most_ten(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    hook = await create_webhook(api)
    clock = FakeClock()
    receiver = Receiver(200)
    ids = [await add_delivery(app, hook["id"], clock) for _ in range(12)]
    later = await add_delivery(app, hook["id"], FakeClock(clock.now + timedelta(minutes=5)))
    worker = make_worker(app, receiver, clock)

    assert await worker.send_due() == 10
    assert len(receiver.requests) == 10
    assert await worker.send_due() == 2
    assert await worker.send_due() == 0
    assert len(receiver.requests) == 12
    for delivery_id in ids:
        assert (await load(app, delivery_id)).status == DeliveryStatus.DELIVERED
    assert (await load(app, later)).status == DeliveryStatus.PENDING


async def test_claimed_deliveries_are_leased(app: FastAPI, api: httpx.AsyncClient) -> None:
    """A delivery whose send never finished is due again after the timeout plus 30 s."""
    await create_project(api)
    hook = await create_webhook(api)
    clock = FakeClock()
    delivery_id = await add_delivery(app, hook["id"], clock)
    worker = make_worker(app, Receiver(200), clock)

    async def interrupted(delivery_id: uuid.UUID) -> IntegrationDelivery:
        raise RuntimeError("the process stopped")

    worker.deliver = interrupted  # type: ignore[method-assign]
    await worker.send_due()

    delivery = await load(app, delivery_id)
    assert delivery.status == DeliveryStatus.PENDING
    assert delivery.next_attempt_at == clock.now + timedelta(seconds=10 + 30)


async def test_run_sends_only_while_leader(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    hook = await create_webhook(api)
    clock = FakeClock()
    delivery_id = await add_delivery(app, hook["id"], clock)
    leader = False
    receiver = Receiver(200)
    worker = make_worker(app, receiver, clock, is_leader=lambda: leader, poll_seconds=0.01)

    task = asyncio.create_task(worker.run())
    try:
        await asyncio.sleep(0.1)
        assert receiver.requests == []
        assert (await load(app, delivery_id)).status == DeliveryStatus.PENDING

        leader = True
        for _ in range(100):
            if (await load(app, delivery_id)).status == DeliveryStatus.DELIVERED:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(receiver.requests) == 1
    assert (await load(app, delivery_id)).status == DeliveryStatus.DELIVERED


async def test_retention_deletes_old_finished_deliveries(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    hook = await create_webhook(api)
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    old = now - timedelta(days=8)
    recent = now - timedelta(days=6)
    rows = {
        "old delivered": (DeliveryStatus.DELIVERED, old),
        "old failed": (DeliveryStatus.FAILED, old),
        "old pending": (DeliveryStatus.PENDING, old),
        "recent delivered": (DeliveryStatus.DELIVERED, recent),
    }
    ids: dict[str, uuid.UUID] = {}
    async with app.state.sessionmaker() as session:
        for name, (status, created_at) in rows.items():
            row = IntegrationDelivery(
                integration_id=uuid.UUID(hook["id"]),
                event_id=EVENT["id"],
                event_type=EVENT["type"],
                payload=EVENT,
                status=status,
                created_at=created_at,
            )
            session.add(row)
            await session.flush()
            ids[name] = row.id
        await session.commit()

    result = await run_delivery_retention(app.state.sessionmaker, now, days=7)

    assert result.ok == 2
    async with app.state.sessionmaker() as session:
        left = set(await session.scalars(select(IntegrationDelivery.id)))
    assert left == {ids["old pending"], ids["recent delivered"]}


def test_retention_is_a_default_job() -> None:
    jobs = {job.name: job for job in default_jobs(Settings(webhook_retention_days=3))}
    job = jobs["integration_retention"]
    assert job.every == timedelta(hours=1)
    assert isinstance(job.run, partial)
    assert job.run.func is run_delivery_retention
    assert job.run.keywords == {"days": 3}
