import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import AfterValidator, AnyHttpUrl, StringConstraints, model_validator

from megooflags_api.models.enums import ScopeKind, SsoProtocol
from megooflags_api.schemas.common import ApiModel, Key, Name
from megooflags_api.schemas.identity import check_scope_fields


def _http_url(value: str) -> str:
    AnyHttpUrl(value)  # raises for anything that is not an http(s) URL
    return value


# Kept as given: an OIDC issuer must match the ID token's `iss` exactly, and AnyHttpUrl would add
# a trailing slash to "https://accounts.google.com".
HttpUrl = Annotated[str, StringConstraints(max_length=2000), AfterValidator(_http_url)]
Secret = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
Claim = Annotated[str, StringConstraints(min_length=1, max_length=200)]
IdpGroup = Annotated[str, StringConstraints(min_length=1, max_length=500)]
MetadataXml = Annotated[str, StringConstraints(min_length=1, max_length=1_000_000)]


def _has_openid(scopes: list[Claim]) -> list[Claim]:
    if "openid" not in scopes:
        raise ValueError("must include 'openid'")
    return scopes


class OidcConfig(ApiModel):
    issuer: HttpUrl
    client_id: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    scopes: Annotated[list[Claim], AfterValidator(_has_openid)] = ["openid", "email", "profile"]
    # The ID token or userinfo claim that lists the user's groups.
    groups_claim: Claim = "groups"


class SamlConfigOut(ApiModel):
    # With `idp_metadata_url`, the XML is the copy fetched when the provider was last saved.
    idp_metadata_xml: str | None = None
    idp_metadata_url: HttpUrl | None = None
    # Defaults to the SP metadata URL, /api/auth/sso/<key>/saml/metadata.
    sp_entity_id: Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None = None
    email_attribute: Claim = "email"
    name_attribute: Claim = "name"
    groups_attribute: Claim = "groups"


class SamlConfig(SamlConfigOut):
    """IdP metadata as XML or as a URL the server fetches (exactly one), plus attribute names."""

    idp_metadata_xml: MetadataXml | None = None

    @model_validator(mode="after")
    def _one_metadata_source(self) -> Self:
        if (self.idp_metadata_xml is None) == (self.idp_metadata_url is None):
            raise ValueError("give exactly one of 'idp_metadata_xml' and 'idp_metadata_url'")
        return self


class SsoProviderCreate(ApiModel):
    key: Key
    name: Name
    protocol: SsoProtocol
    enabled: bool = True
    oidc: OidcConfig | None = None
    saml: SamlConfig | None = None
    # OIDC: the client secret (required). SAML: the SP key pair that signs requests (optional).
    client_secret: Secret | None = None
    sp_private_key: Secret | None = None
    sp_certificate: Secret | None = None
    # Create unknown users on their first sign-in.
    jit_enabled: bool = True
    # Sign an existing user in when the IdP asserts the same, verified, email.
    link_by_verified_email: bool = False
    # Bound at org scope to everyone who signs in through this provider.
    default_role: Key | None = None

    @model_validator(mode="after")
    def _fields_match_protocol(self) -> Self:
        if self.protocol == SsoProtocol.OIDC:
            if self.oidc is None or self.saml is not None:
                raise ValueError("an OIDC provider needs 'oidc' and no 'saml'")
            if self.client_secret is None:
                raise ValueError("an OIDC provider needs 'client_secret'")
            if self.sp_private_key is not None or self.sp_certificate is not None:
                raise ValueError("'sp_private_key' and 'sp_certificate' are for SAML providers")
        else:
            if self.saml is None or self.oidc is not None:
                raise ValueError("a SAML provider needs 'saml' and no 'oidc'")
            if self.client_secret is not None:
                raise ValueError("'client_secret' is for OIDC providers")
            if (self.sp_private_key is None) != (self.sp_certificate is None):
                raise ValueError("give both 'sp_private_key' and 'sp_certificate', or neither")
        return self


class SsoProviderUpdate(ApiModel):
    """Absent or null fields are kept, except `default_role`, which null clears. Blocks and
    secrets must match the provider's protocol; a new block replaces the old one."""

    name: Name | None = None
    enabled: bool | None = None
    oidc: OidcConfig | None = None
    saml: SamlConfig | None = None
    client_secret: Secret | None = None
    sp_private_key: Secret | None = None
    sp_certificate: Secret | None = None
    jit_enabled: bool | None = None
    link_by_verified_email: bool | None = None
    default_role: Key | None = None


class SsoProviderOut(ApiModel):
    id: uuid.UUID
    key: str
    name: str
    protocol: SsoProtocol
    enabled: bool
    oidc: OidcConfig | None
    saml: SamlConfigOut | None
    has_client_secret: bool
    has_sp_key: bool
    jit_enabled: bool
    link_by_verified_email: bool
    default_role: str | None
    created_at: datetime
    updated_at: datetime


class SsoMappingIn(ApiModel):
    """An IdP group mapped to a team, or to a role at a scope (`org`, `project` with `project`,
    or `environment` with `project` and `env`)."""

    group: IdpGroup
    team: Key | None = None
    role: Key | None = None
    scope: ScopeKind | None = None
    project: Key | None = None
    env: Key | None = None

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.team is None) == (self.role is None):
            raise ValueError("give exactly one of 'team' and 'role'")
        if self.team is not None:
            if self.scope is not None or self.project is not None or self.env is not None:
                raise ValueError("a team mapping takes no 'scope', 'project' or 'env'")
        elif self.scope is None:
            raise ValueError("a role mapping needs 'scope'")
        else:
            check_scope_fields(self.scope, self.project, self.env)
        return self


class SsoMappingOut(ApiModel):
    id: uuid.UUID
    group: str
    team: str | None
    role: str | None
    scope: ScopeKind | None
    project: str | None
    env: str | None


class PublicProvider(ApiModel):
    key: str
    name: str
    protocol: SsoProtocol
    # Relative: the browser navigates here to sign in.
    start_url: str
