"""The last-known ruleset on disk, in the Go SDK's file format so a cache directory can be
shared between SDKs."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any


class CacheError(ValueError):
    """A cache file that is corrupt, or a ruleset that cannot be cached."""


@dataclass(frozen=True)
class Cached:
    """A cached ruleset: the decoded ruleset object and its ETag."""

    ruleset: Any
    etag: str


class Cache:
    """The file <dir>/megooflags-<first 16 hex of sha256(key)>.json holding {"etag", "ruleset"}.
    The key itself is never written to disk. Without a directory, load finds nothing and save
    does nothing."""

    def __init__(self, cache_dir: str | os.PathLike[str] | None, sdk_key: str) -> None:
        self._dir: str | None = None
        self._path: str | None = None
        if cache_dir is None or os.fspath(cache_dir) == "":
            return
        digest = hashlib.sha256(sdk_key.encode("utf-8")).hexdigest()
        self._dir = os.fspath(cache_dir)
        self._path = os.path.join(self._dir, f"megooflags-{digest[:16]}.json")

    def load(self) -> Cached | None:
        """The cached ruleset and ETag, or None when there is no file. Raises OSError for a file
        it cannot read and CacheError for corrupt content."""
        if self._path is None:
            return None
        try:
            with open(self._path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return None
        try:
            file = json.loads(data.decode("utf-8"))
        except ValueError as err:
            raise self._corrupt(str(err)) from None
        if not isinstance(file, dict):
            raise self._corrupt("not a JSON object")
        etag = file.get("etag", "")
        if not isinstance(etag, str):
            raise self._corrupt("etag is not a string")
        if "ruleset" not in file:
            raise self._corrupt("no ruleset")
        return Cached(ruleset=file["ruleset"], etag=etag)

    def save(self, raw: str | bytes, etag: str) -> None:
        """Write the ruleset JSON text, embedded as received, and its ETag to a temp file in the
        cache directory and rename it over the cache file, so a reader never sees a partial
        write. Raises CacheError when raw is not JSON and OSError when the write fails; leaves no
        temp file behind."""
        if self._dir is None or self._path is None:
            return
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        try:
            json.loads(text)
        except ValueError as err:
            raise CacheError(f"megooflags: cannot cache an invalid ruleset: {err}") from None
        content = f'{{"etag":{json.dumps(etag)},"ruleset":{text}}}\n'.encode()
        os.makedirs(self._dir, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".megooflags-", suffix=".tmp", dir=self._dir)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _corrupt(self, why: str) -> CacheError:
        return CacheError(f"megooflags: corrupt cache file {self._path}: {why}")
