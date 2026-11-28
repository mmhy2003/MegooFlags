"""Version parsing and the GitHub release fetch for update notices. No database, no network."""

from collections.abc import Callable

import httpx
import pytest

from megooflags_api.services.updates import (
    MAX_BODY_BYTES,
    RELEASES_URL,
    UpdateCheckError,
    fetch_latest,
    is_newer,
    parse_release_tag,
    release_url,
    version_key,
)

Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_parse_release_tag() -> None:
    assert parse_release_tag("v1.2.3") == "1.2.3"
    assert parse_release_tag("v0.10.0") == "0.10.0"
    bad: list[object] = ["1.2.3", "v1.2", "v01.2.3", "v1.2.3-rc.1", "v1.2.3+b", " v1.2.3"]
    for tag in [*bad, "v1.2.3\n", None, 5]:
        assert parse_release_tag(tag) is None, tag


def test_is_newer() -> None:
    assert is_newer("1.1.0", "1.0.0") is True
    assert is_newer("1.0.0", "1.0.0") is False
    assert is_newer("1.0.0", "1.1.0") is False
    assert is_newer("1.1.0", "1.1.0-rc.1") is True
    assert is_newer("1.0.10", "1.0.9") is True
    assert is_newer("2.0.0", "1.99.99") is True
    assert is_newer("1.0.0", "garbage") is False
    assert is_newer("garbage", "1.0.0") is False
    assert is_newer("1.0.1", "1.0.0+build.5") is True
    assert is_newer("1.0.0", "1.0.0+build.5") is False


def test_version_key_prerelease_order() -> None:
    ordered = [
        "1.0.0-alpha",
        "1.0.0-alpha.1",
        "1.0.0-alpha.beta",
        "1.0.0-beta",
        "1.0.0-beta.2",
        "1.0.0-beta.11",
        "1.0.0-rc.1",
        "1.0.0",
    ]
    keys = [version_key(v) for v in ordered]
    assert all(k is not None for k in keys)
    assert keys == sorted(keys)  # type: ignore[type-var]
    assert len(set(keys)) == len(keys)
    assert version_key("1.0.0+build") == version_key("1.0.0")
    for bad in ("", "1.0", "01.0.0", "1.0.0-", "1.0.0-a..b", "v1.0.0", "1.0.0+"):
        assert version_key(bad) is None, bad


def test_release_url() -> None:
    assert release_url("1.1.0") == "https://github.com/mmhy2003/MegooFlags/releases/tag/v1.1.0"


async def test_fetch_latest_ok() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"tag_name": "v1.4.0", "html_url": "https://evil.example"})

    async with client_for(handler) as client:
        assert await fetch_latest(client, running="1.0.0") == "1.4.0"

    [request] = seen
    assert request.method == "GET"
    assert str(request.url) == RELEASES_URL
    assert request.url.query == b""
    assert request.content == b""
    assert request.headers["User-Agent"] == "MegooFlags/1.0.0"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert "Authorization" not in request.headers
    assert "Cookie" not in request.headers


async def test_fetch_latest_404_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    async with client_for(handler) as client:
        assert await fetch_latest(client, running="0.1.0") is None


def _status(code: int) -> Handler:
    return lambda request: httpx.Response(code, json={"tag_name": "v1.4.0"})


def _body(content: bytes) -> Handler:
    return lambda request: httpx.Response(200, content=content)


def _padded(size: int) -> bytes:
    """A valid release body of exactly `size` bytes, so only its size can fail it."""
    prefix = b'{"tag_name": "v1.4.0"'
    return prefix + b" " * (size - len(prefix) - 1) + b"}"


def _raise(error: type[httpx.TransportError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error("boom", request=request)

    return handler


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(_status(500), id="500"),
        pytest.param(_status(403), id="403"),
        pytest.param(_body(b"<html>not json</html>"), id="not-json"),
        pytest.param(_body(b"\xff\xfe\xfa"), id="not-utf8"),
        pytest.param(_body(b'{"tag_name": "v1.4.0-rc.1"}'), id="rc-tag"),
        pytest.param(_body(b"{}"), id="no-tag"),
        pytest.param(_body(b'["v1.4.0"]'), id="not-object"),
        pytest.param(_body(_padded(MAX_BODY_BYTES + 1)), id="oversized"),
        pytest.param(_raise(httpx.ConnectError), id="connect-error"),
        pytest.param(_raise(httpx.ReadTimeout), id="read-timeout"),
    ],
)
async def test_fetch_latest_errors(handler: Handler) -> None:
    async with client_for(handler) as client:
        with pytest.raises(UpdateCheckError):
            await fetch_latest(client, running="1.0.0")


async def test_fetch_latest_accepts_body_at_the_limit() -> None:
    body = _padded(MAX_BODY_BYTES)
    assert len(body) == MAX_BODY_BYTES

    async with client_for(_body(body)) as client:
        assert await fetch_latest(client, running="1.0.0") == "1.4.0"
