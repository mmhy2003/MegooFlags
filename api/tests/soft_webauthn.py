"""A software WebAuthn authenticator: an ES256 key that answers registration (`create`) and
assertion (`get`) options like a browser would, with "none" attestation."""

import base64
import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass
class SoftAuthenticator:
    origin: str
    rp_id: str
    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    credential_id: bytes = field(default_factory=lambda: os.urandom(32))
    sign_count: int = 0
    user_handle: bytes | None = None

    def _auth_data(self, flags: int, attested: bytes = b"") -> bytes:
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        return rp_hash + bytes([flags]) + struct.pack(">I", self.sign_count) + attested

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        ).encode()

    def create(self, options: dict[str, Any]) -> dict[str, Any]:
        self.user_handle = unb64url(options["user"]["id"])
        n = self.key.public_key().public_numbers()
        cose = cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        )
        attested = (
            bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose
        )
        att = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": self._auth_data(0x45, attested)}
        )
        cd = self._client_data("webauthn.create", options["challenge"])
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(cd),
                "attestationObject": b64url(att),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def get(self, options: dict[str, Any], *, bump: bool = True) -> dict[str, Any]:
        if bump:
            self.sign_count += 1
        ad = self._auth_data(0x05)
        cd = self._client_data("webauthn.get", options["challenge"])
        sig = self.key.sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(cd),
                "authenticatorData": b64url(ad),
                "signature": b64url(sig),
                "userHandle": b64url(self.user_handle) if self.user_handle else None,
            },
            "clientExtensionResults": {},
        }
