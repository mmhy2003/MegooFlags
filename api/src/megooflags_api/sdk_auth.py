"""SDK key authentication for /sdk and /ofrep routes, and the client-key rate limit.

Each dependency opens its own short session for the key lookup instead of `SessionDep`, so a
long-lived stream never holds a database connection.
"""

from typing import Annotated

from fastapi import Depends, Header, Query, Request

from megooflags_api.errors import forbidden, rate_limited, unauthorized
from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.services.rate_limit import RateLimiter
from megooflags_api.services.sdk_keys import KeyResolver, SdkPrincipal, key_kind


def bearer_token(request: Request) -> str | None:
    """The key from `Authorization: Bearer <key>`; the scheme is case-insensitive."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def authenticate(request: Request, key: str | None) -> SdkPrincipal:
    principal = None
    if key is not None:
        resolver: KeyResolver = request.app.state.key_resolver
        async with request.app.state.sessionmaker() as session:
            principal = await resolver.resolve(session, key)
    if principal is None:
        raise unauthorized("missing or unknown SDK key")
    if principal.revoked:
        raise forbidden("the SDK key is revoked")
    return principal


async def limit_client(request: Request, principal: SdkPrincipal) -> None:
    if principal.kind != SdkKeyKind.CLIENT:
        return
    limiter: RateLimiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client is not None else "unknown"
    retry = await limiter.hit(principal.key_id, client_ip or "unknown")
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
