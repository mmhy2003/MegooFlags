"""OIDC sign-in: the authorization code flow with PKCE (S256), state and nonce.

Discovery documents and JWKS are cached for 10 minutes; an ID token signed with an unknown key id
refetches the JWKS once, so IdP key rotation works. The code is exchanged with
client_secret_basic, and the ID token is verified with joserfc: signature (RS256, ES256 or
PS256), issuer, audience, nonce and expiry, with 60 s of leeway.
"""

import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from authlib.oauth2.rfc7636 import create_s256_code_challenge  # type: ignore[import-untyped]
from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from megooflags_api.auth.sso import SsoAssertion, SsoDenied
from megooflags_api.schemas.sso import OidcConfig

CACHE_SECONDS = 600
ALGORITHMS = ["RS256", "ES256", "PS256"]
LEEWAY = 60


def _failed(reason: str) -> SsoDenied:
    return SsoDenied("sso_failed", reason)


async def _get_json(http: httpx.AsyncClient, url: str, what: str) -> dict[str, Any]:
    try:
        response = await http.get(url)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise _failed(f"cannot fetch the {what} from {url}: {exc}") from None
    if not isinstance(body, dict):
        raise _failed(f"the {what} at {url} is not a JSON object")
    return body


class OidcCache:
    """Discovery documents by issuer and key sets by JWKS URI, each kept for 10 minutes."""

    def __init__(self) -> None:
        self._discovery: dict[str, tuple[float, dict[str, Any]]] = {}
        self._jwks: dict[str, tuple[float, KeySet]] = {}

    async def discovery(self, http: httpx.AsyncClient, issuer: str) -> dict[str, Any]:
        cached = self._discovery.get(issuer)
        if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        doc = await _get_json(http, url, "discovery document")
        if doc.get("issuer") != issuer:
            raise _failed(f"the discovery document names issuer {doc.get('issuer')!r}")
        for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(doc.get(name), str):
                raise _failed(f"the discovery document has no {name}")
        self._discovery[issuer] = (time.monotonic(), doc)
        return doc

    async def jwks(self, http: httpx.AsyncClient, uri: str, *, refresh: bool = False) -> KeySet:
        cached = self._jwks.get(uri)
        if not refresh and cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        body = await _get_json(http, uri, "JWKS")
        try:
            keys = KeySet.import_key_set(body)  # type: ignore[arg-type]
        except (JoseError, ValueError, TypeError, KeyError) as exc:
            raise _failed(f"the JWKS at {uri} is invalid: {exc}") from None
        self._jwks[uri] = (time.monotonic(), keys)
        return keys


@dataclass(frozen=True)
class AuthRequest:
    """Where to send the browser, and what the callback must check against."""

    url: str
    state: str
    nonce: str
    code_verifier: str


async def authorization_request(
    http: httpx.AsyncClient, cache: OidcCache, config: OidcConfig, redirect_uri: str
) -> AuthRequest:
    doc = await cache.discovery(http, config.issuer)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(config.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": create_s256_code_challenge(code_verifier),
            "code_challenge_method": "S256",
        },
        quote_via=quote,
    )
    endpoint: str = doc["authorization_endpoint"]
    separator = "&" if "?" in endpoint else "?"
    return AuthRequest(f"{endpoint}{separator}{query}", state, nonce, code_verifier)


async def _exchange(
    http: httpx.AsyncClient,
    token_endpoint: str,
    config: OidcConfig,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> str:
    """The ID token for the authorization code."""
    # client_secret_basic: RFC 6749 2.3.1 form-encodes both parts before Basic encoding.
    auth = httpx.BasicAuth(quote(config.client_id, safe=""), quote(client_secret, safe=""))
    try:
        response = await http.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            auth=auth,
            headers={"Accept": "application/json"},
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise _failed(f"the token request failed: {exc}") from None
    if response.status_code != 200 or not isinstance(body, dict):
        error = body.get("error") if isinstance(body, dict) else None
        raise _failed(f"the token endpoint answered {response.status_code} ({error})")
    id_token = body.get("id_token")
    if not isinstance(id_token, str):
        raise _failed("the token response has no id_token")
    return id_token


def _decode(id_token: str, keys: KeySet) -> jwt.Token:
    return jwt.decode(id_token, keys, algorithms=ALGORITHMS)


def _assertion(provider: str, config: OidcConfig, claims: dict[str, Any]) -> SsoAssertion:
    email = claims.get("email")
    verified = claims.get("email_verified")
    name = claims.get("name") or claims.get("preferred_username")
    groups = claims.get(config.groups_claim)
    return SsoAssertion(
        provider=provider,
        subject=str(claims["sub"]),
        email=email if isinstance(email, str) and email else None,
        email_verified=verified is True or verified == "true",
        name=name if isinstance(name, str) else None,
        groups=[g for g in groups if isinstance(g, str)] if isinstance(groups, list) else [],
    )


async def complete(
    http: httpx.AsyncClient,
    cache: OidcCache,
    provider: str,
    config: OidcConfig,
    client_secret: str,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
    nonce: str,
) -> SsoAssertion:
    """Exchange the code and verify the ID token; SsoDenied("sso_failed") on any failure."""
    doc = await cache.discovery(http, config.issuer)
    id_token = await _exchange(
        http, doc["token_endpoint"], config, client_secret, code, redirect_uri, code_verifier
    )
    try:
        try:
            token = _decode(id_token, await cache.jwks(http, doc["jwks_uri"]))
        except InvalidKeyIdError:
            token = _decode(id_token, await cache.jwks(http, doc["jwks_uri"], refresh=True))
        JWTClaimsRegistry(
            leeway=LEEWAY,
            iss={"essential": True, "value": config.issuer},
            aud={"essential": True, "value": config.client_id},
            nonce={"essential": True, "value": nonce},
            exp={"essential": True},
            sub={"essential": True},
        ).validate(token.claims)
    except JoseError as exc:
        raise _failed(f"the ID token is invalid: {exc}") from None
    return _assertion(provider, config, token.claims)
