"""Integrations: webhook, Slack and Teams targets of a project."""

import uuid

import httpx
from fastapi import FastAPI
from helpers import create_project
from sqlalchemy import func, select

from megooflags_api.models import (
    Environment,
    Integration,
    IntegrationDelivery,
    IntegrationEnvironment,
    Project,
)
from megooflags_api.models.enums import DeliveryStatus, EventGroup, IntegrationKind


async def insert_integration(app: FastAPI, environment_keys: list[str]) -> uuid.UUID:
    """An integration of project `demo` limited to `environment_keys`, with one delivery."""
    async with app.state.sessionmaker() as session:
        project_id = await session.scalar(select(Project.id).where(Project.key == "demo"))
        assert project_id is not None
        integration = Integration(
            project_id=project_id,
            kind=IntegrationKind.WEBHOOK,
            name="Hook",
            url_encrypted="encrypted",
            url_hint="https://hooks.example.com/…hook",
            event_groups=[EventGroup.FLAGS.value],
            all_environments=False,
        )
        session.add(integration)
        await session.flush()
        environments = await session.scalars(
            select(Environment.id).where(
                Environment.project_id == project_id, Environment.key.in_(environment_keys)
            )
        )
        for environment_id in environments:
            session.add(
                IntegrationEnvironment(integration_id=integration.id, environment_id=environment_id)
            )
        session.add(
            IntegrationDelivery(
                integration_id=integration.id,
                event_id="evt_1",
                event_type="flag.toggle",
                payload={"id": "evt_1"},
                status=DeliveryStatus.PENDING,
            )
        )
        await session.commit()
        return integration.id


async def count(app: FastAPI, model: type[object]) -> int:
    async with app.state.sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_project_delete_cascades_integrations(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await insert_integration(app, ["staging", "production"])
    assert await count(app, IntegrationEnvironment) == 2

    r = await api.delete("/api/projects/demo/environments/staging")
    assert r.status_code == 204
    assert await count(app, IntegrationEnvironment) == 1
    assert await count(app, Integration) == 1

    r = await api.delete("/api/projects/demo")
    assert r.status_code == 204
    assert await count(app, Integration) == 0
    assert await count(app, IntegrationDelivery) == 0
    assert await count(app, IntegrationEnvironment) == 0
