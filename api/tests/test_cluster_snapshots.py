import asyncio
import uuid
from typing import Any

import httpx
from fastapi import FastAPI
from helpers import create_project, create_sdk_key
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.ext.asyncio import AsyncSession
from test_sdk import FLAGS, environment, setup_demo

from megooflags_api.models import EnvironmentSnapshot
from megooflags_api.services.cluster.signing import SnapshotSigner
from megooflags_api.services.cluster.snapshot_format import SignedSnapshot, content_digest
from megooflags_api.services.cluster.snapshots import (
    build_snapshot,
    current_snapshot_version,
    environment_refs,
)
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.ruleset_cache import CachedRuleset, RulesetCache

SIGNER = SnapshotSigner("snapshot-test-secret")


async def build(app: FastAPI, env: EnvRef, cache: RulesetCache | None = None) -> SignedSnapshot:
    async with app.state.sessionmaker() as session:
        snap = await build_snapshot(session, env, "demo", cache or app.state.ruleset_cache, SIGNER)
    assert snap is not None
    return snap


async def stored(app: FastAPI, environment_id: uuid.UUID) -> EnvironmentSnapshot:
    async with app.state.sessionmaker() as session:
        row = await session.get(EnvironmentSnapshot, environment_id)
    assert row is not None
    return row


async def toggle(api: httpx.AsyncClient, enabled: bool) -> None:
    r = await api.post(f"{FLAGS}/new-ui/environments/development/toggle", json={"enabled": enabled})
    assert r.status_code == 200, r.text


async def test_build_signs_exact_ruleset_bytes(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    env = await environment(app, "development")

    snap = await build(app, env)

    async with app.state.sessionmaker() as session:
        entry = await app.state.ruleset_cache.get_entry(session, env, max_age=0)
    assert snap.ruleset == entry.body
    assert snap.verify(SIGNER.public_key)
    assert snap.header.env_ref() == env
    assert snap.header.project_key == "demo"
    assert snap.header.version > 0
    row = await stored(app, env.id)
    assert (row.version, row.published_at) == (snap.header.version, snap.header.published_at)
    assert row.digest == content_digest(snap.header, snap.ruleset)


async def test_unchanged_content_keeps_version(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    env = await environment(app, "development")

    first = await build(app, env)
    second = await build(app, env)

    assert second.header == first.header
    assert second.ruleset == first.ruleset


async def test_flag_change_bumps_version(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    env = await environment(app, "development")
    first = await build(app, env)

    await toggle(api, False)
    second = await build(app, env)

    assert second.header.version > first.header.version
    assert second.header.published_at >= first.header.published_at
    assert second.ruleset != first.ruleset
    assert second.verify(SIGNER.public_key)


async def test_key_revoke_and_create_change_header_and_version(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await setup_demo(api)
    env = await environment(app)
    empty = await build(app, env)
    assert empty.header.keys == ()

    key = await create_sdk_key(api, "server")
    created = await build(app, env)
    assert created.header.version > empty.header.version
    assert created.ruleset == empty.ruleset
    [entry] = created.header.keys
    assert (entry.id, entry.kind.value, entry.revoked_at) == (uuid.UUID(key["id"]), "server", None)

    r = await api.delete(f"/api/projects/demo/environments/production/sdk-keys/{key['id']}")
    assert r.status_code == 204, r.text
    revoked = await build(app, env)
    assert revoked.header.version > created.header.version
    [entry] = revoked.header.keys
    assert entry.revoked_at is not None


async def test_versions_are_cluster_wide(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)

    development = await build(app, await environment(app, "development"))
    production = await build(app, await environment(app, "production"))

    assert production.header.version > development.header.version


async def test_environment_refs(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_project(api, key="other")
    production = await environment(app, "production")

    async with app.state.sessionmaker() as session:
        every = await environment_refs(session)
        demo = await environment_refs(session, production.project_id)

    assert [ref.id for ref, _ in every] == sorted(ref.id for ref, _ in every)
    assert {project_key for _, project_key in every} == {"demo", "other"}
    assert demo == [(ref, key) for ref, key in every if key == "demo"]
    assert (production, "demo") in demo


class PausingCache(RulesetCache):
    """Holds the first lookup, after it has read the ruleset, until `release` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.read = asyncio.Event()
        self.release = asyncio.Event()

    async def get_entry(
        self, session: AsyncSession, environment: EnvRef, max_age: float = 0.0
    ) -> CachedRuleset:
        entry = await super().get_entry(session, environment, max_age)
        if not self.read.is_set():
            self.read.set()
            await self.release.wait()
        return entry


async def test_concurrent_builds_never_go_backwards(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    env = await environment(app, "development")
    cache = PausingCache()

    first = asyncio.create_task(build(app, env, cache))
    await asyncio.wait_for(cache.read.wait(), 5)  # the first build holds the row lock
    await toggle(api, False)
    second = asyncio.create_task(build(app, env))
    await asyncio.sleep(0.2)
    assert not second.done()  # waits for the lock
    cache.release.set()
    old, new = await asyncio.wait_for(asyncio.gather(first, second), 5)

    async with app.state.sessionmaker() as session:
        newest = await app.state.ruleset_cache.get_entry(session, env, max_age=0)
    assert new.header.version > old.header.version
    assert new.ruleset == newest.body != old.ruleset
    row = await stored(app, env.id)
    assert row.version == new.header.version
    assert row.digest == content_digest(new.header, newest.body)


async def test_deleted_environment_returns_none(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    env = await environment(app, "development")
    r = await api.delete("/api/projects/demo/environments/development")
    assert r.status_code == 204, r.text

    async with app.state.sessionmaker() as session:
        snap = await build_snapshot(session, env, "demo", app.state.ruleset_cache, SIGNER)
        assert snap is None
        assert await session.scalar(select(EnvironmentSnapshot)) is None


async def test_current_snapshot_version(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    async with app.state.sessionmaker() as session:
        assert await current_snapshot_version(session) == 0

    await build(app, await environment(app, "development"))
    latest = await build(app, await environment(app, "production"))

    async with app.state.sessionmaker() as session:
        assert await current_snapshot_version(session) == latest.header.version


async def test_environment_deleted_after_row_insert_returns_none(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await setup_demo(api)
    env = await environment(app, "development")

    async with app.state.sessionmaker() as session:
        execute = session.execute

        async def deleting(statement: Any, *args: Any, **kwargs: Any) -> Any:
            result = await execute(statement, *args, **kwargs)
            if isinstance(statement, Insert):
                # As if the environment were deleted between the insert and the locking read.
                await execute(delete(EnvironmentSnapshot))
            return result

        session.execute = deleting  # type: ignore[method-assign]
        snap = await build_snapshot(session, env, "demo", app.state.ruleset_cache, SIGNER)

    assert snap is None
