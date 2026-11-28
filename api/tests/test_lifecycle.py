from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI
from helpers import (
    FakeClock,
    assert_error,
    audit_entries,
    bearer,
    create_flag,
    create_project,
    make_scheduler,
    seed_counts,
    seed_service_token,
)
from sqlalchemy import text
from test_changes import record_publishes

from megooflags_api.services.events import truncate_hour
from megooflags_api.services.lifecycle import run_lifecycle_sweep

F = "/api/projects/demo/flags/new-ui"


async def set_lifecycle(api: httpx.AsyncClient, permanent: bool) -> httpx.Response:
    return await api.put(f"{F}/lifecycle", json={"permanent": permanent})


async def keys(api: httpx.AsyncClient, **params: Any) -> list[str]:
    r = await api.get("/api/projects/demo/flags", params=params)
    assert r.status_code == 200, r.text
    return [item["key"] for item in r.json()["items"]]


async def test_a_new_flag_is_new_and_never_evaluated(api: httpx.AsyncClient) -> None:
    await create_project(api)
    created = await create_flag(api)

    assert (created["lifecycle"], created["last_evaluated_at"]) == ("new", None)
    body = (await api.get(F)).json()
    assert (body["lifecycle"], body["last_evaluated_at"]) == ("new", None)
    [item] = (await api.get("/api/projects/demo/flags")).json()["items"]
    assert (item["lifecycle"], item["last_evaluated_at"]) == ("new", None)


async def test_archive_shows_archived_and_restore_shows_new(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    assert (await api.post(f"{F}/archive")).json()["lifecycle"] == "archived"
    assert (await api.post(f"{F}/restore")).json()["lifecycle"] == "new"


async def test_mark_and_clear_permanent(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)

    r = await set_lifecycle(api, True)
    assert r.status_code == 200, r.text
    assert (r.json()["key"], r.json()["lifecycle"]) == ("new-ui", "permanent")
    assert (await api.get(F)).json()["lifecycle"] == "permanent"

    r = await set_lifecycle(api, False)
    assert r.status_code == 200, r.text
    assert r.json()["lifecycle"] == "new"

    entries = await audit_entries(app, "flag.lifecycle.update")
    assert [
        (e["resource_key"], e["before"]["lifecycle"], e["after"]["lifecycle"]) for e in entries
    ] == [
        ("new-ui", "new", "permanent"),
        ("new-ui", "permanent", "new"),
    ]


async def test_clearing_permanent_on_an_evaluated_flag_gives_active(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api)
    await set_lifecycle(api, True)
    async with app.state.engine.begin() as conn:
        await conn.execute(text("UPDATE flag SET last_evaluated_at = now() WHERE key = 'new-ui'"))

    r = await set_lifecycle(api, False)

    assert r.status_code == 200, r.text
    assert r.json()["lifecycle"] == "active"
    assert r.json()["last_evaluated_at"] is not None


async def test_a_project_viewer_cannot_set_lifecycle(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    async with app.state.engine.begin() as conn:
        demo_id = await conn.scalar(text("SELECT id FROM project WHERE key = 'demo'"))
    token = await seed_service_token(app, "viewer", scope="project", project_id=demo_id)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=bearer(token)
    ) as viewer:
        assert_error(await set_lifecycle(viewer, True), 403, "forbidden")

    assert (await api.get(F)).json()["lifecycle"] == "new"


async def test_list_filters_by_lifecycle(api: httpx.AsyncClient) -> None:
    await create_project(api)
    for key in ("fresh", "kept", "gone"):
        await create_flag(api, key=key)
    assert (
        await api.put("/api/projects/demo/flags/kept/lifecycle", json={"permanent": True})
    ).status_code == 200
    assert (await api.post("/api/projects/demo/flags/gone/archive")).status_code == 200

    assert await keys(api, lifecycle="permanent") == ["kept"]
    assert await keys(api, lifecycle="new") == ["fresh"]
    assert await keys(api, lifecycle="archived") == ["gone"]
    # lifecycle wins over archived
    assert await keys(api, lifecycle="archived", archived="false") == ["gone"]
    assert await keys(api, lifecycle="new", archived="true") == ["fresh"]
    assert await keys(api, lifecycle="stale") == []


async def test_unknown_lifecycle_filter_is_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.get("/api/projects/demo/flags", params={"lifecycle": "zombie"})

    assert_error(r, 422, "validation_error")


# --- the sweep job ---


def clock() -> FakeClock:
    """A clock at the real time: flags get their `created_at` from the database."""
    return FakeClock(datetime.now(UTC))


async def state(app: FastAPI, key: str = "new-ui", project: str = "demo") -> tuple[str, Any]:
    async with app.state.engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT f.lifecycle, f.last_evaluated_at FROM flag f"
                    " JOIN project p ON p.id = f.project_id WHERE p.key = :p AND f.key = :k"
                ),
                {"p": project, "k": key},
            )
        ).one()
    return row.lifecycle, row.last_evaluated_at


async def age(app: FastAPI, days: float, key: str = "new-ui") -> None:
    async with app.state.engine.begin() as conn:
        await conn.execute(
            text("UPDATE flag SET created_at = now() - make_interval(secs => :s) WHERE key = :k"),
            {"s": days * 86400, "k": key},
        )


async def test_an_evaluation_makes_a_new_flag_active(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    c = clock()
    hour = truncate_hour(c.now - timedelta(hours=1))
    await seed_counts(app, "demo", "production", "new-ui", hour)

    await make_scheduler(app, c).tick()

    assert await state(app) == ("active", hour)
    body = (await api.get(F)).json()
    assert body["lifecycle"] == "active"
    assert datetime.fromisoformat(body["last_evaluated_at"]) == hour


async def test_a_never_evaluated_old_flag_is_stale(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    await create_flag(api, key="young")
    await age(app, 31)
    await age(app, 29, key="young")

    await make_scheduler(app, clock()).tick()

    assert await state(app) == ("stale", None)
    assert await state(app, "young") == ("new", None)


async def test_an_active_flag_not_evaluated_for_long_is_stale(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api)
    await age(app, 60)
    c = clock()
    hour = truncate_hour(c.now - timedelta(days=40))
    await seed_counts(app, "demo", "development", "new-ui", hour)
    async with app.state.engine.begin() as conn:
        await conn.execute(text("UPDATE flag SET lifecycle = 'active'"))

    await make_scheduler(app, c).tick()

    assert await state(app) == ("stale", hour)


async def test_a_new_evaluation_makes_a_stale_flag_active(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api)
    await age(app, 31)
    c = clock()
    scheduler = make_scheduler(app, c)
    await scheduler.tick()
    assert (await state(app))[0] == "stale"

    hour = truncate_hour(c.now)
    await seed_counts(app, "demo", "production", "new-ui", hour)
    c.advance(minutes=16)
    await scheduler.tick()

    assert await state(app) == ("active", hour)


async def test_stale_after_days_is_per_project(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_project(api, "other")
    await create_flag(api)
    await create_flag(api, "other")
    r = await api.patch("/api/projects/demo", json={"stale_after_days": 7})
    assert r.status_code == 200, r.text
    await age(app, 8)

    await make_scheduler(app, clock()).tick()

    assert (await state(app))[0] == "stale"
    assert (await state(app, project="other"))[0] == "new"


async def test_counts_of_another_project_are_ignored(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_project(api, "other")
    await create_flag(api)
    c = clock()
    await seed_counts(app, "other", "production", "new-ui", truncate_hour(c.now))

    await make_scheduler(app, c).tick()

    assert await state(app) == ("new", None)


async def test_permanent_and_archived_flags_keep_their_state(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api, key="kept")
    await create_flag(api, key="gone")
    await api.put("/api/projects/demo/flags/kept/lifecycle", json={"permanent": True})
    await api.post("/api/projects/demo/flags/gone/archive")
    await age(app, 60, key="kept")
    await age(app, 60, key="gone")
    c = clock()
    old = truncate_hour(c.now - timedelta(days=40))
    for key in ("kept", "gone"):
        await seed_counts(app, "demo", "production", key, old)

    await make_scheduler(app, c).tick()

    assert await state(app, "kept") == ("permanent", old)
    assert await state(app, "gone") == ("new", None)


async def test_a_state_change_is_audited_as_the_scheduler(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api)
    c = clock()
    hour = truncate_hour(c.now)
    await seed_counts(app, "demo", "production", "new-ui", hour)

    await make_scheduler(app, c).tick()

    [entry] = await audit_entries(app, "flag.lifecycle.update")
    assert (entry["actor_kind"], entry["actor_id"], entry["actor_label"]) == (
        "system",
        None,
        "scheduler",
    )
    assert (entry["resource_type"], entry["resource_key"]) == ("flag", "new-ui")
    assert entry["project_id"] is not None
    assert entry["before"] == {"lifecycle": "new"}
    assert entry["after"] == {"lifecycle": "active", "last_evaluated_at": hour.isoformat()}


async def test_the_sweep_does_not_touch_updated_at(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    before = (await api.get(F)).json()["updated_at"]
    c = clock()
    await seed_counts(app, "demo", "production", "new-ui", truncate_hour(c.now))

    await make_scheduler(app, c).tick()

    body = (await api.get(F)).json()
    assert (body["lifecycle"], body["updated_at"]) == ("active", before)


async def test_the_sweep_runs_at_most_every_15_minutes(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_flag(api)
    c = clock()
    scheduler = make_scheduler(app, c)
    await scheduler.tick()
    await seed_counts(app, "demo", "production", "new-ui", truncate_hour(c.now))

    c.advance(minutes=5)
    await scheduler.tick()
    assert (await state(app))[0] == "new"

    c.advance(minutes=11)
    await scheduler.tick()
    assert (await state(app))[0] == "active"


async def test_a_tick_that_changed_a_flag_publishes_its_project(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api)
    await create_project(api, "other")
    await create_flag(api)
    await create_flag(api, "other")
    published = record_publishes(app)
    c = clock()
    await seed_counts(app, "demo", "production", "new-ui", truncate_hour(c.now))

    await make_scheduler(app, c).tick()

    async with app.state.engine.connect() as conn:
        demo_id = await conn.scalar(text("SELECT id FROM project WHERE key = 'demo'"))
    assert published == [demo_id]

    c.advance(minutes=16)
    await make_scheduler(app, c).tick()
    assert published == [demo_id]


async def test_the_job_counts_each_state_change(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    for key in ("a", "b"):
        await create_flag(api, key=key)
    c = clock()
    for key in ("a", "b"):
        await seed_counts(app, "demo", "production", key, truncate_hour(c.now))

    result = await run_lifecycle_sweep(app.state.sessionmaker, c.now)

    assert (result.ok, result.failed, len(result.projects)) == (2, 0, 1)
    again = await run_lifecycle_sweep(app.state.sessionmaker, c.now)
    assert (again.ok, again.projects) == (0, set())
