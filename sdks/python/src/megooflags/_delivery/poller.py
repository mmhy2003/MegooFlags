"""One download of GET /sdk/v1/ruleset. The poll loop and its interval belong to Sync."""

from __future__ import annotations

import time
from collections.abc import Callable

from megooflags._transport import Cancel, Cancelled, HttpError, build_headers, status_error


class Poller:
    """Downloads the ruleset. etag returns the ETag sent as If-None-Match when not empty;
    on_ruleset receives a 200 body with its ETag header and returns False for an invalid
    ruleset. timeout (10 s) is the socket timeout: connecting and each read."""

    def __init__(
        self,
        *,
        url: str,
        sdk_key: str,
        etag: Callable[[], str] = lambda: "",
        on_ruleset: Callable[[bytes, str], bool] = lambda raw, etag: True,
        timeout: float = 10.0,
    ) -> None:
        self._url = url
        self._sdk_key = sdk_key
        self._etag = etag
        self._on_ruleset = on_ruleset
        self._timeout = timeout

    def poll(self, cancel: Cancel) -> None:
        """Download the ruleset once. A 304 keeps what we have; a 200 goes to on_ruleset.
        Raises status_error's error for other statuses, TimeoutError after the timeout,
        ValueError for an invalid ruleset, Cancelled when cancel is set and OSError (or an
        http.client error) for a network failure."""
        headers = build_headers(self._sdk_key)
        etag = self._etag()
        if etag:
            headers["If-None-Match"] = etag
        try:
            with cancel.open("GET", self._url, headers=headers, timeout=self._timeout) as resp:
                if resp.status == 304:
                    return
                if resp.status != 200:
                    raise status_error(resp.status, resp.headers, time.time()) or HttpError(
                        resp.status
                    )
                raw = resp.read()
                got = resp.headers.get("ETag") or ""
        except TimeoutError:
            raise TimeoutError(
                f"megooflags: ruleset poll timed out after {self._timeout:g}s"
            ) from None
        except Exception:
            if cancel.is_set():
                raise Cancelled() from None
            raise
        if cancel.is_set():
            raise Cancelled()
        if not self._on_ruleset(raw, got):
            raise ValueError("megooflags: invalid ruleset")
