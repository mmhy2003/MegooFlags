"""Races between two transactions, driven through the services with two sessions.

Transaction 1 does its write and stays open; transaction 2 starts and must wait for it, so it
sees what transaction 1 committed. Without the locks transaction 2 finishes on its own snapshot.
"""

import asyncio
from collections.abc import Awaitable

import httpx
import pytest
from fastapi import FastAPI
from helpers import create_flag, create_project
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.errors import ApiError
from megooflags_api.models import FlagEnvironment
from megooflags_api.schemas.flags import FlagCreate
from megooflags_api.schemas.projects import EnvironmentCreate
from megooflags_api.schemas.targeting import FlagConfigIn
from megooflags_api.services import environments, flags, lookup, targeting


async def finishes_alone[T](task: Awaitable[T]) -> asyncio.Task[T]:
    """Start `task` and give it time to finish if nothing blocks it."""
    started = asyncio.ensure_future(task)
    await asyncio.wait([started], timeout=0.5)
    return started


async def test_flag_created_during_environment_creation_gets_a_row(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    async with app.state.sessionmaker() as s1, app.state.sessionmaker() as s2:
        project1 = await lookup.get_project(s1, "demo")
        env = await environments.create_environment(
            s1, project1, EnvironmentCreate(key="qa", name="QA")
        )

        async def create() -> None:
            project2 = await lookup.get_project(s2, "demo")
            await flags.create_flag(s2, project2, FlagCreate(key="f", name="f", type="boolean"))
            await s2.commit()

        second = await finishes_alone(create())
        await s1.commit()
        await second

    async with app.state.sessionmaker() as s:
        rows = await s.scalars(
            select(FlagEnvironment).where(FlagEnvironment.environment_id == env.id)
        )
        assert len(rows.all()) == 1


async def test_environment_created_during_flag_creation_gets_a_row(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    async with app.state.sessionmaker() as s1, app.state.sessionmaker() as s2:
        project1 = await lookup.get_project(s1, "demo")
        await flags.create_flag(s1, project1, FlagCreate(key="f", name="f", type="boolean"))

        async def create() -> None:
            project2 = await lookup.get_project(s2, "demo")
            await environments.create_environment(
                s2, project2, EnvironmentCreate(key="qa", name="QA")
            )
            await s2.commit()

        second = await finishes_alone(create())
        await s1.commit()
        await second

    r = await api.get("/api/projects/demo/flags/f/environments/qa")
    assert r.status_code == 200, r.text


async def put_prerequisite(session: AsyncSession, flag_key: str, prereq: str) -> None:
    project = await lookup.get_project(session, "demo")
    flag = await lookup.get_flag(session, project, flag_key)
    env = await lookup.get_environment(session, project, "production")
    fe = await lookup.get_flag_environment(session, flag, env, lock=True)
    body = FlagConfigIn(
        version=fe.version,
        enabled=False,
        fallthrough={"variation": "false"},
        prerequisites=[{"flag": prereq, "variation": "true"}],
    )
    await targeting.replace_config(session, flag, fe, body)


async def test_concurrent_prerequisites_cannot_make_a_cycle(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api, key="a")
    await create_flag(api, key="b")
    async with app.state.sessionmaker() as s1, app.state.sessionmaker() as s2:
        await put_prerequisite(s1, "a", "b")

        async def put() -> None:
            await put_prerequisite(s2, "b", "a")
            await s2.commit()

        second = await finishes_alone(put())
        await s1.commit()
        with pytest.raises(ApiError) as exc:
            await second
    assert exc.value.code == "prerequisite_cycle"


async def test_archive_waits_for_a_new_prerequisite_on_the_flag(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api, key="a")
    await create_flag(api, key="b")
    async with app.state.sessionmaker() as s1, app.state.sessionmaker() as s2:
        await put_prerequisite(s1, "a", "b")

        async def archive() -> None:
            project = await lookup.get_project(s2, "demo")
            await flags.archive_flag(s2, await lookup.get_flag(s2, project, "b"))
            await s2.commit()

        second = await finishes_alone(archive())
        await s1.commit()
        with pytest.raises(ApiError) as exc:
            await second
    assert exc.value.code == "in_use"


async def test_prerequisite_waits_for_an_archive_of_the_flag(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api, key="a")
    await create_flag(api, key="b")
    async with app.state.sessionmaker() as s1, app.state.sessionmaker() as s2:
        project = await lookup.get_project(s1, "demo")
        await flags.archive_flag(s1, await lookup.get_flag(s1, project, "b"))

        async def put() -> None:
            await put_prerequisite(s2, "a", "b")
            await s2.commit()

        second = await finishes_alone(put())
        await s1.commit()
        with pytest.raises(ApiError) as exc:
            await second
    assert exc.value.code == "invalid_reference"
