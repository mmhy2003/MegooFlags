"""`GET …/flags/{flag}/environments/{env}/evaluations`: windows, bucketing and empty fill."""

from datetime import UTC, datetime, timedelta

import httpx
from fastapi import FastAPI
from helpers import assert_error, create_flag, create_project
from sqlalchemy import select

from megooflags_api.models import Environment, Project
from megooflags_api.services.evaluation_counts import fill_series, series_window
from megooflags_api.services.events import add_counts, truncate_hour

E = "/api/projects/demo/flags/new-ui/environments/production/evaluations"
NOW = datetime(2026, 11, 13, 10, 25, tzinfo=UTC)


def test_window_24h() -> None:
    interval, starts = series_window("24h", NOW)

    assert interval == "hour"
    assert len(starts) == 24
    assert starts[0] == datetime(2026, 11, 12, 11, tzinfo=UTC)
    assert starts[-1] == datetime(2026, 11, 13, 10, tzinfo=UTC)
    assert all(b - a == timedelta(hours=1) for a, b in zip(starts, starts[1:], strict=False))


def test_window_7d() -> None:
    interval, starts = series_window("7d", NOW)

    assert interval == "day"
    assert starts == [datetime(2026, 11, d, tzinfo=UTC) for d in range(7, 14)]


def test_window_30d() -> None:
    interval, starts = series_window("30d", NOW)

    assert interval == "day"
    assert len(starts) == 30
    assert starts[0] == datetime(2026, 10, 15, tzinfo=UTC)
    assert starts[-1] == datetime(2026, 11, 13, tzinfo=UTC)


def test_fill_series_buckets_and_fills_empty() -> None:
    interval, starts = series_window("7d", NOW)
    d1, d2 = starts[1], starts[4]
    rows = [
        (d1 + timedelta(hours=3), "true", 2),
        (d1 + timedelta(hours=5), "true", 1),
        (d2 + timedelta(hours=1), "false", 4),
        (starts[0] - timedelta(hours=1), "true", 100),
    ]

    series = fill_series(interval, starts, rows)

    assert series.interval == "day"
    assert series.variants == ["false", "true"]
    assert [p.start for p in series.points] == starts
    by_start = {p.start: p.counts for p in series.points}
    assert by_start[d1] == {"true": 3, "false": 0}
    assert by_start[d2] == {"true": 0, "false": 4}
    for start in set(starts) - {d1, d2}:
        assert by_start[start] == {"true": 0, "false": 0}
    assert series.total == 7


async def seed(
    app: FastAPI, env_key: str, hour: datetime, variant: str, count: int, flag: str = "new-ui"
) -> None:
    async with app.state.sessionmaker() as session:
        env_id = await session.scalar(
            select(Environment.id)
            .join(Project, Project.id == Environment.project_id)
            .where(Project.key == "demo", Environment.key == env_key)
        )
        assert env_id is not None
        await add_counts(session, {(env_id, flag, variant, hour): count})
        await session.commit()


async def seed_demo(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    now = datetime.now(UTC)
    last_hour = truncate_hour(now) - timedelta(hours=1)
    await seed(app, "production", last_hour, "true", 3)
    await seed(app, "production", last_hour, "false", 1)
    await seed(app, "production", truncate_hour(now - timedelta(days=3)), "true", 5)


async def test_evaluations_last_24h(app: FastAPI, api: httpx.AsyncClient) -> None:
    await seed_demo(app, api)

    r = await api.get(E)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["interval"] == "hour"
    assert len(body["points"]) == 24
    assert body["total"] == 4
    assert body["variants"] == ["false", "true"]
    last_hour = truncate_hour(datetime.now(UTC)) - timedelta(hours=1)
    point = next(p for p in body["points"] if datetime.fromisoformat(p["start"]) == last_hour)
    assert point["counts"] == {"false": 1, "true": 3}


async def test_evaluations_7d_groups_by_day(app: FastAPI, api: httpx.AsyncClient) -> None:
    await seed_demo(app, api)

    r = await api.get(E, params={"range": "7d"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["interval"] == "day"
    assert len(body["points"]) == 7
    assert body["total"] == 9
    for point in body["points"]:
        start = datetime.fromisoformat(point["start"])
        assert (start.hour, start.minute) == (0, 0)


async def test_evaluations_only_this_flag_and_environment(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await seed_demo(app, api)
    hour = truncate_hour(datetime.now(UTC))
    await create_flag(api, key="other")
    await seed(app, "production", hour, "true", 50, flag="other")
    await seed(app, "staging", hour, "true", 70)

    r = await api.get(E, params={"range": "30d"})

    assert r.status_code == 200, r.text
    assert r.json()["total"] == 9


async def test_invalid_range_is_422(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    assert (await api.get(E, params={"range": "1y"})).status_code == 422


async def test_unknown_flag_is_404(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    r = await api.get("/api/projects/demo/flags/nope/environments/production/evaluations")
    assert_error(r, 404, "not_found")


async def test_unknown_environment_is_404(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_flag(api)
    r = await api.get("/api/projects/demo/flags/new-ui/environments/nope/evaluations")
    assert_error(r, 404, "not_found")
