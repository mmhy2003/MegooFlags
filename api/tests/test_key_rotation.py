import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    assert_error,
    audit_entries,
    bearer,
    create_project,
    create_sdk_key,
    seed_service_token,
)
from sqlalchemy import update

from megooflags_api.models import SdkKey

KEYS = "/api/projects/demo/environments/production/sdk-keys"
RULESET = "/sdk/v1/ruleset"


async def rotate(api: httpx.AsyncClient, key_id: str, overlap: int) -> httpx.Response:
    return await api.post(f"{KEYS}/{key_id}/rotate", json={"overlap_seconds": overlap})


async def rotated(api: httpx.AsyncClient, key_id: str, overlap: int) -> dict[str, Any]:
    r = await rotate(api, key_id, overlap)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def listed(api: httpx.AsyncClient, key_id: str) -> dict[str, Any]:
    r = await api.get(KEYS)
    assert r.status_code == 200, r.text
    return next(k for k in r.json() if k["id"] == key_id)  # type: ignore[no-any-return]


async def expire_now(app: FastAPI, key_id: str) -> None:
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(SdkKey)
            .where(SdkKey.id == uuid.UUID(key_id))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()


async def ruleset_status(api: httpx.AsyncClient, key: str) -> int:
    return (await api.get(RULESET, headers=bearer(key))).status_code


async def test_rotate_returns_a_new_key_and_both_work(api: httpx.AsyncClient) -> None:
    await create_project(api)
    old = await create_sdk_key(api, "server", name="backend")

    new = await rotated(api, old["id"], 3600)

    assert new["key"] != old["key"]
    assert new["key"].startswith("mf_server_")
    assert (new["name"], new["kind"]) == ("backend", "server")
    assert new["rotated_from_id"] == old["id"]
    assert new["expires_at"] is None
    old_out = await listed(api, old["id"])
    assert old_out["revoked_at"] is None
    expires_at = datetime.fromisoformat(old_out["expires_at"])
    assert abs(expires_at - (datetime.now(UTC) + timedelta(seconds=3600))) < timedelta(seconds=30)
    new_out = await listed(api, new["id"])
    assert new_out["rotated_from_id"] == old["id"]
    assert "key" not in new_out
    assert await ruleset_status(api, old["key"]) == 200
    assert await ruleset_status(api, new["key"]) == 200


async def test_old_key_gets_403_once_expired(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    old = await create_sdk_key(api, "server")
    new = await rotated(api, old["id"], 3600)
    assert await ruleset_status(api, old["key"]) == 200

    await expire_now(app, old["id"])
    app.state.key_resolver.forget(uuid.UUID(old["id"]))

    r = await api.get(RULESET, headers=bearer(old["key"]))
    assert_error(r, 403, "forbidden")
    assert await ruleset_status(api, new["key"]) == 200


async def test_cached_key_expires_without_forget(api: httpx.AsyncClient) -> None:
    await create_project(api)
    old = await create_sdk_key(api, "server")
    new = await rotated(api, old["id"], 1)
    assert await ruleset_status(api, old["key"]) == 200  # now cached with its expires_at

    await asyncio.sleep(1.1)

    assert_error(await api.get(RULESET, headers=bearer(old["key"])), 403, "forbidden")
    assert await ruleset_status(api, new["key"]) == 200


async def test_overlap_zero_revokes_at_once(api: httpx.AsyncClient) -> None:
    await create_project(api)
    old = await create_sdk_key(api, "server")
    assert await ruleset_status(api, old["key"]) == 200  # now cached

    new = await rotated(api, old["id"], 0)

    old_out = await listed(api, old["id"])
    assert old_out["revoked_at"] is not None
    assert old_out["expires_at"] is None
    assert_error(await api.get(RULESET, headers=bearer(old["key"])), 403, "forbidden")
    assert await ruleset_status(api, new["key"]) == 200


@pytest.mark.parametrize("state", ["revoked", "expired"])
async def test_rotating_an_inactive_key_is_409(
    app: FastAPI, api: httpx.AsyncClient, state: str
) -> None:
    await create_project(api)
    old = await create_sdk_key(api, "server")
    if state == "revoked":
        assert (await api.delete(f"{KEYS}/{old['id']}")).status_code == 204
    else:
        await expire_now(app, old["id"])

    assert_error(await rotate(api, old["id"], 60), 409, "key_inactive")
    assert len((await api.get(KEYS)).json()) == 1


async def test_rotating_an_already_rotated_key_keeps_the_earlier_expiry(
    api: httpx.AsyncClient,
) -> None:
    await create_project(api)
    old = await create_sdk_key(api, "server")
    await rotated(api, old["id"], 60)
    first = (await listed(api, old["id"]))["expires_at"]

    await rotated(api, old["id"], 3600)

    assert (await listed(api, old["id"]))["expires_at"] == first


@pytest.mark.parametrize("overlap", [2592001, -1])
async def test_overlap_out_of_range_is_422(api: httpx.AsyncClient, overlap: int) -> None:
    await create_project(api)
    old = await create_sdk_key(api, "server")

    assert_error(await rotate(api, old["id"], overlap), 422, "validation_error")


async def test_unknown_key_is_404(api: httpx.AsyncClient) -> None:
    await create_project(api)

    assert_error(await rotate(api, str(uuid.uuid4()), 60), 404, "not_found")


async def test_rotation_is_audited(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    old = await create_sdk_key(api, "server")

    new = await rotated(api, old["id"], 3600)

    [entry] = await audit_entries(app, "sdk_key.rotate")
    assert (entry["resource_type"], entry["resource_key"]) == ("sdk_key", old["id"])
    assert entry["environment_id"] is not None
    assert entry["before"]["old"]["expires_at"] is None
    assert entry["after"]["old"]["expires_at"] is not None
    assert entry["after"]["new"]["id"] == new["id"]
    assert new["key"] not in str(entry)


async def test_a_viewer_gets_403(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    old = await create_sdk_key(api, "server")
    transport = httpx.ASGITransport(app=app)
    headers = bearer(await seed_service_token(app, "viewer", name="viewer"))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as viewer:
        assert_error(await rotate(viewer, old["id"], 60), 403, "forbidden")
