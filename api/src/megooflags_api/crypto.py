"""Encryption of secrets stored in the database (SSO client secrets, SAML keys, TOTP seeds).

The Fernet key is derived from `MEGOOFLAGS_SECRET_KEY` with HKDF-SHA256, so changing that setting
makes every stored secret unreadable.
"""

import base64
from typing import Annotated

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Depends, Request

from megooflags_api.settings import Settings

HKDF_INFO = b"megooflags/secrets/v1"


class SecretBox:
    def __init__(self, secret: str) -> None:
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HKDF_INFO).derive(
            secret.encode()
        )
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        """The plaintext; ValueError when the token is corrupt or was made with another key."""
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("cannot decrypt the stored secret") from exc


def require_secret_key(settings: Settings) -> SecretBox:
    if settings.secret_key is None:
        raise RuntimeError(
            "MEGOOFLAGS_SECRET_KEY is not set: set it to a random value of at least 32 characters, "
            "e.g. the output of `openssl rand -hex 32`"
        )
    return SecretBox(settings.secret_key.get_secret_value())


def get_secret_box(request: Request) -> SecretBox | None:
    box: SecretBox | None = request.app.state.secret_box
    return box


SecretBoxDep = Annotated[SecretBox | None, Depends(get_secret_box)]
