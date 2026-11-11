"""Single sign-on: OIDC (and SAML) sign-in flows, JIT provisioning and group sync."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SsoAssertion:
    """Who the identity provider says signed in."""

    provider: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None
    groups: list[str]


class SsoDenied(Exception):
    """An SSO sign-in is refused. `code` goes to the sign-in page (`/sign-in?error=<code>`);
    `reason` only to the log and the audit entry."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
