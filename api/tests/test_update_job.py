"""The daily update check: when it asks GitHub, and what it keeps from the answer."""

import httpx
from fastapi import FastAPI
from helpers import FakeClock

from megooflags_api.models import UpdateStatus
from megooflags_api.services.scheduler import JobResult
from megooflags_api.services.updates import available_update, run_update_check


class Releases:
    """A MockTransport handler that counts requests and answers with `status` and `tag`."""

    def __init__(self, status: int = 200, tag: str = "v1.1.0") -> None:
        self.status = status
        self.tag = tag
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, json={"tag_name": self.tag})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


async def status(app: FastAPI) -> UpdateStatus:
    async with app.state.sessionmaker() as session:
        row = await session.get(UpdateStatus, 1)
        assert row is not None
        return row


async def check(app: FastAPI, clock: FakeClock, releases: Releases) -> JobResult:
    return await run_update_check(
        app.state.sessionmaker, clock(), transport=releases.transport, running="1.0.0"
    )


async def test_first_run_fetches_and_stores(app: FastAPI) -> None:
    clock = FakeClock()
    releases = Releases()

    result = await check(app, clock, releases)

    assert releases.calls == 1
    assert (result.ok, result.failed) == (1, 0)
    row = await status(app)
    assert row.latest_version == "1.1.0"
    assert row.checked_at == row.attempted_at == clock()


async def test_not_again_within_24h(app: FastAPI) -> None:
    clock = FakeClock()
    releases = Releases()
    await check(app, clock, releases)

    clock.advance(hours=23, minutes=59)
    result = await check(app, clock, releases)
    assert releases.calls == 1
    assert (result.ok, result.failed) == (0, 0)

    clock.advance(minutes=1)
    await check(app, clock, releases)
    assert releases.calls == 2


async def test_failure_retries_after_1h_and_keeps_last_version(app: FastAPI) -> None:
    clock = FakeClock()
    releases = Releases()
    await check(app, clock, releases)
    checked = clock()

    clock.advance(hours=24)
    releases.status = 500
    result = await check(app, clock, releases)
    assert releases.calls == 2
    assert (result.ok, result.failed) == (0, 1)
    row = await status(app)
    assert row.latest_version == "1.1.0"
    assert row.checked_at == checked
    assert row.attempted_at == clock()

    clock.advance(minutes=59)
    await check(app, clock, releases)
    assert releases.calls == 2

    clock.advance(minutes=1)
    await check(app, clock, releases)
    assert releases.calls == 3


async def test_404_is_success_without_version(app: FastAPI) -> None:
    clock = FakeClock()
    releases = Releases()
    await check(app, clock, releases)

    clock.advance(hours=24)
    releases.status = 404
    result = await check(app, clock, releases)

    assert (result.ok, result.failed) == (1, 0)
    row = await status(app)
    assert row.latest_version == "1.1.0"
    assert row.checked_at == row.attempted_at == clock()


async def test_available_update(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        assert await available_update(session, "1.0.0") is None

    await check(app, FakeClock(), Releases(tag="v1.1.0"))

    async with app.state.sessionmaker() as session:
        assert await available_update(session, "1.0.0") == "1.1.0"
        assert await available_update(session, "1.1.0") is None
        assert await available_update(session, "1.1.0-rc.1") == "1.1.0"
