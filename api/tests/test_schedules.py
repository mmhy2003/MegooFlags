import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import FakeClock, audit_entries, create_flag, create_project, make_scheduler
from pydantic import ValidationError
from sqlalchemy import text
from test_changes import record_publishes

from megooflags_api.errors import ApiError
from megooflags_api.models.enums import ScheduledAction
from megooflags_api.schemas.schedules import ScheduleIn
from megooflags_api.schemas.targeting import WeightIn
from megooflags_api.services import schedules
from megooflags_api.services.audit import Actor, SystemAuditor
from megooflags_api.services.lookup import (
    get_environment,
    get_flag,
    get_flag_environment,
    get_project,
)

CONFIG = "/api/projects/demo/flags/color/environments/production"
ALICE = Actor(None, "alice")


async def color_flag(api: httpx.AsyncClient) -> None:
    """A string flag: a is the fallthrough, b the off variation, c unused."""
    await create_project(api)
    await create_flag(
        api,
        key="color",
        type="string",
        variations=[
            {"key": "a", "value": "red"},
            {"key": "b", "value": "green"},
            {"key": "c", "value": "blue"},
        ],
        off_variation="b",
    )


def clock() -> FakeClock:
    return FakeClock(datetime.now(UTC))


def weights(**shares: int) -> list[WeightIn]:
    return [WeightIn(variation=key, weight=weight) for key, weight in shares.items()]


async def create(
    app: FastAPI,
    now: datetime,
    action: str,
    run_at: datetime,
    weights: list[WeightIn] | None = None,
    env: str = "production",
) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        project = await get_project(session, "demo")
        env_row = await get_environment(session, project, env)
        flag = await get_flag(session, project, "color")
        fe = await get_flag_environment(session, flag, env_row)
        row = await schedules.create(
            session,
            SystemAuditor(session),
            flag,
            env_row,
            fe,
            ScheduleIn(action=ScheduledAction(action), run_at=run_at, weights=weights),
            ALICE,
            now,
        )
        await session.commit()
        return row.id


async def schedule_row(app: FastAPI, id_: uuid.UUID) -> Any:
    async with app.state.engine.connect() as conn:
        return (
            await conn.execute(text("SELECT * FROM scheduled_change WHERE id = :id"), {"id": id_})
        ).one()


async def config(api: httpx.AsyncClient) -> dict[str, Any]:
    r = await api.get(CONFIG)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_an_enable_runs_when_it_is_due(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    before = await config(api)
    id_ = await create(app, c.now, "enable", c.now + timedelta(hours=1))
    scheduler = make_scheduler(app, c)

    await scheduler.tick()
    assert (await config(api))["enabled"] is False
    assert (await schedule_row(app, id_)).status == "pending"

    c.advance(hours=2)
    await scheduler.tick()

    after = await config(api)
    assert (after["enabled"], after["version"]) == (True, before["version"] + 1)
    row = await schedule_row(app, id_)
    assert (row.status, row.error) == ("done", None)
    assert row.executed_at == c.now
    [toggle] = await audit_entries(app, "flag.toggle")
    assert (toggle["actor_kind"], toggle["actor_label"]) == ("system", "scheduler")
    assert toggle["resource_key"] == "color"
    assert toggle["after"]["enabled"] is True
    assert toggle["after"]["schedule_id"] == str(id_)
    assert toggle["after"]["created_by"] == "alice"
    assert toggle["after"]["change_request_id"] is None
    [run] = await audit_entries(app, "flag.schedule.run")
    assert run["after"]["status"] == "done"


async def test_a_disable_runs_when_it_is_due(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    r = await api.post(f"{CONFIG}/toggle", json={"enabled": True})
    assert r.status_code == 200, r.text
    c = clock()
    id_ = await create(app, c.now, "disable", c.now + timedelta(minutes=5))

    c.advance(minutes=5)
    await make_scheduler(app, c).tick()

    assert (await config(api))["enabled"] is False
    assert (await schedule_row(app, id_)).status == "done"
    [_, toggle] = await audit_entries(app, "flag.toggle")
    assert toggle["actor_kind"] == "system"
    assert (toggle["before"]["enabled"], toggle["after"]["enabled"]) == (True, False)


async def test_a_set_rollout_sets_the_fallthrough_rollout(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    before = await config(api)
    id_ = await create(
        app, c.now, "set_rollout", c.now + timedelta(hours=1), weights(a=30000, b=70000)
    )

    c.advance(hours=1)
    await make_scheduler(app, c).tick()

    after = await config(api)
    assert after["fallthrough"] == {
        "variation": None,
        "rollout": {
            "bucket_by": "key",
            "weights": [{"variation": "a", "weight": 30000}, {"variation": "b", "weight": 70000}],
        },
    }
    assert after["version"] == before["version"] + 1
    assert (await schedule_row(app, id_)).status == "done"
    [entry] = await audit_entries(app, "flag.config.update")
    assert entry["actor_kind"] == "system"
    assert entry["before"]["fallthrough"]["variation"] == "a"
    assert entry["after"]["fallthrough"]["rollout"]["weights"][1] == {
        "variation": "b",
        "weight": 70000,
    }
    assert entry["after"]["schedule_id"] == str(id_)


async def test_a_set_rollout_keeps_the_existing_bucket_by(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    current = await config(api)
    r = await api.put(
        CONFIG,
        json={
            "version": current["version"],
            "enabled": False,
            "fallthrough": {
                "rollout": {
                    "bucket_by": "org",
                    "weights": [{"variation": "a", "weight": 100000}],
                }
            },
        },
    )
    assert r.status_code == 200, r.text
    c = clock()
    await create(app, c.now, "set_rollout", c.now + timedelta(minutes=1), weights(a=50000, c=50000))

    c.advance(minutes=1)
    await make_scheduler(app, c).tick()

    assert (await config(api))["fallthrough"]["rollout"]["bucket_by"] == "org"


async def test_an_overdue_schedule_runs_on_one_tick(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await create(app, c.now, "enable", c.now + timedelta(hours=1))

    c.advance(days=3)
    await make_scheduler(app, c).tick()

    assert (await schedule_row(app, id_)).status == "done"
    assert (await config(api))["enabled"] is True


async def test_a_deleted_variation_fails_the_schedule(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await create(
        app, c.now, "set_rollout", c.now + timedelta(hours=1), weights(a=50000, c=50000)
    )
    r = await api.delete("/api/projects/demo/flags/color/variations/c")
    assert r.status_code == 204, r.text
    before = await config(api)

    c.advance(hours=1)
    await make_scheduler(app, c).tick()

    row = await schedule_row(app, id_)
    assert row.status == "failed"
    assert "'c'" in row.error
    assert row.executed_at == c.now
    assert await config(api) == before
    [entry] = await audit_entries(app, "flag.schedule.fail")
    assert (entry["actor_kind"], entry["resource_key"]) == ("system", "color")
    assert "'c'" in entry["after"]["error"]
    assert await audit_entries(app, "flag.config.update") == []
    assert await audit_entries(app, "flag.schedule.run") == []


async def test_a_schedule_on_an_archived_flag_fails(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await create(app, c.now, "enable", c.now + timedelta(hours=1))
    async with app.state.engine.begin() as conn:  # archive without cancelling it (step 7)
        await conn.execute(text("UPDATE flag SET archived_at = now()"))

    c.advance(hours=1)
    await make_scheduler(app, c).tick()

    row = await schedule_row(app, id_)
    assert row.status == "failed"
    assert "archived" in row.error
    assert (await config(api))["enabled"] is False


async def test_one_failure_does_not_stop_the_others(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    bad = await create(
        app, c.now, "set_rollout", c.now + timedelta(minutes=1), weights(a=50000, c=50000)
    )
    good = await create(app, c.now, "enable", c.now + timedelta(minutes=2))
    await api.delete("/api/projects/demo/flags/color/variations/c")

    c.advance(minutes=2)
    result = await schedules.run_due_schedules(app.state.sessionmaker, c.now)

    assert (result.ok, result.failed) == (1, 1)
    assert (await schedule_row(app, bad)).status == "failed"
    assert (await schedule_row(app, good)).status == "done"


async def test_two_concurrent_runs_apply_a_schedule_once(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    version = (await config(api))["version"]
    await create(app, c.now, "enable", c.now + timedelta(minutes=1))
    c.advance(minutes=1)

    results = await asyncio.gather(
        schedules.run_due_schedules(app.state.sessionmaker, c.now),
        schedules.run_due_schedules(app.state.sessionmaker, c.now),
    )

    assert sum(r.ok for r in results) == 1
    assert len(await audit_entries(app, "flag.schedule.run")) == 1
    assert (await config(api))["version"] == version + 1


async def test_a_run_publishes_the_project(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    published = record_publishes(app)
    c = clock()
    await create(app, c.now, "enable", c.now + timedelta(minutes=1))
    scheduler = make_scheduler(app, c)
    await scheduler.tick()
    assert published == []

    c.advance(minutes=1)
    await scheduler.tick()

    async with app.state.engine.connect() as conn:
        demo_id = await conn.scalar(text("SELECT id FROM project WHERE key = 'demo'"))
    assert published == [demo_id]


async def test_create_stores_and_audits_the_schedule(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    run_at = c.now + timedelta(days=1)

    id_ = await create(app, c.now, "set_rollout", run_at, weights(a=10000, b=90000))

    row = await schedule_row(app, id_)
    assert (row.action, row.status, row.run_at) == ("set_rollout", "pending", run_at)
    assert row.weights == [{"variation": "a", "weight": 10000}, {"variation": "b", "weight": 90000}]
    assert (row.created_by_id, row.created_by_label) == (None, "alice")
    [entry] = await audit_entries(app, "flag.schedule.create")
    assert (entry["resource_type"], entry["resource_key"]) == ("flag", "color")
    assert entry["environment_id"] is not None
    assert entry["before"] is None
    assert entry["after"]["id"] == str(id_)
    assert entry["after"]["environment"] == "production"
    assert entry["after"]["created_by"] == "alice"


@pytest.mark.parametrize(
    ("run_at", "message"),
    [(timedelta(seconds=-1), "future"), (timedelta(days=365, seconds=1), "365 days")],
)
async def test_create_refuses_a_run_at_out_of_range(
    app: FastAPI, api: httpx.AsyncClient, run_at: timedelta, message: str
) -> None:
    await color_flag(api)
    c = clock()

    with pytest.raises(ApiError) as caught:
        await create(app, c.now, "enable", c.now + run_at)

    assert (caught.value.status, caught.value.code) == (422, "validation_error")
    assert message in caught.value.message
    assert caught.value.details == {"path": "run_at"}
    assert await audit_entries(app, "flag.schedule.create") == []


async def test_a_run_at_365_days_ahead_is_allowed(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()

    await create(app, c.now, "enable", c.now + timedelta(days=365))


@pytest.mark.parametrize(
    ("action", "weights"),
    [("set_rollout", None), ("enable", [WeightIn(variation="a", weight=100000)]), ("disable", [])],
)
def test_weights_go_with_set_rollout_only(action: str, weights: list[WeightIn] | None) -> None:
    with pytest.raises(ValidationError):
        ScheduleIn(action=ScheduledAction(action), run_at=datetime.now(UTC), weights=weights)


@pytest.mark.parametrize(
    ("shares", "code", "path"),
    [
        ({"a": 30000, "b": 30000}, "validation_error", "weights"),
        ({"a": 50000, "z": 50000}, "invalid_reference", "weights[1].variation"),
    ],
)
async def test_create_validates_the_weights(
    app: FastAPI, api: httpx.AsyncClient, shares: dict[str, int], code: str, path: str
) -> None:
    await color_flag(api)
    c = clock()

    with pytest.raises(ApiError) as caught:
        await create(app, c.now, "set_rollout", c.now + timedelta(hours=1), weights(**shares))

    assert (caught.value.status, caught.value.code) == (422, code)
    assert caught.value.details == {"path": path}


async def test_cancel_cancels_a_pending_schedule(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await create(app, c.now, "enable", c.now + timedelta(hours=1))

    async with app.state.sessionmaker() as session:
        project = await get_project(session, "demo")
        env = await get_environment(session, project, "production")
        flag = await get_flag(session, project, "color")
        fe = await get_flag_environment(session, flag, env)
        out = await schedules.cancel(session, SystemAuditor(session), flag, env, fe, id_)
        await session.commit()
    assert out.status == "cancelled"

    c.advance(hours=2)
    await make_scheduler(app, c).tick()
    assert (await schedule_row(app, id_)).status == "cancelled"
    assert (await config(api))["enabled"] is False
    [entry] = await audit_entries(app, "flag.schedule.cancel")
    assert (entry["before"]["status"], entry["after"]["status"]) == ("pending", "cancelled")

    async with app.state.sessionmaker() as session:
        with pytest.raises(ApiError) as caught:
            await schedules.cancel(session, SystemAuditor(session), flag, env, fe, id_)
    assert (caught.value.status, caught.value.code) == (409, "invalid_state")


async def test_get_and_list_are_scoped_to_the_flag_environment(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    later = await create(app, c.now, "enable", c.now + timedelta(hours=2))
    sooner = await create(app, c.now, "disable", c.now + timedelta(hours=1))
    other = await create(app, c.now, "enable", c.now + timedelta(hours=1), env="staging")

    async with app.state.sessionmaker() as session:
        project = await get_project(session, "demo")
        env = await get_environment(session, project, "production")
        flag = await get_flag(session, project, "color")
        fe = await get_flag_environment(session, flag, env)
        listed = await schedules.list_for(session, fe, env.key)
        assert [s.id for s in listed] == [later, sooner]
        assert listed[0].environment == "production"
        assert (await schedules.get(session, fe, sooner)).id == sooner
        with pytest.raises(ApiError) as caught:
            await schedules.get(session, fe, other)
    assert (caught.value.status, caught.value.code) == (404, "not_found")


async def test_cancel_pending_for_flag_cancels_every_environment(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    ids = [
        await create(app, c.now, "enable", c.now + timedelta(hours=1)),
        await create(app, c.now, "enable", c.now + timedelta(hours=1), env="staging"),
    ]
    done = await create(app, c.now, "disable", c.now + timedelta(minutes=1))
    c.advance(minutes=1)
    await make_scheduler(app, c).tick()

    async with app.state.sessionmaker() as session:
        flag = await get_flag(session, await get_project(session, "demo"), "color")
        count = await schedules.cancel_pending_for_flag(
            session, SystemAuditor(session), flag, "flag archived"
        )
        await session.commit()

    assert count == 2
    assert [(await schedule_row(app, i)).status for i in ids] == ["cancelled", "cancelled"]
    assert (await schedule_row(app, done)).status == "done"
    entries = await audit_entries(app, "flag.schedule.cancel")
    assert len(entries) == 2
    assert {e["after"]["reason"] for e in entries} == {"flag archived"}
