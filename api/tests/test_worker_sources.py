import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_worker_store import ENV_A, ENV_B, PROJECT_ID, key, new_store, snapshot

from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.services.cluster.snapshot_format import SnapshotKey
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.sdk_keys import hash_key
from megooflags_api.services.worker.sources import (
    MemoryRateLimiter,
    StoreKeySource,
    StoreRulesetSource,
    StoreStreamSource,
)

PAST = datetime(2020, 1, 1, tzinfo=UTC)
FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
ENV_A_REF = EnvRef(id=ENV_A, project_id=PROJECT_ID, key="production")
ENV_B_REF = EnvRef(id=ENV_B, project_id=PROJECT_ID, key="staging")
SERVER_KEY = "mf_server_" + "a" * 32


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


async def test_ruleset_source_serves_store_entry(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    snap = snapshot(ENV_A)
    store.apply(snap)

    entry = await StoreRulesetSource(store).get_entry(ENV_A_REF)

    assert entry.body == snap.ruleset
    assert entry == store.entry(ENV_A)


async def test_unknown_environment_raises(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    with pytest.raises(RuntimeError):
        await StoreRulesetSource(store).get_entry(ENV_A_REF)


async def test_key_source_resolve_and_resolve_hash(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    stored = key(1)
    by_secret = SnapshotKey(
        id=uuid.UUID(int=2),
        kind=SdkKeyKind.SERVER,
        hash=hash_key(SERVER_KEY),
        revoked_at=None,
        expires_at=None,
    )
    store.apply(snapshot(ENV_A, keys=(stored, by_secret)))
    source = StoreKeySource(store)

    principal = await source.resolve(SERVER_KEY)
    assert principal is not None
    assert principal.key_id == by_secret.id
    assert principal.environment == ENV_A_REF

    hashed = await source.resolve_hash(stored.hash)
    assert hashed is not None
    assert hashed.key_id == stored.id

    assert await source.resolve("mf_other__" + "a" * 32) is None
    assert await source.resolve(SERVER_KEY + "a") is None
    assert await source.resolve("mf_server_" + "b" * 32) is None
    assert await source.resolve_hash("f" * 64) is None


async def test_stream_source_current_none_after_remove(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.apply(snapshot(ENV_A))
    source = StoreStreamSource(store)

    current = await source.current(ENV_A_REF)
    assert current is not None
    assert current == store.entry(ENV_A)

    store.remove(ENV_A)
    assert await source.current(ENV_A_REF) is None


async def test_stream_source_inactive_keys(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.apply(snapshot(ENV_A, keys=(key(1), key(2, revoked_at=PAST))))
    store.apply(snapshot(ENV_B, keys=(key(3, expires_at=PAST), key(4, expires_at=FUTURE))))
    missing = uuid.UUID(int=5)
    source = StoreStreamSource(store)

    inactive = await source.inactive_keys({key(n).id for n in range(1, 5)} | {missing})

    assert inactive == {key(2).id, key(3).id, missing}
    assert await source.inactive_keys(set()) == set()


async def test_memory_limiter_window() -> None:
    clock = Clock(600.0 + 15.5)
    limiter = MemoryRateLimiter(2, clock=clock)
    key_id = uuid.uuid4()

    assert await limiter.hit(key_id, "10.0.0.1") is None
    assert await limiter.hit(key_id, "10.0.0.1") is None
    assert await limiter.hit(key_id, "10.0.0.1") == 45
    assert await limiter.hit(key_id, "10.0.0.2") is None
    assert await limiter.hit(uuid.uuid4(), "10.0.0.1") is None

    clock.now = 600.0 + 59.9
    assert await limiter.hit(key_id, "10.0.0.1") == 1

    clock.now = 660.0
    assert await limiter.hit(key_id, "10.0.0.1") is None
    assert await limiter.hit(key_id, "10.0.0.1") is None
    assert await limiter.hit(key_id, "10.0.0.1") == 60


async def test_memory_limiter_prunes_old_windows() -> None:
    clock = Clock(600.0)
    limiter = MemoryRateLimiter(100, clock=clock)
    for n in range(10):
        await limiter.hit(uuid.uuid4(), f"10.0.0.{n}")
    assert len(limiter) == 10

    clock.now = 660.0
    await limiter.hit(uuid.uuid4(), "10.0.0.1")
    assert len(limiter) == 1
