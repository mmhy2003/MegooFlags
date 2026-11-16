"""What the SDK and OFREP routes read and write: rulesets, keys, events and the client rate limit.

The routes use only these interfaces, so one code path serves SDK traffic from Postgres and Valkey
(standalone and control plane, the adapters below) or from a worker's snapshot store.

The DB adapters hold the app and read its services at call time, so a test that replaces one of
them (for example `app.state.key_resolver`) changes what the routes use.
"""

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fastapi import FastAPI, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from megooflags_api.models import Environment, SdkKey
from megooflags_api.schemas.events import EventsIn
from megooflags_api.services.events import (
    CountKey,
    EvaluationCounter,
    add_counts,
    record_exposures,
    truncate_hour,
)
from megooflags_api.services.rate_limit import RateLimiter
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.ruleset_cache import SDK_MAX_AGE, CachedRuleset, RulesetCache
from megooflags_api.services.sdk_keys import KeyResolver, SdkPrincipal


class RulesetSource(Protocol):
    async def get_entry(self, environment: EnvRef) -> CachedRuleset:
        """The ruleset for an SDK read; it may be up to `SDK_MAX_AGE` seconds old."""
        ...


class KeySource(Protocol):
    async def resolve(self, key: str) -> SdkPrincipal | None: ...

    async def resolve_hash(self, secret_hash: str) -> SdkPrincipal | None: ...


class EventSink(Protocol):
    def record_counts(
        self, environment_id: uuid.UUID, results: Iterable[tuple[str, str | None]]
    ) -> None: ...

    async def store(self, environment_id: uuid.UUID, body: EventsIn, now: datetime) -> None: ...


class ClientLimiter(Protocol):
    async def hit(self, key_id: uuid.UUID, client_ip: str) -> int | None:
        """The Retry-After seconds when over the limit, else None."""
        ...


class StreamSource(Protocol):
    async def current(self, environment: EnvRef) -> CachedRuleset | None:
        """The fresh ruleset, or None when the environment is gone."""
        ...

    async def inactive_keys(self, key_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """The keys of `key_ids` that are revoked or expired."""
        ...


def _available() -> str | None:
    return None


@dataclass
class Delivery:
    rulesets: RulesetSource
    keys: KeySource
    events: EventSink
    limiter: ClientLimiter
    # Why SDK requests cannot be served (a worker's "not_ready" or "revoked"), else None.
    unavailable: Callable[[], str | None] = _available


def get_delivery(request: Request) -> Delivery:
    delivery: Delivery = request.app.state.delivery
    return delivery


class DbRulesetSource:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def get_entry(self, environment: EnvRef) -> CachedRuleset:
        cache: RulesetCache = self._app.state.ruleset_cache
        async with self._app.state.sessionmaker() as session:
            return await cache.get_entry(session, environment, max_age=SDK_MAX_AGE)


class DbKeySource:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def resolve(self, key: str) -> SdkPrincipal | None:
        resolver: KeyResolver = self._app.state.key_resolver
        async with self._app.state.sessionmaker() as session:
            return await resolver.resolve(session, key)

    async def resolve_hash(self, secret_hash: str) -> SdkPrincipal | None:
        resolver: KeyResolver = self._app.state.key_resolver
        async with self._app.state.sessionmaker() as session:
            return await resolver.resolve_hash(session, secret_hash)


class DbEventSink:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def record_counts(
        self, environment_id: uuid.UUID, results: Iterable[tuple[str, str | None]]
    ) -> None:
        counter: EvaluationCounter = self._app.state.evaluation_counter
        counter.record(environment_id, results)

    async def store(self, environment_id: uuid.UUID, body: EventsIn, now: datetime) -> None:
        async with self._app.state.sessionmaker() as session:
            await record_exposures(
                session,
                environment_id,
                body.exposures,
                persist_context=self._app.state.settings.persist_context,
                now=now,
            )
            counts: dict[CountKey, int] = {}
            for c in body.counts:
                key = (environment_id, c.flag_key, c.variant or "", truncate_hour(c.hour))
                counts[key] = counts.get(key, 0) + c.count
            await add_counts(session, counts)
            await session.commit()


class DbLimiter:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def hit(self, key_id: uuid.UUID, client_ip: str) -> int | None:
        limiter: RateLimiter = self._app.state.rate_limiter
        return await limiter.hit(key_id, client_ip)


class DbStreamSource:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], cache: RulesetCache) -> None:
        self._sessionmaker = sessionmaker
        self._cache = cache

    async def current(self, environment: EnvRef) -> CachedRuleset | None:
        async with self._sessionmaker() as session:
            exists = await session.scalar(
                select(Environment.id).where(Environment.id == environment.id)
            )
            if exists is None:
                return None
            return await self._cache.get_entry(session, environment, max_age=0)

    async def inactive_keys(self, key_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        async with self._sessionmaker() as session:
            return set(
                await session.scalars(
                    select(SdkKey.id).where(
                        SdkKey.id.in_(key_ids),
                        or_(SdkKey.revoked_at.is_not(None), SdkKey.expires_at <= func.now()),
                    )
                )
            )


def db_delivery(app: FastAPI) -> Delivery:
    """SDK delivery backed by Postgres and Valkey, through the app's services."""
    return Delivery(DbRulesetSource(app), DbKeySource(app), DbEventSink(app), DbLimiter(app))
