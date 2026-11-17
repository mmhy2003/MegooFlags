"""SDK key authentication for /sdk and /ofrep routes, and the client-key rate limit.

Keys are looked up through the delivery's key source, which on the database opens its own short
session instead of `SessionDep`, so a long-lived stream never holds a database connection.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, Query, Request

from megooflags_api.errors import forbidden, not_ready, rate_limited, unauthorized
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.services.delivery import get_delivery
from megooflags_api.services.sdk_keys import SdkPrincipal, key_kind

UNAVAILABLE_MESSAGES = {
    "not_ready": "this worker has no valid snapshot yet",
    "revoked": "this worker's node token was revoked",
}


def bearer_token(request: Request) -> str | None:
    """The key from `Authorization: Bearer <key>`; the scheme is case-insensitive."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def authenticate(request: Request, key: str | None) -> SdkPrincipal:
    delivery = get_delivery(request)
    reason = delivery.unavailable()
    if reason is not None:
        raise not_ready(reason, UNAVAILABLE_MESSAGES[reason])
    principal = None
    if key is not None:
        principal = await delivery.keys.resolve(key)
    if principal is None:
        raise unauthorized("missing or unknown SDK key")
    if principal.inactive(datetime.now(UTC)):
        raise forbidden("the SDK key is revoked or expired")
    return principal


async def limit_client(request: Request, principal: SdkPrincipal) -> None:
    if principal.kind != SdkKeyKind.CLIENT:
        return
    client_ip = request.client.host if request.client is not None else "unknown"
    retry = await get_delivery(request).limiter.hit(principal.key_id, client_ip or "unknown")
    if retry is not None:
        raise rate_limited(retry)


async def require_server_key(request: Request) -> SdkPrincipal:
    principal = await authenticate(request, bearer_token(request))
    if principal.kind != SdkKeyKind.SERVER:
        raise forbidden("client keys cannot download the ruleset")
    return principal


async def require_any_key(request: Request) -> SdkPrincipal:
    principal = await authenticate(request, bearer_token(request))
    await limit_client(request, principal)
    return principal


async def require_ofrep_key(
    request: Request, x_api_key: str | None = Header(None, alias="X-API-Key")
) -> SdkPrincipal:
    principal = await authenticate(request, bearer_token(request) or x_api_key)
    await limit_client(request, principal)
    return principal


async def require_stream_key(
    request: Request,
    key: str | None = Query(
        None, description="Client keys only: browsers' EventSource cannot set headers."
    ),
) -> SdkPrincipal:
    token = bearer_token(request)
    if token is None and key is not None:
        if key_kind(key) == SdkKeyKind.SERVER:
            raise unauthorized("server keys must not be sent in the URL")
        token = key
    principal = await authenticate(request, token)
    await limit_client(request, principal)
    return principal


ServerKeyDep = Annotated[SdkPrincipal, Depends(require_server_key)]
AnyKeyDep = Annotated[SdkPrincipal, Depends(require_any_key)]
OfrepKeyDep = Annotated[SdkPrincipal, Depends(require_ofrep_key)]
StreamKeyDep = Annotated[SdkPrincipal, Depends(require_stream_key)]
