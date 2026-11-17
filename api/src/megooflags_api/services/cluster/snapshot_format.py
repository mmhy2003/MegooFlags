"""The signed config snapshot of one environment, shared by the control plane and the workers.

A snapshot is three lines: the signature, the header JSON and the ruleset. The same bytes go on
the wire (the three `data:` lines of an SSE `put`) and on a worker's disk.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.services.cluster.signing import verify
from megooflags_api.services.ruleset import EnvRef

FORMAT = 1


class SnapshotError(ValueError):
    pass


def _canonical(data: dict[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _field[T](data: dict[str, Any], name: str, kind: type[T]) -> T:
    value = data.get(name)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise SnapshotError(f"snapshot header field {name!r} is missing or not {kind.__name__}")
    return value


def _uuid(data: dict[str, Any], name: str) -> uuid.UUID:
    try:
        return uuid.UUID(_field(data, name, str))
    except ValueError as exc:
        raise SnapshotError(f"snapshot header field {name!r} is not a UUID") from exc


def _datetime(data: dict[str, Any], name: str) -> datetime:
    try:
        value = datetime.fromisoformat(_field(data, name, str))
    except ValueError as exc:
        raise SnapshotError(f"snapshot header field {name!r} is not a date") from exc
    if value.tzinfo is None:
        raise SnapshotError(f"snapshot header field {name!r} has no time zone")
    return value


def _optional_datetime(data: dict[str, Any], name: str) -> datetime | None:
    return None if data.get(name) is None else _datetime(data, name)


@dataclass(frozen=True, slots=True)
class SnapshotKey:
    id: uuid.UUID
    kind: SdkKeyKind
    hash: str
    revoked_at: datetime | None
    expires_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "kind": self.kind.value,
            "hash": self.hash,
            "revoked_at": _iso(self.revoked_at),
            "expires_at": _iso(self.expires_at),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SnapshotKey":
        if not isinstance(data, dict):
            raise SnapshotError("snapshot key is not an object")
        try:
            kind = SdkKeyKind(_field(data, "kind", str))
        except ValueError as exc:
            raise SnapshotError("snapshot key has an unknown kind") from exc
        return cls(
            id=_uuid(data, "id"),
            kind=kind,
            hash=_field(data, "hash", str),
            revoked_at=_optional_datetime(data, "revoked_at"),
            expires_at=_optional_datetime(data, "expires_at"),
        )


@dataclass(frozen=True, slots=True)
class SnapshotHeader:
    format: int
    environment_id: uuid.UUID
    environment_key: str
    project_id: uuid.UUID
    project_key: str
    version: int
    published_at: datetime
    keys: tuple[SnapshotKey, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "keys", tuple(sorted(self.keys, key=lambda key: key.id)))

    def env_ref(self) -> EnvRef:
        return EnvRef(id=self.environment_id, project_id=self.project_id, key=self.environment_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "environment_id": str(self.environment_id),
            "environment_key": self.environment_key,
            "project_id": str(self.project_id),
            "project_key": self.project_key,
            "version": self.version,
            "published_at": _iso(self.published_at),
            "keys": [key.to_dict() for key in self.keys],
        }

    def to_json(self) -> bytes:
        """Compact JSON with sorted keys and ISO 8601 UTC dates."""
        return _canonical(self.to_dict())

    @classmethod
    def from_json(cls, data: bytes) -> "SnapshotHeader":
        """The header in `data`; SnapshotError when it is malformed or of an unknown format."""
        try:
            parsed = json.loads(data)
        except ValueError as exc:
            raise SnapshotError("snapshot header is not JSON") from exc
        if not isinstance(parsed, dict):
            raise SnapshotError("snapshot header is not an object")
        if _field(parsed, "format", int) != FORMAT:
            raise SnapshotError(f"unknown snapshot format {parsed['format']}")
        return cls(
            format=FORMAT,
            environment_id=_uuid(parsed, "environment_id"),
            environment_key=_field(parsed, "environment_key", str),
            project_id=_uuid(parsed, "project_id"),
            project_key=_field(parsed, "project_key", str),
            version=_field(parsed, "version", int),
            published_at=_datetime(parsed, "published_at"),
            keys=tuple(SnapshotKey.from_dict(key) for key in _field(parsed, "keys", list)),
        )


def content_digest(header: SnapshotHeader, ruleset: bytes) -> str:
    """SHA-256 hex of the header without version and published_at, b"\\n", and the ruleset."""
    content = header.to_dict()
    del content["version"], content["published_at"]
    return hashlib.sha256(_canonical(content) + b"\n" + ruleset).hexdigest()


@dataclass(frozen=True, slots=True)
class SignedSnapshot:
    header: SnapshotHeader
    ruleset: bytes
    signature: str

    def payload(self) -> bytes:
        """What is signed: the header JSON, b"\\n" and the ruleset."""
        return self.header.to_json() + b"\n" + self.ruleset

    def to_bytes(self) -> bytes:
        """The signature, the header JSON and the ruleset, one per line."""
        header = self.header.to_json()
        if any(b"\n" in part for part in (self.signature.encode(), header, self.ruleset)):
            raise SnapshotError("a snapshot line contains a newline")
        return self.signature.encode() + b"\n" + header + b"\n" + self.ruleset

    @classmethod
    def from_bytes(cls, data: bytes) -> "SignedSnapshot":
        """The snapshot in `data`; SnapshotError when it is truncated or malformed."""
        lines = data.split(b"\n", 2)
        if len(lines) < 3:
            raise SnapshotError("snapshot has fewer than three lines")
        signature, header, ruleset = lines
        try:
            decoded = signature.decode("ascii")
        except UnicodeDecodeError as exc:
            raise SnapshotError("snapshot signature is not ASCII") from exc
        return cls(header=SnapshotHeader.from_json(header), ruleset=ruleset, signature=decoded)

    def verify(self, public_key: str) -> bool:
        return verify(public_key, self.payload(), self.signature)
