import hashlib
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from megooflags._delivery.cache import Cache, Cached, CacheError

KEY = "mf_server_secret-key-123"
RAW = '{"project":"p","environment":"e","flags":{},"segments":{}}'
posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def file_name(key: str) -> str:
    return f"megooflags-{hashlib.sha256(key.encode()).hexdigest()[:16]}.json"


def test_round_trips_the_ruleset_and_the_etag(tmp_path: Path) -> None:
    c = Cache(tmp_path, KEY)
    c.save(RAW, '"v1"')
    assert c.load() == Cached(ruleset=json.loads(RAW), etag='"v1"')


def test_accepts_raw_bytes(tmp_path: Path) -> None:
    c = Cache(tmp_path, KEY)
    c.save(RAW.encode(), "b")
    assert c.load() == Cached(ruleset=json.loads(RAW), etag="b")


def test_writes_etag_and_ruleset_embedded_as_received(tmp_path: Path) -> None:
    Cache(tmp_path, KEY).save(RAW, '"v1"')
    text = (tmp_path / file_name(KEY)).read_text("utf-8")
    assert f'"ruleset":{RAW}' in text
    assert json.loads(text) == {"etag": '"v1"', "ruleset": json.loads(RAW)}


def test_reads_a_file_written_by_the_go_sdk(tmp_path: Path) -> None:
    (tmp_path / file_name(KEY)).write_text(f'{{"etag":"\\"g\\"","ruleset":{RAW}}}\n')
    assert Cache(tmp_path, KEY).load() == Cached(ruleset=json.loads(RAW), etag='"g"')


def test_names_the_file_after_16_hex_characters_of_the_key_hash(tmp_path: Path) -> None:
    Cache(tmp_path, KEY).save("{}", "x")
    entries = os.listdir(tmp_path)
    assert entries == [file_name(KEY)]
    assert len(entries[0]) == len("megooflags-") + 16 + len(".json")
    assert all(ch in "0123456789abcdef" for ch in entries[0][11:27])
    assert KEY not in entries[0]
    assert KEY not in (tmp_path / entries[0]).read_text()
    assert file_name("another-key") != file_name(KEY)


def test_hashes_the_key_as_utf8(tmp_path: Path) -> None:
    Cache(tmp_path, "mf_server_é").save("{}", "x")
    assert os.listdir(tmp_path) == [file_name("mf_server_é")]


@posix
def test_writes_the_file_with_mode_0600(tmp_path: Path) -> None:
    Cache(tmp_path, KEY).save("{}", "x")
    assert stat.S_IMODE((tmp_path / file_name(KEY)).stat().st_mode) == 0o600


def test_replaces_the_file_atomically_and_leaves_no_temp_file(tmp_path: Path) -> None:
    c = Cache(tmp_path, KEY)
    c.save('{"n":1}', "1")
    c.save('{"n":2}', "2")
    assert c.load() == Cached(ruleset={"n": 2}, etag="2")
    assert os.listdir(tmp_path) == [file_name(KEY)]


def test_creates_the_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    c = Cache(nested, KEY)
    c.save("{}", "x")
    assert c.load() == Cached(ruleset={}, etag="x")
    if sys.platform != "win32":
        assert stat.S_IMODE(nested.stat().st_mode) == 0o700


def test_refuses_invalid_json_and_writes_nothing(tmp_path: Path) -> None:
    c = Cache(tmp_path, KEY)
    with pytest.raises(CacheError, match="invalid ruleset"):
        c.save("{not json", "x")
    assert os.listdir(tmp_path) == []


def test_a_failed_write_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        Cache(tmp_path, KEY).save("{}", "x")
    assert os.listdir(tmp_path) == []


def test_load_of_a_missing_file_is_not_found(tmp_path: Path) -> None:
    assert Cache(tmp_path, KEY).load() is None


@pytest.mark.parametrize(
    "content",
    [
        '{"etag": "x", "ruleset": ',
        "[]",
        '{"etag": 1, "ruleset": {}}',
        '{"etag": "x"}',
        b"\xff\xfe",
    ],
)
def test_load_of_corrupt_content_raises(tmp_path: Path, content: str | bytes) -> None:
    path = tmp_path / file_name(KEY)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    with pytest.raises(CacheError, match="corrupt cache file"):
        Cache(tmp_path, KEY).load()


def test_a_missing_etag_is_empty(tmp_path: Path) -> None:
    (tmp_path / file_name(KEY)).write_text('{"ruleset": {}}')
    assert Cache(tmp_path, KEY).load() == Cached(ruleset={}, etag="")


def test_load_of_an_unreadable_path_raises(tmp_path: Path) -> None:
    (tmp_path / file_name(KEY)).write_text("x")
    c = Cache(tmp_path / file_name(KEY), KEY)
    with pytest.raises(OSError):
        c.load()


@pytest.mark.parametrize("cache_dir", [None, ""])
def test_without_a_directory_load_is_not_found_and_save_does_nothing(
    cache_dir: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    c = Cache(cache_dir, KEY)
    assert c.load() is None
    c.save("{}", "x")
    assert os.listdir(tmp_path) == []
