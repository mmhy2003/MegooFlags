"""TOTP, recovery codes, the MFA step of sign-in and the org's `require_mfa` setting."""

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    ORIGIN,
    assert_error,
    audit_entries,
    bearer,
    enrol_totp,
    seed_service_token,
    seed_user,
    sign_in,
    totp_code,
    wrong_totp_code,
)
from sqlalchemy import func, select, update

from megooflags_api.auth.mfa import hash_recovery, verify_totp
from megooflags_api.models import OrgSettings, RecoveryCode, TotpCredential, UserSession

COOKIE = "mf_session"


@asynccontextmanager
async def browser(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=ORIGIN
    ) as client:
        yield client


@asynccontextmanager
async def signed_in(
    app: FastAPI, email: str = "admin@example.com"
) -> AsyncIterator[httpx.AsyncClient]:
    async with browser(app) as client:
        r = await sign_in(client, email)
        assert r.status_code == 200, r.text
        yield client


async def totp_row(app: FastAPI, user_id: uuid.UUID) -> TotpCredential | None:
    async with app.state.sessionmaker() as session:
        return await session.get(TotpCredential, user_id)


async def unused_recovery_codes(app: FastAPI, user_id: uuid.UUID) -> int:
    async with app.state.sessionmaker() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(RecoveryCode)
            .where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
        )
        return int(count or 0)


async def age_verification(app: FastAPI, user_id: uuid.UUID, minutes: int = 11) -> None:
    """Pretend the user's sessions last verified a factor `minutes` ago."""
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id)
            .values(mfa_verified_at=datetime.now(UTC) - timedelta(minutes=minutes))
        )
        await session.commit()


async def set_require_mfa(app: FastAPI, value: bool) -> None:
    async with app.state.sessionmaker() as session:
        await session.execute(update(OrgSettings).values(require_mfa=value))
        await session.commit()


async def user_with_totp(app: FastAPI) -> tuple[uuid.UUID, str, list[str]]:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        secret, codes = await enrol_totp(client)
    return user_id, secret, codes


# Enrolment


async def test_enrolment_returns_an_otpauth_url_and_stores_the_secret_encrypted(
    app: FastAPI,
) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        r = await client.post("/api/account/totp")

    assert r.status_code == 201, r.text
    body = r.json()
    url = urlsplit(body["otpauth_url"])
    assert (url.scheme, url.netloc, unquote(url.path)) == (
        "otpauth",
        "totp",
        "/MegooFlags:admin@example.com",
    )
    query = parse_qs(url.query)
    assert query["issuer"] == ["MegooFlags"]
    assert query["secret"] == [body["secret"]]
    row = await totp_row(app, user_id)
    assert row is not None and row.confirmed_at is None
    assert body["secret"] not in row.secret_encrypted
    assert app.state.secret_box.decrypt(row.secret_encrypted) == body["secret"]
    [entry] = await audit_entries(app, "account.totp.enrol")
    assert body["secret"] not in str(entry)


async def test_confirm_with_a_wrong_code_is_refused(app: FastAPI) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        secret = (await client.post("/api/account/totp")).json()["secret"]
        r = await client.post("/api/account/totp/confirm", json={"code": wrong_totp_code(secret)})

    assert_error(r, 401, "invalid_code")
    row = await totp_row(app, user_id)
    assert row is not None and row.confirmed_at is None


async def test_confirm_returns_ten_recovery_codes(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in(app) as client:
        secret = (await client.post("/api/account/totp")).json()["secret"]
        r = await client.post("/api/account/totp/confirm", json={"code": totp_code(secret)})
        me = (await client.get("/api/auth/me")).json()

    assert r.status_code == 200, r.text
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(re.fullmatch(r"[a-z0-9]{5}-[a-z0-9]{5}", code) for code in codes)
    assert me["mfa"] == {"totp": True, "passkeys": 0, "recovery_codes_left": 10}
    async with app.state.sessionmaker() as session:
        hashes = set(await session.scalars(select(RecoveryCode.code_hash)))
    assert hashes == {hash_recovery(code) for code in codes}
    assert await audit_entries(app, "account.totp.confirm")


async def test_enrolling_again_is_409(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in(app) as client:
        await enrol_totp(client)
        r = await client.post("/api/account/totp")

    assert_error(r, 409, "totp_exists")


async def test_an_api_token_cannot_enrol(app: FastAPI, anon: httpx.AsyncClient) -> None:
    token = await seed_service_token(app)

    r = await anon.post("/api/account/totp", headers=bearer(token))

    assert_error(r, 403, "forbidden")


async def test_verify_totp_refuses_used_and_distant_steps(app: FastAPI) -> None:
    secret = "JBSWY3DPEHPK3PXP"
    box = app.state.secret_box
    cred = TotpCredential(secret_encrypted=box.encrypt(secret), last_used_step=None)

    step = verify_totp(cred, totp_code(secret), box)
    assert step is not None
    assert verify_totp(cred, totp_code(secret, 3), box) is None
    cred.last_used_step = step
    assert verify_totp(cred, totp_code(secret), box) is None


# Sign-in


async def test_sign_in_with_totp(app: FastAPI) -> None:
    user_id, secret, _ = await user_with_totp(app)
    async with browser(app) as client:
        r = await sign_in(client)
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "mfa_required", "mfa_methods": ["totp", "recovery"]}
        pending_cookie = client.cookies[COOKIE]
        assert_error(await client.get("/api/projects"), 401, "mfa_required")
        me = (await client.get("/api/auth/me")).json()
        assert me["session_state"] == "pending_mfa"

        r = await client.post("/api/auth/mfa/totp", json={"code": totp_code(secret, 1)})

        assert r.status_code == 200, r.text
        assert r.json()["status"] == "signed_in"
        assert client.cookies[COOKIE] != pending_cookie
        assert (await client.get("/api/projects")).status_code == 200
    entries = await audit_entries(app, "auth.sign_in")
    [entry] = [e for e in entries if e["auth_method"] == "password+totp"]
    assert entry["actor_id"] == user_id
    async with app.state.sessionmaker() as session:
        rows = list(
            await session.scalars(select(UserSession).where(UserSession.user_id == user_id))
        )
    # The enrolling browser's session, and the pending one replaced by an MFA-verified session.
    assert sorted(
        (row.auth_method, row.state, row.mfa_verified_at is not None) for row in rows
    ) == [
        ("password", "active", True),
        ("password+totp", "active", True),
    ]


async def test_a_replayed_code_is_refused(app: FastAPI) -> None:
    _, secret, _ = await user_with_totp(app)
    code = totp_code(secret, 1)
    async with browser(app) as client:
        await sign_in(client)
        assert (await client.post("/api/auth/mfa/totp", json={"code": code})).status_code == 200
    async with browser(app) as client:
        await sign_in(client)
        r = await client.post("/api/auth/mfa/totp", json={"code": code})

    assert_error(r, 401, "invalid_code")
    assert await audit_entries(app, "auth.mfa.failed")


async def test_ten_wrong_codes_are_throttled(valkey_up: None, app: FastAPI) -> None:
    _, secret, _ = await user_with_totp(app)
    async with browser(app) as client:
        await sign_in(client)
        for _ in range(10):
            r = await client.post("/api/auth/mfa/totp", json={"code": wrong_totp_code(secret)})
            assert_error(r, 401, "invalid_code")

        r = await client.post("/api/auth/mfa/totp", json={"code": totp_code(secret, 1)})

    assert_error(r, 429, "rate_limited")
    assert int(r.headers["Retry-After"]) > 0
    assert len(await audit_entries(app, "auth.mfa.failed")) == 10


async def test_each_recovery_code_works_once(app: FastAPI) -> None:
    user_id, _, codes = await user_with_totp(app)
    async with browser(app) as client:
        await sign_in(client)
        r = await client.post("/api/auth/mfa/recovery", json={"code": codes[0].upper()})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "signed_in"
    assert await unused_recovery_codes(app, user_id) == 9
    entries = await audit_entries(app, "auth.sign_in")
    assert entries[-1]["auth_method"] == "password+recovery"

    async with browser(app) as client:
        await sign_in(client)
        assert_error(
            await client.post("/api/auth/mfa/recovery", json={"code": codes[0]}),
            401,
            "invalid_code",
        )
        r = await client.post("/api/auth/mfa/recovery", json={"code": codes[1]})
        assert r.status_code == 200, r.text


async def test_an_expired_pending_session_is_401(app: FastAPI) -> None:
    user_id, secret, _ = await user_with_totp(app)
    async with browser(app) as client:
        await sign_in(client)
        async with app.state.sessionmaker() as session:
            past = datetime.now(UTC) - timedelta(seconds=1)
            await session.execute(
                update(UserSession)
                .where(UserSession.user_id == user_id)
                .values(idle_expires_at=past, absolute_expires_at=past)
            )
            await session.commit()

        r = await client.post("/api/auth/mfa/totp", json={"code": totp_code(secret, 1)})

    assert_error(r, 401, "unauthorized")


async def test_mfa_needs_a_pending_session(app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in(app) as client:
        r = await client.post("/api/auth/mfa/totp", json={"code": "123456"})

    assert_error(r, 403, "forbidden")


# Removing factors and regenerating codes


async def test_removing_totp_deletes_the_recovery_codes(app: FastAPI) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        await enrol_totp(client)
        r = await client.delete("/api/account/totp")

    assert r.status_code == 204, r.text
    assert await totp_row(app, user_id) is None
    assert await unused_recovery_codes(app, user_id) == 0
    assert await audit_entries(app, "account.totp.remove")


async def test_removing_the_last_factor_needs_a_recent_verification(app: FastAPI) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        secret, _ = await enrol_totp(client)
        await age_verification(app, user_id)
        assert_error(await client.delete("/api/account/totp"), 403, "reauth_required")
        assert_error(await client.post("/api/account/recovery-codes"), 403, "reauth_required")

    async with browser(app) as client:
        await sign_in(client)
        await client.post("/api/auth/mfa/totp", json={"code": totp_code(secret, 1)})
        r = await client.post("/api/account/recovery-codes")
        assert r.status_code == 200, r.text
        assert len(r.json()["recovery_codes"]) == 10
        assert (await client.delete("/api/account/totp")).status_code == 204

    assert await audit_entries(app, "account.recovery_codes.regenerate")


async def test_regenerating_replaces_the_old_codes(app: FastAPI) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        _, old = await enrol_totp(client)
        new = (await client.post("/api/account/recovery-codes")).json()["recovery_codes"]

    assert set(old).isdisjoint(new)
    async with app.state.sessionmaker() as session:
        hashes = set(await session.scalars(select(RecoveryCode.code_hash)))
    assert hashes == {hash_recovery(code) for code in new}
    assert await unused_recovery_codes(app, user_id) == 10


async def test_removing_the_last_factor_is_refused_when_the_org_requires_mfa(
    app: FastAPI,
) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        await enrol_totp(client)
        await set_require_mfa(app, True)
        r = await client.delete("/api/account/totp")

    assert_error(r, 409, "mfa_required_by_org")
    assert await totp_row(app, user_id) is not None


# require_mfa


async def test_settings(app: FastAPI, api: httpx.AsyncClient) -> None:
    assert (await api.get("/api/settings")).json() == {"require_mfa": False}

    r = await api.put("/api/settings", json={"require_mfa": True})

    assert r.status_code == 200, r.text
    assert r.json() == {"require_mfa": True}
    assert (await api.get("/api/settings")).json() == {"require_mfa": True}
    [entry] = await audit_entries(app, "settings.update")
    assert (entry["before"], entry["after"]) == ({"require_mfa": False}, {"require_mfa": True})


async def test_a_viewer_cannot_change_settings(app: FastAPI, anon: httpx.AsyncClient) -> None:
    token = await seed_service_token(app, "viewer")

    assert (await anon.get("/api/settings", headers=bearer(token))).status_code == 200
    r = await anon.put("/api/settings", json={"require_mfa": True}, headers=bearer(token))

    assert_error(r, 403, "forbidden")


async def test_require_mfa_makes_users_without_a_factor_enrol(app: FastAPI) -> None:
    await seed_user(app)
    await set_require_mfa(app, True)
    async with browser(app) as client:
        r = await sign_in(client)
        assert r.json() == {"status": "enrol_mfa", "mfa_methods": []}
        enrol_cookie = client.cookies[COOKIE]
        assert_error(await client.get("/api/projects"), 403, "mfa_enrolment_required")
        assert (await client.get("/api/auth/me")).json()["session_state"] == "enrol_mfa"

        r = await client.post("/api/account/totp")
        assert r.status_code == 201, r.text
        secret = r.json()["secret"]
        r = await client.post("/api/account/totp/confirm", json={"code": totp_code(secret)})

        assert r.status_code == 200, r.text
        assert client.cookies[COOKIE] != enrol_cookie
        assert (await client.get("/api/auth/me")).json()["session_state"] == "active"
        assert (await client.get("/api/projects")).status_code == 200


async def test_users_with_a_factor_still_get_the_mfa_step_when_required(app: FastAPI) -> None:
    await user_with_totp(app)
    await set_require_mfa(app, True)
    async with browser(app) as client:
        r = await sign_in(client)

    assert r.json()["status"] == "mfa_required"


# Admin reset


async def test_admin_reset_clears_factors_and_sessions(
    app: FastAPI, api: httpx.AsyncClient
) -> None:
    user_id = await seed_user(app, "dev@example.com", role="viewer")
    async with signed_in(app, "dev@example.com") as client:
        await enrol_totp(client)

    r = await api.delete(f"/api/users/{user_id}/mfa")

    assert r.status_code == 204, r.text
    assert await totp_row(app, user_id) is None
    assert await unused_recovery_codes(app, user_id) == 0
    async with app.state.sessionmaker() as session:
        sessions_left = await session.scalar(
            select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id)
        )
    assert sessions_left == 0
    [entry] = await audit_entries(app, "user.mfa_reset")
    assert entry["resource_key"] == "dev@example.com"
    assert entry["before"]["totp"] is True and entry["after"]["totp"] is False
    async with browser(app) as client:
        assert (await sign_in(client, "dev@example.com")).json()["status"] == "signed_in"


@pytest.mark.parametrize("role", ["viewer", "editor"])
async def test_only_member_managers_can_reset_mfa(
    app: FastAPI, anon: httpx.AsyncClient, role: str
) -> None:
    user_id = await seed_user(app)
    token = await seed_service_token(app, role)

    r = await anon.delete(f"/api/users/{user_id}/mfa", headers=bearer(token))

    assert_error(r, 403, "forbidden")
