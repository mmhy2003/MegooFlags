import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    ORIGIN,
    FakeClock,
    assert_error,
    audit_entries,
    bearer,
    create_flag,
    make_scheduler,
    seed_service_token,
    seed_user,
    sign_in,
)
from test_approvals_gate import protect
from test_changes import record_publishes
from test_rollouts import BODY as ROLLOUT_BODY
from test_rollouts import CONFIG, ROLLOUT, color_flag, config, demo_id
from test_schedules import SCHEDULES, custom_role, in_, token_client

C = "/api/projects/demo/change-requests"
CONFIG_PAYLOAD = {"version": 1, "enabled": True, "fallthrough": {"variation": "b"}}


async def open_cr(
    api: httpx.AsyncClient,
    kind: str = "config",
    payload: dict[str, Any] | None = None,
    env: str = "production",
    flag: str = "color",
) -> httpx.Response:
    return await api.post(
        C,
        json={
            "environment": env,
            "flag": flag,
            "kind": kind,
            "title": f"{kind} {flag} in {env}",
            "payload": CONFIG_PAYLOAD if payload is None else payload,
        },
    )


async def created(api: httpx.AsyncClient, **kw: Any) -> dict[str, Any]:
    r = await open_cr(api, **kw)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_create_a_config_change_request(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api)
    before = await config(api)

    r = await open_cr(api)

    assert r.status_code == 201, r.text
    cr = r.json()
    assert (cr["status"], cr["approvals"], cr["approvals_required"]) == ("open", 0, 1)
    assert (cr["author"], cr["base_version"], cr["closed_at"]) == ("tests", 1, None)
    assert (cr["environment"], cr["flag"], cr["kind"]) == ("production", "color", "config")
    assert cr["title"] == "config color in production"
    assert await config(api) == before
    [entry] = await audit_entries(app, "change_request.create")
    assert (entry["resource_type"], entry["resource_key"]) == ("change_request", cr["id"])
    assert entry["after"]["status"] == "open"
    assert await audit_entries(app, "flag.config.update") == []


@pytest.mark.parametrize(
    ("kind", "payload", "status", "code"),
    [
        (
            "config",
            {"version": 1, "enabled": True, "fallthrough": {"variation": "zzz"}},
            422,
            "invalid_reference",
        ),
        ("config", {**CONFIG_PAYLOAD, "version": 7}, 409, "version_conflict"),
        ("schedule", {"action": "enable", "run_at": in_(hours=-1)}, 422, "validation_error"),
        ("toggle", CONFIG_PAYLOAD, 422, "validation_error"),
        ("rollout", {"from_variation": "a"}, 422, "validation_error"),
    ],
    ids=["unknown variation", "wrong version", "past run_at", "config as toggle", "bad rollout"],
)
async def test_create_refuses_an_invalid_payload(
    app: FastAPI,
    api: httpx.AsyncClient,
    kind: str,
    payload: dict[str, Any],
    status: int,
    code: str,
) -> None:
    await color_flag(api)
    await protect(api)

    body = assert_error(await open_cr(api, kind=kind, payload=payload), status, code)

    if status == 422:
        assert body["details"]["path"].startswith("payload")
    assert (await api.get(C)).json()["items"] == []
    assert await audit_entries(app, "change_request.create") == []


async def test_an_unknown_variation_points_into_the_payload(api: httpx.AsyncClient) -> None:
    await color_flag(api)
    payload = {"version": 1, "enabled": True, "fallthrough": {"variation": "zzz"}}

    body = assert_error(await open_cr(api, payload=payload), 422, "invalid_reference")

    assert body["details"] == {"path": "payload.fallthrough.variation"}


async def test_schedule_and_rollout_requests_leave_nothing_behind(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    await protect(api)
    before = await config(api)
    seen = {entry["id"] for entry in await audit_entries(app)}

    await created(api, kind="schedule", payload={"action": "enable", "run_at": in_(hours=1)})
    await created(api, kind="rollout", payload=ROLLOUT_BODY)

    assert (await api.get(SCHEDULES)).json() == []
    assert (await api.get(ROLLOUT)).json() == {"current": None, "recent": []}
    assert await config(api) == before
    new = [e["action"] for e in await audit_entries(app) if e["id"] not in seen]
    assert new == ["change_request.create", "change_request.create"]


async def test_creating_needs_the_permission_of_the_change(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    await protect(api)
    role = await custom_role(api, "togglers", ["project.read", "flag.toggle"])

    async with token_client(app, role, "project") as client:
        r = await open_cr(client, kind="toggle", payload={"enabled": True})
        assert r.status_code == 201, r.text
        assert r.json()["author"] == "limited"
        assert_error(await open_cr(client), 403, "forbidden")


async def test_change_requests_are_allowed_in_unprotected_environments(
    api: httpx.AsyncClient,
) -> None:
    await color_flag(api)

    cr = await created(api, env="staging")

    assert (cr["environment"], cr["approvals_required"]) == ("staging", 1)


async def test_unknown_flag_or_environment_is_404(api: httpx.AsyncClient) -> None:
    await color_flag(api)

    assert_error(await open_cr(api, flag="nope"), 404, "not_found")
    assert_error(await open_cr(api, env="nope"), 404, "not_found")
    assert_error(await api.get(f"{C}/{uuid.uuid4()}"), 404, "not_found")


async def test_read_shows_current_and_proposed_config(api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api)
    cr = await created(api)
    toggle = await created(api, kind="toggle", payload={"enabled": True})

    r = await api.get(f"{C}/{cr['id']}")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"] == await config(api)
    assert body["proposed"] == {
        "version": 1,
        "enabled": True,
        "targets": [],
        "rules": [],
        "fallthrough": {"variation": "b", "rollout": None},
        "prerequisites": [],
    }
    assert (body["reviews"], body["comments"], body["error"]) == ([], [], None)
    assert body["payload"] == CONFIG_PAYLOAD
    assert body["description"] == ""

    r = await api.get(f"{C}/{toggle['id']}")
    assert r.status_code == 200, r.text
    assert (r.json()["current"], r.json()["proposed"]) == (None, None)
    assert r.json()["payload"] == {"enabled": True}


async def test_list_filters_and_pages_newest_first(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await create_flag(api, key="new-ui")
    first = await created(api)
    second = await created(api, kind="toggle", payload={"enabled": True}, env="staging")
    third = await created(api, kind="toggle", payload={"enabled": True}, flag="new-ui")
    r = await api.post(f"{C}/{first['id']}/cancel")
    assert r.status_code == 200, r.text

    async def ids(query: str) -> list[str]:
        r = await api.get(f"{C}?{query}")
        assert r.status_code == 200, r.text
        return [item["id"] for item in r.json()["items"]]

    assert await ids("") == [third["id"], second["id"], first["id"]]
    assert await ids("status=open") == [third["id"], second["id"]]
    assert await ids("status=cancelled") == [first["id"]]
    assert await ids("env=staging") == [second["id"]]
    assert await ids("flag=new-ui") == [third["id"]]

    walked: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 1} | ({} if cursor is None else {"cursor": cursor})
        r = await api.get(C, params=params)
        assert r.status_code == 200, r.text
        walked += [item["id"] for item in r.json()["items"]]
        cursor = r.json()["next_cursor"]
        if cursor is None:
            break
    assert walked == [third["id"], second["id"], first["id"]]
    assert_error(await api.get(C, params={"cursor": "bm9wZQ"}), 422, "validation_error")


async def test_a_staging_token_sees_no_production_change_requests(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    production = await created(api)
    staging = await created(api, env="staging")

    async with token_client(app, "editor", "environment", "staging") as client:
        r = await client.get(C)
        assert r.status_code == 200, r.text
        assert [item["id"] for item in r.json()["items"]] == [staging["id"]]
        assert_error(await client.get(f"{C}/{production['id']}"), 404, "not_found")
        r2 = await client.post(f"{C}/{production['id']}/comments", json={"body": "hi"})
        assert_error(r2, 404, "not_found")
        assert_error(await client.post(f"{C}/{production['id']}/cancel"), 404, "not_found")
        assert_error(await open_cr(client), 404, "not_found")


async def test_comments(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    cr = await created(api)

    r = await api.post(f"{C}/{cr['id']}/comments", json={"body": "lgtm"})

    assert r.status_code == 201, r.text
    assert (r.json()["author"], r.json()["body"]) == ("tests", "lgtm")
    comments = (await api.get(f"{C}/{cr['id']}")).json()["comments"]
    assert [(c["id"], c["body"]) for c in comments] == [(r.json()["id"], "lgtm")]
    [entry] = await audit_entries(app, "change_request.comment")
    assert entry["resource_key"] == cr["id"]
    assert entry["after"]["body"] == "lgtm"

    for body in ("", "x" * 10001):
        r = await api.post(f"{C}/{cr['id']}/comments", json={"body": body})
        assert r.status_code == 422, r.text

    r = await api.post(f"{C}/{cr['id']}/cancel")
    assert r.status_code == 200, r.text
    r = await api.post(f"{C}/{cr['id']}/comments", json={"body": "too late"})
    assert_error(r, 409, "invalid_state")


async def test_the_author_cancels(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    cr = await created(api)

    r = await api.post(f"{C}/{cr['id']}/cancel")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert r.json()["closed_at"] is not None
    assert_error(await api.post(f"{C}/{cr['id']}/cancel"), 409, "invalid_state")
    [entry] = await audit_entries(app, "change_request.cancel")
    assert (entry["before"]["status"], entry["after"]["status"]) == ("open", "cancelled")


async def test_a_project_manager_cancels_someone_elses_request(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    cr = await created(api)

    async with token_client(app, "editor", "project") as client:
        r = await client.post(f"{C}/{cr['id']}/cancel")

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"


async def test_others_without_project_manage_cannot_cancel(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    cr = await created(api, kind="toggle", payload={"enabled": True})
    role = await custom_role(api, "togglers", ["project.read", "flag.toggle"])

    async with token_client(app, role, "project") as client:
        assert_error(await client.post(f"{C}/{cr['id']}/cancel"), 403, "forbidden")
        mine = await created(client, kind="toggle", payload={"enabled": False})
        r = await client.post(f"{C}/{mine['id']}/cancel")
        assert r.status_code == 200, r.text

    assert (await api.get(f"{C}/{cr['id']}")).json()["status"] == "open"


@asynccontextmanager
async def client_from(app: FastAPI, headers: dict[str, str]) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as client:
        yield client


@asynccontextmanager
async def reviewer(
    app: FastAPI, role: str = "editor", name: str = "reviewer"
) -> AsyncIterator[httpx.AsyncClient]:
    """A client with an org `role` service token called `name`, another person than `api`."""
    async with client_from(app, bearer(await seed_service_token(app, role, name=name))) as client:
        yield client


async def approve(client: httpx.AsyncClient, cr: dict[str, Any], **body: Any) -> httpx.Response:
    return await client.post(f"{C}/{cr['id']}/approve", json=body or None)


async def test_an_approval_applies_a_config_change_request(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    await protect(api)
    project_id = await demo_id(app)
    cr = await created(api)
    published = record_publishes(app)

    async with reviewer(app) as client:
        r = await approve(client, cr, comment="ship it")

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["approvals"], body["error"]) == ("applied", 1, None)
    assert body["closed_at"] is not None
    [review] = body["reviews"]
    assert (review["reviewer"], review["decision"], review["comment"]) == (
        "reviewer",
        "approve",
        "ship it",
    )
    current = await config(api)
    assert (current["fallthrough"]["variation"], current["version"]) == ("b", 2)
    assert published == [project_id]
    [approved] = await audit_entries(app, "change_request.approve")
    [applied] = await audit_entries(app, "change_request.apply")
    assert approved["resource_key"] == applied["resource_key"] == cr["id"]
    assert applied["after"]["status"] == "applied"
    [update] = await audit_entries(app, "flag.config.update")
    assert update["actor_label"] == "reviewer"
    assert (update["after"]["change_request_id"], update["after"]["created_by"]) == (
        cr["id"],
        "tests",
    )


async def test_toggle_schedule_and_rollout_change_requests_apply(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    await protect(api)
    payloads = {
        "toggle": {"enabled": True},
        "schedule": {"action": "disable", "run_at": in_(hours=1)},
        "rollout": ROLLOUT_BODY,
    }
    applied: dict[str, dict[str, Any]] = {}

    # One after the other: each applied change moves the version the next one is based on.
    async with reviewer(app) as client:
        for kind, payload in payloads.items():
            applied[kind] = await created(api, kind=kind, payload=payload)
            r = await approve(client, applied[kind])
            assert (r.status_code, r.json()["status"]) == (200, "applied"), r.text
    schedule = applied["schedule"]

    assert (await config(api))["enabled"] is True
    current = (await api.get(ROLLOUT)).json()["current"]
    assert (current["status"], current["created_by"]) == ("active", "tests")
    [pending] = (await api.get(SCHEDULES)).json()
    assert (pending["status"], pending["created_by"]) == ("pending", "tests")
    assert pending["change_request_id"] == schedule["id"]

    clock = FakeClock(datetime.now(UTC))
    clock.advance(hours=2)
    await make_scheduler(app, clock).tick()

    [done] = (await api.get(SCHEDULES)).json()
    assert done["status"] == "done"
    assert (await config(api))["enabled"] is False


async def test_two_required_approvals(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api, approvals=2)
    cr = await created(api)
    published = record_publishes(app)

    async with reviewer(app) as client:
        r = await approve(client, cr)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["approvals"], r.json()["approvals_required"]) == (
        "open",
        1,
        2,
    )
    assert published == []
    assert (await config(api))["version"] == 1

    async with reviewer(app, name="second") as client:
        r = await approve(client, cr)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["approvals"]) == ("applied", 2)
    assert published == [await demo_id(app)]


async def test_raising_the_requirement_keeps_an_open_request_open(
    api: httpx.AsyncClient, app: FastAPI
) -> None:
    await color_flag(api)
    await protect(api)
    cr = await created(api)
    await protect(api, approvals=2)

    async with reviewer(app) as client:
        r = await approve(client, cr)

    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["approvals"]) == ("open", 1)
    assert (await config(api))["version"] == 1


async def test_the_author_cannot_review(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    cr = await created(api)

    assert_error(await approve(api, cr), 403, "self_approval")
    r = await api.post(f"{C}/{cr['id']}/reject", json={"comment": "no"})
    body = assert_error(r, 403, "self_approval")
    assert body["message"] == "you cannot review your own change request"
    assert (await api.get(f"{C}/{cr['id']}")).json()["reviews"] == []


async def test_a_session_author_cannot_approve_with_a_personal_token(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    await color_flag(api)
    await seed_user(app)
    async with client_from(app, ORIGIN) as session:
        assert (await sign_in(session)).status_code == 200
        cr = await created(session)
        r = await session.post(
            "/api/account/tokens",
            json={"name": "laptop", "permissions": ["project.read", "change.approve"]},
        )
        assert r.status_code == 201, r.text
        secret = r.json()["secret"]

    async with client_from(app, bearer(secret)) as personal:
        assert_error(await approve(personal, cr), 403, "self_approval")


async def test_one_review_per_reviewer(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api, approvals=2)
    cr = await created(api)

    async with reviewer(app) as client:
        assert (await approve(client, cr)).status_code == 200
        assert_error(await approve(client, cr), 409, "invalid_state")
        r = await client.post(f"{C}/{cr['id']}/reject", json={"comment": "changed my mind"})
        assert_error(r, 409, "invalid_state")


async def test_reject(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    cr = await created(api)
    published = record_publishes(app)

    async with reviewer(app) as client:
        r = await client.post(f"{C}/{cr['id']}/reject", json={})
        assert r.status_code == 422, r.text
        r = await client.post(f"{C}/{cr['id']}/reject", json={"comment": "not now"})
        assert r.status_code == 200, r.text
        assert (r.json()["status"], r.json()["approvals"]) == ("rejected", 0)
        assert r.json()["closed_at"] is not None
        [review] = r.json()["reviews"]
        assert (review["decision"], review["comment"]) == ("reject", "not now")

    async with reviewer(app, name="second") as client:
        assert_error(await approve(client, cr), 409, "invalid_state")

    assert published == []
    assert (await config(api))["version"] == 1
    [entry] = await audit_entries(app, "change_request.reject")
    assert (entry["resource_key"], entry["after"]["status"]) == (cr["id"], "rejected")


async def test_a_changed_flag_fails_the_request(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    r = await api.post(f"{CONFIG}/toggle", json={"enabled": True})
    assert r.status_code == 200, r.text
    await protect(api)
    cr = await created(api, payload={**CONFIG_PAYLOAD, "version": 2})
    r = await api.post(f"{CONFIG}/kill")
    assert r.status_code == 200, r.text
    before = await config(api)
    published = record_publishes(app)

    async with reviewer(app) as client:
        r = await approve(client, cr)

    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["status"], body["approvals"]) == ("failed", 1)
    assert body["error"] == "the flag changed since this request was made (version 2, now 3)"
    assert body["closed_at"] is not None
    assert await config(api) == before
    assert published == []
    [entry] = await audit_entries(app, "change_request.fail")
    assert entry["after"]["status"] == "failed"
    assert await audit_entries(app, "change_request.apply") == []


async def test_a_schedule_that_became_due_fails(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api)
    cr = await created(api, kind="schedule", payload={"action": "enable", "run_at": in_(seconds=2)})
    await asyncio.sleep(2.1)

    async with reviewer(app) as client:
        r = await approve(client, cr)

    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["error"]) == ("failed", "run_at must be in the future")
    assert (await api.get(SCHEDULES)).json() == []


async def test_reviewing_needs_change_approve(app: FastAPI, api: httpx.AsyncClient) -> None:
    await color_flag(api)
    await protect(api, approvals=3)
    cr = await created(api)
    role = await custom_role(api, "writers", ["project.read", "flag.config.write"])

    for limited in ("viewer", role):
        async with token_client(app, limited, "project") as client:
            assert_error(await approve(client, cr), 403, "forbidden")
            r = await client.post(f"{C}/{cr['id']}/reject", json={"comment": "no"})
            assert_error(r, 403, "forbidden")

    for allowed in ("editor", "admin"):
        async with reviewer(app, allowed, name=allowed) as client:
            r = await approve(client, cr)
            assert r.status_code == 200, r.text
    assert (await api.get(f"{C}/{cr['id']}")).json()["approvals"] == 2
