import hashlib
import re

import httpx
import pytest
from fastapi import FastAPI
from helpers import assert_error, create_project, create_sdk_key
from sqlalchemy import func, select

from megooflags_api.models import Environment, SdkKey
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.services.sdk_keys import generate_key

KEYS = "/api/projects/demo/environments/production/sdk-keys"


@pytest.mark.parametrize("kind", ["server", "client"])
async def test_create_returns_secret_once(api: httpx.AsyncClient, kind: str) -> None:
    await create_project(api)

    created = await create_sdk_key(api, kind, name="backend")

    key = created["key"]
    assert key.startswith(f"mf_{kind}_")
    assert len(key) == 42
    assert created["prefix"] == key[:14]
    assert (created["name"], created["kind"]) == ("backend", kind)
    r = await api.get(KEYS)
    assert r.status_code == 200, r.text
    [item] = r.json()
    assert "key" not in item
    assert item["id"] == created["id"]
    assert item["prefix"] == created["prefix"]
    assert item["revoked_at"] is None


async def test_hash_stored_not_secret(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    key = (await create_sdk_key(api, "server"))["key"]

    async with app.state.sessionmaker() as session:
        row = await session.scalar(select(SdkKey))

    assert row is not None
    assert row.secret_hash == hashlib.sha256(key.encode()).hexdigest()
    columns = [getattr(row, c.key) for c in SdkKey.__table__.columns]
    assert key not in [str(v) for v in columns]


def test_generate_key_alphabet() -> None:
    keys = [
        generate_key(kind) for kind in (SdkKeyKind.SERVER, SdkKeyKind.CLIENT) for _ in range(100)
    ]

    assert len(set(keys)) == 200
    for key in keys:
        assert re.fullmatch(r"mf_(server|client)_[0-9A-Za-z]{32}", key)


async def test_revoke(api: httpx.AsyncClient) -> None:
    await create_project(api)
    created = await create_sdk_key(api, "client")

    r = await api.delete(f"{KEYS}/{created['id']}")

    assert r.status_code == 204, r.text
    [item] = (await api.get(KEYS)).json()
    assert item["revoked_at"] is not None
    r = await api.delete(f"{KEYS}/{created['id']}")
    assert r.status_code == 204, r.text
    [again] = (await api.get(KEYS)).json()
    assert again["revoked_at"] == item["revoked_at"]


async def test_revoke_other_environment_404(api: httpx.AsyncClient) -> None:
    await create_project(api)
    created = await create_sdk_key(api, "server")

    r = await api.delete(f"/api/projects/demo/environments/development/sdk-keys/{created['id']}")

    assert_error(r, 404, "not_found")
    [item] = (await api.get(KEYS)).json()
    assert item["revoked_at"] is None


async def test_unknown_env_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    assert_error(await api.get("/api/projects/demo/environments/nope/sdk-keys"), 404, "not_found")
    r = await api.post(
        "/api/projects/demo/environments/nope/sdk-keys", json={"name": "k", "kind": "server"}
    )
    assert_error(r, 404, "not_found")


async def test_invalid_kind_422(api: httpx.AsyncClient) -> None:
    await create_project(api)

    r = await api.post(KEYS, json={"name": "k", "kind": "admin"})

    assert r.status_code == 422, r.text


async def test_environment_delete_cascades(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_sdk_key(api, "server")
    await create_sdk_key(api, "client")
    async with app.state.sessionmaker() as session:
        env_id = await session.scalar(select(Environment.id).where(Environment.key == "production"))

    r = await api.delete("/api/projects/demo/environments/production")

    assert r.status_code == 204, r.text
    async with app.state.sessionmaker() as session:
        count = await session.scalar(
            select(func.count()).select_from(SdkKey).where(SdkKey.environment_id == env_id)
        )
    assert count == 0
