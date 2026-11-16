import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from megooflags_api.models.enums import SdkKeyKind
from megooflags_api.services.cluster.signing import SnapshotSigner
from megooflags_api.services.cluster.snapshot_format import (
    FORMAT,
    SignedSnapshot,
    SnapshotHeader,
    SnapshotKey,
)
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.worker.store import ClusterMeta, SnapshotStore

SIGNER = SnapshotSigner("s" * 32)
OTHER_SIGNER = SnapshotSigner("o" * 32)
PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a0")
ENV_A = uuid.UUID("00000000-0000-0000-0000-000000000001")
ENV_B = uuid.UUID("00000000-0000-0000-0000-000000000002")
PUBLISHED = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def ruleset(environment: str, flag_on: bool = True) -> bytes:
    data = {
        "schema_version": 1,
        "project": "web",
        "environment": environment,
        "flags": {
            "new-ui": {
                "key": "new-ui",
                "version": 1,
                "type": "boolean",
                "enabled": flag_on,
                "variations": [{"key": "on", "value": True}, {"key": "off", "value": False}],
                "off_variation": "off",
                "prerequisites": [],
                "targets": [],
                "rules": [],
                "fallthrough": {"variation": "on"},
                "salt": "new-ui",
            }
        },
        "segments": {},
    }
    return json.dumps(data, separators=(",", ":")).encode()


def key(n: int, **changes: datetime | None) -> SnapshotKey:
    return SnapshotKey(
        id=uuid.UUID(int=n),
        kind=SdkKeyKind.SERVER,
        hash=f"{n:064x}",
        revoked_at=changes.get("revoked_at"),
        expires_at=changes.get("expires_at"),
    )


def snapshot(
    environment_id: uuid.UUID = ENV_A,
    version: int = 1,
    keys: tuple[SnapshotKey, ...] = (),
    body: bytes | None = None,
    signer: SnapshotSigner = SIGNER,
) -> SignedSnapshot:
    env_key = "production" if environment_id == ENV_A else "staging"
    header = SnapshotHeader(
        format=FORMAT,
        environment_id=environment_id,
        environment_key=env_key,
        project_id=PROJECT_ID,
        project_key="web",
        version=version,
        published_at=PUBLISHED,
        keys=keys,
    )
    body = body if body is not None else ruleset(env_key)
    signature = signer.sign(header.to_json() + b"\n" + body)
    return SignedSnapshot(header=header, ruleset=body, signature=signature)


def new_store(path: Path, **kwargs: int) -> SnapshotStore:
    store = SnapshotStore(path, **kwargs)
    store.load()
    store.meta.public_key = SIGNER.public_key
    store.save_meta()
    return store


def reopened(path: Path, **kwargs: int) -> SnapshotStore:
    store = SnapshotStore(path, **kwargs)
    store.load()
    return store


def snap_file(path: Path, environment_id: uuid.UUID) -> Path:
    return path / "snapshots" / f"{environment_id}.snap"


def test_apply_writes_atomically_and_reloads(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    snap = snapshot(keys=(key(1),))
    assert store.apply(snap) is True
    entry = store.entry(ENV_A)
    assert entry is not None
    assert snap_file(tmp_path, ENV_A).read_bytes() == snap.to_bytes()

    again = reopened(tmp_path)
    assert again.valid is True
    assert again.versions() == {ENV_A: 1}
    reloaded = again.entry(ENV_A)
    assert reloaded is not None
    assert reloaded.etag == entry.etag
    assert reloaded.body == snap.ruleset
    principal = again.principal(key(1).hash)
    assert principal is not None
    assert principal.key_id == key(1).id
    assert principal.environment == EnvRef(id=ENV_A, project_id=PROJECT_ID, key="production")


def test_apply_rejects_bad_signature_and_keeps_old(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    good = snapshot(version=1, body=ruleset("production", flag_on=True))
    assert store.apply(good)
    forged = snapshot(version=2, body=ruleset("production", flag_on=False), signer=OTHER_SIGNER)
    assert store.apply(forged) is False

    assert store.versions() == {ENV_A: 1}
    entry = store.entry(ENV_A)
    assert entry is not None
    assert entry.body == good.ruleset
    assert snap_file(tmp_path, ENV_A).read_bytes() == good.to_bytes()


def test_apply_rejects_without_public_key(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.load()
    assert store.apply(snapshot()) is False
    assert store.entry(ENV_A) is None


def test_apply_rejects_older_or_equal_version(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    assert store.apply(snapshot(version=5))
    assert store.apply(snapshot(version=5, body=ruleset("production", flag_on=False))) is False
    assert store.apply(snapshot(version=4, body=ruleset("production", flag_on=False))) is False
    assert store.versions() == {ENV_A: 5}
    assert store.apply(snapshot(version=6, body=ruleset("production", flag_on=False)))
    assert store.max_version() == 6
    entry = store.entry(ENV_A)
    assert entry is not None
    assert entry.ruleset.flags["new-ui"].enabled is False


def test_corrupt_file_discarded_on_load(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.apply(snapshot(ENV_A, keys=(key(1),)))
    store.apply(snapshot(ENV_B, keys=(key(2),)))
    store.mark_synced()
    path = snap_file(tmp_path, ENV_A)
    path.write_bytes(path.read_bytes()[:20])

    again = reopened(tmp_path)
    assert not path.exists()
    assert again.valid is False
    assert again.versions() == {ENV_B: 1}
    assert again.entry(ENV_A) is None
    assert again.principal(key(1).hash) is None
    assert again.entry(ENV_B) is not None
    assert again.principal(key(2).hash) is not None

    again.mark_synced()
    assert again.valid is True


def test_tampered_file_discarded(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.apply(snapshot(ENV_A, body=ruleset("production", flag_on=False)))
    path = snap_file(tmp_path, ENV_A)
    path.write_bytes(path.read_bytes().replace(b'"enabled":false', b'"enabled":true'))

    again = reopened(tmp_path)
    assert not path.exists()
    assert again.valid is False
    assert again.entry(ENV_A) is None


def test_file_signed_by_another_key_discarded(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.apply(snapshot(ENV_A))
    store.meta.public_key = OTHER_SIGNER.public_key
    store.save_meta()

    again = reopened(tmp_path)
    assert not snap_file(tmp_path, ENV_A).exists()
    assert again.valid is False
    assert again.versions() == {}


def test_remove_deletes_file_and_keys(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.apply(snapshot(ENV_A, keys=(key(1),)))
    store.apply(snapshot(ENV_B, keys=(key(2),)))

    assert store.remove(ENV_A) == EnvRef(id=ENV_A, project_id=PROJECT_ID, key="production")
    assert not snap_file(tmp_path, ENV_A).exists()
    assert store.entry(ENV_A) is None
    assert store.principal(key(1).hash) is None
    assert store.versions() == {ENV_B: 1}
    assert store.remove(ENV_A) is None

    again = reopened(tmp_path)
    assert again.versions() == {ENV_B: 1}
    assert again.valid is True


def test_new_version_replaces_keys(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.apply(snapshot(version=1, keys=(key(1),)))
    store.apply(snapshot(version=2, keys=(key(2),)))
    assert store.principal(key(1).hash) is None
    assert store.principal(key(2).hash) is not None


def test_principal_reports_revoked_and_expiry(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    revoked = key(1, revoked_at=PUBLISHED)
    expiring = key(2, expires_at=datetime(2027, 1, 1, tzinfo=UTC))
    active = key(3)
    store.apply(snapshot(keys=(revoked, expiring, active)))

    first = store.principal(revoked.hash)
    assert first is not None
    assert first.revoked is True
    assert first.inactive(PUBLISHED)

    second = store.principal(expiring.hash)
    assert second is not None
    assert second.revoked is False
    assert second.expires_at == datetime(2027, 1, 1, tzinfo=UTC)
    assert not second.inactive(PUBLISHED)
    assert second.inactive(datetime(2027, 1, 1, tzinfo=UTC))

    third = store.principal(active.hash)
    assert third is not None
    assert third.kind is SdkKeyKind.SERVER
    assert third.secret_hash == active.hash
    assert not third.inactive(PUBLISHED)

    assert store.principal("f" * 64) is None


def test_lru_reads_from_disk(tmp_path: Path) -> None:
    store = new_store(tmp_path, max_parsed=1)
    snap_a = snapshot(ENV_A)
    snap_b = snapshot(ENV_B)
    store.apply(snap_a)
    store.apply(snap_b)

    for _ in range(2):
        entry_a = store.entry(ENV_A)
        entry_b = store.entry(ENV_B)
        assert entry_a is not None
        assert entry_b is not None
        assert entry_a.body == snap_a.ruleset
        assert entry_a.ruleset.environment == "production"
        assert entry_b.body == snap_b.ruleset
        assert entry_b.ruleset.environment == "staging"


def test_meta_round_trip(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    store.load()
    assert store.meta == ClusterMeta()
    store.meta = ClusterMeta(
        public_key=SIGNER.public_key,
        synced=True,
        revoked=True,
        persist_context=True,
        last_contact_at=datetime(2026, 11, 16, 12, 30, 5, 123456, tzinfo=UTC),
        control_plane_version="0.4.1",
    )
    store.save_meta()

    again = reopened(tmp_path)
    assert again.meta == store.meta


def test_corrupt_meta_starts_empty(tmp_path: Path) -> None:
    (tmp_path / "cluster.json").write_text("{not json")
    store = reopened(tmp_path)
    assert store.meta == ClusterMeta()


def test_no_tmp_files_left(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.apply(snapshot(ENV_A))
    store.apply(snapshot(ENV_A, version=2))
    store.apply(snapshot(ENV_B))
    store.remove(ENV_B)
    store.mark_synced()
    (tmp_path / "snapshots" / f"{ENV_B}.snap.tmp").write_bytes(b"half written")

    reopened(tmp_path)
    names = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert names == ["cluster.json", "snapshots", f"snapshots/{ENV_A}.snap"]
