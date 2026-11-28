import time
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import count_statements, create_flag, create_project
from sqlalchemy import select, text

from megooflags_api.models import Environment, Flag, Project
from megooflags_api.services import ruleset_cache
from megooflags_api.services.ruleset import EnvRef, FlagParts, build_flags, build_ruleset
from megooflags_api.services.ruleset_cache import RulesetCache

FLAGS = "/api/projects/big/flags"
FLAG_COUNT = 10_000

SEED = [
    "INSERT INTO flag (project_id, key, name, type, tags, owner)"
    " SELECT :pid, 'flag-' || lpad(i::text, 5, '0'), 'Flag ' || i, 'boolean',"
    "  CASE WHEN i % 10 = 0 THEN ARRAY['checkout'] ELSE ARRAY['misc'] END, 'team-' || (i % 7)"
    " FROM generate_series(1, 10000) i",
    "INSERT INTO variation (flag_id, key, name, value, sort_order)"
    " SELECT f.id, v.k, v.k, to_jsonb(v.b), v.o FROM flag f"
    " CROSS JOIN (VALUES ('true', true, 0), ('false', false, 1)) v(k, b, o)"
    " WHERE f.project_id = :pid",
    "UPDATE flag f SET off_variation_id = v.id FROM variation v"
    " WHERE v.flag_id = f.id AND v.key = 'false' AND f.project_id = :pid",
    "INSERT INTO flag_environment (flag_id, environment_id, fallthrough_variation_id)"
    " SELECT f.id, e.id, v.id FROM flag f JOIN environment e ON e.project_id = f.project_id"
    " JOIN variation v ON v.flag_id = f.id AND v.key = 'true' WHERE f.project_id = :pid",
]


async def seed_big_project(
    app: FastAPI, api: httpx.AsyncClient, environments: list[dict[str, Any]] | None = None
) -> None:
    await create_project(api, "big", environments)
    async with app.state.engine.begin() as conn:
        pid = await conn.scalar(text("SELECT id FROM project WHERE key = 'big'"))
        for statement in SEED:
            await conn.execute(text(statement), {"pid": pid})


async def list_page(
    app: FastAPI, api: httpx.AsyncClient, params: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    with count_statements(app.state.engine) as statements:
        r = await api.get(FLAGS, params=params)
    assert r.status_code == 200, r.text
    return r.json(), len(statements)


async def test_flag_list_statement_count_is_constant(app: FastAPI, api: httpx.AsyncClient) -> None:
    await seed_big_project(app, api)

    small, small_count = await list_page(app, api, {"limit": 10})
    large, large_count = await list_page(app, api, {"limit": 500})
    following, following_count = await list_page(
        app, api, {"limit": 500, "cursor": large["next_cursor"]}
    )

    assert len(small["items"]) == 10
    assert len(large["items"]) == 500
    assert all(len(item["environments"]) == 3 for item in large["items"])
    assert small_count == large_count <= 5  # 3, plus the API token and its role bindings
    assert following_count == large_count
    assert following["items"][0]["key"] == "flag-00501"
    assert len(following["items"]) == 500


async def test_tag_and_owner_filter_constant(app: FastAPI, api: httpx.AsyncClient) -> None:
    await seed_big_project(app, api)

    _, unfiltered_count = await list_page(app, api, {"limit": 500})
    tagged, tagged_count = await list_page(app, api, {"tag": "checkout", "limit": 500})
    owned, owned_count = await list_page(app, api, {"owner": "team-3", "limit": 500})

    assert len(tagged["items"]) == 500
    assert all(item["tags"] == ["checkout"] for item in tagged["items"])
    assert tagged_count == unfiltered_count <= 5  # 3, plus the API token and its role bindings
    assert len(owned["items"]) == 500
    assert all(item["owner"] == "team-3" for item in owned["items"])
    assert owned_count == unfiltered_count


async def test_add_environment_backfill_fast(app: FastAPI, api: httpx.AsyncClient) -> None:
    await seed_big_project(app, api)

    started = time.monotonic()
    r = await api.post("/api/projects/big/environments", json={"key": "qa", "name": "QA"})
    elapsed = time.monotonic() - started

    assert r.status_code == 201, r.text
    assert elapsed < 15
    async with app.state.engine.connect() as conn:
        backfilled = await conn.scalar(
            text(
                "SELECT count(*) FROM flag_environment fe"
                " JOIN environment e ON e.id = fe.environment_id"
                " JOIN project p ON p.id = e.project_id"
                " WHERE p.key = 'big' AND e.key = 'qa'"
            )
        )
    assert backfilled == FLAG_COUNT


async def test_create_flag_in_big_project(app: FastAPI, api: httpx.AsyncClient) -> None:
    environments = [{"key": k, "name": k.title()} for k in ("dev", "qa", "staging", "prod")]
    await create_project(api, "small", environments)
    with count_statements(app.state.engine) as small_statements:
        await create_flag(api, "small", key="one-more")
    await seed_big_project(app, api, environments)

    with count_statements(app.state.engine) as big_statements:
        flag = await create_flag(api, "big", key="one-more")

    assert len(flag["environments"]) == 4
    assert len(big_statements) == len(small_statements)
    async with app.state.engine.connect() as conn:
        rows = await conn.scalar(
            text(
                "SELECT count(*) FROM flag_environment fe JOIN flag f ON f.id = fe.flag_id"
                " JOIN project p ON p.id = f.project_id WHERE p.key = 'big' AND f.key = 'one-more'"
            )
        )
    assert rows == 4


async def production(session: Any, project: str) -> Environment:
    environment = await session.scalar(
        select(Environment)
        .join(Project, Project.id == Environment.project_id)
        .where(Project.key == project, Environment.key == "production")
    )
    assert environment is not None
    return environment


async def build_production(app: FastAPI, project: str) -> tuple[dict[str, Any], int]:
    async with app.state.sessionmaker() as session:
        environment = await production(session, project)
        with count_statements(app.state.engine) as statements:
            ruleset = await build_ruleset(session, environment)
    return ruleset, len(statements)


async def test_build_ruleset_statement_count_is_constant(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api, "demo")
    await create_flag(api, "demo")
    await seed_big_project(app, api)

    small, small_count = await build_production(app, "demo")
    big, big_count = await build_production(app, "big")

    assert len(small["flags"]) == 1
    assert len(big["flags"]) == FLAG_COUNT
    assert small_count == big_count <= 14


async def build_one_flag(app: FastAPI, project: str, key: str) -> tuple[dict[str, Any], int]:
    async with app.state.sessionmaker() as session:
        environment = await production(session, project)
        flag_id = await session.scalar(
            select(Flag.id)
            .join(Project, Project.id == Flag.project_id)
            .where(Project.key == project, Flag.key == key)
        )
        assert flag_id is not None
        with count_statements(app.state.engine) as statements:
            parts = await build_flags(session, environment, [flag_id])
    return parts.flags, len(statements)


async def test_build_flags_statement_count_is_constant(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await create_project(api, "demo")
    await create_flag(api, "demo", key="one")
    await seed_big_project(app, api)

    small, small_count = await build_one_flag(app, "demo", "one")
    big, big_count = await build_one_flag(app, "big", "flag-00042")

    assert list(small) == ["one"]
    assert list(big) == ["flag-00042"]
    assert small_count == big_count <= 9


async def toggle_rebuild(
    app: FastAPI, api: httpx.AsyncClient, project: str, key: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[set[uuid.UUID]], int, bytes, bytes]:
    """Warm a cache, toggle `key` in production, then rebuild while counting statements."""
    cache = RulesetCache()
    async with app.state.sessionmaker() as session:
        environment = EnvRef.of(await production(session, project))
        await cache.get_entry(session, environment, max_age=0)
    r = await api.post(
        f"/api/projects/{project}/flags/{key}/environments/production/toggle",
        json={"enabled": True},
    )
    assert r.status_code == 200, r.text
    calls: list[set[uuid.UUID]] = []
    build = ruleset_cache.build_flags

    async def spy(session: Any, environment: EnvRef, flag_ids: Any = None) -> FlagParts:
        calls.append(set(flag_ids))
        return await build(session, environment, flag_ids)

    with monkeypatch.context() as m:
        m.setattr(ruleset_cache, "build_flags", spy)
        async with app.state.sessionmaker() as session:
            with count_statements(app.state.engine) as statements:
                got = await cache.get_entry(session, environment, max_age=0)
    async with app.state.sessionmaker() as session:
        full = await RulesetCache().get_entry(session, environment, max_age=0)
    return calls, len(statements), got.body, full.body


async def test_toggle_rebuild_is_incremental(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await create_project(api, "demo")
    await create_flag(api, "demo", key="one")
    await seed_big_project(app, api)
    async with app.state.sessionmaker() as session:
        big_id = await session.scalar(select(Flag.id).where(Flag.key == "flag-00042"))

    _, small_count, small_body, small_full = await toggle_rebuild(
        app, api, "demo", "one", monkeypatch
    )
    calls, big_count, big_body, big_full = await toggle_rebuild(
        app, api, "big", "flag-00042", monkeypatch
    )

    assert small_body == small_full
    assert big_body == big_full
    assert calls == [{big_id}]
    assert big_count == small_count <= 1 + 1 + 9  # fingerprint, versions, one flag's build
