"""Mock identity providers for the SSO tests.

OIDC: served through `app.state.sso_http`. It serves discovery, `/jwks` and `POST /token`. The
test plays the browser: `authorize` reads the authorization request the API redirected to and
returns a code; the token endpoint then checks the client secret and the PKCE verifier and
returns an ID token for that request's nonce.

SAML: `idp_metadata` describes an IdP with a self-signed certificate, and `saml_response` builds
the response that IdP would POST to the ACS, with the Assertion signed by its key.
"""

import base64
import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape, quoteattr

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from joserfc import jwt
from joserfc.jwk import RSAKey
from onelogin.saml2.constants import OneLogin_Saml2_Constants  # type: ignore[import-untyped]
from onelogin.saml2.utils import OneLogin_Saml2_Utils  # type: ignore[import-untyped]
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


# SAML

SAML_KEY = "kc-saml"
SAML_ENTITY_ID = f"{SSO_URL}/api/auth/sso/{SAML_KEY}/saml/metadata"
SAML_ACS_URL = f"{SSO_URL}/api/auth/sso/{SAML_KEY}/saml/acs"
IDP_ENTITY_ID = "https://idp.example.test/realms/test"
IDP_SSO_URL = f"{IDP_ENTITY_ID}/protocol/saml"


def self_signed(common_name: str) -> tuple[str, str]:
    """A new RSA key and a self-signed certificate for it, both PEM."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return key_pem, cert.public_bytes(serialization.Encoding.PEM).decode()


IDP_KEY, IDP_CERT = self_signed("idp.example.test")


def _cert_body(pem: str) -> str:
    return "".join(line for line in pem.splitlines() if "-----" not in line)


def idp_metadata(
    cert: str = IDP_CERT, entity_id: str = IDP_ENTITY_ID, sso_url: str = IDP_SSO_URL
) -> str:
    """IdP metadata: the signing certificate and an HTTP-Redirect SSO endpoint."""
    return f"""<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#" entityID={quoteattr(entity_id)}>
  <md:IDPSSODescriptor WantAuthnRequestsSigned="false"
      protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing">
      <ds:KeyInfo><ds:X509Data><ds:X509Certificate>{_cert_body(cert)}</ds:X509Certificate>
      </ds:X509Data></ds:KeyInfo>
    </md:KeyDescriptor>
    <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location={quoteattr(sso_url)}/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""


def _instant(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _attribute(name: str, values: list[str]) -> str:
    rendered = "".join(
        f'<saml:AttributeValue xsi:type="xs:string">{escape(v)}</saml:AttributeValue>'
        for v in values
    )
    return f"<saml:Attribute Name={quoteattr(name)}>{rendered}</saml:Attribute>"


def saml_response(
    *,
    in_response_to: str,
    email: str = "alice@example.com",
    name: str | None = "Alice",
    groups: list[str] | None = None,
    name_id: str | None = None,
    issuer: str = IDP_ENTITY_ID,
    audience: str = SAML_ENTITY_ID,
    destination: str = SAML_ACS_URL,
    recipient: str = SAML_ACS_URL,
    not_on_or_after: datetime | None = None,
    assertion_id: str | None = None,
    sign: bool = True,
    key: str = IDP_KEY,
    cert: str = IDP_CERT,
) -> str:
    """A base64-encoded samlp:Response whose Assertion is signed (RSA-SHA256, SHA-256) by
    `key` unless `sign` is false. `not_on_or_after` (default: in 5 minutes) bounds the
    SubjectConfirmationData; the Conditions stay valid for 10 minutes."""
    now = datetime.now(UTC)
    expires = not_on_or_after or now + timedelta(minutes=5)
    attributes = [_attribute("email", [email]), _attribute("groups", groups or [])]
    if name is not None:
        attributes.append(_attribute("name", [name]))
    assertion = f"""<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
    xmlns:xs="http://www.w3.org/2001/XMLSchema"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    ID={quoteattr(assertion_id or f"_{uuid.uuid4().hex}")} Version="2.0"
    IssueInstant="{_instant(now)}">
  <saml:Issuer>{escape(issuer)}</saml:Issuer>
  <saml:Subject>
    <saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
      >{escape(name_id or email)}</saml:NameID>
    <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
      <saml:SubjectConfirmationData InResponseTo={quoteattr(in_response_to)}
          NotOnOrAfter="{_instant(expires)}" Recipient={quoteattr(recipient)}/>
    </saml:SubjectConfirmation>
  </saml:Subject>
  <saml:Conditions NotBefore="{_instant(now - timedelta(minutes=1))}"
      NotOnOrAfter="{_instant(now + timedelta(minutes=10))}">
    <saml:AudienceRestriction><saml:Audience>{escape(audience)}</saml:Audience>
    </saml:AudienceRestriction>
  </saml:Conditions>
  <saml:AuthnStatement AuthnInstant="{_instant(now)}" SessionIndex="_session-1">
    <saml:AuthnContext><saml:AuthnContextClassRef
      >urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef>
    </saml:AuthnContext>
  </saml:AuthnStatement>
  <saml:AttributeStatement>{"".join(attributes)}</saml:AttributeStatement>
</saml:Assertion>"""
    if sign:
        signed = OneLogin_Saml2_Utils.add_sign(
            assertion,
            key,
            cert,
            sign_algorithm=OneLogin_Saml2_Constants.RSA_SHA256,
            digest_algorithm=OneLogin_Saml2_Constants.SHA256,
        )
        assertion = signed.decode()
    response = f"""<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
    xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_{uuid.uuid4().hex}" Version="2.0"
    IssueInstant="{_instant(now)}" Destination={quoteattr(destination)}
    InResponseTo={quoteattr(in_response_to)}>
  <saml:Issuer>{escape(issuer)}</saml:Issuer>
  <samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
  </samlp:Status>
  {assertion}
</samlp:Response>"""
    return base64.b64encode(response.encode()).decode()
