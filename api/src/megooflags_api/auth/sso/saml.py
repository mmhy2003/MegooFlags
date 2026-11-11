"""SAML 2.0 sign-in (SP-initiated) with python3-saml, in strict mode.

The AuthnRequest goes out over HTTP-Redirect, signed when the provider has an SP key. The
response comes back to the ACS over HTTP-POST and must carry a signed Assertion (RSA-SHA256 or
better) for this SP's entity id, sent to this ACS URL, in response to the request we sent, and
not expired. Assertion IDs are remembered in Valkey until they expire, so a replay is refused.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import xmlsec
from lxml import etree  # type: ignore[import-untyped]
from onelogin.saml2.auth import OneLogin_Saml2_Auth  # type: ignore[import-untyped]
from onelogin.saml2.constants import OneLogin_Saml2_Constants  # type: ignore[import-untyped]
from onelogin.saml2.errors import (  # type: ignore[import-untyped]
    OneLogin_Saml2_Error,
    OneLogin_Saml2_ValidationError,
)
from onelogin.saml2.idp_metadata_parser import (  # type: ignore[import-untyped]
    OneLogin_Saml2_IdPMetadataParser,
)
from onelogin.saml2.settings import OneLogin_Saml2_Settings  # type: ignore[import-untyped]
from valkey.asyncio import Valkey
from valkey.exceptions import ValkeyError

from megooflags_api.auth.sso import SsoAssertion, SsoDenied
from megooflags_api.auth.sso.state import PREFIX
from megooflags_api.crypto import SecretBox
from megooflags_api.errors import unavailable
from megooflags_api.models import SsoProvider
from megooflags_api.schemas.sso import SamlConfigOut

logger = logging.getLogger(__name__)

NAMEID_EMAIL = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
# Anything python3-saml, lxml or xmlsec raise for a bad message or bad metadata.
_SAML_ERRORS = (
    OneLogin_Saml2_Error,
    OneLogin_Saml2_ValidationError,
    ValueError,
    TypeError,
    KeyError,
    etree.Error,
    xmlsec.Error,
)
MIN_REPLAY_SECONDS = 60
_VALKEY_ERRORS = (ValkeyError, OSError, TimeoutError)


def _failed(reason: str) -> SsoDenied:
    return SsoDenied("sso_failed", reason)


def _path(key: str, endpoint: str) -> str:
    return f"/api/auth/sso/{key}/saml/{endpoint}"


def _config(provider: SsoProvider) -> SamlConfigOut:
    # As stored: a provider with a metadata URL also keeps the XML fetched from it.
    return SamlConfigOut.model_validate(provider.config)


def _sp_key(provider: SsoProvider, box: SecretBox) -> dict[str, str] | None:
    if provider.secret_encrypted is None:
        return None
    try:
        secret: dict[str, str] = json.loads(box.decrypt(provider.secret_encrypted))
        return {"privateKey": secret["sp_private_key"], "x509cert": secret["sp_certificate"]}
    except (ValueError, KeyError):
        raise _failed("cannot decrypt the SP key") from None


def _sp_settings(provider: SsoProvider, public_url: str, box: SecretBox) -> dict[str, Any]:
    config = _config(provider)
    key = _sp_key(provider, box)
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": config.sp_entity_id or public_url + _path(provider.key, "metadata"),
            "assertionConsumerService": {
                "url": public_url + _path(provider.key, "acs"),
                "binding": OneLogin_Saml2_Constants.BINDING_HTTP_POST,
            },
            "NameIDFormat": NAMEID_EMAIL,
            **(key or {}),
        },
        "security": {
            "authnRequestsSigned": key is not None,
            "wantAssertionsSigned": True,
            "wantMessagesSigned": False,
            "rejectDeprecatedAlgorithm": True,
            "wantNameId": True,
            # No RequestedAuthnContext: python3-saml would ask for PasswordProtectedTransport
            # exactly, which IdPs that enforce MFA or passwordless sign-in refuse or downgrade.
            "requestedAuthnContext": False,
            "signatureAlgorithm": OneLogin_Saml2_Constants.RSA_SHA256,
            "digestAlgorithm": OneLogin_Saml2_Constants.SHA256,
        },
    }


def is_idp_metadata(xml: str) -> bool:
    """Whether `xml` is SAML metadata with an IDPSSODescriptor."""
    try:
        return "idp" in OneLogin_Saml2_IdPMetadataParser.parse(xml)
    except _SAML_ERRORS:
        return False


def saml_settings(provider: SsoProvider, public_url: str, box: SecretBox) -> dict[str, Any]:
    """The python3-saml settings: this SP, plus the IdP from the stored metadata."""
    settings = _sp_settings(provider, public_url, box)
    xml = _config(provider).idp_metadata_xml
    if xml is None:
        raise _failed("the provider has no IdP metadata")
    try:
        idp = OneLogin_Saml2_IdPMetadataParser.parse(xml)
    except _SAML_ERRORS as exc:
        raise _failed(f"the IdP metadata is invalid: {exc}") from None
    if "idp" not in idp:
        raise _failed("the IdP metadata has no IDPSSODescriptor")
    settings["idp"] = idp["idp"]
    return settings


def sp_metadata(provider: SsoProvider, public_url: str, box: SecretBox) -> str:
    """This SP's metadata, for the IdP administrator."""
    settings = OneLogin_Saml2_Settings(
        _sp_settings(provider, public_url, box), sp_validation_only=True
    )
    metadata: bytes | str = settings.get_sp_metadata()
    return metadata.decode() if isinstance(metadata, bytes) else metadata


def request_data(public_url: str, path: str, post_data: dict[str, str]) -> dict[str, Any]:
    """The request as python3-saml sees it, built from the public URL rather than the request,
    so Destination and Recipient are checked against the URL the IdP was given. The port stays
    in `http_host` (`server_port` is deprecated)."""
    url = urlsplit(public_url)
    return {
        "https": "on" if url.scheme == "https" else "off",
        "http_host": url.netloc,
        "script_name": url.path.rstrip("/") + path,
        "get_data": {},
        "post_data": post_data,
    }


@dataclass(frozen=True)
class LoginRequest:
    url: str
    request_id: str


def login_request(settings: dict[str, Any], public_url: str, key: str, relay: str) -> LoginRequest:
    """The IdP URL carrying the AuthnRequest, with `relay` as the RelayState."""
    try:
        auth = OneLogin_Saml2_Auth(request_data(public_url, _path(key, "acs"), {}), settings)
        url: str = auth.login(return_to=relay)
    except _SAML_ERRORS as exc:
        raise _failed(f"cannot build the AuthnRequest: {exc}") from None
    return LoginRequest(url, auth.get_last_request_id())


@dataclass(frozen=True)
class SamlResult:
    assertion: SsoAssertion
    assertion_id: str
    not_on_or_after: int | None


def _first(values: Any) -> str | None:
    if isinstance(values, list) and values and isinstance(values[0], str) and values[0]:
        return values[0]
    return None


def complete(
    settings: dict[str, Any],
    public_url: str,
    provider: SsoProvider,
    post_data: dict[str, str],
    request_id: str,
) -> SamlResult:
    """Validate the ACS POST against the request we sent; SsoDenied("sso_failed") on failure."""
    data = request_data(public_url, _path(provider.key, "acs"), post_data)
    try:
        auth = OneLogin_Saml2_Auth(data, settings)
        auth.process_response(request_id=request_id)
    except _SAML_ERRORS as exc:
        raise _failed(f"the SAML response is invalid: {exc}") from None
    if auth.get_errors() or not auth.is_authenticated():
        raise _failed(f"the SAML response is invalid: {auth.get_last_error_reason()}")
    # python3-saml checks InResponseTo only when the response has one; we require it.
    if auth.get_last_response_in_response_to() != request_id:
        raise _failed("the SAML response does not answer our request")
    assertion_id = auth.get_last_assertion_id()
    if not assertion_id:
        raise _failed("the assertion has no ID")
    config = _config(provider)
    attributes = auth.get_attributes()
    name_id = auth.get_nameid()
    groups = attributes.get(config.groups_attribute)
    return SamlResult(
        assertion=SsoAssertion(
            provider=provider.key,
            subject=name_id,
            email=_first(attributes.get(config.email_attribute)) or name_id,
            # The assertion is signed by the IdP, so its email counts as verified.
            email_verified=True,
            name=_first(attributes.get(config.name_attribute)),
            groups=[g for g in groups if isinstance(g, str)] if isinstance(groups, list) else [],
        ),
        assertion_id=assertion_id,
        not_on_or_after=auth.get_last_assertion_not_on_or_after(),
    )


async def remember_assertion(
    client: Valkey, assertion_id: str, not_on_or_after: int | None
) -> None:
    """Mark the assertion used until it expires (at least a minute); SsoDenied for a replay."""
    seconds = max(MIN_REPLAY_SECONDS, int((not_on_or_after or 0) - time.time()))
    try:
        fresh = await client.set(f"{PREFIX}saml-assertion:{assertion_id}", "1", nx=True, ex=seconds)
    except _VALKEY_ERRORS:
        logger.warning("cannot record the SAML assertion ID", exc_info=True)
        raise unavailable("single sign-on is unavailable right now; try again later") from None
    if not fresh:
        raise _failed(f"the assertion {assertion_id!r} was replayed")
