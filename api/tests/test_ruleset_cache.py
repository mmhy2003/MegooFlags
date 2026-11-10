import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import count_statements, create_flag, create_project
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.evaluation import Ruleset
from megooflags_api.models import Environment, Project
from megooflags_api.services import ruleset_cache
from megooflags_api.services.ruleset_cache import RulesetCache

FLAGS = "/api/projects/demo/flags"
COLOR = f"{FLAGS}/color"
PRODUCTION = f"{COLOR}/environments/production"
SEGMENTS = "/api/projects/demo/segments"


async def setup_demo(api: httpx.AsyncClient) -> None:
    await create_project(api)
    variations = [{"key": k, "value": k} for k in ("red", "blue", "green")]
    await create_flag(api, key="color", type="string", variations=variations, off_variation="red")
    await create_flag(api, key="old")
    r = await api.post(f"{FLAGS}/old/archive")
    assert r.status_code == 200, r.text
    r = await api.post(f"{PRODUCTION}/toggle", json={"enabled": True})
    assert r.status_code == 200, r.text
    r = await api.put(
        f"{COLOR}/environments/development",
        json={"version": 1, "enabled": False, "fallthrough": {"variation": "blue"}},
    )
    assert r.status_code == 200, r.text
    r = await api.post(SEGMENTS, json={"key": "beta", "name": "Beta"})
    assert r.status_code == 201, r.text


async def load_environment(session: AsyncSession, env: str = "production") -> Environment:
    environment = await session.scalar(
        select(Environment)
        .join(Project, Project.id == Environment.project_id)
        .where(Project.key == "demo", Environment.key == env)
    )
    assert environment is not None
    return environment


async def cached(app: FastAPI, cache: RulesetCache, env: str = "production") -> Ruleset:
    async with app.state.sessionmaker() as session:
        return await cache.get(session, await load_environment(session, env))


async def test_miss_builds_ruleset(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)

    ruleset = await cached(app, RulesetCache())

    assert isinstance(ruleset, Ruleset)
    assert ruleset.environment == "production"
    assert set(ruleset.flags) == {"color"}
    assert ruleset.flags["color"].enabled is True


async def test_hit_is_one_statement(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    cache = RulesetCache()

    async with app.state.sessionmaker() as session:
        environment = await load_environment(session)
        first = await cache.get(session, environment)
        with count_statements(app.state.engine) as statements:
            second = await cache.get(session, environment)

    assert second is first
    assert len(statements) == 1


async def put_production(api: httpx.AsyncClient) -> httpx.Response:
    version = (await api.get(PRODUCTION)).json()["version"]
    return await api.put(
        PRODUCTION, json={"version": version, "enabled": True, "fallthrough": {"variation": "blue"}}
    )


Write = Callable[[httpx.AsyncClient], Awaitable[httpx.Response]]
Check = Callable[[Ruleset], bool]

WRITES: list[tuple[str, Write, Check]] = [
    ("config_put", put_production, lambda r: r.flags["color"].fallthrough.variation == "blue"),
    (
        "toggle",
        lambda api: api.post(f"{PRODUCTION}/toggle", json={"enabled": False}),
        lambda r: r.flags["color"].enabled is False,
    ),
    (
        "kill",
        lambda api: api.post(f"{PRODUCTION}/kill"),
        lambda r: r.flags["color"].enabled is False,
    ),
    (
        "copy",
        lambda api: api.post(f"{COLOR}/copy", json={"from": "development", "to": ["production"]}),
        lambda r: r.flags["color"].fallthrough.variation == "blue",
    ),
    (
        "variation_add",
        lambda api: api.post(
            f"{COLOR}/variations", json={"key": "pink", "name": "Pink", "value": "pink"}
        ),
        lambda r: "pink" in r.flags["color"].variations,
    ),
    (
        "variation_patch",
        lambda api: api.patch(f"{COLOR}/variations/red", json={"value": "crimson"}),
        lambda r: r.flags["color"].variations["red"].value == "crimson",
    ),
    (
        "variation_delete",
        lambda api: api.delete(f"{COLOR}/variations/green"),
        lambda r: "green" not in r.flags["color"].variations,
    ),
    (
        "off_variation",
        lambda api: api.patch(COLOR, json={"off_variation": "blue"}),
        lambda r: r.flags["color"].off_variation == "blue",
    ),
    (
        "archive",
        lambda api: api.post(f"{COLOR}/archive"),
        lambda r: "color" not in r.flags,
    ),
    (
        "restore",
        lambda api: api.post(f"{FLAGS}/old/restore"),
        lambda r: "old" in r.flags,
    ),
    (
        "segment_put",
        lambda api: api.put(
            f"{SEGMENTS}/beta", json={"included": ["u1"], "excluded": [], "rules": []}
        ),
        lambda r: r.segments["beta"].included == frozenset({"u1"}),
    ),
    (
        "segment_delete",
        lambda api: api.delete(f"{SEGMENTS}/beta"),
        lambda r: "beta" not in r.segments,
    ),
    (
        "flag_create",
        lambda api: api.post(FLAGS, json={"key": "new-ui", "name": "new-ui", "type": "boolean"}),
        lambda r: "new-ui" in r.flags,
    ),
]


@pytest.mark.parametrize(("write", "check"), [w[1:] for w in WRITES], ids=[w[0] for w in WRITES])
async def test_write_invalidates(
    app: FastAPI, api: httpx.AsyncClient, write: Write, check: Check
) -> None:
    await setup_demo(api)
    cache = RulesetCache()
    before = await cached(app, cache)
    assert not check(before)

    r = await write(api)

    assert r.status_code in (200, 201, 204), r.text
    after = await cached(app, cache)
    assert after is not before
    assert check(after)


async def test_lru_evicts_oldest(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    cache = RulesetCache(max_environments=2)

    development = await cached(app, cache, "development")
    staging = await cached(app, cache, "staging")
    await cached(app, cache, "production")

    assert await cached(app, cache, "staging") is staging
    assert await cached(app, cache, "development") is not development


async def test_concurrent_misses_build_once(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_demo(api)
    async with app.state.sessionmaker() as session:
        environment = await load_environment(session)
    calls = 0
    build = ruleset_cache.build_ruleset

    async def counting(session: AsyncSession, environment: Environment) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return await build(session, environment)

    monkeypatch.setattr(ruleset_cache, "build_ruleset", counting)
    cache = RulesetCache()

    async def get() -> Ruleset:
        async with app.state.sessionmaker() as session:
            return await cache.get(session, environment)

    results = await asyncio.gather(*(get() for _ in range(10)))

    assert calls == 1
    assert all(r is results[0] for r in results)
