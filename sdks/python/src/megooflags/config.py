"""The provider's options, their defaults and their validation."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlsplit

DEFAULT_INIT_TIMEOUT = 5.0
DEFAULT_POLL_INTERVAL = 30.0
MIN_POLL_INTERVAL = 15.0
DEFAULT_FLUSH_INTERVAL = 30.0
MIN_FLUSH_INTERVAL = 10.0
MAX_FLUSH_INTERVAL = 60.0

Duration = float | timedelta
PathLike = str | os.PathLike[str]


@dataclass(frozen=True)
class MegooFlagsConfig:
    """A MegooFlagsProvider's options with the defaults filled in and the intervals clamped.
    Durations are seconds. The key is left out of repr()."""

    sdk_key: str = field(repr=False)
    base_url: str
    bootstrap_file: str | None
    cache_dir: str | None
    offline: bool
    init_timeout: float
    disable_stream: bool
    poll_interval: float
    flush_interval: float
    send_context: bool
    logger: logging.Logger


def _seconds(value: Duration | None, default: float) -> float:
    """value in seconds when it is a positive duration, else default."""
    if value is None:
        return default
    s = value.total_seconds() if isinstance(value, timedelta) else float(value)
    return s if math.isfinite(s) and s > 0 else default


def _path(value: PathLike | None) -> str | None:
    if value is None:
        return None
    return os.fspath(value) or None


def make_config(
    *,
    sdk_key: str,
    base_url: str = "",
    bootstrap_file: PathLike | None = None,
    cache_dir: PathLike | None = None,
    offline: bool = False,
    init_timeout: Duration = DEFAULT_INIT_TIMEOUT,
    disable_stream: bool = False,
    poll_interval: Duration = DEFAULT_POLL_INTERVAL,
    flush_interval: Duration = DEFAULT_FLUSH_INTERVAL,
    send_context: bool = False,
    logger: logging.Logger | None = None,
) -> MegooFlagsConfig:
    """Build a MegooFlagsConfig: zero, negative and NaN durations mean the default, the poll
    interval is at least 15 s, the flush interval 10 to 60 s, and the base URL loses its
    trailing slashes."""
    flush = _seconds(flush_interval, DEFAULT_FLUSH_INTERVAL)
    return MegooFlagsConfig(
        sdk_key=sdk_key,
        base_url=base_url.rstrip("/"),
        bootstrap_file=_path(bootstrap_file),
        cache_dir=_path(cache_dir),
        offline=offline,
        init_timeout=_seconds(init_timeout, DEFAULT_INIT_TIMEOUT),
        disable_stream=disable_stream,
        poll_interval=max(_seconds(poll_interval, DEFAULT_POLL_INTERVAL), MIN_POLL_INTERVAL),
        flush_interval=min(max(flush, MIN_FLUSH_INTERVAL), MAX_FLUSH_INTERVAL),
        send_context=send_context,
        logger=logger or logging.getLogger("megooflags"),
    )


def config_error(c: MegooFlagsConfig) -> str | None:
    """Why the provider cannot run with c, or None. The message never includes the key."""
    key = c.sdk_key if isinstance(c.sdk_key, str) else ""
    if key.startswith("mf_client_"):
        return (
            "megooflags: a client SDK key cannot be used by a server SDK; "
            "use a server key (mf_server_…)"
        )
    if not key.startswith("mf_server_"):
        return "megooflags: invalid SDK key; use a server key (mf_server_…)"
    if c.offline:
        if c.bootstrap_file is None and c.cache_dir is None:
            return "megooflags: offline needs a bootstrap_file or a cache_dir"
        return None
    try:
        url = urlsplit(c.base_url)
        scheme, host = url.scheme, url.hostname
    except ValueError:
        scheme, host = "", None
    if scheme not in ("http", "https") or not host:
        return "megooflags: base_url must be an http or https URL"
    return None
