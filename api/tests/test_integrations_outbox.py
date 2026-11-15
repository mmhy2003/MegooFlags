import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import create_flag, create_project
from sqlalchemy import select

from megooflags_api.models import AuditEntry, IntegrationOutbox, Project
from megooflags_api.models.enums import EventGroup
from megooflags_api.services.audit import record_system
from megooflags_api.services.integrations.events import build_event, group_of, ping_event

PUBLIC = "https://flags.example.com"

GROUPED = [
    ("flag.create", EventGroup.FLAGS),
    ("flag.update", EventGroup.FLAGS),
    ("flag.archive", EventGroup.FLAGS),
    ("flag.restore", EventGroup.FLAGS),
    ("flag.delete", EventGroup.FLAGS),
    ("flag.variation.create", EventGroup.FLAGS),
    ("flag.variation.update", EventGroup.FLAGS),
    ("flag.variation.delete", EventGroup.FLAGS),
    ("flag.lifecycle.update", EventGroup.FLAGS),
    ("flag.config.update", EventGroup.TARGETING),
    ("flag.toggle", EventGroup.TARGETING),
    ("flag.kill", EventGroup.TARGETING),
    ("flag.copy", EventGroup.TARGETING),
    ("flag.schedule.create", EventGroup.TARGETING),
    ("flag.schedule.run", EventGroup.TARGETING),
    ("flag.rollout.step", EventGroup.TARGETING),
    ("segment.create", EventGroup.SEGMENTS),
    ("segment.update", EventGroup.SEGMENTS),
    ("segment.config.update", EventGroup.SEGMENTS),
    ("segment.delete", EventGroup.SEGMENTS),
    ("change_request.create", EventGroup.CHANGE_REQUESTS),
    ("change_request.approve", EventGroup.CHANGE_REQUESTS),
    ("change_request.reject", EventGroup.CHANGE_REQUESTS),
    ("change_request.apply", EventGroup.CHANGE_REQUESTS),
    ("change_request.fail", EventGroup.CHANGE_REQUESTS),
    ("change_request.cancel", EventGroup.CHANGE_REQUESTS),
]


@pytest.mark.parametrize(("action", "group"), GROUPED)
def test_group_of_grouped_actions(action: str, group: EventGroup) -> None:
    assert group_of(action) is group


@pytest.mark.parametrize(
    "action",
    [
        "change_request.comment",
        "integration.create",
        "project.update",
        "sdk_key.create",
        "flag.variation",
        "flag.toggled",
        "segment",
    ],
)
def test_group_of_other_actions_is_none(action: str) -> None:
    assert group_of(action) is None


async def outbox_rows(app: FastAPI) -> list[IntegrationOutbox]:
    async with app.state.sessionmaker() as session:
        rows = await session.scalars(select(IntegrationOutbox).order_by(IntegrationOutbox.id))
        return list(rows)


async def test_toggle_writes_one_outbox_row(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    before = await outbox_rows(app)
    r = await api.post(
        "/api/projects/demo/flags/new-ui/environments/development/toggle", json={"enabled": True}
    )
    assert r.status_code == 200, r.text
    rows = await outbox_rows(app)
    assert len(rows) == len(before) + 1
    row = rows[-1]
    async with app.state.sessionmaker() as session:
        entry = await session.get(AuditEntry, row.audit_entry_id)
        project_id = await session.scalar(select(Project.id).where(Project.key == "demo"))
    assert entry is not None
    assert entry.action == "flag.toggle"
    assert row.project_id == entry.project_id == project_id


async def test_rolled_back_change_writes_nothing(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        await record_system(session, "flag.toggle", "flag", "f", project_id=uuid.uuid4())
        await session.rollback()
    assert await outbox_rows(app) == []


async def test_org_actions_write_nothing(app: FastAPI, api: httpx.AsyncClient) -> None:
    r = await api.post("/api/tokens", json={"name": "ci"})
    assert r.status_code == 201, r.text
    assert await outbox_rows(app) == []


async def test_ungrouped_project_action_writes_nothing(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        await record_system(session, "project.update", "project", "demo", project_id=uuid.uuid4())
        await session.commit()
    assert await outbox_rows(app) == []


async def test_scheduler_action_writes_row(app: FastAPI) -> None:
    project_id = uuid.uuid4()
    async with app.state.sessionmaker() as session:
        entry = await record_system(
            session, "flag.schedule.run", "flag", "f", project_id=project_id
        )
        await session.commit()
    [row] = await outbox_rows(app)
    assert (row.audit_entry_id, row.project_id) == (entry.id, project_id)


def entry_of(**overrides: Any) -> AuditEntry:
    fields: dict[str, Any] = {
        "id": 42,
        "occurred_at": datetime(2026, 11, 15, 14, 2, 10, 123456, tzinfo=UTC),
        "actor_kind": "user",
        "actor_label": "mona@example.com",
        "action": "flag.toggle",
        "resource_type": "flag",
        "resource_key": "new-checkout",
        "before": {"enabled": False},
        "after": {"enabled": True},
    }
    return AuditEntry(**{**fields, **overrides})


async def test_build_event_shape(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        await record_system(
            session,
            "flag.toggle",
            "flag",
            "new-checkout",
            label="mona@example.com",
            project_id=uuid.uuid4(),
            environment_id=uuid.uuid4(),
            before={"enabled": False, "token": "t1"},
            after={"enabled": True, "token": "t2"},
        )
        await session.commit()
        entry = await session.scalar(select(AuditEntry))
    assert entry is not None
    event = build_event(entry, "shop", "production", None)
    occurred = entry.occurred_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    assert event == {
        "id": f"evt_{entry.id}",
        "type": "flag.toggle",
        "occurred_at": occurred,
        "project": "shop",
        "environment": "production",
        "resource": {"type": "flag", "key": "new-checkout"},
        "actor": {"kind": "system", "label": "mona@example.com"},
        "before": {"enabled": False, "token": "***"},
        "after": {"enabled": True, "token": "***"},
        "url": None,
    }
    assert occurred.endswith("Z")


def test_build_event_occurred_at_is_utc_with_z() -> None:
    event = build_event(entry_of(), "shop", None, None)
    assert event["occurred_at"] == "2026-11-15T14:02:10.123456Z"
    assert (event["environment"], event["url"]) == (None, None)


@pytest.mark.parametrize(
    ("overrides", "environment", "url"),
    [
        ({}, "production", f"{PUBLIC}/projects/shop/flags/new-checkout?env=production"),
        ({}, None, f"{PUBLIC}/projects/shop/flags/new-checkout"),
        ({"action": "flag.delete"}, None, f"{PUBLIC}/projects/shop/flags"),
        (
            {"action": "segment.update", "resource_type": "segment", "resource_key": "beta"},
            None,
            f"{PUBLIC}/projects/shop/segments",
        ),
        (
            {
                "action": "change_request.approve",
                "resource_type": "change_request",
                "resource_key": str(uuid.UUID(int=7)),
            },
            "production",
            f"{PUBLIC}/projects/shop/change-requests",
        ),
    ],
)
def test_build_event_urls(overrides: dict[str, Any], environment: str | None, url: str) -> None:
    assert build_event(entry_of(**overrides), "shop", environment, PUBLIC)["url"] == url
    assert build_event(entry_of(**overrides), "shop", environment, PUBLIC + "/")["url"] == url


def test_ping_event() -> None:
    event = ping_event("shop", "Ops hook", PUBLIC)
    assert re.fullmatch(r"evt_ping_[0-9a-f]{32}", event["id"])
    assert event["occurred_at"].endswith("Z")
    assert event == {
        "id": event["id"],
        "type": "ping",
        "occurred_at": event["occurred_at"],
        "project": "shop",
        "environment": None,
        "resource": {"type": "integration", "key": "Ops hook"},
        "actor": {"kind": "system", "label": "MegooFlags"},
        "before": None,
        "after": None,
        "url": f"{PUBLIC}/projects/shop/integrations",
    }
    assert ping_event("shop", "x", None)["url"] is None
    assert ping_event("shop", "x", PUBLIC)["id"] != event["id"]
