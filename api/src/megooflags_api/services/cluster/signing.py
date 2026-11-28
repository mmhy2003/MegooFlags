"""Ed25519 signing of config snapshots.

The private key is derived from `MEGOOFLAGS_SECRET_KEY` with HKDF-SHA256, so every control-plane
replica signs with the same key. Workers only hold the public key.
"""

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

HKDF_INFO = b"megooflags/snapshots/v1"


class SnapshotSigner:
    def __init__(self, secret: str) -> None:
        seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HKDF_INFO).derive(
            secret.encode()
        )
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        self._public_key = base64.b64encode(self._key.public_key().public_bytes_raw()).decode()

    @property
    def public_key(self) -> str:
        """Standard base64 of the 32 raw public bytes."""
        return self._public_key

    def sign(self, data: bytes) -> str:
        """Standard base64 signature of `data`."""
        return base64.b64encode(self._key.sign(data)).decode()


def verify(public_key: str, data: bytes, signature: str) -> bool:
    """False for a bad key, bad base64 or a wrong signature; never raises."""
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        key.verify(base64.b64decode(signature, validate=True), data)
    except (InvalidSignature, ValueError):  # binascii.Error is a ValueError
        return False
    return True
