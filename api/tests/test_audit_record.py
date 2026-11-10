import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from helpers import audit_entries, bearer, seed_service_token, seed_user
from pydantic import BaseModel

from megooflags_api.auth.principal import Principal, PrincipalDep, PrincipalKind
from megooflags_api.db import SessionDep
from megooflags_api.services import audit
from megooflags_api.services.audit import AuditDep, RequestMeta, record, redact, snapshot

META = RequestMeta(ip="198.51.100.7", user_agent="pytest-agent", request_id="req-123")


def test_redact_masks_secrets_in_nested_dicts_and_lists() -> None:
    value = {
        "key": "k1",
        "name": "Key one",
        "secret": "s3cret",
        "nested": {"password_hash": "$argon2id$...", "name": "inner"},
        "items": [{"secret": "x", "key": "k2"}, "plain"],
    }
    assert redact(value) == {
        "key": "k1",
        "name": "Key one",
        "secret": "***",
        "nested": {"password_hash": "***", "name": "inner"},
        "items": [{"secret": "***", "key": "k2"}, "plain"],
    }
    assert value["secret"] == "s3cret"  # the input is not modified


class Sample(BaseModel):
    id: uuid.UUID
    at: datetime
    name: str


def test_snapshot_dumps_a_model_in_json_mode() -> None:
    sample_id = uuid.uuid4()
    at = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    assert snapshot(Sample(id=sample_id, at=at, name="n")) == {
        "id": str(sample_id),
        "at": "2026-11-10T12:00:00Z",
        "name": "n",
    }
    assert snapshot({"key": "k"}) == {"key": "k"}


async def user_principal(app: FastAPI) -> Principal:
    user_id = await seed_user(app, "auditor@example.com")
    return Principal(
        kind=PrincipalKind.USER,
        id=user_id,
        label="auditor@example.com",
        auth_method="password",
        user_id=user_id,
    )


async def test_record_rolls_back_with_its_transaction(app: FastAPI) -> None:
    principal = await user_principal(app)
    async with app.state.sessionmaker() as session:
        await record(session, principal, META, "project.create", "project", "demo")
        await session.rollback()
    assert await audit_entries(app) == []


async def test_record_writes_one_row_on_commit(app: FastAPI) -> None:
    principal = await user_principal(app)
    project_id, environment_id = uuid.uuid4(), uuid.uuid4()
    async with app.state.sessionmaker() as session:
        await record(
            session,
            principal,
            META,
            "flag.toggle",
            "flag",
            "new-ui",
            project_id=project_id,
            environment_id=environment_id,
            before={"on": False, "secret": "x"},
            after={"on": True},
        )
        await session.commit()
    [entry] = await audit_entries(app)
    assert entry["actor_kind"] == "user"
    assert entry["actor_id"] == principal.id
    assert entry["actor_label"] == "auditor@example.com"
    assert entry["auth_method"] == "password"
    assert (entry["ip"], entry["user_agent"], entry["request_id"]) == (
        "198.51.100.7",
        "pytest-agent",
        "req-123",
    )
    assert (entry["action"], entry["resource_type"], entry["resource_key"]) == (
        "flag.toggle",
        "flag",
        "new-ui",
    )
    assert (entry["project_id"], entry["environment_id"]) == (project_id, environment_id)
    assert entry["before"] == {"on": False, "secret": "***"}
    assert entry["after"] == {"on": True}
    assert entry["occurred_at"] is not None


async def test_record_without_principal_is_anonymous(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        await record(
            session,
            None,
            META,
            "auth.sign_in.failed",
            "user",
            "who@example.com",
            actor_label="who@example.com",
        )
        await session.commit()
    [entry] = await audit_entries(app, "auth.sign_in.failed")
    assert entry["actor_kind"] == "anonymous"
    assert entry["actor_id"] is None
    assert entry["actor_label"] == "who@example.com"
    assert entry["auth_method"] is None


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    router = APIRouter(prefix="/t")

    @router.post("/audited")
    async def audited(principal: PrincipalDep, auditor: AuditDep, session: SessionDep) -> None:
        await auditor.record("project.update", "project", "demo", after={"name": "Demo"})
        await session.commit()

    app.include_router(router)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_auditor_records_the_request_principal_and_ip(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    token = await seed_service_token(app, name="ci")
    r = await client.post(
        "/t/audited",
        headers={**bearer(token), "User-Agent": "ua/1", "X-Request-ID": "rid-9"},
    )
    assert r.status_code == 200, r.text
    [entry] = await audit_entries(app, "project.update")
    assert entry["actor_kind"] == "service_token"
    assert entry["actor_label"] == "ci"
    assert entry["auth_method"] == "token"
    assert (entry["ip"], entry["user_agent"], entry["request_id"]) == (
        "203.0.113.10",
        "ua/1",
        "rid-9",
    )
    assert entry["after"] == {"name": "Demo"}
    assert await audit_entries(app, "project.create") == []


def test_audit_module_has_no_update_or_delete_function() -> None:
    names = [name.lower() for name in vars(audit)]
    assert not [name for name in names if "update" in name or "delete" in name]
