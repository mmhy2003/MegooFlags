"""Passkeys (WebAuthn): registration on the account, second-factor and passwordless sign-in."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from helpers import (
    ORIGIN,
    PUBLIC_URL,
    assert_error,
    audit_entries,
    enable_passkeys,
    enrol_totp,
    register_passkey,
    seed_user,
    sign_in,
)
from soft_webauthn import SoftAuthenticator, b64url
from sqlalchemy import delete, select, update

from megooflags_api.models import OrgSettings, User, UserSession, WebauthnCredential
from megooflags_api.models.enums import UserStatus
from megooflags_api.valkey import create_valkey

COOKIE = "mf_session"


@pytest.fixture
def authenticator(valkey_up: None, app: FastAPI) -> SoftAuthenticator:
    return enable_passkeys(app)


@asynccontextmanager
async def browser(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=ORIGIN
    ) as client:
        yield client


@asynccontextmanager
async def signed_in(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with browser(app) as client:
        r = await sign_in(client)
        assert r.status_code == 200, r.text
        yield client


async def user_with_passkey(app: FastAPI, authenticator: SoftAuthenticator) -> uuid.UUID:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        await register_passkey(client, authenticator)
    return user_id


async def stored(app: FastAPI) -> list[WebauthnCredential]:
    async with app.state.sessionmaker() as session:
        return list(await session.scalars(select(WebauthnCredential)))


async def assertion_options(client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.post("/api/auth/webauthn/options")
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def verify(
    client: httpx.AsyncClient, options: dict[str, Any], credential: dict[str, Any]
) -> httpx.Response:
    return await client.post(
        "/api/auth/webauthn/verify",
        json={"challenge_id": options["challenge_id"], "credential": credential},
    )


async def passwordless(
    client: httpx.AsyncClient, authenticator: SoftAuthenticator, *, bump: bool = True
) -> httpx.Response:
    options = await assertion_options(client)
    return await verify(client, options, authenticator.get(options["options"], bump=bump))


# Availability


async def test_options_need_a_public_url(valkey_up: None, app: FastAPI) -> None:
    await seed_user(app)
    async with signed_in(app) as client:
        r = await client.post("/api/account/passkeys/options")
    async with browser(app) as client:
        r2 = await client.post("/api/auth/webauthn/options")

    assert_error(r, 409, "public_url_required")
    assert_error(r2, 409, "public_url_required")


async def test_options_are_503_when_valkey_is_down(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    await seed_user(app)
    async with signed_in(app) as client:
        healthy = app.state.valkey
        app.state.valkey = create_valkey("redis://127.0.0.1:1/0")
        try:
            r = await client.post("/api/account/passkeys/options")
        finally:
            await app.state.valkey.aclose()
            app.state.valkey = healthy

    assert_error(r, 503, "unavailable")


# Registration and management


async def test_registration_options_describe_the_relying_party_and_user(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        r = await client.post("/api/account/passkeys/options")

    assert r.status_code == 200, r.text
    options = r.json()["options"]
    assert options["rp"] == {"id": "flags.example.test", "name": "MegooFlags"}
    assert options["user"]["id"] == b64url(user_id.bytes)
    assert options["user"]["name"] == "admin@example.com"
    assert options["excludeCredentials"] == []


async def test_register_returns_recovery_codes_and_is_audited(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        body = await register_passkey(client, authenticator, "Laptop")
        me = (await client.get("/api/auth/me")).json()

    assert len(body["recovery_codes"]) == 10
    passkey = body["passkey"]
    assert passkey["name"] == "Laptop"
    assert passkey["transports"] == ["internal"]
    assert passkey["last_used_at"] is None
    assert me["mfa"] == {"totp": False, "passkeys": 1, "recovery_codes_left": 10}
    [row] = await stored(app)
    assert row.user_id == user_id
    assert row.credential_id == authenticator.credential_id
    assert row.sign_count == 0
    [entry] = await audit_entries(app, "account.passkey.register")
    assert entry["actor_id"] == user_id
    assert entry["after"]["name"] == "Laptop"


async def test_a_second_passkey_gets_no_new_recovery_codes(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    await seed_user(app)
    async with signed_in(app) as client:
        await register_passkey(client, authenticator)
        options = (await client.post("/api/account/passkeys/options")).json()
        body = await register_passkey(
            client, SoftAuthenticator(origin=PUBLIC_URL, rp_id="flags.example.test"), "Phone"
        )

    assert options["options"]["excludeCredentials"][0]["id"] == b64url(authenticator.credential_id)
    assert body["recovery_codes"] is None
    assert len(await stored(app)) == 2


async def test_register_from_a_wrong_origin_is_422(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    await seed_user(app)
    evil = SoftAuthenticator(origin="https://evil.example.test", rp_id="flags.example.test")
    async with signed_in(app) as client:
        options = (await client.post("/api/account/passkeys/options")).json()
        r = await client.post(
            "/api/account/passkeys",
            json={
                "challenge_id": options["challenge_id"],
                "name": "Evil",
                "credential": evil.create(options["options"]),
            },
        )

    assert_error(r, 422, "validation_error")
    assert await stored(app) == []


async def test_list_rename_and_delete(app: FastAPI, authenticator: SoftAuthenticator) -> None:
    await seed_user(app)
    async with signed_in(app) as client:
        await register_passkey(client, authenticator, "Laptop")
        second = await register_passkey(
            client, SoftAuthenticator(origin=PUBLIC_URL, rp_id="flags.example.test"), "Phone"
        )
        passkey_id = second["passkey"]["id"]

        listed = (await client.get("/api/account/passkeys")).json()
        renamed = await client.patch(f"/api/account/passkeys/{passkey_id}", json={"name": "Tab"})
        deleted = await client.delete(f"/api/account/passkeys/{passkey_id}")
        again = await client.delete(f"/api/account/passkeys/{passkey_id}")
        after = (await client.get("/api/account/passkeys")).json()

    assert [p["name"] for p in listed] == ["Laptop", "Phone"]
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Tab"
    assert deleted.status_code == 204, deleted.text
    assert_error(again, 404, "not_found")
    assert [p["name"] for p in after] == ["Laptop"]
    [rename] = await audit_entries(app, "account.passkey.rename")
    assert (rename["before"]["name"], rename["after"]["name"]) == ("Phone", "Tab")
    [delete] = await audit_entries(app, "account.passkey.delete")
    assert delete["resource_key"] == passkey_id


async def test_another_users_passkey_is_404(app: FastAPI, authenticator: SoftAuthenticator) -> None:
    await user_with_passkey(app, authenticator)
    [row] = await stored(app)
    await seed_user(app, "dev@example.com", role="viewer")
    async with browser(app) as client:
        assert (await sign_in(client, "dev@example.com")).status_code == 200
        r = await client.patch(f"/api/account/passkeys/{row.id}", json={"name": "Mine"})

    assert_error(r, 404, "not_found")


async def test_deleting_the_last_factor_needs_reauthentication(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        passkey_id = (await register_passkey(client, authenticator))["passkey"]["id"]
        async with app.state.sessionmaker() as session:
            await session.execute(
                update(UserSession)
                .where(UserSession.user_id == user_id)
                .values(mfa_verified_at=datetime.now(UTC) - timedelta(minutes=11))
            )
            await session.commit()

        stale = await client.delete(f"/api/account/passkeys/{passkey_id}")

        # A fresh passwordless sign-in verifies the factor again.
        assert (await passwordless(client, authenticator)).status_code == 200
        fresh = await client.delete(f"/api/account/passkeys/{passkey_id}")
        me = (await client.get("/api/auth/me")).json()

    assert_error(stale, 403, "reauth_required")
    assert fresh.status_code == 204, fresh.text
    assert me["mfa"] == {"totp": False, "passkeys": 0, "recovery_codes_left": 0}


async def test_registering_in_an_enrolment_session_activates_it(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    await seed_user(app)
    async with app.state.sessionmaker() as session:
        await session.execute(update(OrgSettings).values(require_mfa=True))
        await session.commit()
    async with browser(app) as client:
        assert (await sign_in(client)).json()["status"] == "enrol_mfa"
        enrol_cookie = client.cookies[COOKIE]
        await register_passkey(client, authenticator)
        me = (await client.get("/api/auth/me")).json()
        projects = await client.get("/api/projects")

    assert client.cookies[COOKIE] != enrol_cookie
    assert me["session_state"] == "active"
    assert projects.status_code == 200, projects.text


# Second factor


async def test_second_factor_sign_in(app: FastAPI, authenticator: SoftAuthenticator) -> None:
    user_id = await user_with_passkey(app, authenticator)
    async with browser(app) as client:
        r = await sign_in(client)
        assert r.json() == {"status": "mfa_required", "mfa_methods": ["webauthn", "recovery"]}
        pending_cookie = client.cookies[COOKIE]
        options = await assertion_options(client)
        allowed = [c["id"] for c in options["options"]["allowCredentials"]]

        r = await verify(client, options, authenticator.get(options["options"]))

        assert r.status_code == 200, r.text
        assert r.json()["status"] == "signed_in"
        assert client.cookies[COOKIE] != pending_cookie
        me = (await client.get("/api/auth/me")).json()

    assert allowed == [b64url(authenticator.credential_id)]
    assert me["auth_method"] == "password+webauthn"
    [row] = await stored(app)
    assert row.sign_count == 1
    assert row.last_used_at is not None
    [entry] = [
        e for e in await audit_entries(app, "auth.sign_in") if "webauthn" in e["auth_method"]
    ]
    assert entry["actor_id"] == user_id


async def test_second_factor_refuses_another_users_passkey(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    await user_with_passkey(app, authenticator)
    await seed_user(app, "dev@example.com", role="viewer")
    other = SoftAuthenticator(origin=PUBLIC_URL, rp_id="flags.example.test")
    async with browser(app) as client:
        assert (await sign_in(client, "dev@example.com")).status_code == 200
        await register_passkey(client, other)
    async with browser(app) as client:
        assert (await sign_in(client, "dev@example.com")).json()["status"] == "mfa_required"
        options = await assertion_options(client)
        r = await verify(client, options, authenticator.get(options["options"]))

    assert_error(r, 401, "invalid_code")
    assert await audit_entries(app, "auth.mfa.failed")


# Passwordless


async def test_passwordless_sign_in(app: FastAPI, authenticator: SoftAuthenticator) -> None:
    user_id = await user_with_passkey(app, authenticator)
    async with browser(app) as client:
        options = await assertion_options(client)
        r = await verify(client, options, authenticator.get(options["options"]))
        me = (await client.get("/api/auth/me")).json()

    assert options["options"]["allowCredentials"] == []
    assert options["options"]["userVerification"] == "required"
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "signed_in"
    assert me["auth_method"] == "passkey"
    assert me["session_state"] == "active"
    [entry] = [e for e in await audit_entries(app, "auth.sign_in") if e["auth_method"] == "passkey"]
    assert entry["actor_id"] == user_id
    async with app.state.sessionmaker() as session:
        user = await session.get(User, user_id)
        assert user is not None and user.last_login_at is not None


async def test_an_assertion_that_does_not_increase_the_counter_is_refused(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    await user_with_passkey(app, authenticator)
    async with browser(app) as client:
        assert (await passwordless(client, authenticator)).status_code == 200
    async with browser(app) as client:
        r = await passwordless(client, authenticator, bump=False)

    assert_error(r, 401, "invalid_code")
    [row] = await stored(app)
    assert row.sign_count == 1
    [entry] = await audit_entries(app, "auth.mfa.failed")
    assert entry["after"] == {"method": "webauthn"}


async def test_a_challenge_works_once(app: FastAPI, authenticator: SoftAuthenticator) -> None:
    await user_with_passkey(app, authenticator)
    async with browser(app) as client:
        options = await assertion_options(client)
        assert (await verify(client, options, authenticator.get(options["options"]))).is_success
    async with browser(app) as client:
        r = await verify(client, options, authenticator.get(options["options"]))

    assert_error(r, 401, "invalid_code")


async def test_an_unknown_passkey_is_refused(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    await seed_user(app)
    async with browser(app) as client:
        options = await assertion_options(client)
        authenticator.user_handle = b"unknown"
        r = await verify(client, options, authenticator.get(options["options"]))

    assert_error(r, 401, "invalid_code")
    assert COOKIE not in client.cookies


async def test_a_disabled_user_cannot_sign_in_with_a_passkey(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    user_id = await user_with_passkey(app, authenticator)
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(User).where(User.id == user_id).values(status=UserStatus.DISABLED)
        )
        await session.commit()
    async with browser(app) as client:
        r = await passwordless(client, authenticator)

    assert_error(r, 401, "invalid_code")
    assert COOKIE not in client.cookies


async def age_verification(app: FastAPI, user_id: uuid.UUID) -> None:
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id)
            .values(mfa_verified_at=datetime.now(UTC) - timedelta(minutes=11))
        )
        await session.commit()


async def test_a_passkey_registered_already_is_409(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    await user_with_passkey(app, authenticator)
    await seed_user(app, "dev@example.com", role="viewer")
    async with browser(app) as client:
        assert (await sign_in(client, "dev@example.com")).status_code == 200
        options = (await client.post("/api/account/passkeys/options")).json()
        r = await client.post(
            "/api/account/passkeys",
            json={
                "challenge_id": options["challenge_id"],
                "name": "Stolen",
                "credential": authenticator.create(options["options"]),
            },
        )

    assert_error(r, 409, "key_conflict")
    assert len(await stored(app)) == 1


async def test_adding_a_second_factor_needs_a_recent_verification(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    user_id = await seed_user(app)
    second = SoftAuthenticator(origin=PUBLIC_URL, rp_id="flags.example.test")
    async with signed_in(app) as client:
        # A stolen session, last verified long ago, cannot add its own factor.
        await register_passkey(client, authenticator)
        await age_verification(app, user_id)
        assert_error(await client.post("/api/account/passkeys/options"), 403, "reauth_required")
        assert_error(await client.post("/api/account/totp"), 403, "reauth_required")

        assert (await passwordless(client, authenticator)).status_code == 200
        await register_passkey(client, second, "Phone")
        await enrol_totp(client)

    assert len(await stored(app)) == 2


async def test_a_factor_removal_waits_for_one_in_flight(
    app: FastAPI, authenticator: SoftAuthenticator
) -> None:
    user_id = await seed_user(app)
    async with signed_in(app) as client:
        await enrol_totp(client)
        await register_passkey(client, authenticator)
        await age_verification(app, user_id)

        # Another removal is in flight: it holds the user row and has deleted the passkey.
        async with app.state.sessionmaker() as other:
            await other.execute(select(User.id).where(User.id == user_id).with_for_update())
            await other.execute(delete(WebauthnCredential))
            removal = asyncio.ensure_future(client.delete("/api/account/totp"))
            await asyncio.wait([removal], timeout=0.5)
            assert not removal.done(), "the removal did not wait for the one in flight"
            await other.commit()
        # It then sees that the TOTP is the last factor.
        assert_error(await removal, 403, "reauth_required")
