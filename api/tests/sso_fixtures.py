"""A mock OIDC identity provider for the SSO tests, served through `app.state.sso_http`.

It serves discovery, `/jwks` and `POST /token`. The test plays the browser: `authorize` reads the
authorization request the API redirected to and returns a code; the token endpoint then checks
the client secret and the PKCE verifier and returns an ID token for that request's nonce.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import FastAPI
from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import AnyHttpUrl

ISSUER = "https://idp.example.test/realms/test"
CLIENT_ID = "megooflags"
CLIENT_SECRET = "oidc-client-secret"
SSO_URL = "http://megooflags.test"
KEY = RSAKey.generate_key(2048, parameters={"kid": "k1"})


def enable_sso(app: FastAPI) -> None:
    """Give `app` the public URL SSO needs."""
    app.state.settings = app.state.settings.model_copy(update={"public_url": AnyHttpUrl(SSO_URL)})


@dataclass
class Authorization:
    nonce: str
    code_challenge: str
    redirect_uri: str


@dataclass
class OidcIdp:
    key: RSAKey = field(default_factory=lambda: KEY)
    # Signs ID tokens instead of `key` (which the JWKS keeps serving) when set.
    signing_key: RSAKey | None = None
    # Claims merged into every ID token; a key set to None is left out.
    claims: dict[str, Any] = field(default_factory=dict)
    # Replaces the nonce of the authorization request when set.
    nonce: str | None = None
    jwks_requests: int = 0
    token_requests: list[httpx.Request] = field(default_factory=list)
    _codes: dict[str, Authorization] = field(default_factory=dict)

    def authorize(self, location: str) -> dict[str, str]:
        """The IdP's side of the redirect: returns the callback query (code and state)."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        code = secrets.token_urlsafe(16)
        self._codes[code] = Authorization(
            nonce=query["nonce"],
            code_challenge=query["code_challenge"],
            redirect_uri=query["redirect_uri"],
        )
        return {"code": code, "state": query["state"]}

    def id_token(self, auth: Authorization) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": "kc-subject-1",
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce or auth.nonce,
            "email": "alice@example.com",
            "email_verified": True,
            "name": "Alice",
            "groups": [],
        }
        claims.update(self.claims)
        claims = {k: v for k, v in claims.items() if v is not None}
        key = self.signing_key or self.key
        return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.token_requests.append(request)
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if request.headers.get("authorization") != f"Basic {expected}":
            return httpx.Response(401, json={"error": "invalid_client"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        auth = self._codes.pop(form.get("code", ""), None)
        if auth is None or form.get("redirect_uri") != auth.redirect_uri:
            return httpx.Response(400, json={"error": "invalid_grant"})
        digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != auth.code_challenge:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={"access_token": "at", "token_type": "Bearer", "id_token": self.id_token(auth)},
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/auth",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if url == f"{ISSUER}/jwks":
            self.jwks_requests += 1
            return httpx.Response(200, json={"keys": [self.key.as_dict(private=False)]})
        if url == f"{ISSUER}/token" and request.method == "POST":
            return self._token(request)
        return httpx.Response(404)


def install_oidc_mock(app: FastAPI) -> OidcIdp:
    idp = OidcIdp()
    app.state.sso_http = httpx.AsyncClient(transport=httpx.MockTransport(idp.handler))
    return idp
