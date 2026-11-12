import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    FakeClock,
    assert_error,
    audit_entries,
    create_flag,
    create_project,
    make_scheduler,
)
from pydantic import ValidationError
from sqlalchemy import text
from test_changes import record_publishes
from test_schedules import custom_role, token_client

from megooflags_api.errors import ApiError
from megooflags_api.models.enums import RolloutStatus
from megooflags_api.schemas.rollouts import ProgressiveRolloutIn, RolloutStepIn
from megooflags_api.services import rollouts
from megooflags_api.services.audit import Actor, SystemAuditor
from megooflags_api.services.lookup import (
    get_environment,
    get_flag,
    get_flag_environment,
    get_project,
)

CONFIG = "/api/projects/demo/flags/color/environments/production"
ALICE = Actor(None, "alice")
STEPS = [
    RolloutStepIn(percentage=10000, wait_minutes=60),
    RolloutStepIn(percentage=50000, wait_minutes=60),
    RolloutStepIn(percentage=100000),
]


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


async def config(api: httpx.AsyncClient) -> dict[str, Any]:
    r = await api.get(CONFIG)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def rollout_of(**shares: int) -> dict[str, Any]:
    return {
        "variation": None,
        "rollout": {
            "bucket_by": "key",
            "weights": [{"variation": key, "weight": weight} for key, weight in shares.items()],
        },
    }


async def start(
    app: FastAPI,
    now: datetime,
    steps: list[RolloutStepIn] = STEPS,
    from_: str = "a",
    to: str = "b",
) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        project = await get_project(session, "demo")
        env = await get_environment(session, project, "production")
        flag = await get_flag(session, project, "color")
        fe = await get_flag_environment(session, flag, env, lock=True)
        row = await rollouts.start(
            session,
            SystemAuditor(session),
            flag,
            env,
            fe,
            ProgressiveRolloutIn(from_variation=from_, to_variation=to, steps=steps),
            ALICE,
            now,
        )
        await session.commit()
        return row.id


async def act(app: FastAPI, action: str, now: datetime, **kw: Any) -> Any:
    """Run rollouts.<action> on the production rollout and commit."""
    async with app.state.sessionmaker() as session:
        project = await get_project(session, "demo")
        env = await get_environment(session, project, "production")
        flag = await get_flag(session, project, "color")
        fe = await get_flag_environment(session, flag, env, lock=True)
        result = await getattr(rollouts, action)(
            session, SystemAuditor(session), flag, env, fe, now, **kw
        )
        await session.commit()
        return result


async def rollout_row(app: FastAPI, id_: uuid.UUID) -> Any:
    async with app.state.engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT * FROM progressive_rollout WHERE id = :id"), {"id": id_}
            )
        ).one()


async def test_start_applies_the_first_step(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    before = await config(api)

    id_ = await start(app, c.now)

    after = await config(api)
    assert after["fallthrough"] == rollout_of(a=90000, b=10000)
    assert after["version"] == before["version"] + 1
    row = await rollout_row(app, id_)
    assert (row.status, row.current_step) == ("active", 0)
    assert row.next_step_at == c.now + timedelta(minutes=60)
    assert row.last_applied_weights == [
        {"variation": "a", "weight": 90000},
        {"variation": "b", "weight": 10000},
    ]
    assert (row.created_by_id, row.created_by_label) == (None, "alice")
    [started] = await audit_entries(app, "flag.rollout.start")
    assert (started["resource_type"], started["resource_key"]) == ("flag", "color")
    assert started["after"]["rollout_id"] == str(id_)
    assert started["after"]["created_by"] == "alice"
    assert started["after"]["change_request_id"] is None
    assert started["after"]["status"] == "active"
    [update] = await audit_entries(app, "flag.config.update")
    assert update["before"]["fallthrough"]["variation"] == "a"
    assert update["after"]["fallthrough"] == rollout_of(a=90000, b=10000)
    assert update["after"]["rollout_id"] == str(id_)


async def test_start_keeps_the_bucket_by(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    current = await config(api)
    r = await api.put(
        CONFIG,
        json={
            "version": current["version"],
            "enabled": False,
            "fallthrough": {
                "rollout": {"bucket_by": "org", "weights": [{"variation": "a", "weight": 100000}]}
            },
        },
    )
    assert r.status_code == 200, r.text

    await start(app, clock().now)

    assert (await config(api))["fallthrough"]["rollout"]["bucket_by"] == "org"


async def test_steps_run_over_time_until_complete(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await start(app, c.now)
    scheduler = make_scheduler(app, c)

    c.advance(minutes=59)
    await scheduler.tick()
    assert (await config(api))["fallthrough"] == rollout_of(a=90000, b=10000)

    c.advance(minutes=1)
    await scheduler.tick()
    assert (await config(api))["fallthrough"] == rollout_of(a=50000, b=50000)
    row = await rollout_row(app, id_)
    assert (row.status, row.current_step) == ("active", 1)
    assert row.next_step_at == c.now + timedelta(minutes=60)
    [step] = await audit_entries(app, "flag.rollout.step")
    assert (step["actor_kind"], step["actor_label"]) == ("system", "scheduler")
    assert step["after"]["rollout_id"] == str(id_)
    assert step["after"]["created_by"] == "alice"
    assert step["after"]["current_step"] == 1

    c.advance(minutes=60)
    await scheduler.tick()
    assert (await config(api))["fallthrough"] == {"variation": "b", "rollout": None}
    row = await rollout_row(app, id_)
    assert (row.status, row.current_step, row.next_step_at) == ("completed", 2, None)
    assert row.finished_at == c.now
    [complete] = await audit_entries(app, "flag.rollout.complete")
    assert complete["after"]["status"] == "completed"
    assert len(await audit_entries(app, "flag.config.update")) == 3

    c.advance(days=1)
    await scheduler.tick()
    assert len(await audit_entries(app, "flag.config.update")) == 3


async def test_pause_keeps_the_remaining_wait_and_resume_restores_it(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    t0 = c.now
    id_ = await start(app, c.now)
    scheduler = make_scheduler(app, c)

    c.advance(minutes=20)
    await act(app, "pause", c.now)
    row = await rollout_row(app, id_)
    assert (row.status, row.remaining_seconds, row.next_step_at) == ("paused", 2400, None)
    [paused] = await audit_entries(app, "flag.rollout.pause")
    assert paused["after"]["status"] == "paused"

    c.advance(minutes=80)
    await scheduler.tick()
    assert (await config(api))["fallthrough"] == rollout_of(a=90000, b=10000)

    await act(app, "resume", c.now)
    row = await rollout_row(app, id_)
    assert (row.status, row.remaining_seconds) == ("active", None)
    assert row.next_step_at == t0 + timedelta(minutes=140)
    assert len(await audit_entries(app, "flag.rollout.resume")) == 1

    c.advance(minutes=39)
    await scheduler.tick()
    assert (await config(api))["fallthrough"] == rollout_of(a=90000, b=10000)
    c.advance(minutes=1)
    await scheduler.tick()
    assert (await config(api))["fallthrough"] == rollout_of(a=50000, b=50000)


async def test_pause_and_resume_need_the_right_status(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()

    for action in ("pause", "resume", "abort"):
        with pytest.raises(ApiError) as caught:
            await act(app, action, c.now)
        assert (caught.value.status, caught.value.code) == (409, "invalid_state")

    await start(app, c.now)
    with pytest.raises(ApiError) as caught:
        await act(app, "resume", c.now)
    assert caught.value.code == "invalid_state"
    await act(app, "pause", c.now)
    with pytest.raises(ApiError) as caught:
        await act(app, "pause", c.now)
    assert caught.value.code == "invalid_state"


async def test_abort_sends_everything_back_to_the_from_variation(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    id_ = await start(app, c.now)

    c.advance(minutes=5)
    await act(app, "abort", c.now, reason="too many errors")

    assert (await config(api))["fallthrough"] == {"variation": "a", "rollout": None}
    row = await rollout_row(app, id_)
    assert (row.status, row.next_step_at, row.finished_at) == ("aborted", None, c.now)
    [abort] = await audit_entries(app, "flag.rollout.abort")
    assert abort["after"]["status"] == "aborted"
    assert abort["after"]["reason"] == "too many errors"
    [_, update] = await audit_entries(app, "flag.config.update")
    assert update["after"]["fallthrough"] == {"variation": "a", "rollout": None}

    with pytest.raises(ApiError) as caught:
        await act(app, "abort", c.now)
    assert caught.value.code == "invalid_state"


async def test_abort_a_paused_rollout(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await start(app, c.now)
    await act(app, "pause", c.now)

    await act(app, "abort", c.now)

    row = await rollout_row(app, id_)
    assert (row.status, row.remaining_seconds) == ("aborted", None)
    assert (await config(api))["fallthrough"] == {"variation": "a", "rollout": None}


async def test_abort_with_a_deleted_from_variation_leaves_the_fallthrough(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    id_ = await start(app, c.now)
    async with app.state.engine.begin() as conn:
        await conn.execute(text("UPDATE progressive_rollout SET from_variation_id = NULL"))
    before = await config(api)

    await act(app, "abort", c.now)

    assert await config(api) == before
    row = await rollout_row(app, id_)
    assert row.status == "aborted"
    assert "variation was deleted" in row.error
    assert len(await audit_entries(app, "flag.rollout.abort")) == 1


async def test_a_manual_edit_fails_the_rollout(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await start(app, c.now)
    current = await config(api)
    r = await api.put(
        CONFIG,
        json={
            "version": current["version"],
            "enabled": False,
            "fallthrough": {
                "rollout": {
                    "weights": [
                        {"variation": "a", "weight": 70000},
                        {"variation": "b", "weight": 30000},
                    ]
                }
            },
        },
    )
    assert r.status_code == 200, r.text
    edited = await config(api)

    c.advance(minutes=60)
    await make_scheduler(app, c).tick()

    assert await config(api) == edited
    row = await rollout_row(app, id_)
    assert (row.status, row.error) == ("failed", "fallthrough changed manually")
    assert (row.next_step_at, row.finished_at) == (None, c.now)
    [fail] = await audit_entries(app, "flag.rollout.fail")
    assert (fail["actor_kind"], fail["resource_key"]) == ("system", "color")
    assert fail["after"]["error"] == "fallthrough changed manually"
    assert await audit_entries(app, "flag.rollout.step") == []


async def test_a_deleted_variation_fails_the_rollout(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await start(app, c.now)
    async with app.state.engine.begin() as conn:
        await conn.execute(text("UPDATE progressive_rollout SET to_variation_id = NULL"))
    before = await config(api)

    c.advance(minutes=60)
    result = await rollouts.run_due_rollouts(app.state.sessionmaker, c.now)

    assert (result.ok, result.failed) == (0, 1)
    assert await config(api) == before
    row = await rollout_row(app, id_)
    assert row.status == "failed"
    assert "variation was deleted" in row.error


async def test_an_archived_flag_fails_the_rollout(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await start(app, c.now)
    async with app.state.engine.begin() as conn:  # archive without cancelling it (step 9)
        await conn.execute(text("UPDATE flag SET archived_at = now()"))

    c.advance(minutes=60)
    await make_scheduler(app, c).tick()

    row = await rollout_row(app, id_)
    assert row.status == "failed"
    assert "archived" in row.error


async def test_a_single_full_step_completes_at_start(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()

    id_ = await start(app, c.now, [RolloutStepIn(percentage=100000)])

    assert (await config(api))["fallthrough"] == {"variation": "b", "rollout": None}
    row = await rollout_row(app, id_)
    assert (row.status, row.next_step_at, row.finished_at) == ("completed", None, c.now)
    assert len(await audit_entries(app, "flag.rollout.start")) == 1
    assert len(await audit_entries(app, "flag.rollout.complete")) == 1

    await start(app, c.now)  # a finished rollout does not block a new one


async def test_a_second_start_is_refused(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    await start(app, c.now)
    before = await config(api)

    with pytest.raises(ApiError) as caught:
        await start(app, c.now, from_="a", to="c")

    assert (caught.value.status, caught.value.code) == (409, "invalid_state")
    assert await config(api) == before


@pytest.mark.parametrize(
    ("from_", "to", "path"),
    [("z", "b", "from_variation"), ("a", "z", "to_variation")],
)
async def test_start_refuses_an_unknown_variation(
    app: FastAPI, api: httpx.AsyncClient, from_: str, to: str, path: str
) -> None:
    await color_flag(api)

    with pytest.raises(ApiError) as caught:
        await start(app, clock().now, from_=from_, to=to)

    assert (caught.value.status, caught.value.code) == (422, "invalid_reference")
    assert caught.value.details == {"path": path}
    assert await audit_entries(app, "flag.rollout.start") == []


@pytest.mark.parametrize(
    ("from_", "to", "percentages"),
    [
        ("a", "a", [100000]),
        ("a", "b", [50000, 50000, 100000]),
        ("a", "b", [60000, 40000, 100000]),
        ("a", "b", [10000, 50000]),
        ("a", "b", []),
    ],
    ids=["same variation", "repeated", "decreasing", "not ending at 100000", "no steps"],
)
def test_the_input_is_validated(from_: str, to: str, percentages: list[int]) -> None:
    with pytest.raises(ValidationError):
        ProgressiveRolloutIn(
            from_variation=from_,
            to_variation=to,
            steps=[RolloutStepIn(percentage=p) for p in percentages],
        )


@pytest.mark.parametrize(
    "step", [{"percentage": 0}, {"percentage": 100001}, {"percentage": 1, "wait_minutes": 0}]
)
def test_a_step_is_validated(step: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        RolloutStepIn.model_validate(step)


async def test_two_concurrent_runs_step_once(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    await start(app, c.now)
    version = (await config(api))["version"]
    c.advance(minutes=60)

    results = await asyncio.gather(
        rollouts.run_due_rollouts(app.state.sessionmaker, c.now),
        rollouts.run_due_rollouts(app.state.sessionmaker, c.now),
    )

    assert sum(r.ok for r in results) == 1
    assert len(await audit_entries(app, "flag.rollout.step")) == 1
    assert (await config(api))["version"] == version + 1


async def test_a_step_publishes_the_project(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    published = record_publishes(app)
    c = clock()
    await start(app, c.now)
    scheduler = make_scheduler(app, c)
    await scheduler.tick()
    assert published == []

    c.advance(minutes=60)
    await scheduler.tick()

    async with app.state.engine.connect() as conn:
        demo_id = await conn.scalar(text("SELECT id FROM project WHERE key = 'demo'"))
    assert published == [demo_id]


async def test_pause_live_pauses_only_an_active_rollout(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    assert await act(app, "pause_live", c.now) is None
    id_ = await start(app, c.now)

    paused = await act(app, "pause_live", c.now)

    assert paused.id == id_
    assert (await rollout_row(app, id_)).status == "paused"
    assert await act(app, "pause_live", c.now) is None
    assert len(await audit_entries(app, "flag.rollout.pause")) == 1


async def test_cancel_live_for_flag(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    c = clock()
    id_ = await start(app, c.now)

    async with app.state.sessionmaker() as session:
        flag = await get_flag(session, await get_project(session, "demo"), "color")
        count = await rollouts.cancel_live_for_flag(
            session, SystemAuditor(session), flag, "flag archived"
        )
        await session.commit()

    assert count == 1
    row = await rollout_row(app, id_)
    assert (row.status, row.next_step_at) == ("cancelled", None)
    assert row.finished_at is not None
    [entry] = await audit_entries(app, "flag.rollout.cancel")
    assert (entry["after"]["status"], entry["after"]["reason"]) == ("cancelled", "flag archived")


async def test_view_shows_the_live_rollout_and_recent_ones(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    first = await start(app, c.now, [RolloutStepIn(percentage=100000)])
    second = await start(app, c.now, from_="b", to="c")

    async with app.state.sessionmaker() as session:
        project = await get_project(session, "demo")
        env = await get_environment(session, project, "production")
        flag = await get_flag(session, project, "color")
        fe = await get_flag_environment(session, flag, env)
        view = await rollouts.view(session, fe, env.key)

    assert view.current is not None
    assert view.current.id == second
    assert (view.current.from_variation, view.current.to_variation) == ("b", "c")
    assert view.current.environment == "production"
    assert view.current.created_by == "alice"
    assert view.current.status == RolloutStatus.ACTIVE
    assert [w.model_dump() for w in view.current.last_applied_weights or []] == [
        {"variation": "b", "weight": 90000},
        {"variation": "c", "weight": 10000},
    ]
    assert [r.id for r in view.recent] == [first]


# Routes

ROLLOUT = f"{CONFIG}/rollout"
BODY = {
    "from_variation": "a",
    "to_variation": "b",
    "steps": [{"percentage": 10000}, {"percentage": 100000}],
}


async def demo_id(app: FastAPI) -> uuid.UUID:
    async with app.state.engine.connect() as conn:
        return await conn.scalar(text("SELECT id FROM project WHERE key = 'demo'"))  # type: ignore[no-any-return]


async def test_start_pause_resume_and_abort_through_the_api(api: httpx.AsyncClient) -> None:
    await color_flag(api)

    r = await api.post(ROLLOUT, json=BODY)

    assert r.status_code == 201, r.text
    started = r.json()
    assert (started["status"], started["created_by"]) == ("active", "tests")
    assert (started["from_variation"], started["to_variation"]) == ("a", "b")
    r = await api.get(ROLLOUT)
    assert r.status_code == 200, r.text
    assert r.json()["current"]["id"] == started["id"]
    assert r.json()["recent"] == []

    for action, status in (("pause", "paused"), ("resume", "active"), ("abort", "aborted")):
        r = await api.post(f"{ROLLOUT}/{action}")
        assert r.status_code == 200, (action, r.text)
        assert r.json()["status"] == status

    r = await api.get(ROLLOUT)
    assert r.json()["current"] is None
    assert [(x["id"], x["status"]) for x in r.json()["recent"]] == [(started["id"], "aborted")]
    assert_error(await api.post(f"{ROLLOUT}/pause"), 409, "invalid_state")


async def test_start_and_abort_publish_but_pause_and_resume_do_not(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    project_id = await demo_id(app)
    published = record_publishes(app)

    for action, publishes in (("", True), ("/pause", False), ("/resume", False), ("/abort", True)):
        count = len(published)
        r = await api.post(f"{ROLLOUT}{action}", json=BODY if action == "" else None)
        assert 200 <= r.status_code < 300, (action, r.text)
        assert published[count:] == ([project_id] if publishes else []), action


async def test_toggle_permission_can_read_but_not_drive_a_rollout(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    r = await api.post(ROLLOUT, json=BODY)
    assert r.status_code == 201, r.text
    role = await custom_role(api, "togglers", ["project.read", "flag.toggle"])

    async with token_client(app, role, "project") as client:
        r = await client.get(ROLLOUT)
        assert r.status_code == 200, r.text
        assert_error(await client.post(ROLLOUT, json=BODY), 403, "forbidden")
        assert_error(await client.post(f"{ROLLOUT}/pause"), 403, "forbidden")
        assert_error(await client.post(f"{ROLLOUT}/abort"), 403, "forbidden")

    assert (await api.get(ROLLOUT)).json()["current"]["status"] == "active"


async def test_the_kill_switch_pauses_an_active_rollout(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    r = await api.post(f"{CONFIG}/kill")
    assert r.status_code == 200, r.text
    r = await api.post(ROLLOUT, json=BODY)
    assert r.status_code == 201, r.text

    r = await api.post(f"{CONFIG}/kill")

    assert r.status_code == 200, r.text
    assert (await api.get(ROLLOUT)).json()["current"]["status"] == "paused"
    [entry] = await audit_entries(app, "flag.rollout.pause")
    assert (entry["after"]["status"], entry["after"]["reason"]) == ("paused", "kill switch")


async def test_archiving_cancels_the_live_rollout(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    r = await api.post(ROLLOUT, json=BODY)
    assert r.status_code == 201, r.text

    r = await api.post("/api/projects/demo/flags/color/archive")

    assert r.status_code == 200, r.text
    [entry] = await audit_entries(app, "flag.rollout.cancel")
    assert (entry["after"]["status"], entry["after"]["reason"]) == ("cancelled", "flag archived")
    assert (await rollout_row(app, uuid.UUID(entry["after"]["id"]))).status == "cancelled"


async def test_a_route_started_rollout_steps_on_a_tick(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    c = clock()
    r = await api.post(
        ROLLOUT,
        json=BODY
        | {"steps": [{"percentage": 10000}, {"percentage": 50000}, {"percentage": 100000}]},
    )
    assert r.status_code == 201, r.text

    c.advance(minutes=61)
    await make_scheduler(app, c).tick()

    current = (await api.get(ROLLOUT)).json()["current"]
    assert (current["id"], current["current_step"]) == (r.json()["id"], 1)
