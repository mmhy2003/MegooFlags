import dataclasses
import logging
from datetime import timedelta
from pathlib import Path

import pytest

from megooflags import MegooFlagsConfig
from megooflags.config import config_error, make_config

KEY = "mf_server_config-secret-value"


def test_fills_in_the_defaults() -> None:
    c = make_config(sdk_key=KEY, base_url="https://flags.example.com")
    assert c == MegooFlagsConfig(
        sdk_key=KEY,
        base_url="https://flags.example.com",
        bootstrap_file=None,
        cache_dir=None,
        offline=False,
        init_timeout=5.0,
        disable_stream=False,
        poll_interval=30.0,
        flush_interval=30.0,
        send_context=False,
        logger=logging.getLogger("megooflags"),
    )


def test_is_frozen() -> None:
    c = make_config(sdk_key=KEY, base_url="https://flags.example.com")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.sdk_key = "other"  # type: ignore[misc]


def test_clamps_the_intervals() -> None:
    c = make_config(sdk_key=KEY, poll_interval=1, flush_interval=1)
    assert (c.poll_interval, c.flush_interval) == (15.0, 10.0)
    c = make_config(sdk_key=KEY, poll_interval=120, flush_interval=600)
    assert (c.poll_interval, c.flush_interval) == (120.0, 60.0)


def test_accepts_timedelta_durations() -> None:
    c = make_config(
        sdk_key=KEY,
        init_timeout=timedelta(milliseconds=250),
        poll_interval=timedelta(minutes=1),
        flush_interval=timedelta(seconds=20),
    )
    assert (c.init_timeout, c.poll_interval, c.flush_interval) == (0.25, 60.0, 20.0)
    assert all(isinstance(v, float) for v in (c.init_timeout, c.poll_interval, c.flush_interval))


@pytest.mark.parametrize("bad", [0, -1, float("nan"), timedelta(0)])
def test_treats_zero_negative_and_nan_durations_as_unset(bad: float | timedelta) -> None:
    c = make_config(sdk_key=KEY, init_timeout=bad, poll_interval=bad, flush_interval=bad)
    assert (c.init_timeout, c.poll_interval, c.flush_interval) == (5.0, 30.0, 30.0)


def test_removes_trailing_slashes_from_the_base_url() -> None:
    assert make_config(sdk_key=KEY, base_url="https://x.example//").base_url == "https://x.example"


def test_keeps_paths_and_an_empty_cache_dir_is_none(tmp_path: Path) -> None:
    c = make_config(sdk_key=KEY, bootstrap_file=tmp_path / "r.json", cache_dir="")
    assert c.bootstrap_file == str(tmp_path / "r.json")
    assert c.cache_dir is None


def test_uses_the_given_logger() -> None:
    log = logging.getLogger("app.flags")
    assert make_config(sdk_key=KEY, logger=log).logger is log


def test_accepts_a_server_key_with_an_http_or_https_base_url() -> None:
    for url in ("http://localhost:8000", "https://flags.example.com/base"):
        assert config_error(make_config(sdk_key=KEY, base_url=url)) is None


def test_refuses_a_client_key() -> None:
    err = config_error(make_config(sdk_key="mf_client_abc", base_url="https://x.example"))
    assert err is not None
    assert "client SDK key cannot be used by a server SDK" in err
    assert "mf_server_" in err


@pytest.mark.parametrize("key", ["", "abc", "MF_SERVER_x", "mf_serverx"])
def test_refuses_a_key_that_is_not_a_server_key(key: str) -> None:
    err = config_error(make_config(sdk_key=key, base_url="https://x.example"))
    assert err is not None
    assert "invalid SDK key" in err


@pytest.mark.parametrize(
    "url", ["", "flags.example.com", "ftp://flags.example.com", "http://", "https:///path"]
)
def test_refuses_a_base_url_that_is_not_http_or_https_with_a_host(url: str) -> None:
    err = config_error(make_config(sdk_key=KEY, base_url=url))
    assert err is not None
    assert "base_url must be an http or https URL" in err


def test_needs_a_bootstrap_file_or_a_cache_dir_offline(tmp_path: Path) -> None:
    err = config_error(make_config(sdk_key=KEY, offline=True))
    assert err is not None
    assert "offline needs a bootstrap_file or a cache_dir" in err
    assert config_error(make_config(sdk_key=KEY, offline=True, cache_dir=tmp_path)) is None
    assert config_error(make_config(sdk_key=KEY, offline=True, bootstrap_file="r.json")) is None


def test_offline_needs_no_base_url(tmp_path: Path) -> None:
    assert config_error(make_config(sdk_key=KEY, offline=True, cache_dir=tmp_path)) is None


def test_never_puts_the_key_in_a_message() -> None:
    cases = [
        make_config(sdk_key="mf_client_secret-value-1", base_url="https://x.example"),
        make_config(sdk_key="secret-value-2", base_url="https://x.example"),
        make_config(sdk_key="mf_server_secret-value-3", base_url="nope"),
        make_config(sdk_key="mf_server_secret-value-4", offline=True),
    ]
    for c in cases:
        err = config_error(c)
        assert err is not None
        assert c.sdk_key not in err
        assert "secret-value" not in err
        assert c.sdk_key not in repr(c)
