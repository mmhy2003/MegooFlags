import asyncio
import gc
import hashlib
import json
import random
import re
import time
import uuid
import weakref
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import count_statements, create_flag, create_project
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_spec_schemas import ruleset_schema

from megooflags_api.evaluation import Ruleset
from megooflags_api.models import Environment, Flag, Project
from megooflags_api.services import ruleset_cache
from megooflags_api.services.ruleset import EnvRef, FlagParts, RulesetParts, build_ruleset
from megooflags_api.services.ruleset_cache import CachedRuleset, RulesetCache

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
    build = ruleset_cache.build_parts

    async def counting(session: AsyncSession, environment: EnvRef) -> RulesetParts:
        nonlocal calls
        calls += 1
        return await build(session, environment)

    monkeypatch.setattr(ruleset_cache, "build_parts", counting)
    cache = RulesetCache()

    async def get() -> Ruleset:
        async with app.state.sessionmaker() as session:
            return await cache.get(session, environment)

    results = await asyncio.gather(*(get() for _ in range(10)))

    assert calls == 1
    assert all(r is results[0] for r in results)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def entry(
    app: FastAPI, cache: RulesetCache, max_age: float = 1.0, env: str = "production"
) -> tuple[CachedRuleset, list[str]]:
    async with app.state.sessionmaker() as session:
        environment = await load_environment(session, env)
        with count_statements(app.state.engine) as statements:
            result = await cache.get_entry(session, environment, max_age=max_age)
    return result, statements


async def test_entry_body_and_etag(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)

    result, _ = await entry(app, RulesetCache(), max_age=0.0)

    async with app.state.sessionmaker() as session:
        raw = await build_ruleset(session, await load_environment(session))
    body = json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode()
    assert result.body == body
    assert result.etag == '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    Draft202012Validator(ruleset_schema()).validate(json.loads(result.body))
    assert result.ruleset.environment == "production"


# A locale collation in Postgres orders these keys unlike Python; the body keeps the database order.
ORDER_KEYS = ("b", "ab", "a_b", "a.b", "a-b")


async def setup_fragments(api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    for key in ORDER_KEYS:
        await create_flag(api, key=key)
    variations = [{"key": "de", "value": "grün"}, {"key": "en", "value": "green"}]
    await create_flag(api, key="umlaut", type="string", variations=variations, off_variation="en")
    r = await api.put(
        f"{FLAGS}/umlaut/environments/production",
        json={
            "version": 1,
            "enabled": True,
            "rules": [
                {
                    "description": "größer",
                    "clauses": [{"attribute": "app", "operator": "semver_gt", "values": ["1.0.0"]}],
                    "variation": "de",
                }
            ],
            "fallthrough": {"variation": "en"},
        },
    )
    assert r.status_code == 200, r.text


async def test_entry_body_from_fragments(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_fragments(api)

    result, _ = await entry(app, RulesetCache(), max_age=0.0)

    async with app.state.sessionmaker() as session:
        raw = await build_ruleset(session, await load_environment(session))
    body = json.dumps(raw, separators=(",", ":"), ensure_ascii=False).encode()
    assert "grün".encode() in result.body and "größer".encode() in result.body
    assert result.body == body
    assert result.etag == '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    Draft202012Validator(ruleset_schema()).validate(json.loads(result.body))
    assert result.sorted_keys == tuple(sorted(result.ruleset.flags))


async def test_fingerprint_has_three_parts(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    cache = RulesetCache()
    async with app.state.sessionmaker() as session:
        environment_id = (await load_environment(session)).id

    async def parts() -> list[str]:
        await entry(app, cache, max_age=0.0)
        return cache._entries[environment_id].cached.fingerprint.split("|")

    first = await parts()
    assert len(first) == 3
    assert all(re.fullmatch(r"[0-9a-f]{32}", p) for p in first)

    r = await api.post(f"{PRODUCTION}/toggle", json={"enabled": False})
    assert r.status_code == 200, r.text
    toggled = await parts()
    assert toggled[0] != first[0]
    assert toggled[1:] == first[1:]

    r = await api.put(f"{SEGMENTS}/beta", json={"included": ["u1"], "excluded": [], "rules": []})
    assert r.status_code == 200, r.text
    segment = await parts()
    assert segment[:2] == toggled[:2]
    assert segment[2] != toggled[2]


async def test_entry_has_sorted_keys(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)

    result, _ = await entry(app, RulesetCache(), max_age=0.0)

    assert result.ruleset.flags
    assert result.sorted_keys == tuple(sorted(result.ruleset.flags))


async def test_max_age_hit_is_zero_statements(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(clock=clock)
    first, _ = await entry(app, cache)

    clock.now += 0.9
    second, statements = await entry(app, cache)

    assert second is first
    assert statements == []


async def test_max_age_expired_checks_fingerprint(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(clock=clock)
    first, _ = await entry(app, cache)

    clock.now += 1.5
    second, statements = await entry(app, cache)
    clock.now += 0.5
    third, again = await entry(app, cache)

    assert second is first
    assert len(statements) == 1
    assert third is first
    assert again == []  # the fingerprint check renewed checked_at


async def test_concurrent_stale_lookups_check_fingerprint_once(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(clock=clock)
    first, _ = await entry(app, cache)
    async with app.state.sessionmaker() as session:
        environment = await load_environment(session)

    async def get() -> CachedRuleset:
        async with app.state.sessionmaker() as session:
            return await cache.get_entry(session, environment, max_age=1.0)

    clock.now += 1.5
    with count_statements(app.state.engine) as statements:
        results = await asyncio.gather(*(get() for _ in range(10)))

    assert all(r is first for r in results)
    assert len(statements) == 1


async def test_rebuild_does_not_block_event_loop(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_demo(api)
    parse = ruleset_cache.parse_ruleset

    def slow_parse(raw: dict[str, Any]) -> Ruleset:
        time.sleep(0.3)  # stands in for parsing a large ruleset
        return parse(raw)

    monkeypatch.setattr(ruleset_cache, "parse_ruleset", slow_parse)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        result, _ = await entry(app, RulesetCache(), max_age=0.0)
    finally:
        task.cancel()

    assert result.ruleset.environment == "production"
    assert ticks >= 20  # 0.3 s of parsing would allow none while it blocks the loop


async def test_invalidate_forces_check(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(clock=clock)
    first, _ = await entry(app, cache)
    async with app.state.sessionmaker() as session:
        project_id = (await load_environment(session)).project_id

    cache.invalidate(project_id)
    second, statements = await entry(app, cache)

    assert second is first
    assert len(statements) == 1

    r = await api.post(f"{PRODUCTION}/kill")
    assert r.status_code == 200, r.text
    stale, _ = await entry(app, cache)
    assert stale is first
    cache.invalidate(project_id)
    fresh, _ = await entry(app, cache)
    assert fresh is not first
    assert fresh.ruleset.flags["color"].enabled is False


async def test_invalidate_other_project_no_effect(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    await create_project(api, key="other")
    clock = FakeClock()
    cache = RulesetCache(clock=clock)
    first, _ = await entry(app, cache)
    async with app.state.sessionmaker() as session:
        other = await session.scalar(select(Project.id).where(Project.key == "other"))
    assert other is not None

    cache.invalidate(other)
    second, statements = await entry(app, cache)

    assert second is first
    assert statements == []


async def test_etag_stable_when_content_same(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    cache = RulesetCache()
    first, _ = await entry(app, cache, max_age=0.0)

    r = await api.patch(COLOR, json={"name": "Colour"})
    assert r.status_code == 200, r.text
    second, _ = await entry(app, cache, max_age=0.0)

    assert second.fingerprint != first.fingerprint
    assert second.etag == first.etag
    assert second.body == first.body


async def settle(cache: RulesetCache) -> None:
    """Wait for the background checks that lookups started."""
    while cache._refreshes:
        await asyncio.gather(*cache._refreshes.values())


async def test_stale_entry_served_while_checked_in_background(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(app.state.sessionmaker, clock=clock)
    first, _ = await entry(app, cache)

    clock.now += 1.5
    with count_statements(app.state.engine) as background:
        stale, statements = await entry(app, cache)
        await settle(cache)
    renewed, again = await entry(app, cache)

    assert stale is first
    assert statements == []  # the request itself ran no query
    assert len(background) == 2  # loading the environment, then one fingerprint check
    assert "md5(" in background[1]
    assert renewed is first
    assert again == []  # the background check renewed checked_at


async def test_stale_lookups_do_not_wait_for_the_check(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(app.state.sessionmaker, clock=clock)
    first, _ = await entry(app, cache)
    async with app.state.sessionmaker() as session:
        environment = await load_environment(session)
    checks = 0
    fingerprint = cache._fingerprint

    async def slow(session: AsyncSession, environment: Environment) -> tuple[float, str]:
        nonlocal checks
        checks += 1
        await asyncio.sleep(0.5)  # stands in for the ~50 ms query under load
        return await fingerprint(session, environment)

    monkeypatch.setattr(cache, "_fingerprint", slow)

    async def get() -> CachedRuleset:
        async with app.state.sessionmaker() as session:
            return await cache.get_entry(session, environment, max_age=1.0)

    clock.now += 1.5
    started = time.monotonic()
    results = await asyncio.gather(*(get() for _ in range(10)))
    elapsed = time.monotonic() - started
    await settle(cache)

    assert all(r is first for r in results)
    assert elapsed < 0.25
    assert checks == 1


async def test_peek_needs_no_session(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    async with app.state.sessionmaker() as session:
        environment = EnvRef.of(await load_environment(session))
    clock = FakeClock()
    cache = RulesetCache(clock=clock)

    assert cache.peek(environment, 1.0) is None
    first, _ = await entry(app, cache)
    assert cache.peek(environment, 1.0) is first
    cache.invalidate(environment.project_id)
    assert cache.peek(environment, 1.0) is None

    cache = RulesetCache(app.state.sessionmaker, clock=clock)
    first, _ = await entry(app, cache)
    clock.now += 1.5
    with count_statements(app.state.engine) as statements:
        stale = cache.peek(environment, 1.0)
        again = cache.peek(environment, 1.0)
    assert stale is first
    assert again is first
    assert statements == []
    assert len(cache._refreshes) == 1
    await settle(cache)
    assert cache.peek(environment, 1.0) is first


async def test_background_check_picks_up_a_write(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(app.state.sessionmaker, clock=clock)
    first, _ = await entry(app, cache)
    r = await api.post(f"{PRODUCTION}/kill")
    assert r.status_code == 200, r.text

    clock.now += 1.5
    stale, _ = await entry(app, cache)
    await settle(cache)
    fresh, statements = await entry(app, cache)

    assert stale is first
    assert fresh is not first
    assert fresh.ruleset.flags["color"].enabled is False
    assert statements == []


async def test_invalidated_entry_is_never_served_stale(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(app.state.sessionmaker, clock=clock)
    first, _ = await entry(app, cache)
    async with app.state.sessionmaker() as session:
        project_id = (await load_environment(session)).project_id
    r = await api.post(f"{PRODUCTION}/kill")
    assert r.status_code == 200, r.text

    cache.invalidate(project_id)
    fresh, _ = await entry(app, cache)

    assert fresh is not first
    assert fresh.ruleset.flags["color"].enabled is False


async def test_invalidation_during_a_check_is_kept(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(app.state.sessionmaker, clock=clock)
    first, _ = await entry(app, cache)
    async with app.state.sessionmaker() as session:
        project_id = (await load_environment(session)).project_id
    fingerprint = cache._fingerprint

    async def racing(session: AsyncSession, environment: Environment) -> tuple[float, str]:
        result = await fingerprint(session, environment)
        # A write commits and its change message arrives after the query read the fingerprint.
        r = await api.post(f"{PRODUCTION}/kill")
        assert r.status_code == 200, r.text
        cache.invalidate(project_id)
        return result

    monkeypatch.setattr(cache, "_fingerprint", racing)
    clock.now += 1.5
    await entry(app, cache)
    await settle(cache)
    monkeypatch.setattr(cache, "_fingerprint", fingerprint)

    fresh, _ = await entry(app, cache)

    assert fresh is not first
    assert fresh.ruleset.flags["color"].enabled is False


async def test_failed_background_check_is_retried(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_demo(api)
    clock = FakeClock()
    cache = RulesetCache(app.state.sessionmaker, clock=clock)
    first, _ = await entry(app, cache)
    fingerprint = cache._fingerprint
    calls = 0

    async def flaky(session: AsyncSession, environment: Environment) -> tuple[float, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("database restarting")
        return await fingerprint(session, environment)

    monkeypatch.setattr(cache, "_fingerprint", flaky)
    clock.now += 1.5
    stale, _ = await entry(app, cache)
    await settle(cache)
    again, _ = await entry(app, cache)
    await settle(cache)

    assert stale is first
    assert again is first
    assert calls == 2
    assert cache._locks == {}


async def test_locks_are_released(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_demo(api)
    cache = RulesetCache(app.state.sessionmaker, max_environments=1)

    for env in ("development", "staging", "production"):
        await entry(app, cache, max_age=0.0, env=env)
        await entry(app, cache, max_age=1.0, env=env)

    async def failing(session: AsyncSession, environment: EnvRef) -> RulesetParts:
        raise LookupError("environment deleted")

    monkeypatch.setattr(ruleset_cache, "build_parts", failing)
    async with app.state.sessionmaker() as session:
        environment = EnvRef.of(await load_environment(session))
        gone = EnvRef(id=uuid.uuid4(), project_id=environment.project_id, key="gone")
        for max_age in (0.0, 1.0):
            with pytest.raises(LookupError):
                await cache.get_entry(session, gone, max_age=max_age)

    assert cache._locks == {}


async def assert_same_as_full(
    app: FastAPI, cache: RulesetCache, env: str = "production"
) -> CachedRuleset:
    """The persistent cache's entry after a check equals a fresh full build."""
    async with app.state.sessionmaker() as session:
        environment = await load_environment(session, env)
        got = await cache.get_entry(session, environment, max_age=0)
        full = await RulesetCache().get_entry(session, environment, max_age=0)
    assert got.body == full.body
    assert got.etag == full.etag
    assert got.sorted_keys == full.sorted_keys
    assert got.ruleset == full.ruleset  # compiled patterns compare by pattern and flags
    return got


class BuildSpy:
    """Records the full and the per-flag builds of ruleset_cache."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.flags: list[set[uuid.UUID]] = []
        self.parts = 0
        build_flags = ruleset_cache.build_flags
        build_parts = ruleset_cache.build_parts

        async def flags(
            session: AsyncSession, environment: EnvRef, flag_ids: Any = None
        ) -> FlagParts:
            self.flags.append(set(flag_ids))
            return await build_flags(session, environment, flag_ids)

        async def parts(session: AsyncSession, environment: EnvRef) -> RulesetParts:
            self.parts += 1
            return await build_parts(session, environment)

        monkeypatch.setattr(ruleset_cache, "build_flags", flags)
        monkeypatch.setattr(ruleset_cache, "build_parts", parts)


async def flag_id(app: FastAPI, key: str) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        found = await session.scalar(
            select(Flag.id)
            .join(Project, Project.id == Flag.project_id)
            .where(Project.key == "demo", Flag.key == key)
        )
    assert found is not None
    return found


WRITE_BY_NAME = {w[0]: w[1] for w in WRITES}


@pytest.mark.parametrize("write", [w[1] for w in WRITES], ids=[w[0] for w in WRITES])
async def test_write_matches_full_build(app: FastAPI, api: httpx.AsyncClient, write: Write) -> None:
    await setup_demo(api)
    cache = RulesetCache()
    await assert_same_as_full(app, cache)

    r = await write(api)

    assert r.status_code in (200, 201, 204), r.text
    await assert_same_as_full(app, cache)


@pytest.mark.parametrize("name", ["config_put", "toggle", "kill", "copy"])
async def test_incremental_for_version_writes(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    await setup_demo(api)
    cache = RulesetCache()
    first, _ = await entry(app, cache, max_age=0.0)
    spy = BuildSpy(monkeypatch)

    r = await WRITE_BY_NAME[name](api)
    assert r.status_code == 200, r.text
    got, _ = await entry(app, cache, max_age=0.0)

    assert spy.flags == [{await flag_id(app, "color")}]
    assert spy.parts == 0
    assert got is not first
    assert await assert_same_as_full(app, cache) is got


@pytest.mark.parametrize("name", ["flag_create", "archive", "variation_patch", "segment_put"])
async def test_full_build_for_structural_writes(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    await setup_demo(api)
    cache = RulesetCache()
    await entry(app, cache, max_age=0.0)
    spy = BuildSpy(monkeypatch)

    r = await WRITE_BY_NAME[name](api)
    assert r.status_code in (200, 201), r.text
    await entry(app, cache, max_age=0.0)

    assert spy.parts == 1
    assert spy.flags == []


async def test_toggled_twice_between_checks(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    cache = RulesetCache()
    first = await assert_same_as_full(app, cache)

    for enabled in (False, True):
        r = await api.post(f"{PRODUCTION}/toggle", json={"enabled": enabled})
        assert r.status_code == 200, r.text

    got = await assert_same_as_full(app, cache)
    assert got.ruleset.flags["color"].enabled is True
    assert got.fingerprint != first.fingerprint


async def test_toggled_then_archived(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)
    cache = RulesetCache()
    await assert_same_as_full(app, cache)

    r = await api.post(f"{PRODUCTION}/toggle", json={"enabled": False})
    assert r.status_code == 200, r.text
    r = await api.post(f"{COLOR}/archive")
    assert r.status_code == 200, r.text

    got = await assert_same_as_full(app, cache)
    assert "color" not in got.ruleset.flags


async def random_write(api: httpx.AsyncClient, rng: random.Random, i: int) -> httpx.Response:
    if rng.randrange(8) == 0:
        if rng.random() < 0.5:
            return await api.put(
                f"{SEGMENTS}/beta", json={"included": [f"u{i}"], "excluded": [], "rules": []}
            )
        return await api.patch(f"{FLAGS}/umlaut/variations/de", json={"value": f"grün-{i}"})
    production = f"{FLAGS}/{rng.choice(ORDER_KEYS)}/environments/production"
    kind = rng.choice(("toggle", "kill", "config", "target"))
    if kind == "toggle":
        return await api.post(f"{production}/toggle", json={"enabled": rng.random() < 0.5})
    if kind == "kill":
        return await api.post(f"{production}/kill")
    version = (await api.get(production)).json()["version"]
    config: dict[str, Any] = {
        "version": version,
        "enabled": True,
        "fallthrough": {"variation": rng.choice(("true", "false"))},
    }
    if kind == "config":
        config["rules"] = [
            {
                "description": "größer",
                "clauses": [{"attribute": "app", "operator": "semver_gt", "values": ["1.0.0"]}],
                "variation": rng.choice(("true", "false")),
            }
        ]
    else:
        config["targets"] = [{"variation": "true", "values": [f"u{i}"]}]
    return await api.put(production, json=config)


async def test_random_writes_match_full_build(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_fragments(api)
    cache = RulesetCache()
    await assert_same_as_full(app, cache)
    spy = BuildSpy(monkeypatch)
    rng = random.Random(459)

    for i in range(40):
        r = await random_write(api, rng, i)
        assert r.status_code == 200, r.text
        if rng.random() < 0.5:  # several writes pile up between checks
            await assert_same_as_full(app, cache)
    await assert_same_as_full(app, cache)

    assert spy.flags  # the incremental path ran
    assert any(len(ids) > 1 for ids in spy.flags)


async def test_incremental_failure_falls_back(
    app: FastAPI,
    api: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_demo(api)
    cache = RulesetCache()
    await entry(app, cache, max_age=0.0)

    async def failing(session: AsyncSession, environment: EnvRef, flag_ids: Any = None) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(ruleset_cache, "build_flags", failing)
    r = await api.post(f"{PRODUCTION}/toggle", json={"enabled": False})
    assert r.status_code == 200, r.text

    got = await assert_same_as_full(app, cache)

    assert got.ruleset.flags["color"].enabled is False
    assert any("incremental ruleset rebuild failed" in r.getMessage() for r in caplog.records)


async def test_many_changed_flags_take_the_full_build(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await setup_fragments(api)
    cache = RulesetCache()
    await entry(app, cache, max_age=0.0)
    monkeypatch.setattr(ruleset_cache, "INCREMENTAL_MAX_FLAGS", 1)
    spy = BuildSpy(monkeypatch)

    for key in ("a-b", "b"):
        r = await api.post(f"{FLAGS}/{key}/environments/production/toggle", json={"enabled": True})
        assert r.status_code == 200, r.text
    await assert_same_as_full(app, cache)

    assert spy.flags == []
    assert spy.parts == 2  # the persistent cache, then the fresh one


@pytest.fixture
def unfreeze() -> Iterator[None]:
    yield
    gc.unfreeze()


@pytest.fixture
def freezing(monkeypatch: pytest.MonkeyPatch, unfreeze: None) -> None:
    """Every full build may freeze: no size threshold, no interval."""
    monkeypatch.setattr(ruleset_cache, "FREEZE_MIN_FLAGS", 1)
    monkeypatch.setattr(ruleset_cache, "FREEZE_INTERVAL", 0.0)


class FreezeSpy:
    """Counts the freezes of ruleset_cache, which still run."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        freeze = ruleset_cache._freeze_long_lived

        def counted() -> None:
            self.count += 1
            freeze()

        monkeypatch.setattr(ruleset_cache, "_freeze_long_lived", counted)


def collector_tracks(obj: object) -> bool:
    """`obj` is in a generation the cyclic collector scans, not in the frozen one."""
    return any(o is obj for o in gc.get_objects())


class Cycle:
    def __init__(self) -> None:
        self.me = self


async def test_full_build_freezes_the_ruleset(
    app: FastAPI, api: httpx.AsyncClient, freezing: None
) -> None:
    await setup_demo(api)

    ruleset = await cached(app, RulesetCache(freeze_full_builds=True))

    assert not collector_tracks(ruleset.flags)


async def test_cache_does_not_freeze_by_default(app: FastAPI, api: httpx.AsyncClient) -> None:
    await setup_demo(api)

    ruleset = await cached(app, RulesetCache())

    assert collector_tracks(ruleset.flags)


async def test_next_full_build_reclaims_what_the_last_one_froze(
    app: FastAPI, api: httpx.AsyncClient, freezing: None
) -> None:
    await setup_demo(api)
    cache = RulesetCache(freeze_full_builds=True)
    cycle = Cycle()
    alive = weakref.ref(cycle)
    await cached(app, cache)  # freezes the cycle with everything else
    del cycle
    gc.collect()
    assert alive() is not None  # frozen: the collector no longer sees it

    await create_flag(api, key="new")  # a new flag: a full build
    ruleset = await cached(app, cache)

    assert "new" in ruleset.flags
    assert alive() is None


async def test_incremental_rebuild_does_not_freeze(
    app: FastAPI, api: httpx.AsyncClient, freezing: None
) -> None:
    await setup_demo(api)
    cache = RulesetCache(freeze_full_builds=True)
    await cached(app, cache)
    r = await api.post(f"{PRODUCTION}/toggle", json={"enabled": False})
    assert r.status_code == 200, r.text

    ruleset = await cached(app, cache)

    assert ruleset.flags["color"].enabled is False
    assert collector_tracks(ruleset.flags)


async def test_small_ruleset_is_not_frozen(
    app: FastAPI, api: httpx.AsyncClient, unfreeze: None
) -> None:
    await setup_demo(api)  # one flag, far below FREEZE_MIN_FLAGS

    ruleset = await cached(app, RulesetCache(freeze_full_builds=True))

    assert collector_tracks(ruleset.flags)


async def test_rapid_full_builds_freeze_once_then_once_more_after_the_interval(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, freezing: None
) -> None:
    monkeypatch.setattr(ruleset_cache, "FREEZE_INTERVAL", 0.5)
    spy = FreezeSpy(monkeypatch)
    await setup_demo(api)
    cache = RulesetCache(freeze_full_builds=True, clock=lambda: 0.0)  # the interval never ends
    await cached(app, cache)
    for key in ("a", "b", "c"):
        await create_flag(api, key=key)  # each one a full build
        await cached(app, cache)

    assert spy.count == 1  # the later builds wait for the interval
    await asyncio.sleep(1.0)
    assert spy.count == 2  # one deferred freeze covers them all
    await cache.aclose()


async def test_aclose_cancels_a_deferred_freeze(
    app: FastAPI, api: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, freezing: None
) -> None:
    monkeypatch.setattr(ruleset_cache, "FREEZE_INTERVAL", 0.5)
    spy = FreezeSpy(monkeypatch)
    await setup_demo(api)
    cache = RulesetCache(freeze_full_builds=True, clock=lambda: 0.0)
    await cached(app, cache)
    await create_flag(api, key="new")
    await cached(app, cache)

    await cache.aclose()
    await asyncio.sleep(1.0)

    assert spy.count == 1


def test_served_app_freezes_full_builds() -> None:
    from megooflags_api import main

    assert main.app.state.ruleset_cache.freeze_full_builds is True
    assert RulesetCache().freeze_full_builds is False
