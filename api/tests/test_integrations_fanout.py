"""The fan-out job: outbox rows become one pending delivery per matching integration."""

import uuid
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from helpers import FakeClock, count_statements, create_flag, create_project, make_scheduler
from sqlalchemy import select
from test_rollouts import color_flag, start

from megooflags_api.models import (
    Environment,
    Integration,
    IntegrationDelivery,
    IntegrationEnvironment,
    IntegrationOutbox,
    Project,
)
from megooflags_api.models.enums import DeliveryStatus, EventGroup, IntegrationKind
from megooflags_api.services.audit import record_system
from megooflags_api.services.integrations.fanout import run_integration_fanout

PUBLIC = "https://flags.example.com"


async def ids(app: FastAPI, project: str = "demo") -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """The project's id and its environment ids by key."""
    async with app.state.sessionmaker() as session:
        project_id = await session.scalar(select(Project.id).where(Project.key == project))
        assert project_id is not None
        envs = {
            key: id_
            for key, id_ in await session.execute(
                select(Environment.key, Environment.id).where(Environment.project_id == project_id)
            )
        }
        return project_id, envs


async def add_integration(
    app: FastAPI,
    project_id: uuid.UUID,
    groups: list[EventGroup],
    *,
    name: str = "hook",
    environments: list[uuid.UUID] | None = None,
    enabled: bool = True,
) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        row = Integration(
            project_id=project_id,
            kind=IntegrationKind.WEBHOOK,
            name=name,
            url_encrypted="not used",
            url_hint="https://hooks.example.com/…",
            event_groups=[g.value for g in groups],
            all_environments=environments is None,
            enabled=enabled,
        )
        session.add(row)
        await session.flush()
        for env_id in environments or []:
            session.add(IntegrationEnvironment(integration_id=row.id, environment_id=env_id))
        await session.commit()
        return row.id


async def record(
    app: FastAPI,
    action: str,
    resource_type: str,
    key: str,
    project_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
) -> int:
    async with app.state.sessionmaker() as session:
        entry = await record_system(
            session,
            action,
            resource_type,
            key,
            project_id=project_id,
            environment_id=environment_id,
            after={"enabled": True},
        )
        await session.commit()
        return entry.id


async def deliveries(app: FastAPI, integration_id: uuid.UUID) -> list[IntegrationDelivery]:
    async with app.state.sessionmaker() as session:
        rows = await session.scalars(
            select(IntegrationDelivery)
            .where(IntegrationDelivery.integration_id == integration_id)
            .order_by(IntegrationDelivery.created_at, IntegrationDelivery.event_id)
        )
        return list(rows)


async def outbox_count(app: FastAPI) -> int:
    async with app.state.sessionmaker() as session:
        return len(list(await session.scalars(select(IntegrationOutbox.id))))


async def fanout(app: FastAPI, now: datetime | None = None) -> int:
    result = await run_integration_fanout(
        app.state.sessionmaker, now or datetime.now(UTC), public_url=PUBLIC
    )
    return result.ok


async def test_a_segments_integration_gets_segments_not_toggles(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    project_id, envs = await ids(app)
    hook = await add_integration(app, project_id, [EventGroup.SEGMENTS])
    await record(app, "flag.toggle", "flag", "new-ui", project_id, envs["production"])
    segment_entry = await record(app, "segment.create", "segment", "beta", project_id)
    now = datetime.now(UTC)

    assert await fanout(app, now) == 1

    [delivery] = await deliveries(app, hook)
    assert delivery.event_type == "segment.create"
    assert delivery.event_id == f"evt_{segment_entry}"
    assert delivery.audit_entry_id == segment_entry
    assert delivery.status is DeliveryStatus.PENDING
    assert delivery.next_attempt_at == now
    assert delivery.payload["project"] == "demo"
    assert delivery.payload["environment"] is None
    assert delivery.payload["url"] == f"{PUBLIC}/projects/demo/segments"


async def test_an_environment_filter_skips_other_environments(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    project_id, envs = await ids(app)
    hook = await add_integration(
        app,
        project_id,
        [EventGroup.FLAGS, EventGroup.TARGETING],
        environments=[envs["development"]],
    )
    await record(app, "flag.toggle", "flag", "new-ui", project_id, envs["production"])
    await record(app, "flag.toggle", "flag", "new-ui", project_id, envs["development"])
    await record(app, "flag.create", "flag", "new-ui", project_id)

    assert await fanout(app) == 2

    got = {(d.event_type, d.payload["environment"]) for d in await deliveries(app, hook)}
    assert got == {("flag.toggle", "development"), ("flag.create", None)}


async def test_a_paused_integration_gets_nothing(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    project_id, _ = await ids(app)
    hook = await add_integration(app, project_id, list(EventGroup), enabled=False)
    await record(app, "flag.create", "flag", "new-ui", project_id)

    assert await fanout(app) == 0
    assert await deliveries(app, hook) == []
    assert await outbox_count(app) == 0


async def test_two_integrations_get_the_same_event(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_project(api, key="other")
    project_id, _ = await ids(app)
    other_id, _ = await ids(app, "other")
    a = await add_integration(app, project_id, [EventGroup.FLAGS], name="a")
    b = await add_integration(app, project_id, [EventGroup.FLAGS], name="b")
    elsewhere = await add_integration(app, other_id, [EventGroup.FLAGS], name="c")
    await create_flag(api)

    assert await fanout(app) == 2

    [da] = await deliveries(app, a)
    [db] = await deliveries(app, b)
    assert da.event_id == db.event_id
    assert da.payload == db.payload
    assert da.payload["url"] == f"{PUBLIC}/projects/demo/flags/new-ui"
    assert await deliveries(app, elsewhere) == []


async def test_the_outbox_is_empty_afterwards(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    project_id, _ = await ids(app)
    await add_integration(app, project_id, [EventGroup.SEGMENTS])
    await create_flag(api)  # no matching integration: just dropped
    await record(app, "segment.create", "segment", "beta", project_id)
    assert await outbox_count(app) == 2

    assert await fanout(app) == 1
    assert await outbox_count(app) == 0
    assert await fanout(app) == 0


async def test_a_rollout_step_fans_out_on_the_tick(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    project_id, _ = await ids(app)
    clock = FakeClock(datetime.now(UTC))
    await start(app, clock.now)
    scheduler = make_scheduler(app, clock)
    await scheduler.tick()  # fans out the start
    hook = await add_integration(app, project_id, [EventGroup.TARGETING])

    clock.advance(minutes=60)
    await scheduler.tick()

    got = sorted(d.event_type for d in await deliveries(app, hook))
    assert got == ["flag.config.update", "flag.rollout.step"]
    assert await outbox_count(app) == 0


async def seed_outbox(app: FastAPI, project_id: uuid.UUID, env_id: uuid.UUID, count: int) -> None:
    async with app.state.sessionmaker() as session:
        for i in range(count):
            await record_system(
                session,
                "flag.toggle",
                "flag",
                f"flag-{i}",
                project_id=project_id,
                environment_id=env_id if i % 2 else None,
            )
        await session.commit()


async def test_the_statement_count_does_not_grow_with_the_rows(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    project_id, envs = await ids(app)
    await add_integration(app, project_id, [EventGroup.TARGETING], name="a")
    await add_integration(
        app, project_id, [EventGroup.TARGETING], name="b", environments=[envs["development"]]
    )
    counts = []
    for rows in (5, 50):
        await seed_outbox(app, project_id, envs["staging"], rows)
        with count_statements(app.state.engine) as statements:
            # a gets every row; b only the half without an environment
            assert await fanout(app) == rows + (rows + 1) // 2
        counts.append(len(statements))
    assert counts[0] == counts[1]
