"""Integrations: webhook, Slack and Teams targets of a project."""

import json
import uuid
from collections.abc import AsyncIterator
from ipaddress import ip_address
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    assert_error,
    audit_entries,
    bearer,
    create_project,
    seed_service_token,
)
from sqlalchemy import func, select

from megooflags_api.main import create_app
from megooflags_api.models import (
    Environment,
    Integration,
    IntegrationDelivery,
    IntegrationEnvironment,
    Project,
)
from megooflags_api.models.enums import DeliveryStatus, EventGroup, IntegrationKind
from megooflags_api.services.integrations import crud
from megooflags_api.settings import Settings

BASE = "/api/projects/demo/integrations"
HOOK_URL = "https://hooks.example.com/services/abc/secret-hook"
SLACK_URL = "https://hooks.slack.com/services/T000/B000/XXXXXXXX"
# What the fake resolver answers for each host; anything else does not resolve.
ADDRESSES = {
    "hooks.example.com": ["93.184.215.14"],
    "hooks.slack.com": ["2600:1901:0:3084::"],
    "internal.example.com": ["93.184.215.14", "10.0.0.7"],
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        try:
            return [str(ip_address(host))]  # getaddrinfo answers a literal itself
        except ValueError:
            pass
        if host not in ADDRESSES:
            raise OSError("Name or service not known")
        return ADDRESSES[host]

    monkeypatch.setattr(crud, "resolve", resolve)


def webhook(**overrides: Any) -> dict[str, Any]:
    base = {"kind": "webhook", "name": "Deploy hook", "url": HOOK_URL, "event_groups": ["flags"]}
    return base | overrides


async def create_webhook(api: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    r = await api.post(BASE, json=webhook(**overrides))
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_crud_round_trip(api: httpx.AsyncClient) -> None:
    await create_project(api)
    created = await create_webhook(
        api, all_environments=False, environments=["production", "staging"]
    )
    assert created["kind"] == "webhook"
    assert created["name"] == "Deploy hook"
    assert created["event_groups"] == ["flags"]
    assert created["all_environments"] is False
    assert created["environments"] == ["staging", "production"]  # in environment order
    assert created["enabled"] is True
    assert created["last_delivery_at"] is None
    assert created["last_delivery_ok"] is None
    assert "url" not in created
    id = created["id"]

    r = await api.get(f"{BASE}/{id}")
    assert r.status_code == 200, r.text
    assert r.json() == {k: v for k, v in created.items() if k != "secret"}

    r = await api.patch(
        f"{BASE}/{id}",
        json={
            "name": "Release hook",
            "event_groups": ["targeting", "segments"],
            "all_environments": True,
            "environments": [],
            "enabled": False,
        },
    )
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["name"] == "Release hook"
    assert updated["event_groups"] == ["targeting", "segments"]
    assert updated["all_environments"] is True
    assert updated["environments"] == []
    assert updated["enabled"] is False

    await create_webhook(api, name="A hook")
    r = await api.get(BASE)
    assert r.status_code == 200, r.text
    assert [i["name"] for i in r.json()] == ["A hook", "Release hook"]

    r = await api.delete(f"{BASE}/{id}")
    assert r.status_code == 204
    assert_error(await api.get(f"{BASE}/{id}"), 404, "not_found")
    assert [i["name"] for i in (await api.get(BASE)).json()] == ["A hook"]


async def test_patch_url_changes_the_hint(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    id = (await create_webhook(api))["id"]
    r = await api.patch(f"{BASE}/{id}", json={"url": "https://hooks.example.com/v2/new1"})
    assert r.status_code == 200, r.text
    assert r.json()["url_hint"] == "https://hooks.example.com/…new1"
    async with app.state.sessionmaker() as session:
        row = await session.get(Integration, uuid.UUID(id))
        assert row is not None
        assert app.state.secret_box.decrypt(row.url_encrypted) == (
            "https://hooks.example.com/v2/new1"
        )


async def test_secret_shown_only_on_create_and_rotate(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    created = await create_webhook(api)
    secret = created["secret"]
    assert secret.startswith("mf_whsec_")
    assert len(secret) == 41
    id = created["id"]

    assert "secret" not in (await api.get(f"{BASE}/{id}")).json()
    assert all("secret" not in i for i in (await api.get(BASE)).json())
    r = await api.patch(f"{BASE}/{id}", json={"name": "Renamed"})
    assert "secret" not in r.json()

    r = await api.post(f"{BASE}/{id}/rotate-secret")
    assert r.status_code == 200, r.text
    rotated = r.json()["secret"]
    assert rotated.startswith("mf_whsec_")
    assert len(rotated) == 41
    assert rotated != secret

    async with app.state.sessionmaker() as session:
        row = await session.get(Integration, uuid.UUID(id))
        assert row is not None
        assert row.secret_encrypted is not None
        assert rotated not in row.secret_encrypted
        assert app.state.secret_box.decrypt(row.secret_encrypted) == rotated


async def test_slack_and_teams_have_no_secret(api: httpx.AsyncClient) -> None:
    await create_project(api)
    slack = await create_webhook(api, kind="slack", name="Slack", url=SLACK_URL)
    assert slack["secret"] is None
    r = await api.post(f"{BASE}/{slack['id']}/rotate-secret")
    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "kind"}
    assert body["message"] == "only webhooks have a signing secret"


@pytest.mark.parametrize(
    ("url", "hint"),
    [
        (HOOK_URL, "https://hooks.example.com/…hook"),
        ("https://hooks.example.com/", "https://hooks.example.com"),
        ("https://hooks.example.com", "https://hooks.example.com"),
        ("https://hooks.example.com:8443/x?token=abc", "https://hooks.example.com/…/x"),
    ],
)
def test_url_hint(url: str, hint: str) -> None:
    assert crud.url_hint(url) == hint


async def test_url_is_not_in_audit_entries(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    created = await create_webhook(api)
    id = created["id"]
    await api.patch(f"{BASE}/{id}", json={"url": HOOK_URL + "-2", "name": "Renamed"})
    rotated = (await api.post(f"{BASE}/{id}/rotate-secret")).json()["secret"]
    await api.delete(f"{BASE}/{id}")

    entries = [e for e in await audit_entries(app) if e["resource_type"] == "integration"]
    assert [e["action"] for e in entries] == [
        "integration.create",
        "integration.update",
        "integration.rotate_secret",
        "integration.delete",
    ]
    assert all(e["resource_key"] == id for e in entries)
    dumped = json.dumps(entries, default=str)
    assert "secret-hook" not in dumped
    assert created["secret"] not in dumped
    assert rotated not in dumped
    assert entries[0]["after"]["url_hint"] == "https://hooks.example.com/…hook"


async def test_slack_url_must_be_on_slack_host(api: httpx.AsyncClient) -> None:
    await create_project(api)
    r = await api.post(BASE, json=webhook(kind="slack", url=HOOK_URL))
    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "url"}
    assert "hooks.slack.com" in body["message"]

    id = (await create_webhook(api, kind="slack", url=SLACK_URL))["id"]
    r = await api.patch(f"{BASE}/{id}", json={"url": HOOK_URL})
    assert_error(r, 422, "validation_error")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/x",
        "http://hooks.example.com/x",
        "https://internal.example.com/x",  # one of its addresses is private
        "https://unknown.example.com/x",  # does not resolve
        "https://user:pass@hooks.example.com/x",
    ],
)
async def test_unsafe_urls_are_refused(api: httpx.AsyncClient, url: str) -> None:
    await create_project(api)
    r = await api.post(BASE, json=webhook(url=url))
    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "url"}
    assert url not in body["message"]


@pytest.fixture
async def private_api(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An org admin client of an app with `webhook_allow_private`, on the same database."""
    private = create_app(Settings(webhook_allow_private=True))
    headers = bearer(await seed_service_token(private))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=private), base_url="http://test", headers=headers
        ) as client:
            yield client
    finally:
        await private.state.ruleset_cache.aclose()
        await private.state.valkey.aclose()
        await private.state.engine.dispose()


async def test_private_address_allowed_with_the_setting(private_api: httpx.AsyncClient) -> None:
    await create_project(private_api)
    created = await create_webhook(private_api, url="https://127.0.0.1/x")
    assert created["url_hint"] == "https://127.0.0.1/…/x"
    await create_webhook(private_api, url="http://10.0.0.7:8080/hook")


async def test_environments_validation(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_project(api, key="other", environments=[{"key": "qa", "name": "QA"}])

    r = await api.post(BASE, json=webhook(all_environments=False, environments=["qa"]))
    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "environments"}

    r = await api.post(BASE, json=webhook(all_environments=False, environments=[]))
    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "environments"}

    id = (await create_webhook(api))["id"]
    r = await api.patch(f"{BASE}/{id}", json={"all_environments": False})
    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "environments"}

    r = await api.patch(f"{BASE}/{id}", json={"environments": ["nope"]})
    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "environments"}

    scoped = {"all_environments": False, "environments": ["staging"]}
    r = await api.patch(f"{BASE}/{id}", json=scoped)
    assert r.status_code == 200, r.text
    assert r.json()["environments"] == ["staging"]

    r = await api.patch(f"{BASE}/{id}", json={"environments": []})
    body = assert_error(r, 422, "validation_error")
    assert body["details"] == {"path": "environments"}


async def test_event_groups_are_required(api: httpx.AsyncClient) -> None:
    await create_project(api)
    assert_error(await api.post(BASE, json=webhook(event_groups=[])), 422, "validation_error")
    assert_error(await api.post(BASE, json=webhook(event_groups=["all"])), 422, "validation_error")


async def test_permissions(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_project(api, key="other")
    id = (await create_webhook(api))["id"]

    viewer = bearer(await seed_service_token(app, "viewer", name="viewer"))
    assert_error(await api.get(BASE, headers=viewer), 403, "forbidden")
    assert_error(await api.post(BASE, json=webhook(), headers=viewer), 403, "forbidden")
    assert_error(await api.get(f"{BASE}/{id}", headers=viewer), 403, "forbidden")

    async with app.state.sessionmaker() as session:
        other_id = await session.scalar(select(Project.id).where(Project.key == "other"))
    outsider = bearer(
        await seed_service_token(
            app, "admin", scope="project", project_id=other_id, name="outsider"
        )
    )
    assert_error(await api.get(BASE, headers=outsider), 404, "not_found")
    assert_error(await api.get("/api/projects/nope/integrations"), 404, "not_found")


async def test_unknown_or_foreign_id_404(api: httpx.AsyncClient) -> None:
    await create_project(api)
    await create_project(api, key="other")
    id = (await create_webhook(api))["id"]
    other = f"/api/projects/other/integrations/{id}"
    for r in [
        await api.get(other),
        await api.patch(other, json={"name": "x"}),
        await api.delete(other),
        await api.post(f"{other}/rotate-secret"),
        await api.get(f"{BASE}/{uuid.uuid4()}"),
    ]:
        body = assert_error(r, 404, "not_found")
        assert "integration" in body["message"]


async def insert_integration(app: FastAPI, environment_keys: list[str]) -> uuid.UUID:
    """An integration of project `demo` limited to `environment_keys`, with one delivery."""
    async with app.state.sessionmaker() as session:
        project_id = await session.scalar(select(Project.id).where(Project.key == "demo"))
        assert project_id is not None
        integration = Integration(
            project_id=project_id,
            kind=IntegrationKind.WEBHOOK,
            name="Hook",
            url_encrypted="encrypted",
            url_hint="https://hooks.example.com/…hook",
            event_groups=[EventGroup.FLAGS.value],
            all_environments=False,
        )
        session.add(integration)
        await session.flush()
        environments = await session.scalars(
            select(Environment.id).where(
                Environment.project_id == project_id, Environment.key.in_(environment_keys)
            )
        )
        for environment_id in environments:
            session.add(
                IntegrationEnvironment(integration_id=integration.id, environment_id=environment_id)
            )
        session.add(
            IntegrationDelivery(
                integration_id=integration.id,
                event_id="evt_1",
                event_type="flag.toggle",
                payload={"id": "evt_1"},
                status=DeliveryStatus.PENDING,
            )
        )
        await session.commit()
        return integration.id


async def count(app: FastAPI, model: type[object]) -> int:
    async with app.state.sessionmaker() as session:
        return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_project_delete_cascades_integrations(app: FastAPI, api: httpx.AsyncClient) -> None:
    await create_project(api)
    await insert_integration(app, ["staging", "production"])
    assert await count(app, IntegrationEnvironment) == 2

    r = await api.delete("/api/projects/demo/environments/staging")
    assert r.status_code == 204
    assert await count(app, IntegrationEnvironment) == 1
    assert await count(app, Integration) == 1

    r = await api.delete("/api/projects/demo")
    assert r.status_code == 204
    assert await count(app, Integration) == 0
    assert await count(app, IntegrationDelivery) == 0
    assert await count(app, IntegrationEnvironment) == 0
