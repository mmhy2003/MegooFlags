import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from conftest import close_app
from fastapi import FastAPI
from helpers import SseEvent, bearer, next_events, reset_database, running, seed_service_token
from sqlalchemy import select, update
from test_sdk import FLAGS, setup_demo

from megooflags_api import __version__
from megooflags_api.main import create_app
from megooflags_api.models import Environment, Flag, FlagEnvironment
from megooflags_api.services.cluster.node_hub import NodeStream
from megooflags_api.services.cluster.signing import SnapshotSigner
from megooflags_api.services.cluster.snapshot_format import (
    FORMAT,
    SignedSnapshot,
    SnapshotHeader,
)
from megooflags_api.settings import Settings

STREAM = "/api/cluster/v1/stream"

pytestmark = pytest.mark.usefixtures("valkey_up")


@pytest.fixture
async def cp_app(migrated: None) -> AsyncIterator[FastAPI]:
    app = create_app(
        Settings(role="control-plane", sse_heartbeat_seconds=1, stream_recheck_seconds=1)
    )
    await reset_database(app)
    try:
        yield app
    finally:
        await app.state.ruleset_cache.aclose()
        await close_app(app)


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """The served control plane, a client with an org admin service token, and the demo data."""
    headers = bearer(await seed_service_token(app))
    async with (
        running(app) as url,
        httpx.AsyncClient(base_url=url, timeout=10, headers=headers) as client,
    ):
        await setup_demo(client)
        yield client


async def add_node(client: httpx.AsyncClient, name: str = "worker-1") -> dict[str, Any]:
    r = await client.post("/api/nodes", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


@asynccontextmanager
async def stream(
    client: httpx.AsyncClient, token: str, body: dict[str, Any] | None = None
) -> AsyncIterator[AsyncIterator[str]]:
    headers = {"Authorization": f"Bearer {token}", "X-MegooFlags-Version": __version__}
    async with httpx.AsyncClient(base_url=client.base_url, timeout=10) as worker:
        async with worker.stream("POST", STREAM, json=body or {}, headers=headers) as r:
            assert r.status_code == 200, await r.aread()
            assert r.headers["content-type"].startswith("text/event-stream")
            lines = r.aiter_lines()
            assert await anext(lines) == "retry: 3000"
            assert await anext(lines) == ""
            yield lines


async def environment_ids(app: FastAPI) -> set[uuid.UUID]:
    async with app.state.sessionmaker() as session:
        return set(await session.scalars(select(Environment.id)))


async def initial(app: FastAPI, lines: AsyncIterator[str], puts: int) -> list[SseEvent]:
    """hello, `puts` puts, then synced."""
    events = await next_events(lines, puts + 2, 5)
    assert [e.event for e in events] == ["hello"] + ["put"] * puts + ["synced"]
    return events


async def next_frame(lines: AsyncIterator[str], within: float) -> SseEvent:
    """The next event that is not a `: ping` comment."""
    async with asyncio.timeout(within):
        while True:
            (event,) = await next_events(lines, 1, within)
            if not event.comment:
                return event


def snapshot_of(event: SseEvent) -> SignedSnapshot:
    assert event.event == "put"
    snap = SignedSnapshot.from_bytes(event.data.encode())
    assert event.id == str(snap.header.version)
    return snap


async def test_first_connect_hello_puts_synced(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        env_ids = await environment_ids(cp_app)
        async with stream(client, node["token"]) as lines:
            hello, *puts, synced = await initial(cp_app, lines, len(env_ids))

    assert json.loads(hello.data) == {
        "version": __version__,
        "public_key": cp_app.state.snapshot_signer.public_key,
        "persist_context": False,
        "state": "active",
        "ping_seconds": 1,
    }
    snaps = [snapshot_of(put) for put in puts]
    assert all(snap.verify(json.loads(hello.data)["public_key"]) for snap in snaps)
    assert {snap.header.environment_id for snap in snaps} == env_ids
    assert {snap.header.project_key for snap in snaps} == {"demo"}
    max_version = max(snap.header.version for snap in snaps)
    assert json.loads(synced.data) == {"snapshot_version": max_version}


async def versions_of(app: FastAPI, client: httpx.AsyncClient, token: str) -> dict[str, int]:
    count = len(await environment_ids(app))
    async with stream(client, token) as lines:
        _, *puts, _ = await initial(app, lines, count)
    return {str(s.header.environment_id): s.header.version for s in map(snapshot_of, puts)}


async def test_versions_already_current_get_only_synced(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        versions = await versions_of(cp_app, client, node["token"])
        body = {"versions": versions, "public_key": cp_app.state.snapshot_signer.public_key}
        async with stream(client, node["token"], body) as lines:
            hello, synced = await next_events(lines, 2, 5)
            ping = await next_events(lines, 1, 2)

    assert (hello.event, synced.event) == ("hello", "synced")
    assert json.loads(synced.data) == {"snapshot_version": max(versions.values())}
    assert ping == [SseEvent(event=None, data="ping", id=None, comment=True)]


async def test_key_mismatch_resends_everything(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        versions = await versions_of(cp_app, client, node["token"])
        other = SnapshotSigner("o" * 32).public_key
        async with stream(client, node["token"], {"versions": versions, "public_key": other}) as s:
            _, *puts, _ = await initial(cp_app, s, len(versions))

    assert {str(snapshot_of(p).header.environment_id) for p in puts} == set(versions)


async def test_removed_environment_sends_remove(cp_app: FastAPI) -> None:
    gone = uuid.uuid4()
    async with serving(cp_app) as client:
        node = await add_node(client)
        count = len(await environment_ids(cp_app))
        async with stream(client, node["token"], {"versions": {str(gone): 5}}) as lines:
            events = await next_events(lines, count + 3, 5)

    assert [e.event for e in events] == ["hello"] + ["put"] * count + ["remove", "synced"]
    assert json.loads(events[-2].data) == {"environment_id": str(gone)}


async def production_id(app: FastAPI) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        env = await session.scalar(select(Environment).where(Environment.key == "production"))
        assert env is not None
        return env.id


async def test_flag_change_sends_put_under_1_s(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        count = len(await environment_ids(cp_app))
        production = await production_id(cp_app)
        async with stream(client, node["token"]) as lines:
            _, *puts, _ = await initial(cp_app, lines, count)
            before = {s.header.environment_id: s.header.version for s in map(snapshot_of, puts)}
            r = await client.post(f"{FLAGS}/color/environments/production/kill")
            assert r.status_code == 200, r.text
            put = snapshot_of(await next_frame(lines, 1))

    assert put.header.environment_id == production
    assert put.header.version > max(before.values())
    assert json.loads(put.ruleset)["flags"]["color"]["enabled"] is False


async def test_environment_delete_sends_remove(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        count = len(await environment_ids(cp_app))
        production = await production_id(cp_app)
        async with stream(client, node["token"]) as lines:
            await initial(cp_app, lines, count)
            r = await client.delete("/api/projects/demo/environments/production")
            assert r.status_code == 204, r.text
            remove = await next_frame(lines, 2)

    assert remove.event == "remove"
    assert json.loads(remove.data) == {"environment_id": str(production)}


async def test_drain_and_resume_send_state(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        count = len(await environment_ids(cp_app))
        async with stream(client, node["token"]) as lines:
            await initial(cp_app, lines, count)
            r = await client.post(f"/api/nodes/{node['id']}/drain")
            assert r.status_code == 200, r.text
            drained = await next_frame(lines, 2)
            r = await client.post(f"/api/nodes/{node['id']}/resume")
            assert r.status_code == 200, r.text
            resumed = await next_frame(lines, 2)

    assert (drained.event, json.loads(drained.data)) == ("state", {"state": "draining"})
    assert (resumed.event, json.loads(resumed.data)) == ("state", {"state": "active"})


async def test_drained_node_connects_draining(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        r = await client.post(f"/api/nodes/{node['id']}/drain")
        assert r.status_code == 200, r.text
        async with stream(client, node["token"]) as lines:
            (hello,) = await next_events(lines, 1, 5)

    assert json.loads(hello.data)["state"] == "draining"


async def test_revoke_sends_revoked_and_closes(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        count = len(await environment_ids(cp_app))
        async with stream(client, node["token"]) as lines:
            await initial(cp_app, lines, count)
            r = await client.post(f"/api/nodes/{node['id']}/revoke")
            assert r.status_code == 200, r.text
            revoked = await next_frame(lines, 2)
            async with asyncio.timeout(2):
                rest = [line async for line in lines]

    assert (revoked.event, revoked.data) == ("revoked", "{}")
    assert rest == []


async def test_ping_every_heartbeat(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        count = len(await environment_ids(cp_app))
        async with stream(client, node["token"]) as lines:
            await initial(cp_app, lines, count)
            pings = await next_events(lines, 2, 2.5)

    assert pings == [SseEvent(event=None, data="ping", id=None, comment=True)] * 2


async def test_lost_change_message_caught_by_recheck(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        count = len(await environment_ids(cp_app))
        production = await production_id(cp_app)
        async with stream(client, node["token"]) as lines:
            await initial(cp_app, lines, count)
            async with cp_app.state.sessionmaker() as session:
                flag_id = await session.scalar(select(Flag.id).where(Flag.key == "color"))
                await session.execute(
                    update(FlagEnvironment)
                    .where(
                        FlagEnvironment.flag_id == flag_id,
                        FlagEnvironment.environment_id == production,
                    )
                    .values(enabled=False, version=FlagEnvironment.version + 1)
                )
                await session.commit()
            put = snapshot_of(await next_frame(lines, 3))

    assert put.header.environment_id == production
    assert json.loads(put.ruleset)["flags"]["color"]["enabled"] is False


def node_streams(text: str) -> float:
    match = re.search(r"^megooflags_node_streams (\S+)$", text, re.MULTILINE)
    assert match is not None, text
    return float(match.group(1))


async def test_node_streams_metric(cp_app: FastAPI) -> None:
    async with serving(cp_app) as client:
        node = await add_node(client)
        count = len(await environment_ids(cp_app))
        before = node_streams((await client.get("/metrics")).text)
        async with stream(client, node["token"]) as lines:
            await initial(cp_app, lines, count)
            during = node_streams((await client.get("/metrics")).text)
            assert cp_app.state.node_hub.stream_count == 1
        # The server notices the closed stream on its next write, a ping at the latest.
        for _ in range(30):
            after = node_streams((await client.get("/metrics")).text)
            if after == before:
                break
            await asyncio.sleep(0.1)

    assert during == before + 1
    assert after == before
    assert cp_app.state.node_hub.stream_count == 0


def signed(environment_id: uuid.UUID, version: int) -> SignedSnapshot:
    header = SnapshotHeader(
        format=FORMAT,
        environment_id=environment_id,
        environment_key="production",
        project_id=uuid.uuid4(),
        project_key="demo",
        version=version,
        published_at=datetime(2026, 11, 16, tzinfo=UTC),
        keys=(),
    )
    unsigned = SignedSnapshot(header=header, ruleset=b'{"flags":{}}', signature="")
    signature = SnapshotSigner("s" * 32).sign(unsigned.payload())
    return SignedSnapshot(header, unsigned.ruleset, signature)


async def test_node_stream_keeps_newest_per_environment() -> None:
    stream = NodeStream(uuid.uuid4(), uuid.uuid4())
    a, b = uuid.uuid4(), uuid.uuid4()
    stream.offer_put(signed(a, 1))
    stream.offer_put(signed(b, 2))
    stream.offer_put(signed(a, 3))
    stream.offer_state("draining")
    stream.offer_state("active")

    events = await stream.next(0.1)

    assert events is not None
    assert [(e.event, e.environment_id, e.version) for e in events] == [
        ("put", b, 2),
        ("put", a, 3),
        ("state", None, None),
    ]
    assert json.loads(events[-1].data) == {"state": "active"}
    assert await stream.next(0.05) is None


async def test_node_stream_remove_replaces_put() -> None:
    stream = NodeStream(uuid.uuid4(), uuid.uuid4())
    a = uuid.uuid4()
    stream.offer_put(signed(a, 1))
    stream.offer_remove(a)

    events = await stream.next(0.1)

    assert events is not None
    assert [(e.event, e.frame()) for e in events] == [
        ("remove", f'event: remove\ndata: {{"environment_id":"{a}"}}\n\n'.encode()),
    ]


async def test_revoked_is_final() -> None:
    stream = NodeStream(uuid.uuid4(), uuid.uuid4())
    stream.offer_put(signed(uuid.uuid4(), 1))
    stream.offer_revoked()
    stream.offer_put(signed(uuid.uuid4(), 2))
    stream.offer_state("draining")

    events = await stream.next(0.1)

    assert events is not None
    assert [(e.event, e.frame()) for e in events] == [("revoked", b"event: revoked\ndata: {}\n\n")]
    stream.offer_put(signed(uuid.uuid4(), 3))
    assert await stream.next(0.05) is None
