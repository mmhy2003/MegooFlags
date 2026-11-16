"""The worker's signed snapshots on disk, with the key index and recent rulesets in memory.

Under the data directory, `snapshots/<environment_id>.snap` holds `SignedSnapshot.to_bytes()` and
`cluster.json` what the worker knows about the cluster. Every write goes to `<name>.tmp`, is
synced, then replaces the file, so a crash never leaves a half-written snapshot. Every file is
verified against the control plane's public key when it is loaded; a bad one is deleted and the
store stays invalid until the next full sync.

Rulesets are read from worker threads (`asyncio.to_thread`) while the event loop applies and
removes snapshots, so the in-memory indexes are only touched under one lock.
"""

import json
import logging
import os
import threading
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from megooflags_api.services.cluster.snapshot_format import (
    FORMAT,
    SignedSnapshot,
    SnapshotError,
    SnapshotHeader,
    SnapshotKey,
)
from megooflags_api.services.ruleset import EnvRef
from megooflags_api.services.ruleset_cache import CachedRuleset, from_body
from megooflags_api.services.sdk_keys import SdkPrincipal

logger = logging.getLogger(__name__)

META_FILE = "cluster.json"
SNAPSHOT_DIR = "snapshots"
SNAPSHOT_SUFFIX = ".snap"
TMP_SUFFIX = ".tmp"


@dataclass(slots=True)
class ClusterMeta:
    public_key: str | None = None
    synced: bool = False
    revoked: bool = False
    persist_context: bool = False
    last_contact_at: datetime | None = None
    control_plane_version: str | None = None

    def to_json(self) -> bytes:
        data = asdict(self)
        if self.last_contact_at is not None:
            data["last_contact_at"] = self.last_contact_at.isoformat()
        return json.dumps(data, sort_keys=True).encode()

    @classmethod
    def from_json(cls, data: bytes) -> "ClusterMeta":
        """The fields in `data`; ValueError when it is not a valid cluster.json."""
        parsed: Any = json.loads(data)
        if not isinstance(parsed, dict):
            raise ValueError("cluster.json is not an object")
        contact = parsed.get("last_contact_at")
        meta = cls(
            public_key=parsed.get("public_key"),
            synced=parsed.get("synced", False),
            revoked=parsed.get("revoked", False),
            persist_context=parsed.get("persist_context", False),
            last_contact_at=None if contact is None else datetime.fromisoformat(contact),
            control_plane_version=parsed.get("control_plane_version"),
        )
        for name in ("public_key", "control_plane_version"):
            if not isinstance(getattr(meta, name), str | None):
                raise ValueError(f"cluster.json field {name!r} is not a string")
        for name in ("synced", "revoked", "persist_context"):
            if not isinstance(getattr(meta, name), bool):
                raise ValueError(f"cluster.json field {name!r} is not a boolean")
        return meta


def write_atomic(path: Path, data: bytes) -> None:
    """Write `data` to `path` through a synced temporary file in the same directory."""
    tmp = path.with_name(path.name + TMP_SUFFIX)
    with tmp.open("wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp, path)
    _sync_directory(path.parent)


def _sync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class SnapshotStore:
    """Signed snapshots per environment, on disk and in memory."""

    def __init__(self, data_dir: Path, max_parsed: int = 32) -> None:
        self._dir = data_dir
        self._snapshots = data_dir / SNAPSHOT_DIR
        self._max_parsed = max_parsed
        self._headers: dict[uuid.UUID, SnapshotHeader] = {}
        self._keys: dict[str, tuple[SnapshotKey, SnapshotHeader]] = {}
        self._parsed: OrderedDict[uuid.UUID, CachedRuleset] = OrderedDict()
        self.meta = ClusterMeta()
        self.valid = True
        self._lock = threading.Lock()

    def load(self) -> None:
        """Read cluster.json and every snapshot; delete the files that fail to verify."""
        self._snapshots.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._load()

    def _load(self) -> None:
        self._headers.clear()
        self._keys.clear()
        self._parsed.clear()
        self.meta = self._load_meta()
        for path in sorted(self._snapshots.iterdir()):
            if path.name.endswith(TMP_SUFFIX):
                path.unlink(missing_ok=True)
            elif path.name.endswith(SNAPSHOT_SUFFIX):
                snap = self._read(path)
                if snap is None:
                    logger.warning("discarding snapshot file %s: it failed to verify", path)
                    path.unlink(missing_ok=True)
                    self.valid = False
                else:
                    self._index(snap.header)

    def _load_meta(self) -> ClusterMeta:
        path = self._dir / META_FILE
        path.with_name(path.name + TMP_SUFFIX).unlink(missing_ok=True)
        try:
            return ClusterMeta.from_json(path.read_bytes())
        except FileNotFoundError:
            return ClusterMeta()
        except ValueError:
            logger.warning("ignoring %s: it is not valid", path)
            return ClusterMeta()

    def save_meta(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        write_atomic(self._dir / META_FILE, self.meta.to_json())

    def mark_synced(self) -> None:
        self.meta.synced = True
        self.valid = True
        self.save_meta()

    def versions(self) -> dict[uuid.UUID, int]:
        with self._lock:
            return {env_id: header.version for env_id, header in self._headers.items()}

    def max_version(self) -> int:
        with self._lock:
            return max((header.version for header in self._headers.values()), default=0)

    def apply(self, snap: SignedSnapshot) -> bool:
        """Store `snap`; False, keeping the old one, unless it is valid and newer."""
        header = snap.header
        with self._lock:
            current = self._headers.get(header.environment_id)
        if header.format != FORMAT or (current is not None and header.version <= current.version):
            return False
        if self.meta.public_key is None or not snap.verify(self.meta.public_key):
            return False
        try:
            parsed = from_body(snap.ruleset)
        except ValueError:
            logger.warning("rejecting the snapshot of %s: bad ruleset", header.environment_id)
            return False
        with self._lock:
            write_atomic(self._path(header.environment_id), snap.to_bytes())
            self._unindex(header.environment_id)
            self._index(header)
            self._remember(header.environment_id, parsed)
        return True

    def remove(self, environment_id: uuid.UUID) -> EnvRef | None:
        """Forget the environment and delete its file; None when it was not stored."""
        with self._lock:
            header = self._headers.get(environment_id)
            if header is None:
                return None
            self._unindex(environment_id)
            self._parsed.pop(environment_id, None)
            self._path(environment_id).unlink(missing_ok=True)
        _sync_directory(self._snapshots)
        return header.env_ref()

    def principal(self, secret_hash: str) -> SdkPrincipal | None:
        with self._lock:
            found = self._keys.get(secret_hash)
        if found is None:
            return None
        key, header = found
        return SdkPrincipal(
            key_id=key.id,
            kind=key.kind,
            secret_hash=secret_hash,
            environment=header.env_ref(),
            revoked=key.revoked_at is not None,
            expires_at=key.expires_at,
        )

    def keys_by_id(self, key_ids: set[uuid.UUID]) -> dict[uuid.UUID, SnapshotKey]:
        """The stored keys among `key_ids`, by id; a key in no snapshot is left out."""
        with self._lock:
            return {key.id: key for key, _ in self._keys.values() if key.id in key_ids}

    def entry(self, environment_id: uuid.UUID) -> CachedRuleset | None:
        """The parsed ruleset, from memory or else from its file; None when not stored."""
        while True:
            with self._lock:
                cached = self._parsed.get(environment_id)
                if cached is not None:
                    self._parsed.move_to_end(environment_id)
                    return cached
                header = self._headers.get(environment_id)
            if header is None:
                return None
            # Read and parse outside the lock. A snapshot applied or removed meanwhile means
            # starting again; only a file changed behind the store's back is an error.
            snap = self._read(self._path(environment_id))
            if snap is not None and snap.header.version == header.version:
                parsed = from_body(snap.ruleset)
                with self._lock:
                    if self._headers.get(environment_id) is header:
                        self._remember(environment_id, parsed)
                return parsed
            with self._lock:
                if self._headers.get(environment_id) is header:
                    logger.warning("snapshot file of %s changed on disk", environment_id)
                    return None

    def _path(self, environment_id: uuid.UUID) -> Path:
        return self._snapshots / f"{environment_id}{SNAPSHOT_SUFFIX}"

    def _read(self, path: Path) -> SignedSnapshot | None:
        """The verified snapshot in `path`, for the environment its name says; else None."""
        if self.meta.public_key is None:
            return None
        try:
            snap = SignedSnapshot.from_bytes(path.read_bytes())
        except (OSError, SnapshotError):
            return None
        if path.name != f"{snap.header.environment_id}{SNAPSHOT_SUFFIX}":
            return None
        if not snap.verify(self.meta.public_key):
            return None
        return snap

    def _index(self, header: SnapshotHeader) -> None:
        self._headers[header.environment_id] = header
        for key in header.keys:
            self._keys[key.hash] = (key, header)

    def _unindex(self, environment_id: uuid.UUID) -> None:
        header = self._headers.pop(environment_id, None)
        if header is None:
            return
        for key in header.keys:
            found = self._keys.get(key.hash)
            if found is not None and found[1] is header:
                del self._keys[key.hash]

    def _remember(self, environment_id: uuid.UUID, parsed: CachedRuleset) -> None:
        self._parsed[environment_id] = parsed
        self._parsed.move_to_end(environment_id)
        while len(self._parsed) > self._max_parsed:
            self._parsed.popitem(last=False)
