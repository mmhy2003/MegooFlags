import dataclasses
import json
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.services.cluster.signing import SnapshotSigner
from megooflags_api.services.cluster.snapshot_format import (
    FORMAT,
    SignedSnapshot,
    SnapshotError,
    SnapshotHeader,
    SnapshotKey,
    content_digest,
)
from megooflags_api.services.ruleset import EnvRef

SIGNER = SnapshotSigner("s" * 32)
RULESET = b'{"flags":{},"schema_version":1}'
KEY_A = SnapshotKey(
    id=uuid.UUID("00000000-0000-0000-0000-00000000000a"),
    kind=SdkKeyKind.SERVER,
    hash="a" * 64,
    revoked_at=None,
    expires_at=datetime(2027, 1, 1, tzinfo=UTC),
)
KEY_B = SnapshotKey(
    id=uuid.UUID("00000000-0000-0000-0000-00000000000b"),
    kind=SdkKeyKind.CLIENT,
    hash="b" * 64,
    revoked_at=None,
    expires_at=None,
)


def make_header(**changes: object) -> SnapshotHeader:
    header = SnapshotHeader(
        format=FORMAT,
        environment_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        environment_key="production",
        project_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        project_key="web",
        version=7,
        published_at=datetime(2026, 11, 16, 12, 30, 5, 123456, tzinfo=UTC),
        keys=(KEY_A, KEY_B),
    )
    return dataclasses.replace(header, **changes)  # type: ignore[arg-type]


def make_snapshot(header: SnapshotHeader | None = None, ruleset: bytes = RULESET) -> SignedSnapshot:
    header = header or make_header()
    payload = header.to_json() + b"\n" + ruleset
    return SignedSnapshot(header=header, ruleset=ruleset, signature=SIGNER.sign(payload))


def test_round_trip_bytes() -> None:
    snapshot = make_snapshot()

    data = snapshot.to_bytes()

    assert data.count(b"\n") == 2
    assert data.startswith(snapshot.signature.encode() + b"\n")
    assert data.endswith(b"\n" + RULESET)
    assert SignedSnapshot.from_bytes(data) == snapshot


def test_header_json_is_canonical() -> None:
    eastern = timezone(timedelta(hours=-5))
    header = make_header(published_at=datetime(2026, 11, 16, 7, 30, tzinfo=eastern))

    data = header.to_json()
    parsed = json.loads(data)

    assert b" " not in data
    assert b"\n" not in data
    assert data == json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
    assert parsed["published_at"] == "2026-11-16T12:30:00Z"
    assert parsed["keys"][0]["expires_at"] == "2027-01-01T00:00:00Z"
    assert parsed["keys"][0]["revoked_at"] is None
    assert parsed["format"] == FORMAT
    assert SnapshotHeader.from_json(data) == header


def test_keys_are_sorted_by_id() -> None:
    header = make_header(keys=(KEY_B, KEY_A))

    assert header.keys == (KEY_A, KEY_B)
    assert [key["id"] for key in json.loads(header.to_json())["keys"]] == [
        str(KEY_A.id),
        str(KEY_B.id),
    ]


def test_digest_ignores_version_and_published_at() -> None:
    later = make_header(version=99, published_at=datetime(2030, 1, 1, tzinfo=UTC))

    assert content_digest(make_header(), RULESET) == content_digest(later, RULESET)
    assert len(content_digest(later, RULESET)) == 64


def test_digest_changes_with_keys() -> None:
    revoked = dataclasses.replace(KEY_A, revoked_at=datetime(2026, 11, 16, tzinfo=UTC))

    assert content_digest(make_header(), RULESET) != content_digest(
        make_header(keys=(revoked, KEY_B)), RULESET
    )


def test_digest_changes_with_ruleset() -> None:
    assert content_digest(make_header(), RULESET) != content_digest(
        make_header(), b'{"flags":{"a":{}},"schema_version":1}'
    )


def test_newline_refused() -> None:
    snapshot = make_snapshot(ruleset=b'{"flags":\n{}}')

    with pytest.raises(SnapshotError):
        snapshot.to_bytes()


def test_unknown_format_refused() -> None:
    data = make_snapshot(make_header(format=2)).to_bytes()

    with pytest.raises(SnapshotError):
        SignedSnapshot.from_bytes(data)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"signature",
        b"signature\n{}",
        b"signature\nnot json\n{}",
        b'signature\n{"format":1}\n{}',
        b"signature\n[1]\n{}",
        b"\xff\xfe\n\xff\n{}",
    ],
)
def test_truncated_refused(data: bytes) -> None:
    with pytest.raises(SnapshotError):
        SignedSnapshot.from_bytes(data)


def test_truncated_snapshot_bytes_refused() -> None:
    data = make_snapshot().to_bytes()

    with pytest.raises(SnapshotError):
        SignedSnapshot.from_bytes(data[: data.index(b"\n", data.index(b"\n") + 1)])


def test_verify_with_public_key() -> None:
    snapshot = make_snapshot()
    flipped = bytearray(snapshot.ruleset)
    flipped[2] ^= 1
    tampered = dataclasses.replace(snapshot, ruleset=bytes(flipped))

    assert snapshot.verify(SIGNER.public_key)
    assert SignedSnapshot.from_bytes(snapshot.to_bytes()).verify(SIGNER.public_key)
    assert not tampered.verify(SIGNER.public_key)
    assert not snapshot.verify(SnapshotSigner("t" * 32).public_key)


def test_env_ref() -> None:
    header = make_header()

    assert header.env_ref() == EnvRef(
        id=header.environment_id, project_id=header.project_id, key="production"
    )
